"""Empirical Challenger Test Suite for Milestone E2E-M1 Iteration 2.

Authored by Challenger 1 (Replacement) to adversarially challenge and stress-test:
1. Multi-Session WebSocket and REST Token Routing:
   - Simultaneous connections to distinct sessions without /tun/m/{endpoint} prefix.
   - Both /colab/ssh and /colab/tty concurrent connections across sessions without 401.
   - Intra-session SSH single-connection collision (HTTP 429) while inter-session SSH succeeds.
   - REST VFS data isolation between sessions when addressed by token without prefix.
   - Token header authentication (X-Colab-Runtime-Proxy-Token).
   - Invalid, expired, and missing tokens under multi-session topology.
   - Concurrency stress and thread safety under simultaneous dispatch.

2. Case-Insensitive Accelerator Shape Parameter Rejection:
   - Rejection matrix for l4, L4, v5e1, V5E1, v6e1, V6E1 with shape=hm, HM, Hm, hM on GET and POST.
   - MachineShape=1 verification for high-RAM accelerators when shape is omitted.
   - Canonical uppercase accelerator name normalization in assignment state and responses.
   - Verification of standard accelerators (t4, T4, a100, A100) with and without shape=hm.
"""

from __future__ import annotations

import base64
import concurrent.futures
import json
import logging
import socket
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from backend.tests.mock_colab.mock_control_plane import (
    HIGH_MEM_ACCELERATORS,
    MockControlPlaneServer,
)
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_runtime_proxy import (
    MockRuntimeProxyApp,
    MockRuntimeProxyServer,
    RFC6455WebSocket,
)

logger = logging.getLogger(__name__)


def _send_masked_text_frame(sock: socket.socket, text: str) -> None:
    payload = text.encode("utf-8")
    header = bytearray([0x81, 0x80 | len(payload)])
    mask = b"\x11\x22\x33\x44"
    masked_payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked_payload)


def _send_masked_binary_frame(sock: socket.socket, data: bytes) -> None:
    header = bytearray([0x82, 0x80 | len(data)])
    mask = b"\xaa\xbb\xcc\xdd"
    masked_payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(bytes(header) + mask + masked_payload)


def _recv_ws_echo_binary(sock: socket.socket, timeout: float = 3.0) -> bytes:
    """Helper to consume any initial banner frames and receive the echoed binary frame."""
    sock.settimeout(timeout)
    data = bytearray()
    start_time = time.time()
    while time.time() - start_time < timeout:
        chunk = sock.recv(2048)
        if not chunk:
            break
        data.extend(chunk)
        # Parse WebSocket frame(s)
        buf = bytes(data)
        while buf:
            frame = RFC6455WebSocket.decode_frame(buf)
            if not frame:
                break
            opcode, payload, consumed = frame
            buf = buf[consumed:]
            data = bytearray(buf)
            # If it's an echo binary frame (not the initial OpenSSH banner)
            if opcode == 0x2 and not payload.startswith(b"SSH-2.0-OpenSSH"):
                return payload
    return bytes(data)


