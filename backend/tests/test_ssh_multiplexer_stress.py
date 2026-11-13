"""Empirical Stress Tests: AsyncSSH Multiplexer Gateway & Port Pool.

Challenger Test Suite for Milestone 2 (Phase 2):
1. Concurrency stress testing: 10 concurrent SSH client connections over a single
   upstream WebSocket tunnel, verifying parallel execution, single upstream connection,
   and zero HTTP 429 errors.
2. SFTP & binary data integrity: Non-UTF8 arbitrary byte payloads transferred via SFTP,
   verifying byte-for-byte fidelity and handshake buffering (_pending_input).
3. Port pool boundaries: 10-port exhaustion (2220-2229), 11th allocation rejection,
   session stop/delete port release, and immediate reusability.
4. Empirical bug reproduction:
   - Upstream socketpair unpack error (missing host='localhost' in asyncssh.connect).
   - Premature channel closure on EOF (eof_received returning False and missing _pending_eof).
"""

from __future__ import annotations

import asyncio
import http
import os
import socket
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

import asyncssh
import websockets

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import encrypt_secret
from backend.app.services.session_manager import allocate_ssh_port, stop_session
from backend.app.services.ssh_multiplexer import (
    ColabSSHGateway,
    SSHGatewayServer,
    UpstreamSSHBusyError,
    UpstreamSSHError,
    ssh_multiplexer_service,
)


class MockColabSSHServer(asyncssh.SSHServer):
    """Permissive backend SSH daemon handling command execution and SFTP."""

    def begin_auth(self, username: str) -> bool:
        return False

    def session_requested(self) -> asyncssh.SSHServerSession:
        return MockColabSSHServerSession()


class MockColabSSHServerSession(asyncssh.SSHServerSession):
    def __init__(self):
        self._chan: Optional[asyncssh.SSHServerChannel] = None

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        self._chan = chan

    def exec_requested(self, command: str) -> bool:
        return True

    def session_started(self) -> None:
        if not self._chan:
            return
        cmd = self._chan.get_command() or ""
        # Simulate command execution
        if cmd.startswith("sleep_and_echo "):
            parts = cmd.split(" ", 2)
            delay = float(parts[1])
            msg = parts[2] if len(parts) > 2 else ""

            async def _delayed_reply():
                await asyncio.sleep(delay)
                self._chan.write(f"OK:{msg}\n".encode())
                self._chan.exit(0)
                self._chan.close()

            asyncio.create_task(_delayed_reply())
        else:
            self._chan.write(f"REPLY:{cmd}\n".encode())
            self._chan.exit(0)
            self._chan.close()


