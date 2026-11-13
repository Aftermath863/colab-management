"""In-Daemon AsyncSSH Multiplexer Gateway for Google Colab Sessions.

Bypasses Colab's strict HTTP 429 (single active SSH WebSocket) limitation by
running local AsyncSSH servers on ports 2220-2229 and multiplexing multiple
concurrent client connections (VS Code Remote-SSH, terminals, SFTP) across a
single persistent upstream WebSocket tunnel.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import socket
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import asyncssh
import websockets

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.security import decrypt_secret

logger = logging.getLogger("ssh_multiplexer")

COLAB_SSH_SUBPATH = "/colab/ssh"
PUBKEY_HEADER = "X-Colab-Ssh-Pubkey"


class UpstreamSSHError(Exception):
    """Base exception for upstream Colab SSH errors."""
    pass


class UpstreamSSHBusyError(UpstreamSSHError):
    """Raised when Colab returns HTTP 429 (another connection is already active)."""
    pass


class UpstreamSSHAuthError(UpstreamSSHError):
    """Raised on HTTP 400 (invalid pubkey) or HTTP 401 (invalid proxy token)."""
    pass


class SSHPortManager:
    """Thread-safe and async-safe allocator for host port pool 2220-2229."""

    def __init__(self, start_port: int = 2220, end_port: int = 2229):
        self.start_port = start_port
        self.end_port = end_port
        self._lock = asyncio.Lock()

    def _is_port_available_on_os(self, port: int) -> bool:
        """Check if port can be bound on 0.0.0.0 without conflict."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                return True
        except OSError:
            return False

    async def allocate(self) -> Optional[int]:
        """Atomically find and reserve the lowest available port."""
        async with self._lock:
            with get_db() as conn:
                rows = conn.execute(
                    """
                    SELECT ssh_local_port FROM sessions
                    WHERE ssh_local_port IS NOT NULL
                      AND status NOT IN ('STOPPED', 'FAILED', 'LOST')
                    """
                ).fetchall()
                used_ports = {row["ssh_local_port"] for row in rows}

            for port in range(self.start_port, self.end_port + 1):
                if port not in used_ports and self._is_port_available_on_os(port):
                    return port
            return None


class ProxyClientSession(asyncssh.SSHClientSession):
    """Upstream channel session forwarding remote SSH traffic back to local client."""

    def __init__(self, server_chan: asyncssh.SSHServerChannel, gateway: "ColabSSHGateway"):
        self.server_chan = server_chan
        self.gateway = gateway
        self.exit_status: Optional[int] = None

    def data_received(self, data: bytes, datatype: Optional[int]) -> None:
        self.gateway.record_activity()
        self.server_chan.write(data, datatype=datatype)

    def eof_received(self) -> None:
        self.server_chan.write_eof()
        if self.exit_status is not None:
            try:
                self.server_chan.exit(self.exit_status)
            except Exception:
                pass

    def exit_status_received(self, status: int) -> None:
        self.exit_status = status

    def exit_signal_received(self, signal: str, core_dumped: bool, msg: str, lang: str) -> None:
        try:
            self.server_chan.exit_with_signal(signal, core_dumped, msg, lang)
        except Exception:
            pass

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if self.exit_status is not None:
            try:
                self.server_chan.exit(self.exit_status)
            except Exception:
                pass
        self.server_chan.close()


