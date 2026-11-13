"""Milestone E2E-M2: Adversarial Protocol, Concurrency & Stress Verification Suite.

Independent empirical verification suite written by Challenger 1 (teamwork_challenger_e2e_m2_1).
Adversarially challenges and stress-tests:
1. SSH Port Pool (2220-2229) exhaustion, 11th session NULL, concurrent teardown & port recycling.
2. OS port conflict handling (skipping bound ports).
3. Multi-client WebSocket concurrency against /colab/ssh (verifying HTTP 429 lock adherence)
   and high-frequency reconnect bursts.
4. WebSocket /colab/tty multi-client concurrency.
5. Colab protocol HTTP 412 concurrency limit & HTTP 400 quota handling with transactional rollback.
6. Case-insensitive High-RAM accelerator shape wire rules (l4, v5e1, v6e1 omitting shape=hm).
7. Concurrent admin logins (25 threads) without SQLite WAL locks.
8. Zero socket or file descriptor leaks under repeated test runs.
"""

from __future__ import annotations

import concurrent.futures
import gc
import json
import os
import re
import shutil
import socket
import tempfile
import time
import unittest
from typing import Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import (
    HIGH_MEM_ONLY_ACCELERATORS,
    ColabClient,
    ColabClientError,
    QuotaExceededError,
    TooManyAssignmentsError,
)
from backend.app.services.ssh_multiplexer import SSHPortManager
from backend.tests.mock_colab.mock_runtime_proxy import MockRuntimeProxyServer


