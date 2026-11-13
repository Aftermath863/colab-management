"""Stateless Multi-Tenant Colab Adapter Service.

Implements Google Colab Control Plane and Runtime Data Plane protocols
strictly adhering to google-colab-cli v0.7.5:
- XSSI prefix )]}'\\n stripping
- Two-step assign (GET probe -> POST commit with X-Goog-Colab-Token)
- Two-step unassign (GET token -> POST token)
- UUID web-safe base64 transformation (44 chars, '-' -> '_', padded with '.')
- Accelerator shape rules (omitting shape=hm for L4, V5E1, V6E1)
- Transient error retry (502/503/504/ReadTimeout) with reconciliation probe
- Error mapping (HTTP 400 -> QuotaExceededError, HTTP 412 -> TooManyAssignmentsError)
- Runtime plane file operations (list, download, chunked upload, delete)
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import uuid
from typing import Any, Dict, List, Optional, Union

import requests
from backend.app.config import settings

logger = logging.getLogger(__name__)

# Protocol Constants
XSSI_PREFIX = ")]}'\n"
DEFAULT_COLAB_BASE_URL = "https://colab.research.google.com"
ACCEPT_JSON_HEADER = "Accept"
ACCEPT_JSON_VALUE = "application/json"
COLAB_CLIENT_AGENT_HEADER = "X-Colab-Client-Agent"
COLAB_CLIENT_AGENT_VALUE = "colab-cli"
COLAB_XSRF_TOKEN_HEADER = "X-Goog-Colab-Token"
COLAB_PROXY_TOKEN_PARAM = "colab-runtime-proxy-token"

HIGH_MEM_ONLY_ACCELERATORS = frozenset({"L4", "V5E1", "V6E1"})


# --- Custom Domain Exceptions ---

class ColabClientError(Exception):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        response_body: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class QuotaExceededError(ColabClientError):
    """Raised on HTTP 400 when an account lacks entitlement or quota for an accelerator."""
    pass


class TooManyAssignmentsError(ColabClientError):
    """Raised on HTTP 412 when the account's concurrent instance quota is exhausted."""
    pass


# --- Helper Functions ---

def strip_xssi_prefix(text: str) -> str:
    """Strip Google JSON vulnerability protection prefix )]}'\\n if present."""
    if text.startswith(XSSI_PREFIX):
        return text[len(XSSI_PREFIX):]
    return text


def uuid_to_web_safe_base64(uuid_val: Union[uuid.UUID, str]) -> str:
    """Convert standard UUID to Colab web-safe 44-character notebook hash."""
    uuid_str = str(uuid_val)
    transformed = uuid_str.replace("-", "_")
    padding = "." * (44 - len(transformed))
    return transformed + padding


def generate_colab_notebook_url(email: str, endpoint: str) -> str:
    """Generate official Google Colab direct deep link.

    Invariant:
    - dbu query parameter must be fully URL-encoded (%2Ftun%2Fm%2F...).
    - #datalabBackendUrl hash fragment must NOT be URL-encoded.
    """
    dbu_encoded = urllib.parse.quote(f"/tun/m/{endpoint}", safe="")
    email_encoded = urllib.parse.quote(email)
    raw_backend = f"https://colab.research.google.com/tun/m/{endpoint}"
    return (
        f"https://colab.research.google.com/notebooks/empty.ipynb?"
        f"authuser={email_encoded}&dbu={dbu_encoded}#datalabBackendUrl={raw_backend}"
    )


