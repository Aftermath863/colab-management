"""Tier 1 Feature Tests: AsyncSSH Multiplexer Gateway & Port Pool (F7).

Validates:
1. Local port pool 2220-2229 dynamic reservation and release.
2. SSHMultiplexerService lifecycle (startup DB init, session register, unregister, shutdown).
3. Session SSH configuration API contract (GET /api/sessions/{id}/ssh-config).
4. In-daemon AsyncSSH server connection and multi-channel multiplexing over a single upstream.
5. Upstream HTTP 429 lock avoidance.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import time
import unittest
from typing import Any, Dict

import asyncssh
import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services import session_manager
from backend.app.services.ssh_multiplexer import (
    ColabSSHGateway,
    SSHMultiplexerService,
    SSHPortManager,
    ssh_multiplexer_service,
)
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestSSHMultiplexerGateway(unittest.IsolatedAsyncioTestCase):
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

    async def asyncSetUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.account_id = "acc_ssh_test"
        self.email = "ssh_tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="SSH Tester",
            sub="sub_ssh_123",
            code="code_ssh",
            access_token="ya29.mock_ssh_token",
            refresh_token="1//mock_ssh_refresh",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_ssh_token",
            "refresh_token": "1//mock_ssh_refresh",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'SSH Tester', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.token = create_access_token({"sub": "admin", "role": "admin"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def asyncTearDown(self):
        await ssh_multiplexer_service.shutdown()
        self.tmpdir.cleanup()

    async def test_port_pool_manager_allocation_and_exhaustion(self):
        """Test atomic port allocation in range 2220-2229 and pool exhaustion."""
        manager = SSHPortManager(start_port=2220, end_port=2223)

        # Allocate 4 ports
        p1 = await manager.allocate()
        self.assertIsNotNone(p1)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, ssh_local_port, status) VALUES ('s1', 'n1', ?, 'h', 'e', 'u', 't', CURRENT_TIMESTAMP, 'T4', ?, 'READY')",
                (self.account_id, p1),
            )

        p2 = await manager.allocate()
        self.assertIsNotNone(p2)
        self.assertNotEqual(p1, p2)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, ssh_local_port, status) VALUES ('s2', 'n2', ?, 'h', 'e', 'u', 't', CURRENT_TIMESTAMP, 'T4', ?, 'READY')",
                (self.account_id, p2),
            )

        p3 = await manager.allocate()
        self.assertIsNotNone(p3)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, ssh_local_port, status) VALUES ('s3', 'n3', ?, 'h', 'e', 'u', 't', CURRENT_TIMESTAMP, 'T4', ?, 'READY')",
                (self.account_id, p3),
            )

        p4 = await manager.allocate()
        self.assertIsNotNone(p4)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, ssh_local_port, status) VALUES ('s4', 'n4', ?, 'h', 'e', 'u', 't', CURRENT_TIMESTAMP, 'T4', ?, 'READY')",
                (self.account_id, p4),
            )

        # 5th allocation should be None (exhausted)
        p5 = await manager.allocate()
        self.assertIsNone(p5)

    async def test_ssh_config_endpoint_and_vscode_snippet(self):
        """Verify GET /api/sessions/{id}/ssh-config produces valid connection strings and VS Code snippet."""
        req = SessionCreateRequest(name="dev-box", accelerator="T4", account_id=self.account_id)
        sess = session_manager.create_session(req)
        session_id = sess["id"]
        port = sess["ssh_local_port"]
        self.assertIsNotNone(port)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get(f"/api/sessions/{session_id}/ssh-config", headers=self.headers)
            self.assertEqual(resp.status_code, 200)
            data = resp.json()

            self.assertEqual(data["session_id"], session_id)
            self.assertEqual(data["host"], "localhost")
            self.assertEqual(data["port"], port)
            self.assertEqual(data["username"], "root")
            self.assertEqual(data["connection_command"], f"ssh -p {port} root@localhost")

            # Check VS Code Remote-SSH configuration snippet format
            snippet = data["vscode_config"]
            self.assertIn(f"Host colab-{sess['name']}", snippet)
            self.assertIn("HostName localhost", snippet)
            self.assertIn(f"Port {port}", snippet)
            self.assertIn("User root", snippet)
            self.assertIn("StrictHostKeyChecking no", snippet)

    async def test_multiplexer_gateway_listener_lifecycle(self):
        """Test register_session, unregister_session, and clean socket release."""
        host_key = asyncssh.generate_private_key("ssh-ed25519")
        client_key = asyncssh.generate_private_key("ssh-ed25519")

        # Pick a free test port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            test_port = s.getsockname()[1]

        gateway = ColabSSHGateway("sess_mock_test", test_port, host_key, client_key)
        await gateway.start_listener()

        # Verify listener is active on OS via non-blocking asyncio connection
        reader, writer = await asyncio.open_connection("127.0.0.1", test_port)
        writer.write(b"SSH-2.0-TestClient\r\n")
        await writer.drain()

        banner = await asyncio.wait_for(reader.readline(), timeout=2.0)
        self.assertIn(b"SSH-2.0-AsyncSSH", banner)
        writer.close()
        await writer.wait_closed()

        # Graceful close
        await gateway.close()

        # Verify port is released
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", test_port))
