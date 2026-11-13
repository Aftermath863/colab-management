"""Unified Base Test Cases and Fixture Harness for Colab Management Test Suite.

Compatible with both `pytest backend/tests` and `python -m unittest discover backend/tests`.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from fastapi.testclient import TestClient
import httpx

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import AccountSummary, SessionCreateRequest, SessionSummary
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services import session_manager
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class E2ETestHarnessMixin:
    """Shared fixture orchestration, database isolation, and mock injection mixin."""

    # Configurable flags per test class
    enable_mock_hub: bool = True
    enable_db_isolation: bool = True
    seed_default_account: bool = False
    test_admin_password: str = "test_admin_pass_123"
    test_master_key: str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

    # Class-level state
    hub: Optional[MockControlHub] = None
    _orig_settings: Dict[str, Any] = {}

    @classmethod
    def setup_class_harness(cls) -> None:
        """Class-level setup: starts MockControlHub and redirects settings."""
        cls._orig_settings = {
            "OAUTH_TOKEN_URL": settings.OAUTH_TOKEN_URL,
            "OAUTH_USERINFO_URL": settings.OAUTH_USERINFO_URL,
            "CONTROL_PLANE_URL": settings.CONTROL_PLANE_URL,
            "ADMIN_PASSWORD": settings.ADMIN_PASSWORD,
            "MASTER_KEY": settings.MASTER_KEY,
            "DATABASE_PATH": settings.DATABASE_PATH,
        }

        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        os.environ["TEST_MODE"] = "mock"
        os.environ["MASTER_KEY"] = cls.test_master_key
        settings.ADMIN_PASSWORD = cls.test_admin_password

        if cls.enable_mock_hub:
            cls.hub = MockControlHub()
            cls.hub.start()
            settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
            settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri
            settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]

    @classmethod
    def teardown_class_harness(cls) -> None:
        """Class-level teardown: restores original settings and stops MockControlHub."""
        for key, val in cls._orig_settings.items():
            setattr(settings, key, val)

        if cls.hub and cls.hub.is_running:
            cls.hub.stop()
            cls.hub = None

    def setup_instance_harness(self) -> None:
        """Per-test setup: resets mock state, creates ephemeral SQLite DB, and instantiates clients."""
        if self.hub:
            self.hub.reset()

        if self.enable_db_isolation:
            self.temp_dir = tempfile.mkdtemp(prefix="colab_test_")
            self.db_path = os.path.join(self.temp_dir, "test_manager.db")
            self._prev_db_path = settings.DATABASE_PATH
            settings.DATABASE_PATH = self.db_path
            init_db(self.db_path)

        self.client = TestClient(app)
        self.default_account_id: Optional[str] = None

        if self.seed_default_account:
            self.default_account_id = self.seed_account(
                account_id="acc_default_e2e",
                email="default.dev@example.com",
                balance=100.0,
                burn_rate=1.5,
            )

    def teardown_instance_harness(self) -> None:
        """Per-test teardown: restores database path, removes temp dir, and clears fault injections."""
        if self.hub:
            self.hub.clear_quota_exhaustion()
            if hasattr(self.hub, "control_plane_server") and self.hub.control_plane_server:
                self.hub.control_plane_server.state.concurrency_limit = None


        if self.enable_db_isolation:
            settings.DATABASE_PATH = self._prev_db_path
            if hasattr(self, "temp_dir") and os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------------
    # Helper & Seeding Methods
    # ------------------------------------------------------------------------

    def get_auth_headers(self, role: str = "admin", sub: str = "admin") -> Dict[str, str]:
        """Generate Bearer authorization headers for administrative requests."""
        token = create_access_token({"sub": sub, "role": role})
        return {"Authorization": f"Bearer {token}"}

    def get_async_client(self) -> httpx.AsyncClient:
        """Return an in-process httpx.AsyncClient bound to the FastAPI application."""
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=self.get_auth_headers(),
        )

    def seed_account(
        self,
        account_id: str = "acc_test",
        email: str = "dev@example.com",
        name: str = "Test Developer",
        balance: float = 100.0,
        burn_rate: float = 1.5,
        status: str = "ACTIVE",
        priority: int = 1,
        hard_stop_ccu: float = 2.0,
    ) -> str:
        """Seed an account across MockOAuth, MockControlPlane, and local SQLite DB."""
        if self.hub:
            mock_acc = MockOAuthAccount(
                email=email,
                name=name,
                sub=f"sub_{account_id}",
                code=f"code_{account_id}",
                access_token=f"ya29.mock_{account_id}",
                refresh_token=f"1//mock_refresh_{account_id}",
            )
            self.hub.oauth.state.register_account(mock_acc)
            self.hub.set_account_ccu(email=email, balance=balance, burn_rate=burn_rate)

        creds = {
            "access_token": f"ya29.mock_{account_id}",
            "refresh_token": f"1//mock_refresh_{account_id}",
            "expires_at": int(time.time()) + 3600,
        }
        enc_creds = encrypt_secret(json.dumps(creds))

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO accounts (
                    id, email, display_name, oauth_creds_enc, ccu_balance,
                    burn_rate_hourly, active_assignments, hard_stop_ccu, priority, status
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (account_id, email, name, enc_creds, balance, burn_rate, hard_stop_ccu, priority, status),
            )
        return account_id

    def seed_session(
        self,
        session_id: str = "sess_test",
        account_id: Optional[str] = None,
        name: Optional[str] = None,
        accelerator: str = "T4",
        shape: str = "standard",
        lifecycle_policy: str = "interactive",
        status: str = "READY",
        ssh_local_port: Optional[int] = None,
    ) -> str:
        """Seed a session directly into the test database."""
        acc_id = account_id or self.default_account_id or self.seed_account()
        sess_name = name or f"Session_{session_id}"
        proxy_token = f"tok_{session_id}"
        endpoint = f"ep-{session_id}"
        proxy_url = f"http://127.0.0.1:5000/tun/m/{endpoint}"

        if self.hub:
            self.hub.runtime_proxy_server.app.register_endpoint(endpoint, proxy_token)

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, ssh_local_port, status
                ) VALUES (?, ?, ?, 'hash', ?, ?, ?, datetime('now', '+2 hours'), ?, ?, ?, ?, ?)
                """,
                (
                    session_id, sess_name, acc_id, endpoint, proxy_url,
                    encrypt_secret(proxy_token), accelerator, shape,
                    lifecycle_policy, ssh_local_port, status,
                ),
            )
        return session_id


    def query_db(self, query: str, params: Tuple = ()) -> List[sqlite3.Row]:
        """Execute a query against the test database returning rows."""
        with get_db(self.db_path) as conn:
            return conn.execute(query, params).fetchall()


class BaseE2ETestCase(unittest.IsolatedAsyncioTestCase, E2ETestHarnessMixin):
    """Primary asynchronous base test class for Tier 1, 2, 3, and 4 test suites.

    Supports `async def test_*` and `def test_*` methods, full MockControlHub lifecycle,
    and ephemeral SQLite database isolation per test.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_class_harness()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.teardown_class_harness()

    async def asyncSetUp(self) -> None:
        self.setup_instance_harness()

    async def asyncTearDown(self) -> None:
        self.teardown_instance_harness()


class BaseE2ESyncTestCase(unittest.TestCase, E2ETestHarnessMixin):
    """Synchronous base test class for pure unit tests that do not require an asyncio event loop."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_class_harness()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.teardown_class_harness()

    def setUp(self) -> None:
        self.setup_instance_harness()

    def tearDown(self) -> None:
        self.teardown_instance_harness()