class ProxyServerSession(asyncssh.SSHServerSession):
    """Local client session delegating channel requests to the single upstream connection."""

    def __init__(self, gateway: "ColabSSHGateway"):
        self.gateway = gateway
        self.server_chan: Optional[asyncssh.SSHServerChannel] = None
        self.upstream_chan: Optional[asyncssh.SSHClientChannel] = None
        self.pty_info: Optional[Tuple[str, Tuple[int, int, int, int], Any]] = None
        self._pending_input: List[Tuple[bytes, Optional[int]]] = []
        self._pending_eof: bool = False

    def connection_made(self, chan: asyncssh.SSHServerChannel) -> None:
        self.server_chan = chan
        # Raw binary encoding prevents UTF-8 decode crashes during SFTP / binary streams
        chan.set_encoding(None)

    def pty_requested(
        self, term_type: str, term_size: Tuple[int, int, int, int], term_modes: Any
    ) -> bool:
        self.pty_info = (term_type, term_size, term_modes)
        return True

    def terminal_size_changed(self, width: int, height: int, pixwidth: int, pixheight: int) -> None:
        if self.upstream_chan:
            self.upstream_chan.change_terminal_size(width, height, pixwidth, pixheight)

    def exec_requested(self, command: str) -> bool:
        return True

    def shell_requested(self) -> bool:
        return True

    def subsystem_requested(self, subsystem: str) -> bool:
        return True

    def session_started(self) -> None:
        asyncio.create_task(self._start_upstream_channel())

    async def _start_upstream_channel(self) -> None:
        try:
            upstream = await self.gateway.get_or_connect_upstream()
            cmd = self.server_chan.get_command()
            subsystem = self.server_chan.get_subsystem()

            term_type = self.pty_info[0] if self.pty_info else None
            term_size = self.pty_info[1] if self.pty_info else None
            term_modes = self.pty_info[2] if self.pty_info else None

            client_session = ProxyClientSession(self.server_chan, self.gateway)
            chan, _ = await upstream.create_session(
                lambda: client_session,
                command=cmd,
                subsystem=subsystem,
                term_type=term_type,
                term_size=term_size,
                term_modes=term_modes,
                request_pty=bool(self.pty_info),
                encoding=None,
            )
            self.upstream_chan = chan

            # Drain buffered input received prior to upstream channel readiness
            for data, datatype in self._pending_input:
                self.upstream_chan.write(data, datatype=datatype)
            self._pending_input.clear()

            if self._pending_eof:
                self.upstream_chan.write_eof()

        except Exception as e:
            logger.error(f"Failed to bridge upstream channel for session {self.gateway.session_id}: {e}")
            if self.server_chan:
                try:
                    self.server_chan.write_stderr(
                        f"\r\n\x1b[31m[Colab SSH Gateway Error: {e}]\x1b[0m\r\n".encode()
                    )
                    self.server_chan.exit(1)
                    self.server_chan.close()
                except Exception:
                    pass

    def data_received(self, data: bytes, datatype: Optional[int]) -> None:
        self.gateway.record_activity()
        if self.upstream_chan:
            self.upstream_chan.write(data, datatype=datatype)
        else:
            self._pending_input.append((data, datatype))

    def eof_received(self) -> bool:
        if self.upstream_chan:
            self.upstream_chan.write_eof()
        else:
            self._pending_eof = True
        return True

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if self.upstream_chan:
            self.upstream_chan.close()


class SSHGatewayServer(asyncssh.SSHServer):
    """AsyncSSH server handling client connections on localhost:222X."""

    def __init__(self, gateway: "ColabSSHGateway"):
        self.gateway = gateway

    def begin_auth(self, username: str) -> bool:
        # Permissive local authentication for seamless developer login
        return False

    def session_requested(self) -> asyncssh.SSHServerSession:
        return ProxyServerSession(self.gateway)

    def connection_requested(
        self, dest_host: str, dest_port: int, orig_host: str, orig_port: int
    ) -> Any:
        # Tunnel direct TCP/IP port forwards across the upstream SSH client connection
        return self.gateway.upstream_conn


