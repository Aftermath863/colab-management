"""Zero-dependency Mock Colab Control Plane Server.

Faithfully implements the Google Colab Control Plane REST API specifications
from google-colab-cli v0.7.5:
- Prefixes all JSON responses with the XSSI guard )]}'\\n
- Enforces two-step XSRF token handshakes (GET token -> POST X-Goog-Colab-Token)
- Validates High-RAM shape rules (omits shape=hm for L4, V5E1, V6E1)
- Supports deterministic fault injection (HTTP 400, 412, 502/503/504, delays)
- Zero external dependencies: runnable with Python standard library
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

XSSI_PREFIX = ")]}'\n"
DEFAULT_PROXY_BASE = "http://127.0.0.1:9091"
HIGH_MEM_ACCELERATORS = frozenset({"L4", "V5E1", "V6E1"})


@dataclass
class MockAssignment:
    """Represents an active Google Colab VM assignment."""

    endpoint: str
    accelerator: str = "T4"
    variant: int = 1  # 0=DEFAULT, 1=GPU, 2=TPU
    machine_shape: int = 0  # 0=STANDARD, 1=HIGH_RAM
    nbh: str = ""
    proxy_token: str = field(default_factory=lambda: f"proxy-token-{uuid.uuid4().hex[:16]}")
    proxy_url: str = ""
    token_expires_in_seconds: int = 7200
    account_email: str = "colab-dev@gmail.com"
    created_at: float = field(default_factory=time.time)

    def to_listed_dict(self) -> Dict[str, Any]:
        return {
            "accelerator": self.accelerator,
            "endpoint": self.endpoint,
            "variant": self.variant,
            "machineShape": self.machine_shape,
            "runtimeProxyInfo": {
                "token": self.proxy_token,
                "tokenExpiresInSeconds": self.token_expires_in_seconds,
                "url": self.proxy_url or f"{DEFAULT_PROXY_BASE}/tun/m/{self.endpoint}",
            },
        }

    def to_post_dict(self) -> Dict[str, Any]:
        return {
            "accelerator": self.accelerator,
            "endpoint": self.endpoint,
            "variant": self.variant,
            "runtimeProxyInfo": {
                "token": self.proxy_token,
                "tokenExpiresInSeconds": self.token_expires_in_seconds,
                "url": self.proxy_url or f"{DEFAULT_PROXY_BASE}/tun/m/{self.endpoint}",
            },
        }

    def to_assigned_dict(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "runtimeProxyInfo": {
                "token": self.proxy_token,
                "tokenExpiresInSeconds": self.token_expires_in_seconds,
                "url": self.proxy_url or f"{DEFAULT_PROXY_BASE}/tun/m/{self.endpoint}",
            },
        }


class MockControlPlaneState:
    """Thread-safe state management and fault injection controller."""

    def __init__(self, hub: Optional[Any] = None):
        self._lock = threading.RLock()
        self.hub = hub
        self.reset()

    def reset(self) -> None:
        """Reset state to default clean values."""
        with self._lock:
            # CCU metrics
            self.ccu_balance: float = 100.0
            self.consumption_rate_hourly: float = 1.5
            self.assignments_count_override: Optional[int] = None
            self.account_ccu_map: Dict[str, Tuple[float, float]] = {
                "colab-dev@gmail.com": (100.0, 1.5),
            }

            # Fleet state
            self.assignments: Dict[str, MockAssignment] = {}
            self.nbh_to_endpoint: Dict[str, str] = {}

            # XSRF tokens
            self.assign_tokens: Dict[str, Dict[str, Any]] = {}
            self.unassign_tokens: Dict[str, str] = {}
            self.propagate_tokens: Dict[str, str] = {}

            # Configuration
            self.runtime_proxy_base_url: str = DEFAULT_PROXY_BASE
            self.xssi_prefix_enabled: bool = True
            self.reject_missing_xsrf_token: bool = True
            self.strict_shape_validation: bool = True

            # Fault injection
            self.concurrency_limit: Optional[int] = None
            self.quota_unavailable_accelerators: Set[str] = set()
            self.transient_errors_remaining: int = 0
            self.transient_status_code: int = 502
            self.reconcile_on_transient_error: bool = False
            self.network_delay_sec: float = 0.0
            self.simulate_auth_expired: bool = False
            self.fail_next_assign: Optional[Dict[str, Any]] = None

            # Audit request log
            self.request_log: List[Dict[str, Any]] = []

    # ---------------- State Mutation Hooks ----------------

    def set_ccu(self, balance: float, burn_rate: float, count: Optional[int] = None) -> None:
        with self._lock:
            self.ccu_balance = float(balance)
            self.consumption_rate_hourly = float(burn_rate)
            self.assignments_count_override = count

    def set_account_ccu(self, email: str, balance: float, burn_rate: float) -> None:
        with self._lock:
            self.account_ccu_map[email] = (float(balance), float(burn_rate))
            if email == "colab-dev@gmail.com":
                self.ccu_balance = float(balance)
                self.consumption_rate_hourly = float(burn_rate)

    def trigger_concurrency_limit(self, max_allowed: int = 1) -> None:
        with self._lock:
            self.concurrency_limit = max_allowed

    def trigger_quota_exhaustion(self, accelerators: Union[str, List[str], Set[str]]) -> None:
        with self._lock:
            if isinstance(accelerators, str):
                self.quota_unavailable_accelerators = {accelerators.upper()}
            else:
                self.quota_unavailable_accelerators = {a.upper() for a in accelerators}

    def clear_quota_exhaustion(self) -> None:
        with self._lock:
            self.quota_unavailable_accelerators.clear()

    def trigger_transient_error(self, count: int = 1, status_code: int = 502, reconcile: bool = False) -> None:
        """Trigger transient gateway timeout; if reconcile=True, provisions assignment despite error."""
        with self._lock:
            self.transient_errors_remaining = count
            self.transient_status_code = status_code
            self.reconcile_on_transient_error = reconcile

    def set_network_delay(self, seconds: float) -> None:
        with self._lock:
            self.network_delay_sec = max(0.0, float(seconds))

    def inject_orphan_assignment(
        self,
        endpoint: str,
        accelerator: str = "T4",
        variant: int = 1,
        machine_shape: int = 0,
        nbh: Optional[str] = None,
        account_email: str = "colab-dev@gmail.com",
    ) -> MockAssignment:
        """Inject an unmanaged/orphan assignment to test orphan discovery."""
        with self._lock:
            effective_nbh = nbh or f"orphan_nbh_{uuid.uuid4().hex[:16]}"
            assignment = MockAssignment(
                endpoint=endpoint,
                accelerator=accelerator,
                variant=variant,
                machine_shape=machine_shape,
                nbh=effective_nbh,
                proxy_url=f"{self.runtime_proxy_base_url}/tun/m/{endpoint}",
                account_email=account_email,
            )
            self.assignments[endpoint] = assignment
            self.nbh_to_endpoint[effective_nbh] = endpoint
            if self.hub and hasattr(self.hub, "on_assignment_created"):
                self.hub.on_assignment_created(endpoint, assignment.proxy_token)
            return assignment

    def inject_unmanaged_assignment(self, email: str, endpoint: str = "ep-orphan-999", accelerator: str = "T4") -> str:
        self.inject_orphan_assignment(endpoint=endpoint, accelerator=accelerator, account_email=email)
        return endpoint

    def expire_proxy_token(self, endpoint: str) -> None:
        """Force the proxy token of an assignment to expire."""
        with self._lock:
            if endpoint in self.assignments:
                self.assignments[endpoint].token_expires_in_seconds = 0

    def dispatch(
        self, method: str, path: str, query_params: Dict[str, List[str]], body_bytes: bytes, headers: Dict[str, str]
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Programmatic request dispatcher."""
        if self.network_delay_sec > 0:
            time.sleep(self.network_delay_sec)

        xsrf_token = headers.get("x-goog-colab-token", headers.get("X-Goog-Colab-Token", ""))

        with self._lock:
            self.request_log.append({"method": method, "path": path, "query": query_params, "xsrf": xsrf_token})

            if self.simulate_auth_expired:
                return self._format_response(401, {"error": "Authentication expired"})

            if method == "GET":
                if path == "/tun/m/ccu-info":
                    count = (
                        self.assignments_count_override
                        if self.assignments_count_override is not None
                        else len(self.assignments)
                    )
                    return self._format_response(
                        200,
                        {
                            "currentBalance": self.ccu_balance,
                            "consumptionRateHourly": self.consumption_rate_hourly,
                            "assignmentsCount": count,
                        },
                    )

                if path == "/tun/m/assignments":
                    listed = [a.to_listed_dict() for a in self.assignments.values()]
                    return self._format_response(200, {"assignments": listed})

                if path == "/tun/m/assign":
                    nbh = query_params.get("nbh", [""])[0]
                    acc = query_params.get("accelerator", ["T4"])[0]
                    var = query_params.get("variant", ["GPU"])[0]
                    shape = query_params.get("shape", [None])[0]
                    shape_str = (shape or "").lower()

                    if self.strict_shape_validation and acc.upper() in HIGH_MEM_ACCELERATORS and shape_str == "hm":
                        return self._format_response(
                            400, {"error": f"Invalid parameter: shape=hm is not supported for accelerator {acc}"}
                        )

                    if acc.upper() in self.quota_unavailable_accelerators:
                        return self._format_response(
                            400, {"error": f"Resource has no available quota in region for {acc}"}
                        )

                    if nbh in self.nbh_to_endpoint:
                        ep = self.nbh_to_endpoint[nbh]
                        return self._format_response(200, self.assignments[ep].to_assigned_dict())

                    token = f"xsrf-assign-{uuid.uuid4().hex[:12]}"
                    self.assign_tokens[token] = {"nbh": nbh, "accelerator": acc, "variant": var, "shape": shape}
                    return self._format_response(200, {"acc": acc, "nbh": nbh, "token": token, "variant": var})

                if path.startswith("/tun/m/unassign/"):
                    endpoint = path.split("/tun/m/unassign/", 1)[1].strip("/")
                    if endpoint not in self.assignments:
                        return self._format_response(404, {"error": f"Assignment {endpoint} not found"})
                    token = f"xsrf-unassign-{uuid.uuid4().hex[:12]}"
                    self.unassign_tokens[token] = endpoint
                    return self._format_response(200, {"token": token})

                if path.startswith("/tun/m/credentials-propagation/"):
                    endpoint = path.split("/tun/m/credentials-propagation/", 1)[1].strip("/")
                    token = f"xsrf-propagate-{uuid.uuid4().hex[:12]}"
                    self.propagate_tokens[token] = endpoint
                    return self._format_response(200, {"token": token})

            elif method == "POST":
                if self.fail_next_assign and path == "/tun/m/assign":
                    err = self.fail_next_assign
                    self.fail_next_assign = None
                    return self._format_response(
                        err.get("status", 500), err.get("body", {"error": "Injected fault"})
                    )

                if self.transient_errors_remaining > 0 and path == "/tun/m/assign":
                    self.transient_errors_remaining -= 1
                    nbh = query_params.get("nbh", [""])[0]
                    acc = query_params.get("accelerator", ["T4"])[0]
                    if self.reconcile_on_transient_error and nbh:
                        endpoint = f"ep-{uuid.uuid4().hex[:8]}"
                        assignment = MockAssignment(
                            endpoint=endpoint,
                            accelerator=acc,
                            nbh=nbh,
                            proxy_url=f"{self.runtime_proxy_base_url}/tun/m/{endpoint}",
                        )
                        self.assignments[endpoint] = assignment
                        self.nbh_to_endpoint[nbh] = endpoint
                        if self.hub and hasattr(self.hub, "on_assignment_created"):
                            self.hub.on_assignment_created(endpoint, assignment.proxy_token)
                    return self._format_response(self.transient_status_code, {"error": "Bad Gateway"})

                if path == "/tun/m/assign":
                    if self.reject_missing_xsrf_token and (
                        not xsrf_token or xsrf_token not in self.assign_tokens
                    ):
                        return self._format_response(403, {"error": "Invalid or missing XSRF token"})

                    if self.concurrency_limit is not None and len(self.assignments) >= self.concurrency_limit:
                        return self._format_response(
                            412, {"error": "Too many active assignments for account"}
                        )

                    nbh = query_params.get("nbh", [""])[0]
                    acc = query_params.get("accelerator", ["T4"])[0]
                    var_str = query_params.get("variant", ["GPU"])[0].upper()
                    shape = query_params.get("shape", [None])[0]
                    shape_str = (shape or "").lower()

                    if self.strict_shape_validation and acc.upper() in HIGH_MEM_ACCELERATORS and shape_str == "hm":
                        return self._format_response(400, {"error": f"Invalid shape=hm for {acc}"})

                    if acc.upper() in self.quota_unavailable_accelerators:
                        return self._format_response(400, {"error": f"Quota unavailable for {acc}"})

                    var_int = 2 if var_str == "TPU" else (1 if var_str == "GPU" else 0)
                    machine_shape = 1 if (acc.upper() in HIGH_MEM_ACCELERATORS or shape_str == "hm") else 0

                    endpoint = f"ep-{uuid.uuid4().hex[:8]}"
                    assignment = MockAssignment(
                        endpoint=endpoint,
                        accelerator=acc.upper(),
                        variant=var_int,
                        machine_shape=machine_shape,
                        nbh=nbh,
                        proxy_url=f"{self.runtime_proxy_base_url}/tun/m/{endpoint}",
                    )
                    self.assignments[endpoint] = assignment
                    if nbh:
                        self.nbh_to_endpoint[nbh] = endpoint
                    self.assign_tokens.pop(xsrf_token, None)

                    if self.hub and hasattr(self.hub, "on_assignment_created"):
                        self.hub.on_assignment_created(endpoint, assignment.proxy_token)

                    return self._format_response(200, assignment.to_post_dict())

                if path.startswith("/tun/m/unassign/"):
                    endpoint = path.split("/tun/m/unassign/", 1)[1].strip("/")
                    if endpoint not in self.assignments:
                        return self._format_response(404, {"error": f"Assignment {endpoint} not found"})
                    if self.reject_missing_xsrf_token and (
                        not xsrf_token or xsrf_token not in self.unassign_tokens
                    ):
                        return self._format_response(
                            403, {"error": "Invalid or missing XSRF token for unassign"}
                        )

                    assignment = self.assignments.pop(endpoint, None)
                    if assignment and assignment.nbh:
                        self.nbh_to_endpoint.pop(assignment.nbh, None)
                    self.unassign_tokens.pop(xsrf_token, None)

                    if self.hub and hasattr(self.hub, "on_assignment_destroyed"):
                        self.hub.on_assignment_destroyed(endpoint)

                    return self._format_response(200, {})

                if path.startswith("/tun/m/credentials-propagation/"):
                    dryrun = query_params.get("dryrun", ["false"])[0].lower() == "true"
                    if dryrun:
                        return self._format_response(200, {"success": True})
                    return self._format_response(200, {"status": "propagated", "success": True})

        return self._format_response(404, {"error": f"Endpoint {path} not found"})

    def _format_response(self, status: int, data: Any) -> Tuple[int, Dict[str, str], bytes]:
        payload = json.dumps(data)
        if self.xssi_prefix_enabled:
            payload = f"{XSSI_PREFIX}{payload}"
        body_bytes = payload.encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body_bytes)),
        }
        return status, headers, body_bytes


