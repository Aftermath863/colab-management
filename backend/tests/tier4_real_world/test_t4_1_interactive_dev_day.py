"""T4.1: Interactive Dev Day Workflow.

Simulates a full realistic developer journey:
1. Admin Login (JWT issuance).
2. Remote Google OAuth onboarding (consent URL generation, code exchange, AES-256-GCM vault).
3. Interactive T4 session provisioning (control plane handshake, port 2220 allocation, deep link).
4. Web TTY terminal inspection (bidirectional JSON frames, nvidia-smi execution, terminal resize).
5. Remote Monaco code editing (uploading, editing, and downloading workspace/train.py).
6. Dual SSH multiplexing (simultaneous client channels over a single upstream /colab/ssh connection, bypassing HTTP 429).
7. Auto-idle shutdown (watchdog evaluates inactivity > 900s, teardown unassigns Colab instance and frees port 2220).
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

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.main import app
from backend.app.services import session_manager
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount
from backend.tests.mock_colab.mock_runtime_proxy import RFC6455WebSocket


class MockSSHMultiplexerGateway:
    """In-daemon multiplexer gateway simulating AsyncSSH local port pool proxying.

    Maintains exactly ONE persistent upstream connection to /colab/ssh and multiplexes
    multiple incoming client sessions over it, defeating Colab's HTTP 429 single-connection restriction.
    """

    def __init__(self, proxy_url: str, proxy_token: str, pubkey: str):
        self.proxy_url = proxy_url
        self.proxy_token = proxy_token
        self.pubkey = pubkey
        self.upstream_sock: Optional[socket.socket] = None
        self.active_channels: Dict[str, Any] = {}

    def connect_upstream(self) -> int:
        parsed = urlparse(self.proxy_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.settimeout(5.0)

        # RFC 6455 Handshake with X-Colab-Ssh-Pubkey header
        handshake = (
            f"GET /colab/ssh?colab-runtime-proxy-token={self.proxy_token} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"X-Colab-Ssh-Pubkey: {self.pubkey}\r\n"
            f"\r\n"
        )
        s.sendall(handshake.encode("utf-8"))

        resp = s.recv(4096).decode("utf-8", errors="ignore")
        if "101 Switching Protocols" not in resp:
            s.close()
            if "429" in resp:
                return 429
            return 400

        self.upstream_sock = s
        return 101

    def send_client_payload(self, channel_id: str, payload: bytes) -> bytes:
        """Multiplex client channel payload over single upstream socket and receive response."""
        if not self.upstream_sock:
            raise ConnectionError("Upstream socket not connected")

        # Encode masked binary frame to upstream
        frame = bytearray([0x82, 0x80 | len(payload)])
        mask = b"\x33\x44\x55\x66"
        masked_payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.upstream_sock.sendall(bytes(frame) + mask + masked_payload)

        # Read back unmasked binary response frame
        raw = self.upstream_sock.recv(4096)
        if not raw:
            return b""
        decoded = RFC6455WebSocket.decode_frame(raw)
        if decoded:
            return decoded[1]
        return raw

    def close(self) -> None:
        if self.upstream_sock:
            try:
                self.upstream_sock.close()
            except Exception:
                pass
            self.upstream_sock = None


class TestT41InteractiveDevDayWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        cls.orig_control_url = settings.CONTROL_PLANE_URL
        cls.orig_token_url = settings.OAUTH_TOKEN_URL
        cls.orig_userinfo_url = settings.OAUTH_USERINFO_URL
        cls.orig_admin_pass = settings.ADMIN_PASSWORD

        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]
        settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
        settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri
        settings.ADMIN_PASSWORD = "super_secure_dev_day_password"

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        settings.OAUTH_TOKEN_URL = cls.orig_token_url
        settings.OAUTH_USERINFO_URL = cls.orig_userinfo_url
        settings.ADMIN_PASSWORD = cls.orig_admin_pass
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)
        self.client = TestClient(app)

    def tearDown(self):
        import asyncio
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(ssh_multiplexer_service.shutdown())
            else:
                loop.run_until_complete(ssh_multiplexer_service.shutdown())
        except Exception:
            try:
                asyncio.run(ssh_multiplexer_service.shutdown())
            except Exception:
                pass
        self.tmpdir.cleanup()

    def test_t4_1_complete_interactive_developer_day_journey(self):
        # ====================================================================
        # Phase 1: Admin Authentication
        # ====================================================================
        login_resp = self.client.post("/api/auth/login", json={"password": settings.ADMIN_PASSWORD})
        self.assertEqual(login_resp.status_code, 200)
        auth_data = login_resp.json()
        jwt_token = auth_data["token"]
        headers = {"Authorization": f"Bearer {jwt_token}"}

        # Verify auth status
        status_resp = self.client.get("/api/auth/me", headers=headers)
        self.assertEqual(status_resp.status_code, 200)
        self.assertTrue(status_resp.json()["authenticated"])
        self.assertEqual(status_resp.json()["role"], "admin")

        # ====================================================================
        # Phase 2: Remote OAuth Authorization
        # ====================================================================
        oauth_start_resp = self.client.post("/api/accounts/oauth/start", headers=headers)
        self.assertEqual(oauth_start_resp.status_code, 200)
        auth_url = oauth_start_resp.json()["auth_url"]
        self.assertIn("client_id=", auth_url)

        # Register account in mock OAuth server
        code = "4/dev_day_oauth_code_xyz"
        dev_email = "devday.engineer@gmail.com"
        mock_acc = MockOAuthAccount(
            email=dev_email,
            name="DevDay Engineer",
            sub="sub_devday_999",
            code=code,
            access_token="ya29.devday_access_token",
            refresh_token="1//devday_refresh_token",
        )
        self.hub.oauth.state.register_account(mock_acc)

        # Complete OAuth flow
        oauth_complete_resp = self.client.post("/api/accounts/oauth/complete", json={"code": code}, headers=headers)
        self.assertEqual(oauth_complete_resp.status_code, 200)
        account_data = oauth_complete_resp.json()
        account_id = account_data["id"]
        self.assertEqual(account_data["email"], dev_email)
        self.assertEqual(account_data["status"], "ACTIVE")

        # ====================================================================
        # Phase 3: Provision Interactive T4 Session
        # ====================================================================
        sess_create_req = {
            "name": "devday-interactive-session",
            "accelerator": "T4",
            "shape": "standard",
            "lifecycle_policy": "interactive",
            "idle_timeout_sec": 900,
            "account_id": account_id,
        }
        create_resp = self.client.post("/api/sessions", json=sess_create_req, headers=headers)
        self.assertEqual(create_resp.status_code, 201)
        sess = create_resp.json()
        session_id = sess["id"]
        endpoint = sess["endpoint"]
        proxy_url = sess["proxy_url"]

        self.assertEqual(sess["status"], "READY")
        self.assertEqual(sess["accelerator"], "T4")
        self.assertEqual(sess["ssh_local_port"], 2220)

        # Invariant: Colab web deep link generated
        colab_url = sess["colab_web_url"]
        self.assertIn("colab.research.google.com/notebooks/empty.ipynb", colab_url)
        self.assertIn(f"#datalabBackendUrl=https://colab.research.google.com/tun/m/{endpoint}", colab_url)

        # ====================================================================
        # Phase 4: Web TTY Terminal Inspection
        # ====================================================================
        with self.client.websocket_connect(f"/api/sessions/{session_id}/terminal?token={jwt_token}") as ws:
            # 1. Terminal resize frame
            ws.send_text(json.dumps({"cols": 100, "rows": 30}))

            # 2. Command execution frame: nvidia-smi
            ws.send_text(json.dumps({"data": "nvidia-smi\n"}))

            # Receive output frames
            output_received = ""
            for _ in range(50):
                frame_text = ws.receive_text()
                msg = json.loads(frame_text)
                if "data" in msg:
                    output_received += msg["data"]
                    if "NVIDIA-SMI" in output_received:
                        break

            self.assertIn("NVIDIA-SMI", output_received)

        # ====================================================================
        # Phase 5: Monaco Code Editing
        # ====================================================================
        train_script = (
            "import torch\n"
            "print('Dev Day: Initializing model training on', torch.cuda.get_device_name(0))\n"
        )
        b64_script = base64.b64encode(train_script.encode("utf-8")).decode("ascii")

        # PUT code file to remote VFS
        upload_resp = self.client.put(
            f"/api/sessions/{session_id}/contents/workspace/train.py",
            json={"content": b64_script, "format": "base64", "type": "file"},
            headers=headers,
        )
        self.assertEqual(upload_resp.status_code, 200)

        # Read back via GET
        read_resp = self.client.get(
            f"/api/sessions/{session_id}/contents/workspace/train.py?content=1",
            headers=headers,
        )
        self.assertEqual(read_resp.status_code, 200)
        self.assertEqual(read_resp.json()["format"], "base64")
        decoded_script = base64.b64decode(read_resp.json()["content"]).decode("utf-8")
        self.assertEqual(decoded_script, train_script)

        # ====================================================================
        # Phase 6: Dual SSH Connection Multiplexing (Defeating HTTP 429)
        # ====================================================================
        internal = session_manager.get_session_internal(session_id)
        proxy_token = internal["proxy_token"]

        # 1. Verify that trying two DIRECT upstream connections causes HTTP 429:
        pubkey1 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleEd25519PublicKeyClient1"
        pubkey2 = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleEd25519PublicKeyClient2"

        gw1 = MockSSHMultiplexerGateway(proxy_url, proxy_token, pubkey1)
        res1 = gw1.connect_upstream()
        self.assertEqual(res1, 101)  # First upstream connection succeeds

        # Direct 2nd connection to same endpoint must receive HTTP 429
        gw_direct_2 = MockSSHMultiplexerGateway(proxy_url, proxy_token, pubkey2)
        res2 = gw_direct_2.connect_upstream()
        self.assertEqual(res2, 429)  # Rejected by Colab single-connection lock!

        # 2. Verify that dual client channels over the SINGLE multiplexer gateway BOTH succeed:
        # Client Channel 1 sends data
        reply_ch1 = gw1.send_client_payload("channel_vscode_1", b"echo 'channel 1 active'\n")
        self.assertTrue(len(reply_ch1) > 0)

        # Client Channel 2 sends data over the same multiplexed upstream connection
        reply_ch2 = gw1.send_client_payload("channel_vscode_2", b"echo 'channel 2 active'\n")
        self.assertTrue(len(reply_ch2) > 0)

        # Clean up gateway
        gw1.close()

        # ====================================================================
        # Phase 7: Auto-Idle Teardown
        # ====================================================================
        # Advance simulated idle time past 900 seconds
        t_now = time.time() + 905.0

        # Lifecycle watchdog checks idle sessions
        from backend.tests.tier3_pairwise.test_t3_2_ssh_traffic_idle_reset import InteractiveIdleWatchdog
        watchdog = InteractiveIdleWatchdog()
        watchdog.register_session(session_id, initial_timestamp=time.time())

        # Evaluate idle watchdog with elapsed time = 905s (> 900s timeout)
        terminated = watchdog.evaluate_idle_sessions(current_timestamp=t_now)
        self.assertIn(session_id, terminated)

        # Invariants: Session in DB is STOPPED, port 2220 is freed
        sess_final = session_manager.get_session(session_id)
        self.assertEqual(sess_final["status"], "STOPPED")
        self.assertIsNone(sess_final["ssh_local_port"])
        self.assertIsNotNone(sess_final["stopped_at"])

        # Port 2220 is available again for next session
        self.assertEqual(session_manager.allocate_ssh_port(), 2220)


if __name__ == "__main__":
    unittest.main()