class ColabSSHGateway:
    """Manages the local SSH listener and single upstream WebSocket for one session."""

    def __init__(
        self,
        session_id: str,
        port: int,
        host_key: asyncssh.SSHKey,
        client_key: asyncssh.SSHKey,
    ):
        self.session_id = session_id
        self.port = port
        self.host_key = host_key
        self.client_key = client_key

        self.server: Optional[asyncssh.SSHServerListener] = None
        self.upstream_conn: Optional[asyncssh.SSHClientConnection] = None
        self.upstream_ws: Optional[Any] = None
        self._upstream_lock = asyncio.Lock()
        self._bridge_tasks: List[asyncio.Task] = []
        self._sockets: List[socket.socket] = []
        self.last_activity_ts = time.time()

    def record_activity(self) -> None:
        self.last_activity_ts = time.time()
        try:
            from backend.app.services.lifecycle_engine import lifecycle_engine
            lifecycle_engine.record_activity(self.session_id, self.last_activity_ts)
        except Exception:
            pass

    async def start_listener(self) -> None:
        """Start listening on 0.0.0.0:port."""
        self.server = await asyncssh.create_server(
            lambda: SSHGatewayServer(self),
            host="0.0.0.0",
            port=self.port,
            server_host_keys=[self.host_key],
            encoding=None,
        )
        logger.info(f"SSH Gateway for session {self.session_id} listening on 0.0.0.0:{self.port}")

    async def get_or_connect_upstream(self) -> asyncssh.SSHClientConnection:
        """Return existing upstream connection or establish it lazily."""
        if self.upstream_conn and not self.upstream_conn.is_closed():
            return self.upstream_conn

        async with self._upstream_lock:
            if self.upstream_conn and not self.upstream_conn.is_closed():
                return self.upstream_conn

            await self._connect_upstream()
            return self.upstream_conn

    async def run_command(
        self,
        command: str,
        timeout: Optional[float] = 300.0,
        check: bool = False,
        encoding: Optional[str] = "utf-8",
    ) -> asyncssh.SSHCompletedProcess:
        """Execute a command over the persistent upstream SSH connection."""
        conn = await self.get_or_connect_upstream()
        self.record_activity()
        if timeout:
            return await asyncio.wait_for(
                conn.run(command, check=check, encoding=encoding),
                timeout=timeout,
            )
        return await conn.run(command, check=check, encoding=encoding)

    async def _connect_upstream(self) -> None:
        """Establish single WebSocket to Colab and connect AsyncSSH client."""
        # 1. Fetch credentials from DB
        with get_db() as conn:
            row = conn.execute(
                "SELECT proxy_url, proxy_token_enc, status FROM sessions WHERE id = ?",
                (self.session_id,),
            ).fetchone()
            if not row:
                raise UpstreamSSHError(f"Session {self.session_id} not found in database")
            if row["status"] in ("STOPPED", "FAILED", "LOST"):
                raise UpstreamSSHError(f"Session {self.session_id} is {row['status']}")

            proxy_url = row["proxy_url"]
            proxy_token = decrypt_secret(row["proxy_token_enc"])

        # 2. Build WebSocket URL and Ed25519 Pubkey Header
        ws_url = (
            proxy_url.rstrip("/")
            .replace("https://", "wss://")
            .replace("http://", "ws://")
            + f"{COLAB_SSH_SUBPATH}?colab-runtime-proxy-token={proxy_token}"
        )
        pubkey_str = self.client_key.export_public_key().decode("ascii").strip()

        logger.info(f"Connecting upstream SSH WebSocket for session {self.session_id}...")

        # 3. Connect WebSocket with version-compatible header argument
        headers = {PUBKEY_HEADER: pubkey_str}
        kw = {}
        if "additional_headers" in inspect.signature(websockets.connect).parameters:
            kw["additional_headers"] = headers
        else:
            kw["extra_headers"] = headers

        try:
            ws = await websockets.connect(ws_url, open_timeout=15, ping_interval=30, **kw)
        except Exception as e:
            status_code = getattr(e, "status_code", None)
            if status_code == 429 or "429" in str(e):
                raise UpstreamSSHBusyError("Colab runtime has an active SSH session (HTTP 429).")
            if status_code in (400, 401, 403):
                raise UpstreamSSHAuthError(f"Colab rejected SSH WebSocket handshake (HTTP {status_code}): {e}")
            raise UpstreamSSHError(f"Failed to connect upstream WebSocket: {e}")

        self.upstream_ws = ws

        # 4. Create Socketpair for In-Memory Byte Stream Bridging
        sock_ws, sock_ssh = socket.socketpair()
        sock_ws.setblocking(False)
        sock_ssh.setblocking(False)
        self._sockets.extend([sock_ws, sock_ssh])

        loop = asyncio.get_running_loop()

        async def ws_to_sock():
            try:
                async for message in ws:
                    if isinstance(message, str):
                        message = message.encode("utf-8")
                    await loop.sock_sendall(sock_ws, message)
            except (websockets.ConnectionClosed, OSError, asyncio.CancelledError):
                pass

        async def sock_to_ws():
            try:
                while True:
                    chunk = await loop.sock_recv(sock_ws, 8192)
                    if not chunk:
                        break
                    await ws.send(chunk)
            except (websockets.ConnectionClosed, OSError, asyncio.CancelledError):
                pass

        t1 = asyncio.create_task(ws_to_sock())
        t2 = asyncio.create_task(sock_to_ws())
        self._bridge_tasks.extend([t1, t2])

        # 5. Connect AsyncSSH Client over sock_ssh
        try:
            self.upstream_conn = await asyncssh.connect(
                host="localhost",
                sock=sock_ssh,
                username="root",
                client_keys=[self.client_key],
                known_hosts=None,
                encoding=None,
            )
            logger.info(f"Upstream SSH client connected successfully for session {self.session_id}")
        except Exception as e:
            try:
                await ws.close()
            except Exception:
                pass
            raise UpstreamSSHError(f"Failed AsyncSSH handshake over WebSocket bridge: {e}")

    async def close(self) -> None:
        """Gracefully close listener, client channels, and upstream WebSocket."""
        if self.server:
            try:
                self.server.close()
                await self.server.wait_closed()
            except Exception:
                pass
            self.server = None

        if self.upstream_conn:
            try:
                self.upstream_conn.close()
                await self.upstream_conn.wait_closed()
            except Exception:
                pass
            self.upstream_conn = None

        for t in self._bridge_tasks:
            t.cancel()
        self._bridge_tasks.clear()

        for s in self._sockets:
            try:
                s.close()
            except Exception:
                pass
        self._sockets.clear()

        if self.upstream_ws:
            try:
                await self.upstream_ws.close()
            except Exception:
                pass
            self.upstream_ws = None


