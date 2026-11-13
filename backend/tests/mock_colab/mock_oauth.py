"""Zero-Dependency Mock Google OAuth Server.

Implements a local HTTP server emulating Google OAuth2 endpoints:
- POST /token (grant_type=authorization_code, grant_type=refresh_token)
- GET /userinfo (and /oauth2/v3/userinfo)
- POST /revoke (and /o/oauth2/revoke)
- GET /health

Features:
- Pure Python standard library (http.server, urllib, threading, json, dataclasses).
- Dynamic ephemeral port allocation (port=0) to prevent port collisions.
- Pre-seeded test accounts and dynamic account synthesis.
- Programmatic token expiration and revocation controls.
- Fault injection engine (custom status codes, error payloads, network delays).
- Dual synchronous (`with`) and asynchronous (`async with`) context managers.
"""

from __future__ import annotations

import asyncio
import dataclasses
import http.server
import json
import logging
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Standard Google Colab Scopes
DEFAULT_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/colaboratory",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclasses.dataclass
class MockOAuthAccount:
    """Represents a simulated Google Account in the mock OAuth database."""

    email: str
    name: str
    sub: str
    code: str
    access_token: str
    refresh_token: str
    expires_in: int = 3600
    issued_at: float = dataclasses.field(default_factory=time.time)
    scopes: List[str] = dataclasses.field(default_factory=lambda: list(DEFAULT_SCOPES))
    picture: str = "https://lh3.googleusercontent.com/a/default-user-avatar"
    is_revoked: bool = False
    is_expired: bool = False

    @property
    def is_valid_access(self) -> bool:
        if self.is_revoked or self.is_expired:
            return False
        return (time.time() - self.issued_at) < self.expires_in

    @property
    def is_valid_refresh(self) -> bool:
        return not self.is_revoked


