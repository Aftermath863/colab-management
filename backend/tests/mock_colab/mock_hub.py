"""Unified Test Controller, Fixture Manager, and Fault Injector for Mock Colab Suite.

Coordinates MockGoogleOAuthServer, MockControlPlaneServer, and MockRuntimeProxyServer.
Supports both in-process ASGI transports and loopback socket servers.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .mock_control_plane import MockControlPlaneServer, MockControlPlaneState
from .mock_oauth import MockGoogleOAuthServer, MockOAuthState
from .mock_runtime_proxy import MockRuntimeProxyApp, MockRuntimeProxyServer

logger = logging.getLogger(__name__)


class MockControlHub:
    """Central test fixture and lifecycle orchestrator.

    Manages accounts, assignments, fault injection, and server lifecycle across
    the entire Mock Colab ecosystem.
    """

    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self.oauth_server = MockGoogleOAuthServer(host=host, port=0)
        self.runtime_proxy_server = MockRuntimeProxyServer(host=host, port=0)
        self.control_plane_server = MockControlPlaneServer(host=host, port=0, hub=self)

        # Aliases for convenience
        self.oauth = self.oauth_server
        self.control_plane = self.control_plane_server
        self.runtime_proxy = self.runtime_proxy_server

        # Fault injection tracking
        self.forced_ssh_collisions: Set[str] = set()

        # Port and URL metadata
        self.ports: Dict[str, int] = {}
        self.urls: Dict[str, str] = {}
        self.is_running: bool = False

        self.reset()

    # ========================================================================
    # Lifecycle Management
    # ========================================================================

    def start(self, host: Optional[str] = None) -> Dict[str, str]:
        """Starts all mock servers on OS-allocated ephemeral ports."""
        if self.is_running:
            return self.urls

        if host:
            self.host = host

        # 1. Start OAuth Server
        self.oauth_server.start()
        self.ports["oauth"] = self.oauth_server.port
        self.urls["oauth"] = self.oauth_server.base_url

        # 2. Start Runtime Proxy Server
        self.runtime_proxy_server.start()
        self.ports["runtime_proxy"] = self.runtime_proxy_server.port
        self.urls["runtime_proxy"] = self.runtime_proxy_server.url

        # Link Control Plane to Runtime Proxy URL
        self.control_plane_server.state.runtime_proxy_base_url = self.runtime_proxy_server.url

        # 3. Start Control Plane Server
        self.control_plane_server.start()
        self.ports["control_plane"] = self.control_plane_server.port
        self.urls["control_plane"] = self.control_plane_server.url

        self.is_running = True
        return self.urls

    def stop(self) -> None:
        """Cleanly shuts down all mock servers."""
        if not self.is_running:
            return

        self.control_plane_server.stop()
        self.runtime_proxy_server.stop()
        self.oauth_server.stop()

        self.is_running = False

    def reset(self) -> None:
        """Resets all accounts, assignments, runtimes, and fault injections to clean baseline."""
        self.forced_ssh_collisions.clear()
        self.oauth_server.reset()
        self.runtime_proxy_server.reset()
        self.control_plane_server.reset()

        if self.is_running:
            self.control_plane_server.state.runtime_proxy_base_url = self.runtime_proxy_server.url

        # Seed standard development accounts
        self.set_account_ccu("colab-dev@gmail.com", balance=100.0, burn_rate=1.5)
        self.set_account_ccu("colab-prod@gmail.com", balance=50.0, burn_rate=0.0)

    def __enter__(self) -> MockControlHub:
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    async def __aenter__(self) -> MockControlHub:
        self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    # ========================================================================
    # Cross-Server State Synchronization Hooks
    # ========================================================================

    def on_assignment_created(self, endpoint_id: str, token: str) -> None:
        """Called automatically when an assignment is created."""
        ep = self.runtime_proxy_server.app.register_endpoint(endpoint_id, token)
        if endpoint_id in self.forced_ssh_collisions:
            ep.active_ssh_connection = True

    def on_assignment_destroyed(self, endpoint_id: str) -> None:
        """Called automatically when an assignment is unassigned."""
        with self.runtime_proxy_server.app._lock:
            self.runtime_proxy_server.app.endpoints.pop(endpoint_id, None)

    # ========================================================================
    # Programmatic Fault Injection Interface
    # ========================================================================

    def trigger_quota_exhaustion(self, accelerator: str) -> None:
        """Forces assign requests for this accelerator to fail with HTTP 400 Bad Request."""
        self.control_plane_server.state.trigger_quota_exhaustion(accelerator)

    def clear_quota_exhaustion(self) -> None:
        self.control_plane_server.state.clear_quota_exhaustion()

    def trigger_concurrency_limit(self, max_allowed: int = 1) -> None:
        """Sets max allowed concurrent assignments (triggers HTTP 412 when exceeded)."""
        self.control_plane_server.state.trigger_concurrency_limit(max_allowed)

    def trigger_ssh_collision(self, endpoint: str) -> None:
        """Forces the runtime proxy endpoint into active SSH connection state (triggers HTTP 429)."""
        self.forced_ssh_collisions.add(endpoint)
        ep = self.runtime_proxy_server.app.get_endpoint(endpoint)
        if ep:
            ep.active_ssh_connection = True

    def release_ssh_collision(self, endpoint: str) -> None:
        self.forced_ssh_collisions.discard(endpoint)
        ep = self.runtime_proxy_server.app.get_endpoint(endpoint)
        if ep:
            ep.active_ssh_connection = False

    def expire_proxy_token(self, endpoint: str) -> None:
        """Invalidates proxy token for endpoint (triggers HTTP 401 on contents/tty/ssh)."""
        self.control_plane_server.state.expire_proxy_token(endpoint)
        ep = self.runtime_proxy_server.app.get_endpoint(endpoint)
        if ep:
            ep.token_expired = True

    def simulate_network_delay(self, seconds: float) -> None:
        """Injects artificial network latency for control plane requests."""
        self.control_plane_server.state.set_network_delay(seconds)

    def simulate_server_error(self, count: int = 1, status_code: int = 502, reconcile: bool = False) -> None:
        """Forces server to respond with error (e.g. 502 Bad Gateway)."""
        self.control_plane_server.state.trigger_transient_error(
            count=count, status_code=status_code, reconcile=reconcile
        )

    def inject_orphan_assignment(
        self, endpoint: str = "ep-orphan-999", accelerator: str = "T4", email: str = "colab-dev@gmail.com"
    ) -> str:
        """Injects an unmanaged assignment in control plane state to verify orphan reconciliation."""
        assignment = self.control_plane_server.state.inject_orphan_assignment(
            endpoint=endpoint, accelerator=accelerator, account_email=email
        )
        return assignment.endpoint

    def set_account_ccu(self, email: str, balance: float, burn_rate: float, count: Optional[int] = None) -> None:
        """Sets CCU balance and burn rate for an account."""
        self.control_plane_server.state.set_account_ccu(email, balance, burn_rate)
        if count is not None:
            self.control_plane_server.state.assignments_count_override = count