class TestE2EM2ChallengerStress(unittest.TestCase):
    """Adversarial challenge and stress test suite for Milestone E2E-M2."""

    @classmethod
    def setUpClass(cls):
        cls.proxy_server = MockRuntimeProxyServer()
        cls.proxy_server.start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy_server.stop()

    def setUp(self):
        self.proxy_server.reset()
        self.test_dir = tempfile.mkdtemp(prefix="challenger_e2e_m2_")
        self.db_path = os.path.join(self.test_dir, "challenger_stress.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path

        # Setup mock Colab endpoint
        self.endpoint_id = "ep-challenger-m2"
        self.proxy_token = "tok-challenger-m2-xyz"
        self.proxy_server.app.register_endpoint(self.endpoint_id, self.proxy_token)
        self.host = self.proxy_server.host
        self.port = self.proxy_server.port

        # Seed primary test account with valid encrypted OAuth credentials
        self.account_id = "acc_challenger_m2"
        valid_creds = encrypt_secret(
            json.dumps({
                "access_token": "ya29.test-access-token",
                "refresh_token": "1//test-refresh-token",
                "expires_at": int(time.time()) + 7200,
            })
        )
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, active_assignments)
                VALUES (?, 'challenger@example.com', ?, 200.0, 0)
                """,
                (self.account_id, valid_creds),
            )

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _attempt_ssh_handshake(self, headers: Dict[str, str]) -> Tuple[int, str, str, socket.socket]:
        """Attempts SSH WebSocket handshake and returns (status_code, headers_str, body_str, socket)."""
        sock = socket.create_connection((self.host, self.port), timeout=4.0)
        lines = [
            f"GET /tun/m/{self.endpoint_id}/colab/ssh?colab-runtime-proxy-token={self.proxy_token} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
        ]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        req_bytes = "\r\n".join(lines).encode("utf-8") + b"\r\n\r\n"
        sock.sendall(req_bytes)

        sock.settimeout(2.5)
        buf = bytearray()
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\r\n\r\n" in buf:
                    if b" 101 " in buf:
                        break
                    headers_raw = bytes(buf).split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="ignore")
                    cl_match = re.search(r"Content-Length:\s*(\d+)", headers_raw, re.IGNORECASE)
                    if cl_match:
                        expected_len = int(cl_match.group(1))
                        current_body_len = len(bytes(buf).split(b"\r\n\r\n", 1)[1])
                        if current_body_len >= expected_len:
                            break
                    else:
                        break
            except socket.timeout:
                break

        raw = bytes(buf).decode("utf-8", errors="ignore")
        parts = raw.split("\r\n\r\n", 1)
        headers_part = parts[0]
        body_part = parts[1] if len(parts) > 1 else ""
        status_line = headers_part.splitlines()[0] if headers_part else ""
        status_code = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0

        if status_code != 101:
            sock.close()

        return status_code, headers_part, body_part, sock

    # =========================================================================
    # 1. SSH Port Pool (2220-2229) Exhaustion, Teardown & Recycling Stress
    # =========================================================================

    def test_ssh_port_pool_exact_10_allocation_and_11th_session_null(self):
        """Stress-test that all 10 ports (2220-2229) are allocated, and 11th gets None."""
        allocated_ports = []
        for i in range(10):
            port = session_manager.allocate_ssh_port()
            self.assertIsNotNone(port, f"Session {i+1} should have received a port")
            self.assertNotIn(port, allocated_ports, f"Port {port} allocated twice")
            allocated_ports.append(port)
            # Record in DB to simulate active session
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                    ) VALUES (?, ?, ?, 'nbh', 'ep', 'url', 'tok', datetime('now'), 'T4', 'READY', ?)
                    """,
                    (f"sess_pool_{i}", f"Pool Session {i}", self.account_id, port),
                )

        self.assertEqual(sorted(allocated_ports), list(range(2220, 2230)))

        # 11th allocation attempt must return None without raising error
        eleventh_port = session_manager.allocate_ssh_port()
        self.assertIsNone(eleventh_port, "11th allocation must return None when pool is exhausted")

    def test_ssh_port_pool_concurrent_teardown_and_port_recycling(self):
        """Stress-test concurrent session teardown and verify immediate port recycling."""
        # 1. Fill pool with 10 sessions
        session_ids = []
        for port in range(2220, 2230):
            sid = f"sess_rec_{port}"
            session_ids.append(sid)
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                    ) VALUES (?, ?, ?, 'nbh', 'ep', 'url', 'tok', datetime('now'), 'T4', 'READY', ?)
                    """,
                    (sid, f"Rec Session {port}", self.account_id, port),
                )

        # Verify pool is exhausted
        self.assertIsNone(session_manager.allocate_ssh_port())

        # 2. Concurrently tear down 5 sessions (ports 2221, 2223, 2225, 2227, 2229)
        teardown_sids = [f"sess_rec_{p}" for p in (2221, 2223, 2225, 2227, 2229)]

        mock_colab = MagicMock()
        mock_colab.unassign.return_value = True

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(session_manager.stop_session, sid, client=mock_colab)
                for sid in teardown_sids
            ]
            results = [f.result() for f in futures]
            self.assertTrue(all(results))

        # 3. Verify exactly 5 ports are now freed and recycled in lowest-first order
        freed_expected = [2221, 2223, 2225, 2227, 2229]
        recycled_ports = []
        for _ in range(5):
            p = session_manager.allocate_ssh_port()
            self.assertIsNotNone(p)
            recycled_ports.append(p)
            # Re-occupy in DB
            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                    ) VALUES (?, ?, ?, 'nbh', 'ep', 'url', 'tok', datetime('now'), 'T4', 'READY', ?)
                    """,
                    (f"sess_rebound_{p}", f"Rebound {p}", self.account_id, p),
                )

        self.assertEqual(sorted(recycled_ports), freed_expected)
        # Pool must be exhausted again
        self.assertIsNone(session_manager.allocate_ssh_port())

    def test_ssh_port_manager_skips_locally_bound_os_port(self):
        """SSHPortManager._is_port_available_on_os skips ports already bound on OS."""
        # Bind ephemeral socket on a high port range or test port manager logic
        pm = SSHPortManager(start_port=29100, end_port=29105)
        # Allocate on clean OS
        p1 = pm._is_port_available_on_os(29100)
        self.assertTrue(p1)

        # Now artificially bind a socket to 29100
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 29100))
        sock.listen(1)

        try:
            # OS availability probe must detect port is in use
            p_bound = pm._is_port_available_on_os(29100)
            self.assertFalse(p_bound, "OS port availability check must return False for bound port")

            # Port 29101 must still be available
            p_next = pm._is_port_available_on_os(29101)
            self.assertTrue(p_next)
        finally:
            sock.close()

    # =========================================================================
    # 2. Multi-Client WebSocket Concurrency & HTTP 429 Lock Adherence
    # =========================================================================

    def test_colab_ssh_multi_client_concurrency_lock_http_429(self):
        """Colab /colab/ssh allows only 1 active WebSocket; concurrent connections receive HTTP 429."""
        ed25519_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"

        # Client 1 connects successfully (HTTP 101)
        s1, _, _, sock1 = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": ed25519_key})
        self.assertEqual(s1, 101, "First SSH connection must succeed with HTTP 101")

        try:
            # Clients 2, 3, 4, 5 attempt concurrent connections; all must receive HTTP 429
            def connect_attempt():
                code, _, body, _ = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": ed25519_key})
                return code, body

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(connect_attempt) for _ in range(4)]
                results = [f.result() for f in futures]

            for code, body in results:
                self.assertEqual(code, 429, f"Concurrent connection must receive HTTP 429, got {code}")
                self.assertIn("Already-active SSH session", body)
        finally:
            # Client 1 disconnects
            sock1.close()

        time.sleep(0.05)

        # Client 6 connects successfully after lock release
        s6, _, _, sock6 = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": ed25519_key})
        self.assertEqual(s6, 101, "Connection after lock release must succeed with HTTP 101")
        sock6.close()

    def test_colab_ssh_high_frequency_reconnect_burst(self):
        """Stress-test 15 rapid sequential connect/disconnect cycles without stuck locks."""
        ed25519_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"

        for i in range(15):
            s, _, _, sock = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": ed25519_key})
            self.assertEqual(s, 101, f"Iteration {i+1} must succeed with HTTP 101")
            sock.close()
            time.sleep(0.01)

    def test_colab_ssh_pubkey_validation_boundaries(self):
        """Pubkey validation: missing key and ssh-rsa rejected with HTTP 400; ECDSA accepted with 101."""
        # 1. Missing header -> 400
        s_none, _, b_none, _ = self._attempt_ssh_handshake({})
        self.assertEqual(s_none, 400)
        self.assertIn("missing pubkey header", b_none)

        # 2. ssh-rsa -> 400
        rsa_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7vbvF8mQ... dev@colab"
        s_rsa, _, b_rsa, _ = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": rsa_key})
        self.assertEqual(s_rsa, 400)
        self.assertIn("RSA keys (ssh-rsa) are NOT accepted", b_rsa)

        # 3. ECDSA -> 101
        ecdsa_key = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTY... dev@colab"
        s_ec, _, _, sock_ec = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": ecdsa_key})
        self.assertEqual(s_ec, 101)
        sock_ec.close()

    def test_colab_tty_multi_client_concurrency(self):
        """Verify that /colab/tty allows concurrent WebSocket connections (unlike /colab/ssh)."""
        def connect_tty():
            sock = socket.create_connection((self.host, self.port), timeout=3.0)
            req = (
                f"GET /tun/m/{self.endpoint_id}/colab/tty?colab-runtime-proxy-token={self.proxy_token} HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            sock.sendall(req.encode("utf-8"))
            buf = sock.recv(1024)
            status_line = buf.splitlines()[0].decode("utf-8", errors="ignore")
            status_code = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0
            return status_code, sock

        sockets = []
        try:
            for i in range(3):
                code, sock = connect_tty()
                self.assertEqual(code, 101, f"TTY connection {i+1} must succeed with HTTP 101")
                sockets.append(sock)
        finally:
            for s in sockets:
                s.close()

    # =========================================================================
    # 3. Protocol Fidelity & Error Injection (HTTP 412, HTTP 400, Shape Rules)
    # =========================================================================

    def test_colab_http_412_concurrency_limit_mapping(self):
        """Colab HTTP 412 maps to TooManyAssignmentsError on both GET probe and POST commit."""
        client = ColabClient(base_url="http://mock-colab")

        # Case 1: GET returns 412
        mock_resp_412 = MagicMock()
        mock_resp_412.status_code = 412
        mock_resp_412.text = "Too many assignments"
        client.session.get = MagicMock(return_value=mock_resp_412)

        with self.assertRaises(TooManyAssignmentsError) as cm:
            client.assign("tok", "nbh", "T4")
        self.assertEqual(cm.exception.status_code, 412)

        # Case 2: GET succeeds (200), POST returns 412
        mock_resp_get = MagicMock()
        mock_resp_get.ok = True
        mock_resp_get.status_code = 200
        mock_resp_get.text = ')]}\'\n{"token": "xsrf-123"}'
        client.session.get = MagicMock(return_value=mock_resp_get)
        client.session.post = MagicMock(return_value=mock_resp_412)

        with self.assertRaises(TooManyAssignmentsError) as cm:
            client.assign("tok", "nbh", "T4")
        self.assertEqual(cm.exception.status_code, 412)

    def test_colab_http_400_quota_exceeded_mapping(self):
        """Colab HTTP 400 maps to QuotaExceededError on both GET probe and POST commit."""
        client = ColabClient(base_url="http://mock-colab")

        # Case 1: GET returns 400
        mock_resp_400 = MagicMock()
        mock_resp_400.status_code = 400
        mock_resp_400.text = "Quota exceeded"
        client.session.get = MagicMock(return_value=mock_resp_400)

        with self.assertRaises(QuotaExceededError) as cm:
            client.assign("tok", "nbh", "A100")
        self.assertEqual(cm.exception.status_code, 400)

        # Case 2: GET succeeds (200), POST returns 400
        mock_resp_get = MagicMock()
        mock_resp_get.ok = True
        mock_resp_get.status_code = 200
        mock_resp_get.text = ')]}\'\n{"token": "xsrf-123"}'
        client.session.get = MagicMock(return_value=mock_resp_get)
        client.session.post = MagicMock(return_value=mock_resp_400)

        with self.assertRaises(QuotaExceededError) as cm:
            client.assign("tok", "nbh", "A100")
        self.assertEqual(cm.exception.status_code, 400)

    def test_session_creation_rollback_on_assign_failure(self):
        """If Colab assign raises 412 or 400, no orphan session or port leak occurs in DB."""
        mock_client = MagicMock(spec=ColabClient)
        mock_client.assign.side_effect = TooManyAssignmentsError("Limit exceeded", status_code=412)

        with self.assertRaises(TooManyAssignmentsError):
            session_manager.create_session(
                SessionCreateRequest(name="sess_fail_test", accelerator="T4", account_id=self.account_id),
                client=mock_client,
            )

        # Verify DB has zero sessions created and active_assignments is 0
        with get_db(self.db_path) as conn:
            sess_count = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
            active_count = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (self.account_id,)).fetchone()["active_assignments"]

        self.assertEqual(sess_count, 0, "No session row should be persisted when assign fails")
        self.assertEqual(active_count, 0, "Active assignments should remain 0")

    def test_accelerator_shape_rule_case_insensitivity_matrix(self):
        """Adversarially verify High-RAM shape wire rules across case variations and accelerators."""
        client = ColabClient(base_url="http://mock-colab")

        # Captured query parameters
        captured_params: List[Dict[str, Any]] = []

        def mock_get(url, params=None, **kwargs):
            captured_params.append(dict(params or {}))
            resp = MagicMock()
            resp.ok = True
            resp.status_code = 200
            resp.text = ')]}\'\n{"endpoint": "ep-done", "runtimeProxyInfo": {"token": "t", "url": "http://p"}}'
            return resp

        client.session.get = MagicMock(side_effect=mock_get)

        # Test cases: (accelerator_input, shape_input, expected_shape_in_params, expected_variant)
        test_matrix = [
            ("L4", "hm", False, "GPU"),
            ("l4", "hm", False, "GPU"),
            ("V5E1", "hm", False, "TPU"),
            ("v5e1", "hm", False, "TPU"),
            ("V6E1", "hm", False, "TPU"),
            ("v6e1", "hm", False, "TPU"),
            ("T4", "hm", True, "GPU"),
            ("t4", "hm", True, "GPU"),
            ("A100", "hm", True, "GPU"),
            ("a100", "hm", True, "GPU"),
            ("H100", "hm", True, "GPU"),
            ("G4", "hm", True, "GPU"),
            ("t4", "standard", False, "GPU"),
            ("a100", "standard", False, "GPU"),
            ("l4", "standard", False, "GPU"),
            ("v5e1", "standard", False, "TPU"),
        ]

        for acc, shape, should_have_shape, expected_var in test_matrix:
            captured_params.clear()
            client.assign("test-tok", "test-nbh", accelerator=acc, shape=shape)
            self.assertTrue(len(captured_params) > 0)
            p = captured_params[0]

            self.assertEqual(p["variant"], expected_var, f"Variant mismatch for {acc}")
            self.assertEqual(p["accelerator"], acc.upper(), f"Accelerator uppercase normalization mismatch for {acc}")

            if should_have_shape:
                self.assertIn("shape", p, f"shape=hm expected for {acc} with shape={shape}")
                self.assertEqual(p["shape"], "hm")
            else:
                self.assertNotIn("shape", p, f"shape param must be omitted for {acc} with shape={shape}")

    # =========================================================================
    # 4. Database SQLite WAL Concurrency Stress (25+ Threads)
    # =========================================================================

    def test_concurrent_admin_logins_wal_concurrency(self):
        """25 concurrent threads issuing admin login and /me endpoints under SQLite WAL."""
        client = TestClient(app)

        def worker_login(idx: int):
            resp = client.post("/api/auth/login", json={"password": settings.ADMIN_PASSWORD})
            if resp.status_code != 200:
                return False, f"Login failed: {resp.status_code}"
            data = resp.json()
            token = data.get("token") or data.get("access_token")
            resp_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            if resp_me.status_code != 200:
                return False, f"Me failed: {resp_me.status_code}"
            return True, "OK"

        with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
            futures = [executor.submit(worker_login, i) for i in range(25)]
            results = [f.result() for f in futures]

        for success, msg in results:
            self.assertTrue(success, msg)

    # =========================================================================
    # 5. Socket and File Descriptor Leak Audit under Repeated Test Runs
    # =========================================================================

    def test_repeated_runs_zero_fd_and_socket_leaks(self):
        """Verify zero file descriptor or socket leaks across repeated test operations."""
        gc.collect()

        def get_open_fds() -> int:
            try:
                return len(os.listdir("/dev/fd"))
            except Exception:
                return 0

        # Warm up
        for _ in range(3):
            s = session_manager.allocate_ssh_port()

        gc.collect()
        initial_fds = get_open_fds()

        # Run 20 repetitive allocation, release, and handshake cycles
        for i in range(20):
            # Port allocation & release cycle
            p = session_manager.allocate_ssh_port()
            self.assertIsNotNone(p)

            # SSH handshake cycle
            s, _, _, sock = self._attempt_ssh_handshake({
                "X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"
            })
            self.assertEqual(s, 101)
            sock.close()

        gc.collect()
        final_fds = get_open_fds()

        # Allowance of +/- 2 fds for internal Python/OS dynamic caches
        fd_diff = abs(final_fds - initial_fds)
        self.assertLessEqual(
            fd_diff,
            2,
            f"File descriptor leak detected! Initial: {initial_fds}, Final: {final_fds}, Diff: {fd_diff}",
        )


if __name__ == "__main__":
    unittest.main()
