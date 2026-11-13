"""Integration tests for FastAPI REST and WebSocket API endpoints."""

import base64
import json
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestApiEndpoints(unittest.TestCase):
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
        settings.ADMIN_PASSWORD = "test_admin_pass_123"

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

        # Set up an active Google account in DB and mock OAuth
        self.account_id = "acc_api_test"
        self.email = "api.tester@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="API Tester",
            sub="sub_api_123",
            code="code_api",
            access_token="ya29.mock_api_token",
            refresh_token="1//mock_refresh_api",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_api_token",
            "refresh_token": "1//mock_refresh_api",
            "expires_at": 9999999999,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, priority, status)
                VALUES (?, ?, 'API Tester', ?, 80.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.client = TestClient(app)

        # Obtain valid admin JWT token
        resp = self.client.post("/api/auth/login", json={"password": settings.ADMIN_PASSWORD})
        self.token = resp.json()["token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_healthz_endpoint(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["port"], settings.PORT)
        self.assertEqual(data["database"]["status"], "healthy")

    def test_auth_login_and_me(self):
        # 1. Correct password
        resp = self.client.post("/api/auth/login", json={"password": settings.ADMIN_PASSWORD})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("token", resp.json())

        # 2. Incorrect password
        fail_resp = self.client.post("/api/auth/login", json={"password": "wrong_password"})
        self.assertEqual(fail_resp.status_code, 401)

        # 3. GET /api/auth/me with valid Bearer token
        me_resp = self.client.get("/api/auth/me", headers=self.headers)
        self.assertEqual(me_resp.status_code, 200)
        self.assertTrue(me_resp.json()["authenticated"])
        self.assertEqual(me_resp.json()["role"], "admin")

        # 4. GET /api/auth/me without token
        unauth_resp = self.client.get("/api/auth/me")
        self.assertEqual(unauth_resp.status_code, 401)

    def test_accounts_endpoints_crud(self):
        # 1. List accounts
        list_resp = self.client.get("/api/accounts", headers=self.headers)
        self.assertEqual(list_resp.status_code, 200)
        accs = list_resp.json()
        self.assertEqual(len(accs), 1)
        self.assertEqual(accs[0]["email"], self.email)
        self.assertNotIn("oauth_creds_enc", accs[0])  # Redacted

        # 2. OAuth start
        start_resp = self.client.post("/api/accounts/oauth/start", headers=self.headers)
        self.assertEqual(start_resp.status_code, 200)
        self.assertTrue(start_resp.json()["auth_url"].startswith("http"))

        # 3. OAuth complete
        new_code = "4/0A_new_account_code"
        new_acc = MockOAuthAccount(
            email="new.user@example.com",
            name="New User",
            sub="sub_new_123",
            code=new_code,
            access_token="ya29.new_token",
            refresh_token="1//new_refresh",
        )
        self.hub.oauth.state.register_account(new_acc)
        complete_resp = self.client.post(
            "/api/accounts/oauth/complete",
            json={"code": new_code},
            headers=self.headers,
        )
        self.assertEqual(complete_resp.status_code, 200)
        self.assertEqual(complete_resp.json()["email"], "new.user@example.com")

        # 4. Sync account
        sync_resp = self.client.post(f"/api/accounts/{self.account_id}/sync", headers=self.headers)
        self.assertEqual(sync_resp.status_code, 200)
        self.assertGreater(sync_resp.json()["ccu_balance"], 0.0)

        # 5. Delete account
        del_resp = self.client.delete(f"/api/accounts/{self.account_id}", headers=self.headers)
        self.assertEqual(del_resp.status_code, 204)

    def test_sessions_and_files_and_terminal_endpoints(self):
        # 1. Create Session via POST /api/sessions
        sess_payload = {
            "name": "api-session-t4",
            "accelerator": "T4",
            "shape": "standard",
            "lifecycle_policy": "interactive",
            "account_id": self.account_id,
        }
        create_resp = self.client.post("/api/sessions", json=sess_payload, headers=self.headers)
        self.assertEqual(create_resp.status_code, 201)
        sess = create_resp.json()
        sess_id = sess["id"]
        self.assertEqual(sess["name"], "api-session-t4")
        self.assertEqual(sess["status"], "READY")
        self.assertIn("colab_web_url", sess)

        # 2. GET /api/sessions
        get_all_resp = self.client.get("/api/sessions", headers=self.headers)
        self.assertEqual(get_all_resp.status_code, 200)
        self.assertEqual(len(get_all_resp.json()), 1)

        # 3. GET /api/sessions/{id}
        get_one_resp = self.client.get(f"/api/sessions/{sess_id}", headers=self.headers)
        self.assertEqual(get_one_resp.status_code, 200)
        self.assertEqual(get_one_resp.json()["id"], sess_id)

        # 4. GET /api/sessions/{id}/colab-url
        url_resp = self.client.get(f"/api/sessions/{sess_id}/colab-url", headers=self.headers)
        self.assertEqual(url_resp.status_code, 200)
        self.assertTrue(url_resp.json()["url"].startswith("https://colab.research.google.com/notebooks/empty.ipynb"))

        # 5. Files REST: List directory
        list_files_resp = self.client.get(f"/api/sessions/{sess_id}/contents", headers=self.headers)
        self.assertEqual(list_files_resp.status_code, 200)
        self.assertEqual(list_files_resp.json().get("type"), "directory")

        # 6. Files REST: Upload file
        code_text = "x = 42\nprint(x)\n"
        b64 = base64.b64encode(code_text.encode("utf-8")).decode("ascii")
        upload_resp = self.client.put(
            f"/api/sessions/{sess_id}/contents/demo.py",
            json={"name": "demo.py", "path": "demo.py", "content": b64, "chunk": 1},
            headers=self.headers,
        )
        self.assertEqual(upload_resp.status_code, 200)

        # 7. Files REST: Download file
        download_resp = self.client.get(f"/api/sessions/{sess_id}/contents/demo.py?content=1", headers=self.headers)
        self.assertEqual(download_resp.status_code, 200)
        self.assertEqual(download_resp.json().get("format"), "base64")
        decoded = base64.b64decode(download_resp.json()["content"]).decode("utf-8")
        self.assertEqual(decoded, code_text)

        # 8. Files REST: Delete file
        del_file_resp = self.client.delete(f"/api/sessions/{sess_id}/contents/demo.py", headers=self.headers)
        self.assertEqual(del_file_resp.status_code, 204)

        # 9. Terminal WebSocket: Unauthorized attempt
        with self.assertRaises(Exception):
            with self.client.websocket_connect(f"/api/sessions/{sess_id}/terminal?token=bad_token") as ws:
                pass

        # 10. Terminal WebSocket: Successful connection and prompt receive
        with self.client.websocket_connect(f"/api/sessions/{sess_id}/terminal?token={self.token}") as ws:
            # Send terminal resize frame
            ws.send_text(json.dumps({"cols": 80, "rows": 24}))
            # Receive initial prompt or banner frame
            frame = ws.receive_text()
            data = json.loads(frame)
            self.assertIn("data", data)

        # 11. Stop session: DELETE /api/sessions/{id}
        stop_resp = self.client.delete(f"/api/sessions/{sess_id}", headers=self.headers)
        self.assertEqual(stop_resp.status_code, 204)

    def test_spa_catchall_serving(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        self.assertIn("Colab Management", resp.text)

    def test_spa_path_traversal_prevention(self):
        """Verify CWE-22 path traversal attempts do not leak arbitrary files outside static_dir."""
        traversal_payloads = [
            "/%2e%2e/%2e%2e/backend/app/config.py",
            "/../../backend/app/config.py",
            "/%2e%2e/%2e%2e/data/.master_key",
            "/../../../../../../../etc/passwd",
            "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
            "/%252e%252e%252fbackend/app/config.py",
            "///etc/passwd",
        ]
        for payload in traversal_payloads:
            resp = self.client.get(payload)
            self.assertNotIn("Application configuration", resp.text, f"Source leaked via {payload}")
            self.assertNotIn("root:", resp.text, f"/etc/passwd leaked via {payload}")
            self.assertNotIn("MASTER_KEY", resp.text, f"Key leaked via {payload}")
            if resp.status_code == 200:
                self.assertIn("text/html", resp.headers.get("content-type", ""))

    def test_spa_api_routes_not_swallowed(self):
        """Verify API 404s are returned as JSON rather than falling back to SPA index.html."""
        resp = self.client.get("/api/nonexistent_endpoint")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.headers.get("content-type"), "application/json")
        self.assertEqual(resp.json().get("detail"), "API route not found")


if __name__ == "__main__":
    unittest.main()