class ColabClient:
    """Stateless multi-tenant client for Google Colab control & runtime planes."""

    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or settings.CONTROL_PLANE_URL).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, access_token: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            ACCEPT_JSON_HEADER: ACCEPT_JSON_VALUE,
            COLAB_CLIENT_AGENT_HEADER: COLAB_CLIENT_AGENT_VALUE,
        }
        if extra:
            headers.update(extra)
        return headers

    def _parse_response(self, resp: requests.Response) -> Any:
        cleaned = strip_xssi_prefix(resp.text)
        if not cleaned.strip():
            return {}
        try:
            return json.loads(cleaned)
        except Exception as e:
            raise ColabClientError(
                f"Failed to parse JSON response: {e}",
                status_code=resp.status_code,
                response_body=resp.text,
            )

    # --- Control Plane: CCU & Assignments ---

    def get_ccu_info(self, access_token: str) -> Dict[str, Any]:
        """Query account CCU balance and burn rate."""
        url = f"{self.base_url}/tun/m/ccu-info"
        resp = self.session.get(
            url,
            params={"authuser": "0"},
            headers=self._headers(access_token),
            timeout=self.timeout,
        )
        if not resp.ok:
            raise ColabClientError(
                f"Failed to fetch CCU info: HTTP {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text,
            )
        data = self._parse_response(resp)
        return {
            "current_balance": float(data.get("currentBalance", 0.0)),
            "consumption_rate_hourly": float(data.get("consumptionRateHourly", 0.0)),
            "assignments_count": int(data.get("assignmentsCount", 0)),
        }

    def list_assignments(self, access_token: str) -> List[Dict[str, Any]]:
        """List active instances running under the account."""
        url = f"{self.base_url}/tun/m/assignments"
        resp = self.session.get(
            url,
            params={"authuser": "0"},
            headers=self._headers(access_token),
            timeout=self.timeout,
        )
        if not resp.ok:
            raise ColabClientError(
                f"Failed to list assignments: HTTP {resp.status_code}",
                status_code=resp.status_code,
                response_body=resp.text,
            )
        data = self._parse_response(resp)
        return data.get("assignments", [])

    # --- Control Plane: Two-Step Provisioning (Assign) ---

    def assign(
        self,
        access_token: str,
        notebook_hash: str,
        accelerator: str = "T4",
        shape: str = "standard",
        max_retries: int = 3,
        backoff_sec: float = 1.0,
    ) -> Dict[str, Any]:
        """Provision a Colab instance via two-phase assign handshake.

        Step 1: GET /tun/m/assign probe.
        Step 2: POST /tun/m/assign with X-Goog-Colab-Token.
        Transient errors (502/503/504/timeout) trigger retry with reconciliation probe.
        """
        # Determine variant and normalize wire accelerator per official colab-cli protocol
        acc_upper = (accelerator or "").strip().upper()
        if acc_upper in ("CPU", "NONE", ""):
            variant = "DEFAULT"
            wire_accelerator = "NONE"
        elif acc_upper.startswith("TPU") or acc_upper.startswith("V5E") or acc_upper.startswith("V6E"):
            variant = "TPU"
            wire_accelerator = acc_upper
        else:
            variant = "GPU"
            wire_accelerator = acc_upper

        params: Dict[str, Any] = {
            "nbh": notebook_hash,
            "variant": variant,
            "accelerator": wire_accelerator,
            "authuser": "0",
        }

        # Apply accelerator shape rule: omit shape=hm on wire for L4, V5E1, V6E1
        if shape.lower() == "hm" and wire_accelerator not in HIGH_MEM_ONLY_ACCELERATORS:
            params["shape"] = "hm"

        assign_url = f"{self.base_url}/tun/m/assign"

        for attempt in range(max_retries + 1):
            # Phase 1: Probe GET
            try:
                get_resp = self.session.get(
                    assign_url,
                    params=params,
                    headers=self._headers(access_token),
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < max_retries:
                    time.sleep(backoff_sec * (2**attempt))
                    continue
                raise ColabClientError(f"Network error probing assignment: {e}")

            if get_resp.status_code == 400:
                raise QuotaExceededError("Quota exceeded or unsupported accelerator", status_code=400, response_body=get_resp.text)
            if get_resp.status_code == 412:
                raise TooManyAssignmentsError("Too many active assignments for account", status_code=412, response_body=get_resp.text)
            if get_resp.status_code in (502, 503, 504):
                if attempt < max_retries:
                    time.sleep(backoff_sec * (2**attempt))
                    continue
                raise ColabClientError(f"Colab gateway error {get_resp.status_code}", status_code=get_resp.status_code, response_body=get_resp.text)

            if not get_resp.ok:
                raise ColabClientError(f"Assign probe failed: HTTP {get_resp.status_code}", status_code=get_resp.status_code, response_body=get_resp.text)

            get_data = self._parse_response(get_resp)

            # Check if assignment already exists (reconciliation / idempotent return)
            if "endpoint" in get_data and "runtimeProxyInfo" in get_data:
                return get_data

            xsrf_token = get_data.get("token")
            if not xsrf_token:
                raise ColabClientError("Missing token in GET assign probe response", response_body=get_resp.text)

            # Phase 2: Commit POST
            post_headers = self._headers(access_token, extra={COLAB_XSRF_TOKEN_HEADER: xsrf_token})
            try:
                post_resp = self.session.post(
                    assign_url,
                    params=params,
                    headers=post_headers,
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt < max_retries:
                    time.sleep(backoff_sec * (2**attempt))
                    # Next loop iteration will probe via GET to reconcile
                    continue
                raise ColabClientError(f"Network error committing assignment: {e}")

            if post_resp.status_code == 400:
                raise QuotaExceededError("Quota exceeded committing assignment", status_code=400, response_body=post_resp.text)
            if post_resp.status_code == 412:
                raise TooManyAssignmentsError("Too many assignments committing assignment", status_code=412, response_body=post_resp.text)
            if post_resp.status_code in (502, 503, 504):
                if attempt < max_retries:
                    time.sleep(backoff_sec * (2**attempt))
                    continue
                raise ColabClientError(f"Colab gateway error on commit {post_resp.status_code}", status_code=post_resp.status_code, response_body=post_resp.text)

            if not post_resp.ok:
                raise ColabClientError(f"Assign commit failed: HTTP {post_resp.status_code}", status_code=post_resp.status_code, response_body=post_resp.text)

            return self._parse_response(post_resp)

        raise ColabClientError("Assignment failed after retries exhausted")

    # --- Control Plane: Two-Step Teardown (Unassign) ---

    def unassign(self, access_token: str, endpoint: str) -> bool:
        """Teardown a Colab instance via two-step unassign handshake.

        Step 1: GET /tun/m/unassign/{endpoint} -> receives {"token": "..."}
        Step 2: POST /tun/m/unassign/{endpoint} with X-Goog-Colab-Token.
        """
        unassign_url = f"{self.base_url}/tun/m/unassign/{endpoint}"
        params = {"authuser": "0"}

        # Step 1: GET token
        get_resp = self.session.get(
            unassign_url,
            params=params,
            headers=self._headers(access_token),
            timeout=self.timeout,
        )
        if not get_resp.ok:
            if get_resp.status_code == 404:
                # Already unassigned
                return True
            raise ColabClientError(
                f"Failed to get unassign token: HTTP {get_resp.status_code}",
                status_code=get_resp.status_code,
                response_body=get_resp.text,
            )

        token_data = self._parse_response(get_resp)
        xsrf_token = token_data.get("token")
        if not xsrf_token:
            raise ColabClientError("Unassign probe did not return a valid token", response_body=get_resp.text)

        # Step 2: POST unassign
        post_headers = self._headers(access_token, extra={COLAB_XSRF_TOKEN_HEADER: xsrf_token})
        post_resp = self.session.post(
            unassign_url,
            params=params,
            headers=post_headers,
            timeout=self.timeout,
        )
        if not post_resp.ok and post_resp.status_code != 404:
            raise ColabClientError(
                f"Failed to unassign instance: HTTP {post_resp.status_code}",
                status_code=post_resp.status_code,
                response_body=post_resp.text,
            )
        return True

    # --- Runtime Data Plane: Contents REST API ---

    def _proxy_headers(self, proxy_token: str) -> Dict[str, str]:
        return {
            ACCEPT_JSON_HEADER: ACCEPT_JSON_VALUE,
            COLAB_CLIENT_AGENT_HEADER: COLAB_CLIENT_AGENT_VALUE,
        }

    def list_contents(self, proxy_url: str, proxy_token: str, path: str = "") -> Dict[str, Any]:
        """Fetch directory listing from Colab runtime Virtual Filesystem."""
        clean_path = urllib.parse.quote(path.strip("/"), safe="/")
        url = f"{proxy_url.rstrip('/')}/api/contents/{clean_path}"
        params = {"authuser": "0", COLAB_PROXY_TOKEN_PARAM: proxy_token}
        resp = self.session.get(url, params=params, headers=self._proxy_headers(proxy_token), timeout=self.timeout)
        if not resp.ok:
            raise ColabClientError(f"Failed to list directory contents: HTTP {resp.status_code}", status_code=resp.status_code, response_body=resp.text)
        return resp.json()

    def download_file(self, proxy_url: str, proxy_token: str, path: str) -> Dict[str, Any]:
        """Download file content from Colab runtime (returns dict with base64 content)."""
        clean_path = urllib.parse.quote(path.strip("/"), safe="/")
        url = f"{proxy_url.rstrip('/')}/api/contents/{clean_path}"
        params = {"authuser": "0", "content": "1", COLAB_PROXY_TOKEN_PARAM: proxy_token}
        resp = self.session.get(url, params=params, headers=self._proxy_headers(proxy_token), timeout=self.timeout)
        if not resp.ok:
            raise ColabClientError(f"Failed to download file: HTTP {resp.status_code}", status_code=resp.status_code, response_body=resp.text)
        return resp.json()

    def upload_file(
        self,
        proxy_url: str,
        proxy_token: str,
        path: str,
        content_base64: str,
        file_name: Optional[str] = None,
        chunk: int = 1,
    ) -> Dict[str, Any]:
        """Upload file chunk (base64 encoded) to Colab runtime."""
        clean_path = urllib.parse.quote(path.strip("/"), safe="/")
        url = f"{proxy_url.rstrip('/')}/api/contents/{clean_path}"
        params = {"authuser": "0", COLAB_PROXY_TOKEN_PARAM: proxy_token}
        payload = {
            "name": file_name or os.path.basename(path),
            "path": path.strip("/"),
            "type": "file",
            "format": "base64",
            "content": content_base64,
            "chunk": chunk,
        }
        resp = self.session.put(
            url,
            params=params,
            json=payload,
            headers=self._proxy_headers(proxy_token),
            timeout=self.timeout,
        )
        if not resp.ok:
            raise ColabClientError(f"Failed to upload file: HTTP {resp.status_code}", status_code=resp.status_code, response_body=resp.text)
        return resp.json()

    def delete_file(self, proxy_url: str, proxy_token: str, path: str) -> bool:
        """Delete file or directory from Colab runtime."""
        clean_path = urllib.parse.quote(path.strip("/"), safe="/")
        url = f"{proxy_url.rstrip('/')}/api/contents/{clean_path}"
        params = {"authuser": "0", COLAB_PROXY_TOKEN_PARAM: proxy_token}
        resp = self.session.delete(url, params=params, headers=self._proxy_headers(proxy_token), timeout=self.timeout)
        if not resp.ok and resp.status_code != 404:
            raise ColabClientError(f"Failed to delete path: HTTP {resp.status_code}", status_code=resp.status_code, response_body=resp.text)
        return True
