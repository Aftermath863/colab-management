"""WebSocket TTY Terminal proxy bridging browser xterm.js to Colab runtime /colab/tty."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
import websockets

from backend.app.core.security import AuthenticationError, decode_access_token
from backend.app.services import session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["terminal"])


@router.websocket("/{session_id}/terminal")
async def websocket_terminal(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(None),
):
    """Full-duplex WebSocket bridge between browser xterm.js and Colab runtime /colab/tty.

    Accepts JWT token in query parameter (or header if available).
    Relays JSON-encoded keystrokes, resize commands, and ANSI output streams.
    """
    # 1. Validate Authentication
    auth_token = token
    if not auth_token:
        # Check subprotocol
        protocols = websocket.scope.get("subprotocols", [])
        if protocols:
            auth_token = protocols[0]

    if not auth_token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing auth token")
        return

    try:
        payload = decode_access_token(auth_token)
        if payload.get("role") != "admin":
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized role")
            return
    except AuthenticationError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")
        return

    # 2. Lookup Session and Decrypted Credentials
    sess = session_manager.get_session_internal(session_id)
    if not sess:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=f"Session {session_id} not found")
        return

    proxy_url = sess.get("proxy_url", "")
    proxy_token = sess.get("proxy_token", "")
    if not proxy_url or not proxy_token:
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Missing session proxy credentials")
        return

    # Convert http/https to ws/wss
    ws_proxy_url = proxy_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
    upstream_url = f"{ws_proxy_url}/colab/tty?colab-runtime-proxy-token={proxy_token}"

    await websocket.accept()

    try:
        async with websockets.connect(upstream_url, open_timeout=10, ping_interval=20) as upstream:
            # Task: Upstream (Colab) -> Browser
            async def forward_upstream_to_client():
                try:
                    async for message in upstream:
                        # Forward verbatim JSON frame to browser
                        if isinstance(message, bytes):
                            message = message.decode("utf-8", errors="replace")
                        await websocket.send_text(message)
                        try:
                            from backend.app.services.lifecycle_engine import lifecycle_engine
                            lifecycle_engine.record_activity(session_id)
                        except Exception:
                            pass
                except (websockets.ConnectionClosed, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.debug(f"Terminal upstream->client error: {e}")

            # Task: Browser -> Upstream (Colab)
            async def forward_client_to_upstream():
                try:
                    while True:
                        client_msg = await websocket.receive_text()
                        # Forward JSON frame to Colab TTY
                        await upstream.send(client_msg)
                        try:
                            from backend.app.services.lifecycle_engine import lifecycle_engine
                            lifecycle_engine.record_activity(session_id)
                        except Exception:
                            pass
                except (WebSocketDisconnect, asyncio.CancelledError):
                    pass
                except Exception as e:
                    logger.debug(f"Terminal client->upstream error: {e}")

            up_task = asyncio.create_task(forward_upstream_to_client())
            down_task = asyncio.create_task(forward_client_to_upstream())

            done, pending = await asyncio.wait(
                [up_task, down_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()

    except Exception as e:
        logger.warning(f"Terminal bridge connection to {upstream_url} failed: {e}")
        try:
            await websocket.send_text(json.dumps({"data": f"\r\n\x1b[31m[Colab Terminal Error: {e}]\x1b[0m\r\n"}))
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason=str(e))
        except Exception:
            pass