class TestEmpiricalMultiSessionRouting(unittest.TestCase):
    """Adversarial challenge test suite for Multi-Session Routing (Defect 1)."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockRuntimeProxyServer(host="127.0.0.1", port=0)
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.reset()
        self.host = self.server.host
        self.port = self.server.port

        # Register 3 distinct active sessions
        self.s1_ep = "ep-session-alpha"
        self.s1_tok = "tok-alpha-1111"
        self.s2_ep = "ep-session-beta"
        self.s2_tok = "tok-beta-2222"
        self.s3_ep = "ep-session-gamma"
        self.s3_tok = "tok-gamma-3333"

        self.server.app.register_endpoint(self.s1_ep, self.s1_tok)
        self.server.app.register_endpoint(self.s2_ep, self.s2_tok)
        self.server.app.register_endpoint(self.s3_ep, self.s3_tok)

    def _read_http_response(self, sock: socket.socket, timeout: float = 3.0) -> str:
        sock.settimeout(timeout)
        data = bytearray()
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
                if b"\r\n\r\n" in data:
                    header_bytes, rest = data.split(b"\r\n\r\n", 1)
                    header_str = header_bytes.decode("iso-8859-1")
                    status_line = header_str.split("\r\n")[0]
                    if "101" in status_line:
                        return data.decode("utf-8", errors="replace")
                    content_length = None
                    for line in header_str.split("\r\n")[1:]:
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":", 1)[1].strip())
                            break
                    if content_length is not None and len(rest) >= content_length:
                        return data.decode("utf-8", errors="replace")
                    if content_length is None:
                        return data.decode("utf-8", errors="replace")
        except socket.timeout:
            pass
        return data.decode("utf-8", errors="replace")

    def _connect_ws(
        self, path: str, headers: Optional[Dict[str, str]] = None
    ) -> Tuple[socket.socket, str]:
        sock = socket.create_connection((self.host, self.port), timeout=5.0)
        hdrs = [
            f"GET {path} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
        ]
        if headers:
            for k, v in headers.items():
                hdrs.append(f"{k}: {v}")
        req = "\r\n".join(hdrs) + "\r\n\r\n"
        sock.sendall(req.encode("utf-8"))
        resp = self._read_http_response(sock)
        return sock, resp

    def test_multi_session_simultaneous_ssh_and_tty_connections(self):
        """Adversarially challenge that multiple sessions can hold simultaneous
        SSH and TTY WebSocket connections without /tun/m/{endpoint} prefix.
        """
        open_sockets = []
        try:
            # 1. Connect Session 1 SSH without endpoint prefix
            s1_ssh, r1_ssh = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s1_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user1@colab"},
            )
            open_sockets.append(s1_ssh)
            self.assertIn("101 Switching Protocols", r1_ssh, f"Session 1 SSH failed: {r1_ssh}")

            # 2. Concurrently connect Session 2 SSH without endpoint prefix
            s2_ssh, r2_ssh = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s2_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user2@colab"},
            )
            open_sockets.append(s2_ssh)
            self.assertIn("101 Switching Protocols", r2_ssh, f"Session 2 SSH failed: {r2_ssh}")

            # 3. Concurrently connect Session 1 TTY without endpoint prefix
            s1_tty, r1_tty = self._connect_ws(
                f"/colab/tty?colab-runtime-proxy-token={self.s1_tok}"
            )
            open_sockets.append(s1_tty)
            self.assertIn("101 Switching Protocols", r1_tty, f"Session 1 TTY failed: {r1_tty}")

            # 4. Concurrently connect Session 2 TTY without endpoint prefix
            s2_tty, r2_tty = self._connect_ws(
                f"/colab/tty?colab-runtime-proxy-token={self.s2_tok}"
            )
            open_sockets.append(s2_tty)
            self.assertIn("101 Switching Protocols", r2_tty, f"Session 2 TTY failed: {r2_tty}")

            # 5. Concurrently connect Session 3 TTY without endpoint prefix
            s3_tty, r3_tty = self._connect_ws(
                f"/colab/tty?colab-runtime-proxy-token={self.s3_tok}"
            )
            open_sockets.append(s3_tty)
            self.assertIn("101 Switching Protocols", r3_tty, f"Session 3 TTY failed: {r3_tty}")

            # All 5 WebSockets are active simultaneously!
            # Test TTY interaction on Session 1
            _send_masked_text_frame(s1_tty, json.dumps({"data": "whoami\n"}))
            buf1 = s1_tty.recv(2048)
            self.assertTrue(len(buf1) > 0)
            self.assertIn(b"root", buf1)

            # Test TTY interaction on Session 2
            _send_masked_text_frame(s2_tty, json.dumps({"data": "pwd\n"}))
            buf2 = s2_tty.recv(2048)
            self.assertTrue(len(buf2) > 0)
            self.assertIn(b"/content", buf2)

            # Test SSH binary echo on Session 1
            test_payload = b"SSH-BINARY-ALPHA-PAYLOAD"
            _send_masked_binary_frame(s1_ssh, test_payload)
            echo1 = _recv_ws_echo_binary(s1_ssh)
            self.assertEqual(test_payload, echo1)

            # Test SSH binary echo on Session 2
            test_payload2 = b"SSH-BINARY-BETA-PAYLOAD"
            _send_masked_binary_frame(s2_ssh, test_payload2)
            echo2 = _recv_ws_echo_binary(s2_ssh)
            self.assertEqual(test_payload2, echo2)

        finally:
            for s in open_sockets:
                try:
                    s.close()
                except Exception:
                    pass

    def test_intra_session_ssh_collision_preserves_inter_session_freedom(self):
        """Verify that intra-session duplicate SSH returns HTTP 429 without affecting
        concurrent SSH connections to distinct sessions.
        """
        open_sockets = []
        try:
            # 1. Connect Session 1 SSH
            s1_ssh, r1 = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s1_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user1@colab"},
            )
            open_sockets.append(s1_ssh)
            self.assertIn("101 Switching Protocols", r1)

            # 2. Connect Session 2 SSH
            s2_ssh, r2 = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s2_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user2@colab"},
            )
            open_sockets.append(s2_ssh)
            self.assertIn("101 Switching Protocols", r2)

            # 3. Collision: 2nd connection to Session 1 MUST be rejected with HTTP 429
            s1_dup, r_dup1 = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s1_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... dup1@colab"},
            )
            s1_dup.close()
            self.assertIn("429 Too Many Requests", r_dup1)
            self.assertIn("Already-active SSH session", r_dup1)

            # 4. Collision: 2nd connection to Session 2 MUST also be rejected with HTTP 429
            s2_dup, r_dup2 = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s2_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... dup2@colab"},
            )
            s2_dup.close()
            self.assertIn("429 Too Many Requests", r_dup2)
            self.assertIn("Already-active SSH session", r_dup2)

            # 5. Session 3 is unaffected and can establish SSH freely
            s3_ssh, r3 = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s3_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user3@colab"},
            )
            open_sockets.append(s3_ssh)
            self.assertIn("101 Switching Protocols", r3)

            # 6. Disconnect Session 1 SSH -> Lock released -> New connection to Session 1 succeeds
            s1_ssh.close()
            open_sockets.remove(s1_ssh)
            time.sleep(0.05)  # Wait for server thread cleanup

            s1_retry, r1_retry = self._connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={self.s1_tok}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... retry@colab"},
            )
            open_sockets.append(s1_retry)
            self.assertIn("101 Switching Protocols", r1_retry)

            # Session 2 and Session 3 are still alive
            _send_masked_binary_frame(s2_ssh, b"PING")
            echo_ping = _recv_ws_echo_binary(s2_ssh)
            self.assertEqual(b"PING", echo_ping)

        finally:
            for s in open_sockets:
                try:
                    s.close()
                except Exception:
                    pass

    def test_multi_session_rest_vfs_isolation(self):
        """Verify that REST VFS operations without /tun/m/{endpoint} prefix route
        deterministically by proxy token and maintain complete filesystem isolation.
        """
        # Upload file to Session 1
        content_alpha = "Hello from Session Alpha VFS!"
        put_body_alpha = json.dumps(
            {
                "path": "alpha_note.txt",
                "type": "file",
                "content": base64.b64encode(content_alpha.encode()).decode("ascii"),
                "chunk": 1,
            }
        ).encode("utf-8")
        req_put_alpha = urllib.request.Request(
            f"{self.server.url}/api/contents/alpha_note.txt?colab-runtime-proxy-token={self.s1_tok}",
            data=put_body_alpha,
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_put_alpha) as resp:
            self.assertEqual(resp.status, 200)

        # Upload file to Session 2
        content_beta = "Hello from Session Beta VFS!"
        put_body_beta = json.dumps(
            {
                "path": "beta_note.txt",
                "type": "file",
                "content": base64.b64encode(content_beta.encode()).decode("ascii"),
                "chunk": 1,
            }
        ).encode("utf-8")
        req_put_beta = urllib.request.Request(
            f"{self.server.url}/api/contents/beta_note.txt?colab-runtime-proxy-token={self.s2_tok}",
            data=put_body_beta,
            method="PUT",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req_put_beta) as resp:
            self.assertEqual(resp.status, 200)

        # Verify Session 1 can read its own file
        req_get_alpha = urllib.request.Request(
            f"{self.server.url}/api/contents/alpha_note.txt?content=1&colab-runtime-proxy-token={self.s1_tok}"
        )
        with urllib.request.urlopen(req_get_alpha) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            decoded = base64.b64decode(data["content"]).decode()
            self.assertEqual(decoded, content_alpha)

        # Verify Session 2 CANNOT read Session 1's file (404)
        req_iso_1 = urllib.request.Request(
            f"{self.server.url}/api/contents/alpha_note.txt?colab-runtime-proxy-token={self.s2_tok}"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_iso_1)
        self.assertEqual(ctx.exception.code, 404)

        # Verify Session 1 CANNOT read Session 2's file (404)
        req_iso_2 = urllib.request.Request(
            f"{self.server.url}/api/contents/beta_note.txt?colab-runtime-proxy-token={self.s1_tok}"
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_iso_2)
        self.assertEqual(ctx.exception.code, 404)

    def test_token_passed_via_http_header_instead_of_query_param(self):
        """Verify routing works when token is supplied via X-Colab-Runtime-Proxy-Token
        header rather than query parameter (REST, SSH WS, and TTY WS).
        """
        # REST with header
        req_rest = urllib.request.Request(
            f"{self.server.url}/api/contents/",
            headers={"X-Colab-Runtime-Proxy-Token": self.s2_tok},
        )
        with urllib.request.urlopen(req_rest) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["type"], "directory")

        # SSH WS with header
        s_ssh, r_ssh = self._connect_ws(
            "/colab/ssh",
            {
                "X-Colab-Runtime-Proxy-Token": self.s2_tok,
                "X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... header@colab",
            },
        )
        s_ssh.close()
        self.assertIn("101 Switching Protocols", r_ssh)

        # TTY WS with header
        s_tty, r_tty = self._connect_ws(
            "/colab/tty",
            {"X-Colab-Runtime-Proxy-Token": self.s3_tok},
        )
        s_tty.close()
        self.assertIn("101 Switching Protocols", r_tty)

    def test_invalid_expired_and_missing_tokens_under_multi_session(self):
        """Verify that invalid, expired, and missing tokens reliably return HTTP 401."""
        # 1. Missing token under multi-session topology -> 401
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f"{self.server.url}/api/contents/")
        self.assertEqual(ctx.exception.code, 401)

        s_missing, r_missing = self._connect_ws(
            "/colab/ssh",
            {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... missing@colab"},
        )
        s_missing.close()
        self.assertIn("401 Unauthorized", r_missing)

        # 2. Invalid token -> 401
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                f"{self.server.url}/api/contents/?colab-runtime-proxy-token=fake_tok_9999"
            )
        self.assertEqual(ctx.exception.code, 401)

        s_fake, r_fake = self._connect_ws(
            "/colab/tty?colab-runtime-proxy-token=fake_tok_9999"
        )
        s_fake.close()
        self.assertIn("401 Unauthorized", r_fake)

        # 3. Expired token -> 401
        ep1 = self.server.app.get_endpoint(self.s1_ep)
        self.assertIsNotNone(ep1)
        ep1.token_expired = True

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(
                f"{self.server.url}/api/contents/?colab-runtime-proxy-token={self.s1_tok}"
            )
        self.assertEqual(ctx.exception.code, 401)

        s_exp, r_exp = self._connect_ws(
            f"/colab/ssh?colab-runtime-proxy-token={self.s1_tok}",
            {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... exp@colab"},
        )
        s_exp.close()
        self.assertIn("401 Unauthorized", r_exp)

    def test_concurrent_multi_threaded_app_dispatch_stress(self):
        """Stress test thread safety and race conditions in get_endpoint_by_token
        and request dispatching across 50 concurrent worker threads executing 1000 dispatches.
        """
        app = MockRuntimeProxyApp()
        for i in range(10):
            app.register_endpoint(f"ep-stress-{i}", f"tok-stress-{i}")

        errors = []

        def worker_task(thread_id: int):
            try:
                tok = f"tok-stress-{thread_id % 10}"
                status, hdrs, body = app.dispatch_rest(
                    "GET",
                    "/api/contents/",
                    {"colab-runtime-proxy-token": [tok]},
                    b"",
                    {},
                )
                if status != 200:
                    errors.append(f"Thread {thread_id} got status {status}")
            except Exception as e:
                errors.append(f"Thread {thread_id} raised: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(worker_task, i) for i in range(1000)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, f"App dispatch stress test failed: {errors}")


class TestEmpiricalCaseInsensitiveShapeRejection(unittest.TestCase):
    """Adversarial challenge test suite for Case-Insensitive Shape Rules (Defect 2)."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockControlPlaneServer(host="127.0.0.1", port=0)
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.reset()

    def _get(self, path: str) -> Tuple[int, dict]:
        req = urllib.request.Request(f"{self.server.url}{path}")
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                clean = raw.removeprefix(")]}'\n")
                return resp.status, json.loads(clean)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            clean = raw.removeprefix(")]}'\n")
            return e.code, json.loads(clean)

    def _post(self, path: str, headers: Optional[dict] = None) -> Tuple[int, dict]:
        req = urllib.request.Request(
            f"{self.server.url}{path}", data=b"", headers=headers or {}, method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                raw = resp.read().decode("utf-8")
                clean = raw.removeprefix(")]}'\n")
                return resp.status, json.loads(clean)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            clean = raw.removeprefix(")]}'\n")
            return e.code, json.loads(clean)

    def test_adversarial_casing_rejection_matrix(self):
        """Stress-test all case permutations of high-RAM accelerators and shape parameter.
        Every combination of [l4, L4, v5e1, V5E1, v6e1, V6E1] x [hm, HM, Hm, hM]
        MUST be rejected with HTTP 400 Bad Request on both GET and POST.
        """
        acc_variants = [
            "l4", "L4",
            "v5e1", "V5e1", "v5E1", "V5E1",
            "v6e1", "V6e1", "v6E1", "V6E1",
        ]
        shape_variants = ["hm", "HM", "Hm", "hM"]

        for acc in acc_variants:
            for shape in shape_variants:
                # 1. Probe GET
                get_path = f"/tun/m/assign?nbh=nbh_{acc}_{shape}&accelerator={acc}&shape={shape}"
                status_get, data_get = self._get(get_path)
                self.assertEqual(
                    status_get,
                    400,
                    f"GET {get_path} expected 400 Bad Request, got {status_get}",
                )
                self.assertIn("error", data_get)
                self.assertIn("not supported", data_get["error"].lower())

                # 2. Probe POST (with valid XSRF token obtained via standard accelerator)
                status_tok_i, data_tok_i = self._get(f"/tun/m/assign?nbh=nbh_post_{acc}_{shape}&accelerator=T4")
                xsrf_i = data_tok_i["token"]

                post_path = f"/tun/m/assign?nbh=nbh_post_{acc}_{shape}&accelerator={acc}&shape={shape}"
                status_post, data_post = self._post(
                    post_path, headers={"X-Goog-Colab-Token": xsrf_i}
                )
                self.assertEqual(
                    status_post,
                    400,
                    f"POST {post_path} expected 400 Bad Request, got {status_post}",
                )
                self.assertIn("error", data_post)
                self.assertIn("invalid shape=hm", data_post["error"].lower())

    def test_valid_high_ram_accelerators_assignment_and_normalization(self):
        """Verify that high-RAM accelerators without shape=hm succeed, return machineShape=1
        in assignment listings, and normalize accelerator strings to canonical uppercase.
        """
        test_accs = ["l4", "v5e1", "v6e1", "V5e1", "v6E1"]
        expected_canonical = {
            "l4": "L4",
            "v5e1": "V5E1",
            "v6e1": "V6E1",
            "V5e1": "V5E1",
            "v6E1": "V6E1",
        }

        for acc in test_accs:
            # 1. GET returns XSRF
            status_get, data_get = self._get(f"/tun/m/assign?nbh=nbh_ok_{acc}&accelerator={acc}")
            self.assertEqual(status_get, 200, f"GET for {acc} failed: {data_get}")
            xsrf = data_get["token"]

            # 2. POST assigns instance
            status_post, data_post = self._post(
                f"/tun/m/assign?nbh=nbh_ok_{acc}&accelerator={acc}",
                headers={"X-Goog-Colab-Token": xsrf},
            )
            self.assertEqual(status_post, 200, f"POST for {acc} failed: {data_post}")
            self.assertTrue(data_post["endpoint"].startswith("ep-"))
            self.assertEqual(
                data_post["accelerator"],
                expected_canonical[acc],
                f"Accelerator {acc} not normalized to uppercase",
            )

        # 3. Verify /tun/m/assignments listing reflects canonical states and machineShape=1
        status_list, data_list = self._get("/tun/m/assignments?authuser=0")
        self.assertEqual(status_list, 200)
        assignments = data_list.get("assignments", [])
        self.assertEqual(len(assignments), len(test_accs))
        for a in assignments:
            self.assertEqual(
                a["machineShape"],
                1,
                f"Accelerator {a.get('accelerator')} must have machineShape=1 in assignments listing",
            )
            self.assertIn(a["accelerator"], HIGH_MEM_ACCELERATORS)

    def test_standard_accelerator_shape_matrix(self):
        """Verify standard accelerators (T4, A100):
        - shape=hm / shape=HM -> succeeds with machineShape == 1 in assignments
        - no shape -> succeeds with machineShape == 0 in assignments
        """
        # T4 with shape=hm -> machineShape: 1
        s_get1, d_get1 = self._get("/tun/m/assign?nbh=nbh_t4_hm&accelerator=T4&shape=hm")
        self.assertEqual(s_get1, 200)
        s_post1, d_post1 = self._post(
            "/tun/m/assign?nbh=nbh_t4_hm&accelerator=T4&shape=hm",
            headers={"X-Goog-Colab-Token": d_get1["token"]},
        )
        self.assertEqual(s_post1, 200)
        ep1 = d_post1["endpoint"]

        # lowercase t4 with uppercase shape=HM -> machineShape: 1
        s_get2, d_get2 = self._get("/tun/m/assign?nbh=nbh_t4_HM&accelerator=t4&shape=HM")
        self.assertEqual(s_get2, 200)
        s_post2, d_post2 = self._post(
            "/tun/m/assign?nbh=nbh_t4_HM&accelerator=t4&shape=HM",
            headers={"X-Goog-Colab-Token": d_get2["token"]},
        )
        self.assertEqual(s_post2, 200)
        self.assertEqual(d_post2["accelerator"], "T4")
        ep2 = d_post2["endpoint"]

        # T4 without shape -> machineShape: 0
        s_get3, d_get3 = self._get("/tun/m/assign?nbh=nbh_t4_std&accelerator=T4")
        self.assertEqual(s_get3, 200)
        s_post3, d_post3 = self._post(
            "/tun/m/assign?nbh=nbh_t4_std&accelerator=T4",
            headers={"X-Goog-Colab-Token": d_get3["token"]},
        )
        self.assertEqual(s_post3, 200)
        ep3 = d_post3["endpoint"]

        # a100 without shape -> machineShape: 0
        s_get4, d_get4 = self._get("/tun/m/assign?nbh=nbh_a100_std&accelerator=a100")
        self.assertEqual(s_get4, 200)
        s_post4, d_post4 = self._post(
            "/tun/m/assign?nbh=nbh_a100_std&accelerator=a100",
            headers={"X-Goog-Colab-Token": d_get4["token"]},
        )
        self.assertEqual(s_post4, 200)
        self.assertEqual(d_post4["accelerator"], "A100")
        ep4 = d_post4["endpoint"]

        # Verify machineShape in assignments list
        s_list, d_list = self._get("/tun/m/assignments?authuser=0")
        self.assertEqual(s_list, 200)
        by_ep = {a["endpoint"]: a for a in d_list.get("assignments", [])}
        self.assertEqual(by_ep[ep1]["machineShape"], 1)
        self.assertEqual(by_ep[ep2]["machineShape"], 1)
        self.assertEqual(by_ep[ep3]["machineShape"], 0)
        self.assertEqual(by_ep[ep4]["machineShape"], 0)


class TestEmpiricalEndToEndHubIntegration(unittest.TestCase):
    """End-to-end integration test combining MockControlHub, multi-session creation,
    and cross-service routing.
    """

    def test_hub_e2e_multi_session_and_shape_validation(self):
        with MockControlHub() as hub:
            cp_url = hub.urls["control_plane"]
            rp_url = hub.urls["runtime_proxy"]

            # 1. Attempt assigning L4 with shape=hm -> rejected with 400
            req_bad = urllib.request.Request(f"{cp_url}/tun/m/assign?accelerator=l4&shape=hm")
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req_bad)
            self.assertEqual(ctx.exception.code, 400)

            # 2. Assign Session 1: L4 without shape
            req_s1_get = urllib.request.Request(f"{cp_url}/tun/m/assign?nbh=nbh_hub_1&accelerator=l4")
            with urllib.request.urlopen(req_s1_get) as resp:
                data_s1_get = json.loads(resp.read().decode().removeprefix(")]}'\n"))
            xsrf1 = data_s1_get["token"]

            req_s1_post = urllib.request.Request(
                f"{cp_url}/tun/m/assign?nbh=nbh_hub_1&accelerator=l4",
                data=b"",
                headers={"X-Goog-Colab-Token": xsrf1},
                method="POST",
            )
            with urllib.request.urlopen(req_s1_post) as resp:
                data_s1_post = json.loads(resp.read().decode().removeprefix(")]}'\n"))
            self.assertEqual(data_s1_post["accelerator"], "L4")
            ep1 = data_s1_post["endpoint"]
            tok1 = data_s1_post["runtimeProxyInfo"]["token"]

            # 3. Assign Session 2: V5E1 without shape
            req_s2_get = urllib.request.Request(f"{cp_url}/tun/m/assign?nbh=nbh_hub_2&accelerator=v5e1")
            with urllib.request.urlopen(req_s2_get) as resp:
                data_s2_get = json.loads(resp.read().decode().removeprefix(")]}'\n"))
            xsrf2 = data_s2_get["token"]

            req_s2_post = urllib.request.Request(
                f"{cp_url}/tun/m/assign?nbh=nbh_hub_2&accelerator=v5e1",
                data=b"",
                headers={"X-Goog-Colab-Token": xsrf2},
                method="POST",
            )
            with urllib.request.urlopen(req_s2_post) as resp:
                data_s2_post = json.loads(resp.read().decode().removeprefix(")]}'\n"))
            self.assertEqual(data_s2_post["accelerator"], "V5E1")
            ep2 = data_s2_post["endpoint"]
            tok2 = data_s2_post["runtimeProxyInfo"]["token"]

            # Verify machineShape in assignments listing
            req_list = urllib.request.Request(f"{cp_url}/tun/m/assignments?authuser=0")
            with urllib.request.urlopen(req_list) as resp:
                data_list = json.loads(resp.read().decode().removeprefix(")]}'\n"))
            by_ep = {a["endpoint"]: a for a in data_list.get("assignments", [])}
            self.assertEqual(by_ep[ep1]["machineShape"], 1)
            self.assertEqual(by_ep[ep2]["machineShape"], 1)

            # 4. Both sessions are registered in Runtime Proxy; perform simultaneous WebSocket connections
            host = hub.runtime_proxy_server.host
            port = hub.runtime_proxy_server.port

            def _connect_raw(path: str, headers: dict) -> Tuple[socket.socket, str]:
                sock = socket.create_connection((host, port))
                hdrs = [
                    f"GET {path} HTTP/1.1",
                    f"Host: {host}:{port}",
                    "Upgrade: websocket",
                    "Connection: Upgrade",
                    "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
                    "Sec-WebSocket-Version: 13",
                ]
                for k, v in headers.items():
                    hdrs.append(f"{k}: {v}")
                req_b = "\r\n".join(hdrs) + "\r\n\r\n"
                sock.sendall(req_b.encode())
                sock.settimeout(2.0)
                resp_b = sock.recv(4096).decode(errors="ignore")
                return sock, resp_b

            s1, r1 = _connect_raw(
                f"/colab/ssh?colab-runtime-proxy-token={tok1}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... hub1@colab"},
            )
            s2, r2 = _connect_raw(
                f"/colab/ssh?colab-runtime-proxy-token={tok2}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... hub2@colab"},
            )
            try:
                self.assertIn("101 Switching Protocols", r1)
                self.assertIn("101 Switching Protocols", r2)

                # Duplicate SSH to Session 1 gives 429
                s1_dup, r1_dup = _connect_raw(
                    f"/colab/ssh?colab-runtime-proxy-token={tok1}",
                    {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... hubdup@colab"},
                )
                s1_dup.close()
                self.assertIn("429 Too Many Requests", r1_dup)

            finally:
                s1.close()
                s2.close()


if __name__ == "__main__":
    unittest.main()
