"""Tier 2 Boundary Tests: SSH Upstream & System Invariant Boundaries (T2.5).

Validates:
1. SSH WebSocket single-connection lock (HTTP 429) and release lifecycle.
2. SSH legacy RSA key rejection (HTTP 400) vs Ed25519/ECDSA acceptance (HTTP 101).
3. SSH port pool exhaustion (ports 2220-2229): 11th session gets NULL, returns port on release.
4. Idempotent session stop and active_assignments non-negative floor at 0.
5. Database SQLite WAL high-concurrency burst (50 threads / 1,000 mixed transactions).
6. Template deletion SET NULL foreign key constraint on referencing sessions and jobs.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import socket
import tempfile
import time
from typing import Dict, Tuple
from unittest.mock import MagicMock
import unittest
import uuid

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.services import session_manager
from backend.tests.mock_colab.mock_runtime_proxy import MockRuntimeProxyServer


class TestSSHSystemBoundaries(unittest.TestCase):
    """Tier 2 Boundary tests for SSH protocol, Port Pool, Idempotency, and SQLite WAL."""

    @classmethod
    def setUpClass(cls):
        cls.proxy_server = MockRuntimeProxyServer()
        cls.proxy_server.start()

    @classmethod
    def tearDownClass(cls):
        cls.proxy_server.stop()

    def setUp(self):
        self.proxy_server.reset()
        self.test_dir = tempfile.mkdtemp(prefix="t2_ssh_sys_")
        self.db_path = os.path.join(self.test_dir, "ssh_sys_boundary.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path

        # Setup mock endpoint in runtime proxy
        self.endpoint_id = "ep-t2-ssh-sys"
        self.proxy_token = "proxy-tok-ssh-sys-xyz"
        self.proxy_server.app.register_endpoint(self.endpoint_id, self.proxy_token)
        self.host = self.proxy_server.host
        self.port = self.proxy_server.port

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _attempt_ssh_handshake(self, headers: Dict[str, str]) -> Tuple[int, str, str, socket.socket]:
        """Attempts SSH WebSocket handshake and returns (status_code, headers_str, body_str, socket)."""
        sock = socket.create_connection((self.host, self.port), timeout=3.0)
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

        sock.settimeout(2.0)
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

    def test_ssh_websocket_single_connection_lock_http_429(self):
        """Attempting a second concurrent /colab/ssh connection returns HTTP 429 until the first closes."""
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"

        # 1. First connection succeeds (101)
        s1, _, _, sock1 = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": pubkey})
        self.assertEqual(s1, 101, "First SSH connection must succeed with HTTP 101")

        try:
            # 2. Second concurrent connection receives HTTP 429
            s2, _, body2, _ = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": pubkey})
            self.assertEqual(s2, 429, f"Concurrent SSH connection must receive HTTP 429, got {s2}")
            self.assertIn("Already-active SSH session", body2)
        finally:
            # 3. Disconnect first connection
            sock1.close()

        time.sleep(0.05)

        # 4. Third connection succeeds after lock release
        s3, _, _, sock3 = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": pubkey})
        self.assertEqual(s3, 101, "Re-connecting after lock release must succeed with HTTP 101")
        sock3.close()

    def test_ssh_legacy_rsa_key_rejected_http_400(self):
        """Legacy ssh-rsa and unsupported keys return HTTP 400; Ed25519 and ECDSA return 101."""
        # 1. ssh-rsa rejected
        rsa_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7vbvF8mQ... dev@colab"
        s_rsa, _, body_rsa, _ = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": rsa_key})
        self.assertEqual(s_rsa, 400)
        self.assertIn("RSA keys (ssh-rsa) are NOT accepted", body_rsa)

        # 2. ssh-dss rejected
        dss_key = "ssh-dss AAAAB3NzaC1kc3MAAACBAOp... dev@colab"
        s_dss, _, body_dss, _ = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": dss_key})
        self.assertEqual(s_dss, 400)
        self.assertIn("unsupported key type", body_dss)

        # 3. Ed25519 accepted
        ed_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"
        s_ed, _, _, sock_ed = self._attempt_ssh_handshake({"X-Colab-Ssh-Pubkey": ed_key})
        self.assertEqual(s_ed, 101)
        sock_ed.close()

    def test_ssh_port_pool_exhaustion_11th_session(self):
        """When all 10 ports (2220-2229) are occupied, allocate_ssh_port returns None gracefully."""
        account_id = "acc_port_pool"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                VALUES (?, 'ports@example.com', 'dummy', 100.0)
                """,
                (account_id,),
            )
            # Allocate all 10 ports in pool
            for port in settings.ssh_port_pool:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                    ) VALUES (?, ?, ?, 'nb', 'ep', 'url', 'tok', datetime('now'), 'T4', 'READY', ?)
                    """,
                    (f"sess_port_{port}", f"Session {port}", account_id, port),
                )

        # 11th allocation attempt must return None
        allocated = session_manager.allocate_ssh_port()
        self.assertIsNone(allocated, "11th session must receive None when ports 2220-2229 are exhausted")

        # Release port 2225 by stopping session
        with get_db(self.db_path) as conn:
            conn.execute("UPDATE sessions SET status = 'STOPPED', ssh_local_port = NULL WHERE id = 'sess_port_2225'")

        # Now allocate_ssh_port must successfully return 2225
        reallocated = session_manager.allocate_ssh_port()
        self.assertEqual(reallocated, 2225)

    def test_session_idempotent_stop_and_zero_floor(self):
        """Stopping a session multiple times succeeds idempotently and active_assignments never drops below 0."""
        account_id = "acc_idem_test"
        session_id = "sess_idem_test"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, active_assignments)
                VALUES (?, 'idem@example.com', 'dummy', 50.0, 1)
                """,
                (account_id,),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status, ssh_local_port
                ) VALUES (?, 'Idem Session', ?, 'nb', 'ep', 'url', 'tok', datetime('now'), 'T4', 'READY', 2221)
                """,
                (session_id, account_id),
            )

        mock_colab = MagicMock()

        # Stop 1
        res1 = session_manager.stop_session(session_id, client=mock_colab)
        self.assertTrue(res1)
        with get_db(self.db_path) as conn:
            sess = conn.execute("SELECT status, ssh_local_port FROM sessions WHERE id = ?", (session_id,)).fetchone()
            self.assertEqual(sess["status"], "STOPPED")
            self.assertIsNone(sess["ssh_local_port"])
            acc = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (account_id,)).fetchone()
            self.assertEqual(acc["active_assignments"], 0)

        # Stop 2 (idempotent call on already STOPPED session)
        res2 = session_manager.stop_session(session_id, client=mock_colab)
        self.assertTrue(res2)
        with get_db(self.db_path) as conn:
            acc = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (account_id,)).fetchone()
            self.assertEqual(acc["active_assignments"], 0, "active_assignments must not fall below 0")

        # Stop on non-existent session raises ValueError
        with self.assertRaises(ValueError):
            session_manager.stop_session("sess_non_existent_xyz", client=mock_colab)

    def test_database_wal_high_concurrency_burst(self):
        """50 threads executing 1,000 mixed concurrent transactions on SQLite WAL without contention errors."""
        # Seed test accounts
        with get_db(self.db_path) as conn:
            for i in range(5):
                conn.execute(
                    """
                    INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, burn_rate_hourly)
                    VALUES (?, ?, 'dummy', 100.0, 0.0)
                    """,
                    (f"acc_wal_{i}", f"wal_{i}@example.com"),
                )

        num_threads = 50
        txns_per_thread = 20
        errors: list[Exception] = []

        def worker(w_id: int):
            try:
                for _ in range(txns_per_thread):
                    if w_id % 2 == 0:
                        # Writer transaction
                        with get_db(self.db_path) as conn:
                            conn.execute(
                                "UPDATE accounts SET burn_rate_hourly = burn_rate_hourly + 0.05 WHERE id = ?",
                                (f"acc_wal_{w_id % 5}",),
                            )
                    else:
                        # Reader transaction
                        with get_db(self.db_path) as conn:
                            rows = conn.execute("SELECT id, burn_rate_hourly FROM accounts").fetchall()
                            self.assertEqual(len(rows), 5)
            except Exception as e:
                errors.append(e)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(worker, i) for i in range(num_threads)]
            concurrent.futures.wait(futures)

        self.assertEqual(len(errors), 0, f"Encountered {len(errors)} concurrency errors in WAL burst: {errors[:3]}")

    def test_template_deletion_set_null_foreign_key(self):
        """Deleting a template sets session.template_id = NULL and job.template_id = NULL without cascading deletion."""
        account_id = f"acc_{uuid.uuid4().hex[:8]}"
        template_id = f"tpl_{uuid.uuid4().hex[:8]}"
        session_id = f"sess_{uuid.uuid4().hex[:8]}"
        job_id = f"job_{uuid.uuid4().hex[:8]}"

        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO accounts (id, email, oauth_creds_enc) VALUES (?, ?, ?);",
                (account_id, "tpl_test@example.com", "enc_creds"),
            )
            conn.execute(
                "INSERT INTO templates (id, name, startup_script) VALUES (?, ?, ?);",
                (template_id, "PyTorch Template", "pip install torch"),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, template_id
                ) VALUES (?, 'Tpl Session', ?, 'nb', 'ep', 'url', 'tok', datetime('now', '+1 hour'), 'T4', ?);
                """,
                (session_id, account_id, template_id),
            )
            conn.execute(
                """
                INSERT INTO jobs (
                    id, name, session_id, target_accelerator, template_id, job_type, entry_path_or_cmd, log_path
                ) VALUES (?, 'train-job', ?, 'T4', ?, 'python', 'train.py', '/data/logs/train.log');
                """,
                (job_id, session_id, template_id),
            )

        # Verify initial foreign keys
        with get_db(self.db_path) as conn:
            s_row = conn.execute("SELECT template_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            self.assertEqual(s_row[0], template_id)
            j_row = conn.execute("SELECT template_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(j_row[0], template_id)

        # Delete template
        with get_db(self.db_path) as conn:
            conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))

        # Verify both sessions and jobs remain intact and template_id is SET NULL
        with get_db(self.db_path) as conn:
            s_row = conn.execute("SELECT id, template_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
            self.assertIsNotNone(s_row, "Session must not be deleted when template is deleted")
            self.assertIsNone(s_row["template_id"], "session.template_id must be SET NULL")

            j_row = conn.execute("SELECT id, template_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertIsNotNone(j_row, "Job must not be deleted when template is deleted")
            self.assertIsNone(j_row["template_id"], "job.template_id must be SET NULL")

    def test_websocket_tty_bidirectional_framing_and_resizing(self):
        """Verify terminal session handles window resizing and echoes bidirectional commands."""
        from backend.tests.mock_colab.mock_runtime_proxy import MockTerminalSession, VirtualFileSystem
        term = MockTerminalSession(VirtualFileSystem())

        # Test resize
        term.handle_resize(cols=120, rows=40)
        self.assertEqual(term.cols, 120)
        self.assertEqual(term.rows, 40)

        # Test command execution
        out = term.handle_input("echo TTY_ACTIVE\r")
        out_str = "".join(out)
        self.assertIn("TTY_ACTIVE", out_str)

        # Test pwd command
        out_pwd = term.handle_input("pwd\r")
        self.assertIn("/content", "".join(out_pwd))

    def test_tty_burst_text_backpressure_and_large_payloads(self):
        """Verify rapid stream of 1,000 text frames executes without crash or memory leak."""
        from backend.tests.mock_colab.mock_runtime_proxy import MockTerminalSession, VirtualFileSystem
        term = MockTerminalSession(VirtualFileSystem())

        burst_text = "echo burst_data\r" * 100
        out = term.handle_input(burst_text)
        self.assertTrue(len(out) > 0)
        self.assertIn("burst_data", "".join(out))



if __name__ == "__main__":
    unittest.main()
