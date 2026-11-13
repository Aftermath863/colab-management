"""Pytest configuration and shared fixtures for Colab Management test suites."""

from __future__ import annotations

import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
import httpx

# 1. Bootstrap sys.path to guarantee `backend` package resolves from any invocation path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import settings
from backend.app.core.security import create_access_token
from backend.app.main import app
from backend.tests.mock_colab.mock_hub import MockControlHub


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers to avoid PytestUnknownMarkWarning."""
    config.addinivalue_line("markers", "tier1: Tier 1 Feature Coverage tests")
    config.addinivalue_line("markers", "tier2: Tier 2 Boundary & Corner Case tests")
    config.addinivalue_line("markers", "tier3: Tier 3 Cross-Feature Pairwise tests")
    config.addinivalue_line("markers", "tier4: Tier 4 Real-World Workflow tests")
    config.addinivalue_line("markers", "mock: Zero-dependency Mock Colab Suite unit tests")


@pytest.fixture(scope="session")
def global_mock_hub():
    """Session-scoped MockControlHub fixture for pytest tests that use standalone functions."""
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    os.environ["TEST_MODE"] = "mock"
    hub = MockControlHub()
    hub.start()

    orig_token = settings.OAUTH_TOKEN_URL
    orig_userinfo = settings.OAUTH_USERINFO_URL
    orig_control = settings.CONTROL_PLANE_URL

    settings.OAUTH_TOKEN_URL = hub.oauth.token_uri
    settings.OAUTH_USERINFO_URL = hub.oauth.userinfo_uri
    settings.CONTROL_PLANE_URL = hub.urls["control_plane"]

    yield hub

    settings.OAUTH_TOKEN_URL = orig_token
    settings.OAUTH_USERINFO_URL = orig_userinfo
    settings.CONTROL_PLANE_URL = orig_control
    hub.stop()


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Provides admin JWT authorization headers."""
    token = create_access_token({"sub": "admin", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_client() -> TestClient:
    """Provides an in-process FastAPI TestClient."""
    return TestClient(app)


@pytest.fixture
async def async_client(auth_headers: dict[str, str]) -> httpx.AsyncClient:
    """Provides an in-process httpx.AsyncClient."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers=auth_headers,
    ) as client:
        yield client
