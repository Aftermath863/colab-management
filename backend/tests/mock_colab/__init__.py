"""Zero-Dependency Mock Google Colab Server Suite.

Provides hermetic, high-fidelity mock implementations of Google OAuth, Colab Control Plane,
and Colab Runtime Proxy (REST VFS, Web TTY WebSocket, and SSH WebSocket) with centralized
coordination and fault injection.
"""

from .mock_control_plane import (
    HIGH_MEM_ACCELERATORS,
    XSSI_PREFIX,
    MockAssignment,
    MockControlPlaneHandler,
    MockControlPlaneServer,
    MockControlPlaneState,
    create_asgi_app,
)
from .mock_hub import MockControlHub
from .mock_oauth import (
    DEFAULT_SCOPES,
    MockGoogleOAuthServer,
    MockOAuthAccount,
    MockOAuthRequestHandler,
    MockOAuthState,
)
from .mock_runtime_proxy import (
    MockRuntimeProxyApp,
    MockRuntimeProxyEndpoint,
    MockRuntimeProxyHandler,
    MockRuntimeProxyServer,
    MockTerminalSession,
    RFC6455WebSocket,
    VFSNode,
    VirtualFileSystem,
)

__all__ = [
    "MockGoogleOAuthServer",
    "MockOAuthAccount",
    "MockOAuthState",
    "DEFAULT_SCOPES",
    "MockControlPlaneServer",
    "MockControlPlaneState",
    "MockAssignment",
    "HIGH_MEM_ACCELERATORS",
    "XSSI_PREFIX",
    "create_asgi_app",
    "MockRuntimeProxyServer",
    "MockRuntimeProxyApp",
    "MockRuntimeProxyEndpoint",
    "VirtualFileSystem",
    "VFSNode",
    "MockTerminalSession",
    "RFC6455WebSocket",
    "MockControlHub",
]