class MockControlPlaneHandler(BaseHTTPRequestHandler):
    """HTTP request handler executing Colab Control Plane API endpoints."""

    state: MockControlPlaneState

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        headers_dict = {k.lower(): v for k, v in self.headers.items()}
        status, hdrs, body = self.state.dispatch("GET", parsed.path, qs, b"", headers_dict)
        self.send_response(status)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        content_len = int(self.headers.get("Content-Length", 0))
        body_in = self.rfile.read(content_len) if content_len > 0 else b""
        headers_dict = {k.lower(): v for k, v in self.headers.items()}
        status, hdrs, body = self.state.dispatch("POST", parsed.path, qs, body_in, headers_dict)
        self.send_response(status)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


class MockControlPlaneServer:
    """Threaded HTTP server running the Mock Colab Control Plane."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        state: Optional[MockControlPlaneState] = None,
        hub: Optional[Any] = None,
    ):
        self.host = host
        self.state = state or MockControlPlaneState(hub=hub)
        if hub:
            self.state.hub = hub

        class BoundHandler(MockControlPlaneHandler):
            pass

        BoundHandler.state = self.state
        self._httpd = ThreadingHTTPServer((host, port), BoundHandler)
        self.port = self._httpd.server_port
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> MockControlPlaneServer:
        if self._thread is None:
            self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def reset(self) -> None:
        self.state.reset()

    def __enter__(self) -> MockControlPlaneServer:
        return self.start()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    async def __aenter__(self) -> MockControlPlaneServer:
        return self.start()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()


def create_asgi_app(state: Optional[MockControlPlaneState] = None) -> Any:
    """Standard-library pure ASGI 3.0 callable for in-process testing."""
    app_state = state or MockControlPlaneState()

    async def asgi_app(scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return

        method = scope["method"]
        raw_path = scope["path"]
        raw_query = scope.get("query_string", b"").decode("utf-8")
        headers_dict = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        qs = urllib.parse.parse_qs(raw_query)

        body_bytes = b""
        if method == "POST":
            more_body = True
            body_chunks = []
            while more_body:
                message = await receive()
                body_chunks.append(message.get("body", b""))
                more_body = message.get("more_body", False)
            body_bytes = b"".join(body_chunks)

        status, hdrs, payload = app_state.dispatch(method, raw_path, qs, body_bytes, headers_dict)
        asgi_headers = [(k.lower().encode("latin1"), v.encode("latin1")) for k, v in hdrs.items()]

        await send({
            "type": "http.response.start",
            "status": status,
            "headers": asgi_headers,
        })
        await send({"type": "http.response.body", "body": payload})

    return asgi_app