class MockOAuthState:
    """Thread-safe in-memory state store and fault injection engine."""

    def __init__(self, allow_dynamic_codes: bool = True):
        self.lock = threading.RLock()
        self.allow_dynamic_codes = allow_dynamic_codes
        self.accounts_by_code: Dict[str, MockOAuthAccount] = {}
        self.accounts_by_access: Dict[str, MockOAuthAccount] = {}
        self.accounts_by_refresh: Dict[str, MockOAuthAccount] = {}
        self.fault_injections: Dict[str, Dict[str, Any]] = {}
        self.request_history: List[Dict[str, Any]] = []
        self._seed_default_accounts()

    def _seed_default_accounts(self) -> None:
        """Seed standard test accounts matching test scenarios."""
        # Account 1: Standard Active User
        acc1 = MockOAuthAccount(
            email="colab-dev@gmail.com",
            name="Colab Test Dev",
            sub="100000000000000000001",
            code="mock_code_account_1",
            access_token="ya29.mock_access_token_account_1",
            refresh_token="1//mock_refresh_token_account_1",
        )
        # Account 2: Secondary Pool Account
        acc2 = MockOAuthAccount(
            email="colab-prod@gmail.com",
            name="Colab Test Prod",
            sub="100000000000000000002",
            code="mock_code_account_2",
            access_token="ya29.mock_access_token_account_2",
            refresh_token="1//mock_refresh_token_account_2",
        )
        # Account 3: Pre-revoked account for error handling tests
        acc3 = MockOAuthAccount(
            email="revoked-user@gmail.com",
            name="Revoked Account",
            sub="100000000000000000003",
            code="mock_code_revoked",
            access_token="ya29.mock_access_token_revoked",
            refresh_token="1//mock_refresh_token_revoked",
            is_revoked=True,
        )
        for acc in (acc1, acc2, acc3):
            self.register_account(acc)

    def register_account(self, account: MockOAuthAccount) -> None:
        with self.lock:
            self.accounts_by_code[account.code] = account
            self.accounts_by_access[account.access_token] = account
            self.accounts_by_refresh[account.refresh_token] = account

    def record_request(self, method: str, path: str, params: Dict[str, Any]) -> None:
        with self.lock:
            self.request_history.append(
                {
                    "method": method,
                    "path": path,
                    "params": params,
                    "ts": time.time(),
                }
            )

    def inject_fault(self, endpoint: str, status_code: int, response_body: Dict[str, Any]) -> None:
        with self.lock:
            self.fault_injections[endpoint] = {
                "status": status_code,
                "body": response_body,
            }

    def clear_faults(self) -> None:
        with self.lock:
            self.fault_injections.clear()

    def reset(self) -> None:
        with self.lock:
            self.accounts_by_code.clear()
            self.accounts_by_access.clear()
            self.accounts_by_refresh.clear()
            self.fault_injections.clear()
            self.request_history.clear()
            self._seed_default_accounts()

    def exchange_code(self, code: str, client_id: str = "", redirect_uri: str = "") -> Tuple[int, Dict[str, Any]]:
        with self.lock:
            if "/token" in self.fault_injections:
                f = self.fault_injections["/token"]
                return f["status"], f["body"]

            # Error injection trigger via magic code string
            if code == "error:invalid_grant":
                return 400, {"error": "invalid_grant", "error_description": "Malformed auth code."}
            if code == "error:500":
                return 500, {"error": "internal_server_error", "error_description": "OAuth service unavailable."}

            account = self.accounts_by_code.get(code)
            if not account and self.allow_dynamic_codes and code.strip():
                # Synthesize dynamic account for unseeded tests
                safe_suffix = abs(hash(code)) % 10000
                account = MockOAuthAccount(
                    email=f"user-{safe_suffix}@gmail.com",
                    name=f"Dynamic User {safe_suffix}",
                    sub=f"1000000000000000{safe_suffix:04d}",
                    code=code,
                    access_token=f"ya29.mock_access_token_dynamic_{safe_suffix}",
                    refresh_token=f"1//mock_refresh_token_dynamic_{safe_suffix}",
                )
                self.accounts_by_code[account.code] = account
                self.accounts_by_access[account.access_token] = account
                self.accounts_by_refresh[account.refresh_token] = account

            if not account or account.is_revoked:
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "Malformed auth code.",
                }

            # Update issue time
            account.issued_at = time.time()
            account.is_expired = False

            return 200, {
                "access_token": account.access_token,
                "refresh_token": account.refresh_token,
                "expires_in": account.expires_in,
                "token_type": "Bearer",
                "scope": " ".join(account.scopes),
                "id_token": f"mock_id_token_{account.sub}",
            }

    def refresh_access_token(self, refresh_token: str) -> Tuple[int, Dict[str, Any]]:
        with self.lock:
            if "/token" in self.fault_injections:
                f = self.fault_injections["/token"]
                return f["status"], f["body"]

            account = self.accounts_by_refresh.get(refresh_token)
            if not account or not account.is_valid_refresh:
                return 400, {
                    "error": "invalid_grant",
                    "error_description": "Token has been expired or revoked.",
                }

            # Generate new access token
            new_access_token = f"ya29.mock_refreshed_{account.sub}_{int(time.time())}"
            # Clean up old mapping
            self.accounts_by_access.pop(account.access_token, None)
            account.access_token = new_access_token
            account.issued_at = time.time()
            account.is_expired = False
            self.accounts_by_access[new_access_token] = account

            return 200, {
                "access_token": new_access_token,
                "expires_in": account.expires_in,
                "token_type": "Bearer",
                "scope": " ".join(account.scopes),
            }

    def get_userinfo(self, bearer_token: str) -> Tuple[int, Dict[str, Any]]:
        with self.lock:
            if "/userinfo" in self.fault_injections:
                f = self.fault_injections["/userinfo"]
                return f["status"], f["body"]

            account = self.accounts_by_access.get(bearer_token)
            if not account or not account.is_valid_access:
                return 401, {
                    "error": {
                        "code": 401,
                        "message": "Invalid Credentials",
                        "status": "UNAUTHENTICATED",
                    }
                }

            return 200, {
                "sub": account.sub,
                "email": account.email,
                "email_verified": True,
                "name": account.name,
                "picture": account.picture,
                "locale": "en",
            }

    def revoke_token(self, token: str) -> Tuple[int, Dict[str, Any]]:
        with self.lock:
            account = self.accounts_by_access.get(token) or self.accounts_by_refresh.get(token)
            if account:
                account.is_revoked = True
                return 200, {}
            # If unknown token is revoked, Google still returns 200 OK
            return 200, {}

    def expire_access_token(self, access_token: str) -> None:
        with self.lock:
            account = self.accounts_by_access.get(access_token)
            if account:
                account.is_expired = True
                account.issued_at = 0.0

    # Programmatic Handlers (for direct in-process or Hub dispatch)
    def handle_token(self, body_bytes: bytes, headers: Dict[str, str]) -> Tuple[int, Dict[str, str], bytes]:
        raw = body_bytes.decode("utf-8") if body_bytes else ""
        ctype = headers.get("content-type", headers.get("Content-Type", ""))
        if "application/json" in ctype:
            try:
                params = json.loads(raw)
            except Exception:
                params = {}
        else:
            params = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

        grant_type = params.get("grant_type", "")
        if grant_type == "authorization_code":
            code = params.get("code", "")
            client_id = params.get("client_id", "")
            redirect_uri = params.get("redirect_uri", "")
            status, resp = self.exchange_code(code, client_id, redirect_uri)
        elif grant_type == "refresh_token":
            refresh_token = params.get("refresh_token", "")
            status, resp = self.refresh_access_token(refresh_token)
        else:
            status, resp = 400, {
                "error": "unsupported_grant_type",
                "error_description": f"Grant type '{grant_type}' is not supported.",
            }

        resp_bytes = json.dumps(resp).encode("utf-8")
        hdrs = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(resp_bytes)),
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        }
        return status, hdrs, resp_bytes

    def handle_userinfo(self, headers: Dict[str, str]) -> Tuple[int, Dict[str, str], bytes]:
        auth_header = headers.get("authorization", headers.get("Authorization", ""))
        if not auth_header.startswith("Bearer "):
            resp = {
                "error": {
                    "code": 401,
                    "message": "Request is missing required authentication credential.",
                    "status": "UNAUTHENTICATED",
                }
            }
            resp_bytes = json.dumps(resp).encode("utf-8")
            return 401, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(resp_bytes))}, resp_bytes

        token = auth_header.removeprefix("Bearer ").strip()
        status, resp = self.get_userinfo(token)
        resp_bytes = json.dumps(resp).encode("utf-8")
        hdrs = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(resp_bytes)),
        }
        return status, hdrs, resp_bytes

    def handle_revoke(self, body_bytes: bytes, headers: Dict[str, str], query: str = "") -> Tuple[int, Dict[str, str], bytes]:
        raw = body_bytes.decode("utf-8") if body_bytes else ""
        params = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
        token = params.get("token") or urllib.parse.parse_qs(query).get("token", [""])[0]
        status, resp = self.revoke_token(token)
        resp_bytes = json.dumps(resp).encode("utf-8")
        return status, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(resp_bytes))}, resp_bytes


class MockOAuthRequestHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler routing Google OAuth endpoints."""

    state: MockOAuthState

    def _read_body_params(self) -> Dict[str, str]:
        clen = int(self.headers.get("Content-Length", 0))
        if clen == 0:
            return {}
        raw = self.rfile.read(clen).decode("utf-8")
        ctype = self.headers.get("Content-Type", "")
        if "application/json" in ctype:
            try:
                return json.loads(raw)
            except Exception:
                return {}
        # Default form urlencoded
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

    def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        params = self._read_body_params()
        self.state.record_request("POST", path, params)

        if path in ("/token", "/o/oauth2/token"):
            grant_type = params.get("grant_type", "")
            if grant_type == "authorization_code":
                code = params.get("code", "")
                client_id = params.get("client_id", "")
                redirect_uri = params.get("redirect_uri", "")
                status, resp = self.state.exchange_code(code, client_id, redirect_uri)
                self._send_json(status, resp)
            elif grant_type == "refresh_token":
                refresh_token = params.get("refresh_token", "")
                status, resp = self.state.refresh_access_token(refresh_token)
                self._send_json(status, resp)
            else:
                self._send_json(
                    400,
                    {
                        "error": "unsupported_grant_type",
                        "error_description": f"Grant type '{grant_type}' is not supported.",
                    },
                )
        elif path in ("/revoke", "/o/oauth2/revoke"):
            token = params.get("token") or urllib.parse.parse_qs(parsed_url.query).get("token", [""])[0]
            status, resp = self.state.revoke_token(token)
            self._send_json(status, resp)
        else:
            self._send_json(404, {"error": "not_found", "path": path})

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query = dict(urllib.parse.parse_qsl(parsed_url.query))
        self.state.record_request("GET", path, query)

        if path in ("/userinfo", "/oauth2/v3/userinfo"):
            auth_header = self.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                self._send_json(
                    401,
                    {
                        "error": {
                            "code": 401,
                            "message": "Request is missing required authentication credential.",
                            "status": "UNAUTHENTICATED",
                        }
                    },
                )
                return
            token = auth_header.removeprefix("Bearer ").strip()
            status, resp = self.state.get_userinfo(token)
            self._send_json(status, resp)
        elif path == "/health":
            self._send_json(200, {"status": "healthy", "service": "mock_google_oauth"})
        else:
            self._send_json(404, {"error": "not_found", "path": path})

    def log_message(self, format: str, *args: Any) -> None:
        """Silence standard request logging during test runs."""
        pass


class MockGoogleOAuthServer:
    """Standalone Mock Google OAuth Server with zero third-party dependencies."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, allow_dynamic_codes: bool = True):
        self.host = host
        self.port = port
        self.state = MockOAuthState(allow_dynamic_codes=allow_dynamic_codes)
        self.httpd: Optional[http.server.ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> MockGoogleOAuthServer:
        """Starts the server daemon on an ephemeral port."""
        if self.httpd is not None:
            return self

        state_ref = self.state

        class ConfiguredHandler(MockOAuthRequestHandler):
            state = state_ref

        self.httpd = http.server.ThreadingHTTPServer((self.host, self.port), ConfiguredHandler)
        self.port = self.httpd.server_port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        logger.info(f"Mock Google OAuth Server running at {self.base_url}")
        return self

    def stop(self) -> None:
        """Stops the server daemon and releases socket resources."""
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None

    def reset(self) -> None:
        """Resets the state and pre-seeded accounts."""
        self.state.reset()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def token_uri(self) -> str:
        return f"{self.base_url}/token"

    @property
    def userinfo_uri(self) -> str:
        return f"{self.base_url}/userinfo"

    @property
    def revoke_uri(self) -> str:
        return f"{self.base_url}/revoke"

    def get_client_config(self) -> Dict[str, Any]:
        """Returns Google Cloud SDK client configuration targeting this mock server."""
        return {
            "installed": {
                "client_id": "mock_client_id_764086051850.apps.googleusercontent.com",
                "client_secret": "mock_client_secret_d-FL95Q19q7MQmFpd7hHD0Ty",
                "auth_uri": f"{self.base_url}/auth",
                "token_uri": self.token_uri,
                "project_id": "colab-cli-mock",
                "redirect_uris": ["https://sdk.cloud.google.com/applicationdefaultauthcode.html"],
            }
        }

    # Context Manager Support (Synchronous)
    def __enter__(self) -> MockGoogleOAuthServer:
        return self.start()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    # Context Manager Support (Asynchronous)
    async def __aenter__(self) -> MockGoogleOAuthServer:
        return self.start()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
