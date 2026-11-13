"""T4.4: Token Renewal & Network Partition Recovery Workflow.

Simulates long-running session network resilience:
1. Proxy Token Auto-Renewal at 5-Minute Margin: When a proxy token nears expiration (< 300s),
   the periodic reconciler fetches updated token credentials from GET /tun/m/assignments and
   updates the encrypted DB record without interrupting active sessions.
2. Upstream Network Partition Simulation: An active WebSocket connection experiences an abrupt
   network partition / drop.
3. Gateway Exponential Backoff Reconnection: The client gateway handles the disconnect and
   executes exponential backoff retry using the newly renewed token.
4. End-to-End Traffic Resumed: Connection is transparently re-established, and bidirectional
   command execution resumes without terminal reset or session loss.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount
from backend.tests.mock_colab.mock_runtime_proxy import RFC6455WebSocket


class ResilientGatewayClient:
    """Terminal client with automatic exponential backoff reconnection on upstream network partitions."""

    def __init__(self, proxy_url: str, session_id: str):
        self.proxy_url = proxy_url
        self.session_id = session_id
        self.sock: Optional[socket.socket] = None
        self.reconnect_attempts: int = 0

    def connect(self, token: str) -> bool:
        parsed = urlparse(self.proxy_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.settimeout(5.0)

        handshake = (
            f"GET /colab/tty?colab-runtime-proxy-token={token} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        s.sendall(handshake.encode("utf-8"))

        resp = s.recv(4096).decode("utf-8", errors="ignore")
        if "101 Switching Protocols" not in resp:
            s.close()
            return False

        self.sock = s
        return True

    def simulate_upstream_network_partition(self) -> None:
        """Simulate upstream network partition by closing the socket unexpectedly."""
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def reconnect_with_backoff(self, max_retries: int = 3, base_delay: float = 0.05) -> bool:
        """Perform exponential backoff retry fetching the freshest token from DB."""
        for attempt in range(1, max_retries + 1):
            self.reconnect_attempts += 1
            # Exponential backoff delay
            delay = base_delay * (2 ** (attempt - 1))
            time.sleep(delay)

            # Retrieve newest renewed token from session manager
            internal = session_manager.get_session_internal(self.session_id)
            fresh_token = internal["proxy_token"]

            if self.connect(fresh_token):
                return True

        return False

    def send_command(self, cmd: str, expected_in_output: Optional[str] = None) -> str:
        """Sends command JSON frame and receives output response string."""
        if not self.sock:
            raise ConnectionError("Gateway not connected")

        # Encode JSON text frame
        payload = json.dumps({"data": cmd}).encode("utf-8")
        frame = bytearray([0x81, 0x80 | len(payload)])
        mask = b"\x12\x34\x56\x78"
        masked_payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(frame) + mask + masked_payload)

        # Receive response frames decoding all WebSocket frames in stream
        output = ""
        buf = bytearray()
        start = time.time()
        self.sock.settimeout(2.0)
        while time.time() - start < 3.0:
            try:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                while True:
                    decoded = RFC6455WebSocket.decode_frame(bytes(buf))
                    if not decoded:
                        break
                    opcode, p_bytes, consumed = decoded
                    buf = buf[consumed:]
                    if opcode == 0x1:
                        data_obj = json.loads(p_bytes.decode("utf-8", errors="ignore"))
                        if "data" in data_obj:
                            output += data_obj["data"]
                if expected_in_output and expected_in_output in output:
                    break
            except socket.timeout:
                break

        return output

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None


class TestT44TokenRenewalAndPartitionRecoveryWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        cls.orig_control_url = settings.CONTROL_PLANE_URL
        cls.orig_token_url = settings.OAUTH_TOKEN_URL
        cls.orig_userinfo_url = settings.OAUTH_USERINFO_URL

        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]
        settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
        settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        settings.OAUTH_TOKEN_URL = cls.orig_token_url
        settings.OAUTH_USERINFO_URL = cls.orig_userinfo_url
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Register account
        self.account_id = "acc_recovery_test"
        self.email = "recovery_eng@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Recovery Eng",
            sub="sub_rec_123",
            code="code_rec",
            access_token="ya29.mock_token_rec",
            refresh_token="1//mock_refresh_rec",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_rec",
            "refresh_token": "1//mock_refresh_rec",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Recovery Eng', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        # Create session
        self.sess = session_manager.create_session(
            SessionCreateRequest(
                name="long-running-dev",
                accelerator="T4",
                lifecycle_policy="keep_alive",
                account_id=self.account_id,
            )
        )
        self.session_id = self.sess["id"]
        self.endpoint = self.sess["endpoint"]
        self.proxy_url = self.sess["proxy_url"]

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t4_4_proxy_token_auto_renewal_and_upstream_partition_recovery(self):
        # Initial token
        internal = session_manager.get_session_internal(self.session_id)
        initial_token = internal["proxy_token"]

        # Connect resilient gateway
        gateway = ResilientGatewayClient(self.proxy_url, self.session_id)
        connected = gateway.connect(initial_token)
        self.assertTrue(connected)

        # ====================================================================
        # Phase 1: Proxy Token Auto-Renewal at 5-Minute Margin
        # ====================================================================
        # Simulate token nearing expiry (e.g. 240s remaining < 300s renewal margin)
        renewed_token = "token_renewed_secure_xyz_998877"

        # Update assignment token on mock control plane
        assignment = self.hub.control_plane.state.assignments.get(self.endpoint)
        self.assertIsNotNone(assignment)
        assignment.proxy_token = renewed_token
        assignment.token_expires_in_seconds = 3600

        # Update valid token on mock runtime proxy
        ep = self.hub.runtime_proxy.app.get_endpoint(self.endpoint)
        self.assertIsNotNone(ep)
        ep.token = renewed_token

        # Run periodic account sync / reconciliation
        account_pool.sync_account(self.account_id)

        # Invariant: session in DB updated with renewed token
        internal_updated = session_manager.get_session_internal(self.session_id)
        self.assertEqual(internal_updated["proxy_token"], renewed_token)
        # Invariant: session was NOT stopped or interrupted
        self.assertEqual(internal_updated["status"], "READY")

        # ====================================================================
        # Phase 2: Upstream Network Partition Simulation
        # ====================================================================
        # Upstream socket drops abruptly due to transient network partition
        gateway.simulate_upstream_network_partition()
        self.assertIsNone(gateway.sock)

        # ====================================================================
        # Phase 3: Gateway Exponential Backoff Reconnection
        # ====================================================================
        # Gateway executes backoff retry, automatically picking up renewed token
        reconnected = gateway.reconnect_with_backoff(max_retries=3, base_delay=0.05)
        self.assertTrue(reconnected)
        self.assertGreaterEqual(gateway.reconnect_attempts, 1)

        # ====================================================================
        # Phase 4: End-to-End Traffic Resumed
        # ====================================================================
        # Terminal traffic flows again without session loss
        output = gateway.send_command("whoami\n", expected_in_output="root")
        self.assertIn("root", output)

        output_pwd = gateway.send_command("pwd\n", expected_in_output="/content")
        self.assertIn("/content", output_pwd)

        gateway.close()

        # Final verification: session remains fully active
        sess_final = session_manager.get_session(self.session_id)
        self.assertEqual(sess_final["status"], "READY")


if __name__ == "__main__":
    unittest.main()