class TestSSHMultiplexerStress(unittest.IsolatedAsyncioTestCase):
    """Comprehensive Empirical Stress Tests for AsyncSSH Multiplexer."""

    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "stress_manager.db")
        self.orig_db_path = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.account_id = "acc_stress_001"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly, status)
                VALUES (?, 'stress@example.com', 'Stress Account', 'enc', 500.0, 1.0, 'ACTIVE')
                """,
                (self.account_id,),
            )

        self.servers_to_close: List[Any] = []
        self.connections_to_close: List[Any] = []

    async def asyncTearDown(self):
        for conn in self.connections_to_close:
            try:
                conn.close()
            except Exception:
                pass
        self.connections_to_close.clear()

        for s in self.servers_to_close:
            try:
                s.close()
                if hasattr(s, "wait_closed"):
                    await s.wait_closed()
            except Exception:
                pass
        self.servers_to_close.clear()

        await ssh_multiplexer_service.shutdown()
        settings.DATABASE_PATH = self.orig_db_path
        self.tmpdir.cleanup()

    # =========================================================================
    # 1. Port Pool Boundary Tests
    # =========================================================================

    async def test_port_pool_boundaries_and_immediate_reuse(self):
        """Stress-test 10-port pool boundary (2220-2229), exhaustion, release, and reuse."""
        allocated_ports: List[int] = []

        # 1. Allocate all 10 ports in sequence
        for i in range(10):
            port = allocate_ssh_port()
            self.assertIsNotNone(port, f"Failed to allocate port at index {i}")
            allocated_ports.append(port)
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, ssh_local_port, status
                    ) VALUES (?, ?, ?, 'hash', 'ep', 'url', 'token', CURRENT_TIMESTAMP, 'T4', ?, 'READY')
                    """,
                    (f"sess_{i}", f"node_{i}", self.account_id, port),
                )

        self.assertEqual(len(set(allocated_ports)), 10)
        self.assertEqual(allocated_ports, list(range(2220, 2230)))

        # 2. 11th allocation attempt MUST fail gracefully (return None)
        port_11 = allocate_ssh_port()
        self.assertIsNone(port_11, "11th port allocation should be rejected when pool is exhausted")

        # 3. Stop session #5 (which holds port 2225)
        released_port = allocated_ports[5]
        stop_session("sess_5")

        with get_db() as conn:
            row = conn.execute("SELECT ssh_local_port, status FROM sessions WHERE id = 'sess_5'").fetchone()
            self.assertEqual(row["status"], "STOPPED")
            self.assertIsNone(row["ssh_local_port"])

        # 4. Immediate reallocation MUST retrieve the released port (2225)
        reallocated_port = allocate_ssh_port()
        self.assertEqual(reallocated_port, released_port)

    # =========================================================================
    # 2. Concurrency Stress Test: 10 Clients over 1 Upstream WebSocket
    # =========================================================================

    async def test_concurrency_10_clients_over_single_upstream_websocket(self):
        """Verify 10 concurrent SSH client connections execute commands in parallel
        over a single upstream WebSocket tunnel without HTTP 429 lock triggering.
        """
        backend_key = asyncssh.generate_private_key("ssh-ed25519")
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        gw_key = asyncssh.generate_private_key("ssh-ed25519")

        # 1. Start backend SSH server
        backend = await asyncssh.create_server(
            MockColabSSHServer,
            "127.0.0.1",
            0,
            server_host_keys=[backend_key],
            encoding=None,
        )
        backend_port = backend.sockets[0].getsockname()[1]
        self.servers_to_close.append(backend)

        # 2. Start WebSocket server tracking active connections and enforcing single connection
        ws_lock_acquired = False
        ws_connection_count = 0
        http_429_count = 0

        async def ws_handler(ws):
            nonlocal ws_lock_acquired, ws_connection_count, http_429_count
            if ws_lock_acquired:
                http_429_count += 1
                await ws.close(code=4429, reason="HTTP 429 Lock Exceeded")
                return

            ws_lock_acquired = True
            ws_connection_count += 1
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", backend_port)

                async def from_ws():
                    try:
                        async for msg in ws:
                            writer.write(msg if isinstance(msg, bytes) else msg.encode())
                            await writer.drain()
                    except Exception:
                        pass
                    finally:
                        writer.close()

                async def to_ws():
                    try:
                        while True:
                            data = await reader.read(4096)
                            if not data:
                                break
                            await ws.send(data)
                    except Exception:
                        pass

                await asyncio.gather(from_ws(), to_ws(), return_exceptions=True)
            finally:
                ws_lock_acquired = False

        ws_server = await websockets.serve(ws_handler, "127.0.0.1", 0)
        ws_port = ws_server.sockets[0].getsockname()[1]
        self.servers_to_close.append(ws_server)

        # 3. Setup Gateway with upstream connection over WebSocket
        gw = ColabSSHGateway("sess_concurrency", 0, gw_key, client_key)
        ws = await websockets.connect(f"ws://127.0.0.1:{ws_port}", open_timeout=5)
        gw.upstream_ws = ws

        sock_ws, sock_ssh = socket.socketpair()
        sock_ws.setblocking(False)
        sock_ssh.setblocking(False)
        gw._sockets.extend([sock_ws, sock_ssh])
        loop = asyncio.get_running_loop()

        async def ws_to_sock():
            try:
                async for m in ws:
                    if isinstance(m, str):
                        m = m.encode()
                    await loop.sock_sendall(sock_ws, m)
            except Exception:
                pass

        async def sock_to_ws():
            try:
                while True:
                    c = await loop.sock_recv(sock_ws, 8192)
                    if not c:
                        break
                    await ws.send(c)
            except Exception:
                pass

        gw._bridge_tasks.extend([asyncio.create_task(ws_to_sock()), asyncio.create_task(sock_to_ws())])

        # Connect upstream AsyncSSH using host='localhost'
        gw.upstream_conn = await asyncssh.connect(
            host="localhost",
            sock=sock_ssh,
            username="root",
            client_keys=[client_key],
            known_hosts=None,
            encoding=None,
        )

        gw_listener = await asyncssh.create_server(
            lambda: SSHGatewayServer(gw),
            host="127.0.0.1",
            port=0,
            server_host_keys=[gw_key],
            encoding=None,
        )
        gw_port = gw_listener.sockets[0].getsockname()[1]
        self.servers_to_close.append(gw_listener)

        # 4. Launch 10 concurrent SSH clients executing commands in parallel
        start_time = time.time()

        async def run_client(idx: int) -> bytes:
            conn = await asyncssh.connect("127.0.0.1", gw_port, known_hosts=None, encoding=None)
            res = await conn.run(f"sleep_and_echo 0.2 client_{idx}")
            conn.close()
            return res.stdout

        tasks = [run_client(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - start_time

        # Verify all 10 clients got correct output
        for i, out in enumerate(results):
            self.assertEqual(out, f"OK:client_{i}\n".encode())

        # If executed sequentially, 10 * 0.2s = 2.0s+. Parallel execution finishes < 1.5s.
        self.assertLess(elapsed, 1.5, f"Execution took {elapsed:.2f}s, expected parallel execution < 1.5s")

        # 5. Verify upstream invariant: EXACTLY 1 WebSocket, 0 HTTP 429
        self.assertEqual(ws_connection_count, 1)
        self.assertEqual(http_429_count, 0)
        self.assertFalse(gw.upstream_conn.is_closed())

        await gw.close()

    # =========================================================================
    # 3. SFTP & Binary Data Integrity Test
    # =========================================================================

    async def test_sftp_binary_data_integrity_and_handshake_buffering(self):
        """Stress-test SFTP file transfers through the multiplexer with non-UTF8 arbitrary
        binary payloads, verifying zero decoding errors and proper input buffering.
        """
        backend_key = asyncssh.generate_private_key("ssh-ed25519")
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        gw_key = asyncssh.generate_private_key("ssh-ed25519")

        with tempfile.TemporaryDirectory() as sftp_root:
            backend = await asyncssh.create_server(
                MockColabSSHServer,
                "127.0.0.1",
                0,
                server_host_keys=[backend_key],
                sftp_factory=lambda chan: asyncssh.SFTPServer(chan, chroot=sftp_root),
                encoding=None,
            )
            backend_port = backend.sockets[0].getsockname()[1]
            self.servers_to_close.append(backend)

            # Upstream bridge
            gw = ColabSSHGateway("sess_sftp", 0, gw_key, client_key)
            gw.upstream_conn = await asyncssh.connect(
                host="localhost",
                port=backend_port,
                client_keys=[client_key],
                known_hosts=None,
                encoding=None,
            )

            gw_listener = await asyncssh.create_server(
                lambda: SSHGatewayServer(gw),
                host="127.0.0.1",
                port=0,
                server_host_keys=[gw_key],
                encoding=None,
            )
            gw_port = gw_listener.sockets[0].getsockname()[1]
            self.servers_to_close.append(gw_listener)

            # Connect client and open SFTP session
            client_conn = await asyncssh.connect("127.0.0.1", gw_port, known_hosts=None, encoding=None)
            self.connections_to_close.append(client_conn)

            async with client_conn.start_sftp_client() as sftp:
                # 64KB non-UTF8 binary payload with nulls, high bytes, and control chars
                binary_payload = bytes(range(256)) * 256
                self.assertEqual(len(binary_payload), 65536)

                async with sftp.open("binary_payload.bin", "wb") as f:
                    await f.write(binary_payload)

                async with sftp.open("binary_payload.bin", "rb") as f:
                    received_data = await f.read()

                self.assertEqual(len(received_data), 65536)
                self.assertEqual(received_data, binary_payload)

            await gw.close()

    # =========================================================================
    # 4. Empirical Vulnerability Repositories
    # =========================================================================

    async def test_vulnerability_socketpair_peername_unpack_failure(self):
        """EMPIRICAL FINDING: ColabSSHGateway._connect_upstream() fails with
        TypeError: cannot unpack non-iterable NoneType object because asyncssh.connect()
        omits host='localhost' when using an AF_UNIX socketpair.
        """
        # Echoing WebSocket server that responds to initial SSH banner
        async def echo_ws(ws):
            async for m in ws:
                await ws.send(m)

        ws_server = await websockets.serve(echo_ws, "127.0.0.1", 0)
        ws_port = ws_server.sockets[0].getsockname()[1]
        self.servers_to_close.append(ws_server)

        # Register session in DB
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status
                ) VALUES ('sess_vuln_1', 'vuln1', ?, 'h', 'ep', ?, ?, CURRENT_TIMESTAMP, 'T4', 'READY')
                """,
                (self.account_id, f"http://127.0.0.1:{ws_port}", encrypt_secret("token")),
            )

        client_key = asyncssh.generate_private_key("ssh-ed25519")
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        gw = ColabSSHGateway("sess_vuln_1", 2220, host_key, client_key)

        # Directly invoke _connect_upstream() - must NOT fail with NoneType unpack error
        try:
            await gw._connect_upstream()
        except UpstreamSSHError as e:
            self.assertNotIn("cannot unpack non-iterable NoneType object", str(e))
        finally:
            await gw.close()

    async def test_vulnerability_proxy_server_session_eof_drops_stdout(self):
        """EMPIRICAL FINDING: ProxyServerSession.eof_received() returns False and
        drops _pending_eof, causing client to hang and time out on stdin EOF.
        """
        backend_key = asyncssh.generate_private_key("ssh-ed25519")
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        gw_key = asyncssh.generate_private_key("ssh-ed25519")

        class EofEchoServer(asyncssh.SSHServer):
            def begin_auth(self, username: str) -> bool:
                return False

            def session_requested(self) -> asyncssh.SSHServerSession:
                return EofEchoSession()

        class EofEchoSession(asyncssh.SSHServerSession):
            def __init__(self):
                self._chan = None
                self.buf = bytearray()

            def connection_made(self, chan):
                self._chan = chan

            def exec_requested(self, cmd):
                return True

            def data_received(self, data, datatype):
                self.buf.extend(data)

            def eof_received(self):
                self._chan.write(b"ECHO:" + self.buf)
                self._chan.write_eof()
                self._chan.exit(0)
                self._chan.close()

        backend = await asyncssh.create_server(
            EofEchoServer, "127.0.0.1", 0, server_host_keys=[backend_key], encoding=None
        )
        b_port = backend.sockets[0].getsockname()[1]
        self.servers_to_close.append(backend)

        gw = ColabSSHGateway("sess_eof_drop", 0, gw_key, client_key)
        gw.upstream_conn = await asyncssh.connect(
            host="localhost", port=b_port, client_keys=[client_key], known_hosts=None, encoding=None
        )

        gw_listener = await asyncssh.create_server(
            lambda: SSHGatewayServer(gw),
            host="127.0.0.1",
            port=0,
            server_host_keys=[gw_key],
            encoding=None,
        )
        gw_port = gw_listener.sockets[0].getsockname()[1]
        self.servers_to_close.append(gw_listener)

        client_conn = await asyncssh.connect("127.0.0.1", gw_port, known_hosts=None, encoding=None)
        self.connections_to_close.append(client_conn)

        # Command completes successfully because stdin EOF is properly forwarded and channel stays open for stdout
        res = await asyncio.wait_for(client_conn.run("cat", input=b"test_payload"), timeout=5.0)
        self.assertEqual(res.stdout, b"ECHO:test_payload")
        self.assertEqual(res.exit_status, 0)

        await gw.close()

    async def test_upstream_http_429_busy_detection(self):
        """Verify that HTTP 429 status code raises UpstreamSSHBusyError."""
        client_key = asyncssh.generate_private_key("ssh-ed25519")
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        gw = ColabSSHGateway("sess_busy", 2220, host_key, client_key)

        async def busy_ws(ws):
            pass

        def reject_429(connection, request):
            return connection.respond(http.HTTPStatus.TOO_MANY_REQUESTS, "HTTP 429 Busy\n")

        ws_server = await websockets.serve(busy_ws, "127.0.0.1", 0, process_request=reject_429)
        ws_port = ws_server.sockets[0].getsockname()[1]
        self.servers_to_close.append(ws_server)

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status
                ) VALUES ('sess_busy', 'busy', ?, 'h', 'ep', ?, ?, CURRENT_TIMESTAMP, 'T4', 'READY')
                """,
                (self.account_id, f"http://127.0.0.1:{ws_port}", encrypt_secret("token")),
            )

        with self.assertRaises(UpstreamSSHBusyError):
            await gw._connect_upstream()

        await gw.close()