class SSHMultiplexerService:
    """Daemon-wide coordinator managing active gateways and port allocations."""

    def __init__(self):
        self.port_manager = SSHPortManager(settings.SSH_PORT_START, settings.SSH_PORT_END)
        self.gateways: Dict[str, ColabSSHGateway] = {}
        self._host_key: Optional[asyncssh.SSHKey] = None
        self._client_key: Optional[asyncssh.SSHKey] = None

    def _ensure_keys(self) -> None:
        """Generate or load persistent host and upstream Ed25519 keys."""
        if not self._host_key:
            self._host_key = asyncssh.generate_private_key("ssh-ed25519")
        if not self._client_key:
            self._client_key = asyncssh.generate_private_key("ssh-ed25519")

    async def init_from_db(self) -> None:
        """Startup reconciliation: restart listeners for active DB sessions."""
        self._ensure_keys()
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, ssh_local_port FROM sessions
                WHERE ssh_local_port IS NOT NULL
                  AND status IN ('READY', 'BUSY', 'BOOTSTRAPPING')
                """
            ).fetchall()

        for r in rows:
            session_id = r["id"]
            port = r["ssh_local_port"]
            try:
                await self.register_session(session_id, port)
                logger.info(f"Re-bound SSH gateway for session {session_id} on port {port}")
            except Exception as e:
                logger.error(f"Failed to restore SSH gateway for session {session_id}: {e}")

    async def register_session(self, session_id: str, port: int) -> ColabSSHGateway:
        """Instantiate and start listener for a session."""
        self._ensure_keys()
        if session_id in self.gateways:
            return self.gateways[session_id]

        gateway = ColabSSHGateway(session_id, port, self._host_key, self._client_key)
        await gateway.start_listener()
        self.gateways[session_id] = gateway
        return gateway

    def unregister_session_sync(self, session_id: str) -> Optional[ColabSSHGateway]:
        """Synchronously close listening socket and unregister gateway."""
        gateway = self.gateways.pop(session_id, None)
        if gateway:
            if gateway.server:
                try:
                    gateway.server.close()
                except Exception:
                    pass
                gateway.server = None
            for s in gateway._sockets:
                try:
                    s.close()
                except Exception:
                    pass
            gateway._sockets.clear()
            for t in gateway._bridge_tasks:
                try:
                    t.cancel()
                except Exception:
                    pass
            gateway._bridge_tasks.clear()
            logger.info(f"Unregistered SSH gateway (sync) for session {session_id}")
        return gateway

    async def unregister_session(self, session_id: str) -> None:
        """Tear down and remove gateway listener for a session."""
        gateway = self.unregister_session_sync(session_id)
        if gateway:
            await gateway.close()
            logger.info(f"Unregistered SSH gateway for session {session_id}")

    def get_gateway(self, session_id: str) -> Optional[ColabSSHGateway]:
        return self.gateways.get(session_id)

    async def shutdown(self) -> None:
        """Shutdown all active gateways."""
        for session_id in list(self.gateways.keys()):
            await self.unregister_session(session_id)


# Global Singleton Service
ssh_multiplexer_service = SSHMultiplexerService()
