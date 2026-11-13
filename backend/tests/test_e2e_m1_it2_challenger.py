"""Milestone E2E-M1 Iteration 2 Adversarial Stress Test Suite.

Independent empirical verification suite written by teamwork_challenger_e2e_m1_it2_2_rep.
Tests:
1. Multi-session VFS isolation across concurrent sessions A and B (different files, no cross-talk).
2. Cross-session token tampering and endpoint mismatch security rejection.
3. Concurrent chunked file uploads across multiple sessions without buffer pollution.
4. Concurrent TTY WebSocket handshakes & command execution across 3 active sessions simultaneously.
5. Concurrent SSH WebSocket handshakes & independent single-connection locks across 3 active sessions simultaneously.
6. Combined high-concurrency stress test: simultaneous TTY, SSH, and REST VFS operations across multiple sessions.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Dict, List, Optional, Tuple

from backend.tests.mock_colab.mock_control_plane import XSSI_PREFIX
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_runtime_proxy import RFC6455WebSocket


class TestMultiSessionVFSIsolation(unittest.TestCase):
    """Stress-test Virtual Filesystem (VFS) isolation across concurrent sessions."""

    def setUp(self):
        self.hub = MockControlHub()
        self.hub.start()
        self.cp_url = self.hub.urls["control_plane"]
        self.rp_url = self.hub.urls["runtime_proxy"]

    def tearDown(self):
        self.hub.stop()

    def _assign_session(self, nbh: str, acc: str = "T4", variant: str = "GPU") -> Tuple[str, str]:
        """Probes and assigns a session on the control plane, returning (endpoint, proxy_token)."""
        # Step 1: Probe GET
        probe_url = f"{self.cp_url}/tun/m/assign?nbh={nbh}&variant={variant}&accelerator={acc}"
        req = urllib.request.Request(probe_url)
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8")[len(XSSI_PREFIX) :])
            xsrf = data["token"]

        # Step 2: Commit POST
        commit_req = urllib.request.Request(
            probe_url,
            data=b"{}",
            headers={"X-Goog-Colab-Token": xsrf},
        )
        with urllib.request.urlopen(commit_req) as resp:
            data = json.loads(resp.read().decode("utf-8")[len(XSSI_PREFIX) :])
            return data["endpoint"], data["runtimeProxyInfo"]["token"]

    def _upload_file(
        self,
        endpoint: str,
        token: str,
        path: str,
        content: str,
        use_path_prefix: bool = True,
    ) -> int:
        """Uploads a file to VFS via REST API."""
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        payload = json.dumps({"type": "file", "path": path, "content": b64, "chunk": 1}).encode("utf-8")

        if use_path_prefix:
            url = f"{self.rp_url}/tun/m/{endpoint}/api/contents/{path}?colab-runtime-proxy-token={token}"
        else:
            url = f"{self.rp_url}/api/contents/{path}?colab-runtime-proxy-token={token}"

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(req) as resp:
            return resp.status

    def _read_file(
        self,
        endpoint: str,
        token: str,
        path: str,
        use_path_prefix: bool = True,
    ) -> str:
        """Reads a file from VFS via REST API and decodes base64 content."""
        if use_path_prefix:
            url = f"{self.rp_url}/tun/m/{endpoint}/api/contents/{path}?content=1&colab-runtime-proxy-token={token}"
        else:
            url = f"{self.rp_url}/api/contents/{path}?content=1&colab-runtime-proxy-token={token}"

        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            b64_content = body.get("content", "")
            return base64.b64decode(b64_content).decode("utf-8")

    def test_multi_session_vfs_strict_isolation(self):
        """Dispatch Test 1: Upload 'data.txt' with 'ALPHA' to Session A and 'BETA' to Session B.

        Verify Session A reads 'ALPHA' and Session B reads 'BETA' without any cross-talk,
        testing both prefixed URL (/tun/m/{ep}/api/contents/...) and native token-routed URL (/api/contents/...).
        """
        nbh_a = f"nbh_a_{uuid.uuid4().hex[:6]}"
        nbh_b = f"nbh_b_{uuid.uuid4().hex[:6]}"

        ep_a, token_a = self._assign_session(nbh_a, "T4", "GPU")
        ep_b, token_b = self._assign_session(nbh_b, "T4", "GPU")

        self.assertNotEqual(ep_a, ep_b)
        self.assertNotEqual(token_a, token_b)

        # 1. Upload to Session A via prefixed URL and Session B via native token URL
        status_a = self._upload_file(ep_a, token_a, "data.txt", "ALPHA", use_path_prefix=True)
        self.assertEqual(status_a, 200)

        status_b = self._upload_file(ep_b, token_b, "data.txt", "BETA", use_path_prefix=False)
        self.assertEqual(status_b, 200)

        # 2. Verify Session A has ALPHA, not BETA (using both URL styles)
        content_a_pref = self._read_file(ep_a, token_a, "data.txt", use_path_prefix=True)
        content_a_native = self._read_file(ep_a, token_a, "data.txt", use_path_prefix=False)
        self.assertEqual(content_a_pref, "ALPHA")
        self.assertEqual(content_a_native, "ALPHA")
        self.assertNotIn("BETA", content_a_pref)

        # 3. Verify Session B has BETA, not ALPHA (using both URL styles)
        content_b_pref = self._read_file(ep_b, token_b, "data.txt", use_path_prefix=True)
        content_b_native = self._read_file(ep_b, token_b, "data.txt", use_path_prefix=False)
        self.assertEqual(content_b_pref, "BETA")
        self.assertEqual(content_b_native, "BETA")
        self.assertNotIn("ALPHA", content_b_pref)

        # 4. Verify directory listing isolation
        list_url_a = f"{self.rp_url}/api/contents/?content=1&colab-runtime-proxy-token={token_a}"
        with urllib.request.urlopen(list_url_a) as resp:
            list_a = json.loads(resp.read().decode("utf-8"))
            file_names_a = [item["name"] for item in list_a.get("content", [])]
            self.assertIn("data.txt", file_names_a)

        list_url_b = f"{self.rp_url}/api/contents/?content=1&colab-runtime-proxy-token={token_b}"
        with urllib.request.urlopen(list_url_b) as resp:
            list_b = json.loads(resp.read().decode("utf-8"))
            file_names_b = [item["name"] for item in list_b.get("content", [])]
            self.assertIn("data.txt", file_names_b)

        # 5. Modify file in Session A, verify Session B is completely unaffected
        self._upload_file(ep_a, token_a, "data.txt", "ALPHA_MODIFIED_V2", use_path_prefix=True)
        self.assertEqual(self._read_file(ep_a, token_a, "data.txt"), "ALPHA_MODIFIED_V2")
        self.assertEqual(self._read_file(ep_b, token_b, "data.txt"), "BETA")

        # 6. Delete file in Session A, verify Session A returns 404 while Session B still has BETA
        del_req = urllib.request.Request(
            f"{self.rp_url}/api/contents/data.txt?colab-runtime-proxy-token={token_a}",
            method="DELETE",
        )
        with urllib.request.urlopen(del_req) as resp:
            self.assertEqual(resp.status, 204)

        try:
            self._read_file(ep_a, token_a, "data.txt")
            self.fail("Expected 404 for deleted file in Session A")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

        # Session B file must still exist and be completely intact
        self.assertEqual(self._read_file(ep_b, token_b, "data.txt"), "BETA")

    def test_cross_session_token_tampering_security(self):
        """Verify that attempting to access Session A endpoint with Session B token is rejected."""
        ep_a, token_a = self._assign_session("nbh_sec_a")
        ep_b, token_b = self._assign_session("nbh_sec_b")

        self._upload_file(ep_a, token_a, "secret_a.txt", "SECRET_A_DATA")

        # Attack: Client knows ep_a, but presents token_b
        mismatched_url = f"{self.rp_url}/tun/m/{ep_a}/api/contents/secret_a.txt?colab-runtime-proxy-token={token_b}"
        try:
            urllib.request.urlopen(mismatched_url)
            self.fail("Expected 401 Unauthorized when presenting mismatched session token")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_concurrent_chunked_upload_stress(self):
        """Stress-test concurrent multi-chunk uploads across sessions A and B simultaneously."""
        ep_a, token_a = self._assign_session("nbh_chunk_a")
        ep_b, token_b = self._assign_session("nbh_chunk_b")

        # Session A: 10 chunks of "CHUNK_A_{i}\n"
        # Session B: 10 chunks of "CHUNK_B_{i}\n"
        chunks_a = [f"CHUNK_A_{i:03d}\n" for i in range(10)]
        chunks_b = [f"CHUNK_B_{i:03d}\n" for i in range(10)]

        def upload_stream(endpoint: str, token: str, filename: str, chunks: List[str]):
            for i, chunk_data in enumerate(chunks):
                b64 = base64.b64encode(chunk_data.encode("utf-8")).decode("ascii")
                payload = json.dumps({"type": "file", "path": filename, "content": b64, "chunk": i + 1}).encode("utf-8")
                url = f"{self.rp_url}/api/contents/{filename}?colab-runtime-proxy-token={token}"
                req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="PUT")
                with urllib.request.urlopen(req) as resp:
                    assert resp.status == 200

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_a = executor.submit(upload_stream, ep_a, token_a, "stream.log", chunks_a)
            fut_b = executor.submit(upload_stream, ep_b, token_b, "stream.log", chunks_b)
            fut_a.result()
            fut_b.result()

        expected_a = "".join(chunks_a)
        expected_b = "".join(chunks_b)

        content_a = self._read_file(ep_a, token_a, "stream.log")
        content_b = self._read_file(ep_b, token_b, "stream.log")

        self.assertEqual(content_a, expected_a)
        self.assertEqual(content_b, expected_b)
        self.assertNotIn("CHUNK_B", content_a)
        self.assertNotIn("CHUNK_A", content_b)


class TestConcurrentTTYAndSSHHandshakes(unittest.TestCase):
    """Stress-test concurrent TTY & SSH WebSocket connections across 3 active sessions simultaneously."""

    def setUp(self):
        self.hub = MockControlHub()
        self.hub.start()
        self.cp_url = self.hub.urls["control_plane"]
        self.rp_url = self.hub.urls["runtime_proxy"]

    def tearDown(self):
        self.hub.stop()

    def _assign_session(self, nbh: str) -> Tuple[str, str]:
        probe_url = f"{self.cp_url}/tun/m/assign?nbh={nbh}&variant=GPU&accelerator=T4"
        with urllib.request.urlopen(urllib.request.Request(probe_url)) as resp:
            xsrf = json.loads(resp.read().decode()[len(XSSI_PREFIX) :])["token"]
        commit_req = urllib.request.Request(probe_url, data=b"{}", headers={"X-Goog-Colab-Token": xsrf})
        with urllib.request.urlopen(commit_req) as resp:
            data = json.loads(resp.read().decode()[len(XSSI_PREFIX) :])
            return data["endpoint"], data["runtimeProxyInfo"]["token"]

    def _connect_ws(
        self,
        path: str,
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: float = 5.0,
    ) -> Tuple[socket.socket, str, bytearray]:
        """Performs raw HTTP WebSocket upgrade handshake and returns socket + headers + unconsumed rest bytes."""
        parsed = urllib.parse.urlparse(self.rp_url)
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=timeout)
        hdrs = [
            f"GET {path} HTTP/1.1",
            f"Host: {parsed.netloc}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
        ]
        if extra_headers:
            for k, v in extra_headers.items():
                hdrs.append(f"{k}: {v}")
        hdrs.append("\r\n")
        req_raw = "\r\n".join(hdrs).encode("utf-8")
        sock.sendall(req_raw)

        # Read handshake response
        resp_buf = bytearray()
        sock.settimeout(timeout)
        while b"\r\n\r\n" not in resp_buf:
            chunk = sock.recv(1024)
            if not chunk:
                break
            resp_buf.extend(chunk)

        header_bytes, _, rest = resp_buf.partition(b"\r\n\r\n")
        header_text = header_bytes.decode("utf-8", errors="replace")

        # If HTTP response status is not 101, read remaining body
        if not header_text.startswith("HTTP/1.1 101"):
            content_len = 0
            for line in header_text.splitlines():
                if line.lower().startswith("content-length:"):
                    content_len = int(line.split(":")[1].strip())
            remaining = content_len - len(rest)
            while remaining > 0:
                chunk = sock.recv(min(4096, remaining))
                if not chunk:
                    break
                rest += chunk
                remaining -= len(chunk)
            return sock, header_text + "\r\n\r\n" + rest.decode("utf-8", errors="replace"), bytearray()

        return sock, header_text, bytearray(rest)

    def _recv_ws_frame(self, sock: socket.socket, buffer: Optional[bytearray] = None, timeout: float = 3.0) -> Tuple[int, bytes]:
        """Receives a single RFC 6455 frame from the buffer/socket."""
        sock.settimeout(timeout)
        buf = buffer if buffer is not None else bytearray()
        while True:
            frame = RFC6455WebSocket.decode_frame(bytes(buf))
            if frame:
                opcode, payload, consumed = frame
                del buf[:consumed]
                return opcode, payload
            chunk = sock.recv(1024)
            if not chunk:
                raise ConnectionError("Socket closed prematurely")
            buf.extend(chunk)

    def _send_ws_frame(self, sock: socket.socket, payload: bytes, opcode: int = 0x1) -> None:
        """Sends a masked client-to-server WebSocket frame."""
        mask_key = b"\x12\x34\x56\x78"
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            import struct
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            import struct
            header.extend(struct.pack(">Q", length))
        header.extend(mask_key)
        masked_payload = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        sock.sendall(bytes(header) + masked_payload)

    def test_concurrent_tty_handshakes_across_three_sessions(self):
        """Dispatch Test 2: Stress test concurrent connections to /colab/tty across 3 active sessions simultaneously.

        Verify no socket deadlocks, no thread contention exceptions, prompt reception,
        and interactive command isolation.
        """
        # 1. Assign 3 active sessions
        sessions: List[Tuple[str, str]] = []
        for i in range(1, 4):
            ep, tok = self._assign_session(f"nbh_tty_{i}")
            sessions.append((ep, tok))

        results: Dict[int, Dict[str, Any]] = {}
        barrier = threading.Barrier(3)

        def tty_worker(idx: int, endpoint: str, token: str):
            res: Dict[str, Any] = {"success": False, "handshake": "", "prompt": "", "echo": "", "error": None}
            sock = None
            try:
                # Wait for all 3 threads to be ready to execute simultaneously
                barrier.wait(timeout=5.0)

                # Connect concurrently using native colab-cli query format
                path = f"/colab/tty?colab-runtime-proxy-token={token}"
                sock, header_resp, rest_buf = self._connect_ws(path, timeout=5.0)
                res["handshake"] = header_resp

                if "HTTP/1.1 101 Switching Protocols" not in header_resp:
                    raise AssertionError(f"Session {idx} failed WS upgrade: {header_resp}")

                # Read initial prompt frame
                opcode, payload = self._recv_ws_frame(sock, buffer=rest_buf, timeout=4.0)
                prompt_msg = json.loads(payload.decode("utf-8"))
                res["prompt"] = prompt_msg.get("data", "")

                # Send interactive command: echo SECRET_SESS_{idx}
                secret_str = f"SECRET_SESS_{idx}"
                cmd = f"echo {secret_str}\r"
                self._send_ws_frame(sock, json.dumps({"data": cmd}).encode("utf-8"), opcode=0x1)

                # Collect echoed frames until we see our secret string
                accum_echo = ""
                start_t = time.time()
                while time.time() - start_t < 4.0:
                    op, pl = self._recv_ws_frame(sock, buffer=rest_buf, timeout=2.0)
                    if op == 0x1:
                        msg = json.loads(pl.decode("utf-8"))
                        accum_echo += msg.get("data", "")
                        if secret_str in accum_echo:
                            break

                res["echo"] = accum_echo
                res["success"] = secret_str in accum_echo

            except Exception as e:
                res["error"] = str(e)
            finally:
                if sock:
                    try:
                        sock.close()
                    except Exception:
                        pass
                results[idx] = res

        # Launch 3 threads concurrently
        threads = []
        for i, (ep, tok) in enumerate(sessions):
            t = threading.Thread(target=tty_worker, args=(i + 1, ep, tok))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=8.0)
            self.assertFalse(t.is_alive(), "TTY worker thread hung/deadlocked!")

        # Verify results for all 3 sessions
        for i in range(1, 4):
            r = results.get(i, {})
            self.assertIsNone(r.get("error"), f"Session {i} encountered error: {r.get('error')}")
            self.assertTrue(r.get("success"), f"Session {i} failed to execute command and receive secret echo")
            self.assertIn("root@colab-runtime", r.get("prompt", ""))
            self.assertIn(f"SECRET_SESS_{i}", r.get("echo", ""))

    def test_concurrent_ssh_handshakes_and_independent_locks(self):
        """Stress-test concurrent SSH connections across 3 active sessions simultaneously.

        Verify:
        1. All 3 sessions establish SSH connections concurrently (HTTP 101).
        2. All 3 receive the OpenSSH binary banner frame.
        3. A duplicate connection to Session 1 receives HTTP 429 while Sessions 2 and 3 remain connected.
        4. No socket deadlocks or thread contention.
        """
        sessions: List[Tuple[str, str]] = []
        for i in range(1, 4):
            ep, tok = self._assign_session(f"nbh_ssh_{i}")
            sessions.append((ep, tok))

        active_socks: Dict[int, Tuple[socket.socket, bytearray]] = {}
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... test@colab"

        try:
            # 1. Concurrently establish SSH connections for all 3 sessions
            def connect_ssh(idx: int, token: str) -> Tuple[int, socket.socket, str, bytearray]:
                path = f"/colab/ssh?colab-runtime-proxy-token={token}"
                sock, header_resp, rest_buf = self._connect_ws(path, extra_headers={"X-Colab-Ssh-Pubkey": pubkey}, timeout=5.0)
                return idx, sock, header_resp, rest_buf

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futs = [executor.submit(connect_ssh, i + 1, sessions[i][1]) for i in range(3)]
                for fut in concurrent.futures.as_completed(futs):
                    idx, sock, hdr, rest_buf = fut.result()
                    active_socks[idx] = (sock, rest_buf)
                    self.assertIn("HTTP/1.1 101 Switching Protocols", hdr, f"Session {idx} failed SSH handshake")

            # 2. Verify OpenSSH banner on all 3 sockets
            for idx, (sock, rest_buf) in active_socks.items():
                opcode, banner_payload = self._recv_ws_frame(sock, buffer=rest_buf, timeout=3.0)
                self.assertEqual(opcode, 0x2)  # Binary frame
                self.assertIn(b"SSH-2.0-OpenSSH", banner_payload)

            # 3. Adversarial challenge: attempt 2nd connection to Session 1
            # MUST return HTTP 429
            dup_sock, dup_resp, _ = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={sessions[0][1]}",
                extra_headers={"X-Colab-Ssh-Pubkey": pubkey},
                timeout=4.0,
            )
            dup_sock.close()
            self.assertIn("HTTP/1.1 429", dup_resp)
            self.assertIn("Already-active SSH session", dup_resp)

            # 4. Verify Session 2 and Session 3 are still fully operational and not deadlocked
            # Send binary ping payload to Session 2 and Session 3 and verify echo
            test_packet = b"\x00\x00\x00\x04test"
            sock2, buf2 = active_socks[2]
            self._send_ws_frame(sock2, test_packet, opcode=0x2)
            op2, resp2 = self._recv_ws_frame(sock2, buffer=buf2, timeout=3.0)
            self.assertEqual(op2, 0x2)
            self.assertEqual(resp2, test_packet)

            sock3, buf3 = active_socks[3]
            self._send_ws_frame(sock3, test_packet, opcode=0x2)
            op3, resp3 = self._recv_ws_frame(sock3, buffer=buf3, timeout=3.0)
            self.assertEqual(op3, 0x2)
            self.assertEqual(resp3, test_packet)

        finally:
            for s, _ in active_socks.values():
                try:
                    s.close()
                except Exception:
                    pass

    def test_combined_high_concurrency_stress(self):
        """Mixed heavy stress: 3 sessions undergoing concurrent TTY, SSH, and REST VFS calls repeatedly."""
        sessions = [self._assign_session(f"nbh_stress_{i}") for i in range(1, 4)]
        errors: List[str] = []

        def session_cycle(cycle_id: int, sess_idx: int, ep: str, token: str):
            try:
                # 1. REST file write & read with unique file per thread/cycle
                fname = f"stress_{sess_idx}_c{cycle_id}_{uuid.uuid4().hex[:6]}.txt"
                content = f"STRESS_DATA_{sess_idx}_{cycle_id}_{uuid.uuid4().hex}"
                b64 = base64.b64encode(content.encode()).decode("ascii")
                payload = json.dumps({"type": "file", "path": fname, "content": b64, "chunk": 1}).encode()

                put_req = urllib.request.Request(
                    f"{self.rp_url}/api/contents/{fname}?colab-runtime-proxy-token={token}",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="PUT",
                )
                with urllib.request.urlopen(put_req, timeout=3.0) as resp:
                    assert resp.status == 200, f"PUT failed with status {resp.status}"

                get_req = urllib.request.Request(
                    f"{self.rp_url}/api/contents/{fname}?content=1&colab-runtime-proxy-token={token}",
                    method="GET",
                )
                with urllib.request.urlopen(get_req, timeout=3.0) as resp:
                    body = json.loads(resp.read().decode())
                    read_back = base64.b64decode(body["content"]).decode()
                    assert read_back == content, f"VFS mismatch in sess {sess_idx}: expected {content[:10]} got {read_back[:10]}"

                # 2. TTY handshake
                tty_sock, tty_hdr, _ = self._connect_ws(
                    f"/colab/tty?colab-runtime-proxy-token={token}",
                    timeout=3.0,
                )
                assert "HTTP/1.1 101" in tty_hdr, f"TTY handshake failed: {tty_hdr.splitlines()[0] if tty_hdr else 'empty'}"
                tty_sock.close()

                # 3. SSH handshake and disconnect
                ssh_sock, ssh_hdr, _ = self._connect_ws(
                    f"/colab/ssh?colab-runtime-proxy-token={token}",
                    extra_headers={"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... stress@colab"},
                    timeout=3.0,
                )
                assert "HTTP/1.1 101" in ssh_hdr, f"SSH handshake failed: {ssh_hdr.splitlines()[0] if ssh_hdr else 'empty'}"
                ssh_sock.close()

            except Exception as e:
                errors.append(f"Sess {sess_idx} iteration {cycle_id} error: {e}")

        def session_worker(sess_idx: int, ep: str, token: str):
            for cycle_id in range(5):
                session_cycle(cycle_id, sess_idx, ep, token)
                time.sleep(0.05)

        # Run 3 sessions simultaneously in parallel threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [
                executor.submit(session_worker, i + 1, ep, tok)
                for i, (ep, tok) in enumerate(sessions)
            ]
            concurrent.futures.wait(futures)

        self.assertEqual(errors, [], f"Encountered concurrency stress errors: {errors}")


    def test_rapid_lifecycle_churn_with_active_connections(self):
        """Stress test: Unassigning Session 1 while Session 2 and Session 3 are actively connected.

        Verify:
        - Session 1 unassignment succeeds.
        - Session 1 endpoints are unregistered and return 401.
        - Active connections on Session 2 (SSH) and Session 3 (TTY) remain stable and unaffected.
        """
        # Assign 3 sessions
        ep1, tok1 = self._assign_session("nbh_churn_1")
        ep2, tok2 = self._assign_session("nbh_churn_2")
        ep3, tok3 = self._assign_session("nbh_churn_3")

        # Connect Session 2 SSH
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... churn@colab"
        s2_sock, s2_hdr, s2_buf = self._connect_ws(f"/colab/ssh?colab-runtime-proxy-token={tok2}", extra_headers={"X-Colab-Ssh-Pubkey": pubkey})
        self.assertIn("101 Switching Protocols", s2_hdr)
        op_banner, banner = self._recv_ws_frame(s2_sock, buffer=s2_buf, timeout=3.0)
        self.assertEqual(op_banner, 0x2)
        self.assertIn(b"SSH-2.0-OpenSSH", banner)

        # Connect Session 3 TTY
        s3_sock, s3_hdr, s3_buf = self._connect_ws(f"/colab/tty?colab-runtime-proxy-token={tok3}")
        self.assertIn("101 Switching Protocols", s3_hdr)
        op_prompt, prompt = self._recv_ws_frame(s3_sock, buffer=s3_buf, timeout=3.0)
        self.assertEqual(op_prompt, 0x1)
        self.assertIn("root@colab-runtime", json.loads(prompt.decode()).get("data", ""))

        try:
            # Unassign Session 1 via Control Plane
            # 1. Probe unassign GET
            with urllib.request.urlopen(f"{self.cp_url}/tun/m/unassign/{ep1}") as resp:
                un_xsrf = json.loads(resp.read().decode()[len(XSSI_PREFIX) :])["token"]

            # 2. Commit unassign POST
            un_req = urllib.request.Request(
                f"{self.cp_url}/tun/m/unassign/{ep1}",
                data=b"{}",
                headers={"X-Goog-Colab-Token": un_xsrf},
            )
            with urllib.request.urlopen(un_req) as resp:
                self.assertEqual(resp.status, 200)

            # Verify Session 1 is dead
            try:
                urllib.request.urlopen(f"{self.rp_url}/api/contents/?colab-runtime-proxy-token={tok1}")
                self.fail("Expected 401 for unassigned Session 1")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 401)

            # Verify Session 2 SSH still responds to binary echo
            test_packet = b"\x00\x00\x00\x06echo2"
            self._send_ws_frame(s2_sock, test_packet, opcode=0x2)
            op2, resp2 = self._recv_ws_frame(s2_sock, buffer=s2_buf, timeout=3.0)
            self.assertEqual(op2, 0x2)
            self.assertEqual(resp2, test_packet)

            # Verify Session 3 TTY still responds to commands
            self._send_ws_frame(s3_sock, json.dumps({"data": "whoami\r"}).encode("utf-8"), opcode=0x1)
            op3, resp3 = self._recv_ws_frame(s3_sock, buffer=s3_buf, timeout=3.0)
            self.assertEqual(op3, 0x1)

        finally:
            s2_sock.close()
            s3_sock.close()

    def test_adversarial_malformed_ws_frames(self):
        """Stress test: Injecting corrupted/malformed WebSocket frames does not crash server or leak locks."""
        ep, tok = self._assign_session("nbh_malformed")
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... mal@colab"

        # 1. Connect SSH
        sock, hdr, buf = self._connect_ws(f"/colab/ssh?colab-runtime-proxy-token={tok}", extra_headers={"X-Colab-Ssh-Pubkey": pubkey})
        self.assertIn("101 Switching Protocols", hdr)

        # Send raw invalid bytes (not valid RFC 6455 frame)
        sock.sendall(b"\xff\xff\x00\x12\x34\x56\x78\x9a\xbc\xde\xf0")
        time.sleep(0.1)
        sock.close()

        # Reconnect SSH to same session — verify lock was properly released despite malformed data
        time.sleep(0.1)
        sock2, hdr2, _ = self._connect_ws(f"/colab/ssh?colab-runtime-proxy-token={tok}", extra_headers={"X-Colab-Ssh-Pubkey": pubkey})
        self.assertIn("101 Switching Protocols", hdr2, "Lock was not released after malformed frame disconnect")
        sock2.close()


class TestAdversarialControlPlaneShapeRules(unittest.TestCase):
    """Stress-test case-insensitivity and shape validation in MockControlPlane."""

    def setUp(self):
        self.hub = MockControlHub()
        self.hub.start()
        self.cp_url = self.hub.urls["control_plane"]

    def tearDown(self):
        self.hub.stop()

    def test_all_high_mem_accelerator_case_variations(self):
        """Verify strict rejection of shape=hm across all case permutations of L4, V5E1, V6E1 and shape."""
        high_mem_cases = [
            ("l4", "hm"), ("L4", "HM"), ("l4", "Hm"), ("L4", "hM"),
            ("v5e1", "hm"), ("V5E1", "HM"), ("v5E1", "Hm"),
            ("v6e1", "hm"), ("V6E1", "HM"), ("V6e1", "hM"),
        ]

        for acc, shape in high_mem_cases:
            probe_url = f"{self.cp_url}/tun/m/assign?accelerator={acc}&shape={shape}"
            try:
                urllib.request.urlopen(probe_url)
                self.fail(f"Expected 400 for {acc} with shape={shape}")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 400, f"Expected 400 for {acc} with {shape}, got {e.code}")

    def test_high_mem_accelerator_valid_creation_canonical_properties(self):
        """Verify that valid high-mem accelerator assignments have machineShape=1 and canonical uppercase names."""
        for acc in ["l4", "v5e1", "v6e1"]:
            nbh = f"nbh_{acc}"
            # Probe
            probe_url = f"{self.cp_url}/tun/m/assign?nbh={nbh}&variant=GPU&accelerator={acc}"
            with urllib.request.urlopen(probe_url) as resp:
                xsrf = json.loads(resp.read().decode()[len(XSSI_PREFIX) :])["token"]

            # Commit
            req = urllib.request.Request(probe_url, data=b"{}", headers={"X-Goog-Colab-Token": xsrf})
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode()[len(XSSI_PREFIX) :])
                self.assertEqual(data["accelerator"], acc.upper())

        # Verify assignments list
        with urllib.request.urlopen(f"{self.cp_url}/tun/m/assignments?authuser=0") as resp:
            data_list = json.loads(resp.read().decode()[len(XSSI_PREFIX) :])
            assignments = data_list["assignments"]
            self.assertEqual(len(assignments), 3)
            for a in assignments:
                self.assertEqual(a["machineShape"], 1)
                self.assertIn(a["accelerator"], ["L4", "V5E1", "V6E1"])


if __name__ == "__main__":
    unittest.main()
