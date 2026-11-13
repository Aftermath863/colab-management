"""Zero-dependency Colab Runtime Proxy Mock Server.

Implements:
1. REST /api/contents/{path}: In-memory Virtual Filesystem (VFS) with chunking.
2. WebSocket /colab/tty: JSON text frames for interactive ANSI terminal.
3. WebSocket /colab/ssh: Binary SSH tunnel with X-Colab-Ssh-Pubkey validation
   and HTTP 429 single-connection lock.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import socket
import struct
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)


# ============================================================================
# 1. In-Memory Virtual Filesystem (VFS)
# ============================================================================


class VFSNode:
    """Represents a file or directory node in the virtual filesystem."""

    def __init__(self, name: str, path: str, is_dir: bool = False, content: bytes = b""):
        self.name = name
        self.path = path.strip("/")
        self.is_dir = is_dir
        self.content: bytearray = bytearray(content)
        self.mtime: str = datetime.now(timezone.utc).isoformat()
        self.children: Optional[Dict[str, VFSNode]] = {} if is_dir else None

    @property
    def size(self) -> Optional[int]:
        return None if self.is_dir else len(self.content)

    def to_jupyter_dict(self, include_content: bool = False) -> Dict[str, Any]:
        """Converts to Jupyter Contents API response schema."""
        data: Dict[str, Any] = {
            "name": self.name,
            "path": self.path,
            "type": "directory" if self.is_dir else "file",
            "mtime": self.mtime,
            "size": self.size,
            "format": None,
            "content": None,
        }
        if self.is_dir:
            if include_content and self.children is not None:
                data["content"] = [child.to_jupyter_dict(include_content=False) for child in self.children.values()]
                data["format"] = "json"
        else:
            if include_content:
                data["format"] = "base64"
                data["content"] = base64.b64encode(self.content).decode("ascii")
        return data


class VirtualFileSystem:
    """Thread-safe in-memory virtual filesystem."""

    def __init__(self):
        self._lock = threading.RLock()
        self.root = VFSNode("", "", is_dir=True)
        self._in_progress_chunks: Dict[str, bytearray] = {}
        self.reset()

    def reset(self) -> None:
        """Resets the VFS to the standard Colab default layout."""
        with self._lock:
            self.root = VFSNode("", "", is_dir=True)
            self._in_progress_chunks.clear()
            self.mkdir("sample_data")
            self.mkdir("workspace")
            self.write_file(
                "sample_data/README.md",
                base64.b64encode(b"This directory contains sample datasets for Google Colab.\n").decode("ascii"),
                chunk=1,
            )
            self.write_file(
                "sample_data/california_housing_train.csv",
                base64.b64encode(
                    b"longitude,latitude,housing_median_age,total_rooms\n-114.31,34.19,15.0,5612.0\n"
                ).decode("ascii"),
                chunk=1,
            )

    def _resolve(self, path: str) -> Tuple[Optional[VFSNode], Optional[VFSNode], str]:
        """Resolves path into (parent_node, target_node, target_name)."""
        clean_path = path.strip("/")
        if not clean_path:
            return None, self.root, ""

        parts = clean_path.split("/")
        if any(part in (".", "..", "") for part in parts):
            raise PermissionError("Path traversal or invalid path component detected")

        curr = self.root
        parent = None
        for part in parts[:-1]:
            if not curr.is_dir or curr.children is None or part not in curr.children:
                return None, None, parts[-1]
            parent = curr
            curr = curr.children[part]

        target_name = parts[-1]
        target = curr.children.get(target_name) if (curr.is_dir and curr.children is not None) else None
        return curr, target, target_name

    def get(self, path: str, include_content: bool = False) -> Dict[str, Any]:
        """Retrieves directory listing or file metadata/content."""
        with self._lock:
            _, node, _ = self._resolve(path)
            if node is None:
                raise FileNotFoundError(f"File or directory not found: {path}")
            if node.is_dir and not include_content:
                include_content = True
            return node.to_jupyter_dict(include_content=include_content)

    def mkdir(self, path: str) -> Dict[str, Any]:
        """Creates directory and any required parent directories."""
        with self._lock:
            parts = [p for p in path.strip("/").split("/") if p]
            curr = self.root
            accum = []
            for part in parts:
                accum.append(part)
                cur_path = "/".join(accum)
                if curr.children is not None and part not in curr.children:
                    curr.children[part] = VFSNode(part, cur_path, is_dir=True)
                if curr.children is not None:
                    curr = curr.children[part]
            return curr.to_jupyter_dict(include_content=False)

    def write_file(self, path: str, b64_content: str, chunk: int = 1) -> Dict[str, Any]:
        """Writes or appends file content using base64 payload and chunking."""
        with self._lock:
            data = base64.b64decode(b64_content)
            clean_path = path.strip("/")
            parent, target, name = self._resolve(clean_path)
            if parent is None:
                parent_path = "/".join(clean_path.split("/")[:-1])
                self.mkdir(parent_path)
                parent, target, name = self._resolve(clean_path)

            if chunk == 1:
                self._in_progress_chunks[clean_path] = bytearray(data)
            else:
                if clean_path not in self._in_progress_chunks:
                    self._in_progress_chunks[clean_path] = bytearray()
                self._in_progress_chunks[clean_path].extend(data)

            if target is None:
                target = VFSNode(name, clean_path, is_dir=False, content=bytes(self._in_progress_chunks[clean_path]))
                if parent is not None and parent.children is not None:
                    parent.children[name] = target
            else:
                target.content = bytearray(self._in_progress_chunks[clean_path])
                target.mtime = datetime.now(timezone.utc).isoformat()

            return target.to_jupyter_dict(include_content=False)

    def delete(self, path: str) -> bool:
        """Deletes file or directory."""
        with self._lock:
            clean_path = path.strip("/")
            parent, target, name = self._resolve(clean_path)
            if target is None or parent is None or parent.children is None:
                raise FileNotFoundError(f"File or directory not found: {path}")
            del parent.children[name]
            self._in_progress_chunks.pop(clean_path, None)
            return True


# ============================================================================
# 2. Interactive TTY Terminal Simulation
# ============================================================================


class MockTerminalSession:
    """Simulates an interactive bash terminal session over WebSocket."""

    def __init__(self, vfs: VirtualFileSystem):
        self.vfs = vfs
        self.cols: int = 80
        self.rows: int = 24
        self.cwd: str = "/content"
        self.prompt: str = "\x1b[1;32mroot@colab-runtime\x1b[0m:\x1b[1;34m/content\x1b[0m# "
        self.line_buffer: str = ""
        self.closed: bool = False

    def handle_resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows

    def handle_input(self, data: str) -> List[str]:
        """Processes keystrokes, echoes, and executes commands upon newline."""
        outputs: List[str] = []
        for char in data:
            if char in ("\r", "\n"):
                cmd = self.line_buffer.strip()
                outputs.append("\r\n")
                if cmd:
                    out = self._execute_command(cmd)
                    if out:
                        outputs.append(out)
                if not self.closed:
                    outputs.append(self.prompt)
                self.line_buffer = ""
            elif char in ("\x08", "\x7f"):  # Backspace
                if self.line_buffer:
                    self.line_buffer = self.line_buffer[:-1]
                    outputs.append("\b \b")
            elif char == "\x03":  # Ctrl+C
                outputs.append("^C\r\n" + self.prompt)
                self.line_buffer = ""
            elif char == "\x04":  # Ctrl+D
                if not self.line_buffer:
                    outputs.append("exit\r\n")
                    self.closed = True
            else:
                self.line_buffer += char
                outputs.append(char)
        return outputs

    def _execute_command(self, cmd: str) -> str:
        parts = cmd.split()
        if not parts:
            return ""
        op = parts[0]
        if op == "exit":
            self.closed = True
            return ""
        elif op == "pwd":
            return f"{self.cwd}\r\n"
        elif op == "whoami":
            return "root\r\n"
        elif op == "ls":
            try:
                listing = self.vfs.get("", include_content=True)
                names = [c["name"] for c in listing.get("content", [])]
                return "  ".join(names) + "\r\n"
            except Exception:
                return "sample_data  workspace\r\n"
        elif op == "nvidia-smi":
            return (
                "+-----------------------------------------------------------------------------+\r\n"
                "| NVIDIA-SMI 535.104.05             Driver Version: 535.104.05   CUDA: 12.2    |\r\n"
                "|-------------------------------+----------------------+----------------------|\r\n"
                "| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |\r\n"
                "| Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |\r\n"
                "|===============================+======================+======================|\r\n"
                "|   0  NVIDIA A100-SXM4-40GB Off| 00000000:00:04.0 Off |                    0 |\r\n"
                "| N/A   32C    P0    42W / 400W |      0MiB / 40960MiB |      0%      Default |\r\n"
                "+-------------------------------+----------------------+----------------------+\r\n"
            )
        elif op == "python3":
            if "-c" in parts:
                code_idx = parts.index("-c") + 1
                if code_idx < len(parts):
                    code = " ".join(parts[code_idx:]).strip("'\"")
                    if "torch" in code:
                        return "2.5.1+cu121\r\n"
                    return "Mock Python Execution Successful\r\n"
            return "Python 3.12.7 (main, Oct  1 2026, 00:00:00) [GCC 11.4.0] on linux\r\n"
        elif op == "echo":
            return " ".join(parts[1:]).strip("'\"") + "\r\n"
        return f"bash: {op}: command not found\r\n"


# ============================================================================
# 3. Endpoint State & Single-Connection SSH Lock
# ============================================================================


class MockRuntimeProxyEndpoint:
    """State manager for a single active Colab instance runtime proxy."""

    def __init__(self, endpoint: str, token: str):
        self.endpoint = endpoint
        self.token = token
        self.vfs = VirtualFileSystem()
        self.active_ssh_connection: bool = False
        self.active_ssh_pubkey: Optional[str] = None
        self.token_expired: bool = False
        self._lock = threading.RLock()

    def validate_token(self, provided_token: Optional[str]) -> bool:
        if self.token_expired:
            return False
        return provided_token == self.token

    def acquire_ssh_lock(self, pubkey_header: Optional[str]) -> Tuple[int, str]:
        """Validates pubkey header and acquires the single-connection lock.

        Returns: (http_status_code, error_or_success_message)
        """
        with self._lock:
            # 1. Pubkey header validation
            if not pubkey_header:
                return 400, "Server rejected request: missing pubkey header."

            clean_key = pubkey_header.strip()
            if clean_key.startswith("ssh-rsa"):
                return (
                    400,
                    "Server rejected pubkey: unsupported key type. Accepted key types: ssh-ed25519, "
                    "ecdsa-sha2-nistp{256,384,521}. RSA keys (ssh-rsa) are NOT accepted.",
                )

            if not (clean_key.startswith("ssh-ed25519") or clean_key.startswith("ecdsa-")):
                return 400, f"Server rejected pubkey (HTTP 400): unsupported key type '{clean_key[:15]}...'"

            # 2. Critical Single-Connection Lock
            if self.active_ssh_connection:
                return (
                    429,
                    "Already-active SSH session (HTTP 429): another colab ssh is connected to this runtime. "
                    "Disconnect it and retry.",
                )

            # Lock acquired successfully
            self.active_ssh_connection = True
            self.active_ssh_pubkey = clean_key
            return 101, "Switching Protocols"

    def release_ssh_lock(self) -> None:
        """Releases the single-connection lock upon disconnect."""
        with self._lock:
            self.active_ssh_connection = False
            self.active_ssh_pubkey = None


# ============================================================================
# 4. Zero-Dependency RFC 6455 WebSocket Implementation
# ============================================================================


class RFC6455WebSocket:
    """Zero-dependency RFC 6455 WebSocket protocol frame builder and parser."""

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    @classmethod
    def compute_accept_key(cls, sec_key: str) -> str:
        sha1 = hashlib.sha1((sec_key.strip() + cls.GUID).encode("utf-8")).digest()
        return base64.b64encode(sha1).decode("ascii")

    @classmethod
    def encode_frame(cls, payload: bytes, opcode: int = 0x1) -> bytes:
        """Builds an unmasked server-to-client WebSocket frame."""
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(length)
        elif length <= 0xFFFF:
            header.append(126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", length))
        return bytes(header) + payload

    @classmethod
    def decode_frame(cls, data: bytes) -> Optional[Tuple[int, bytes, int]]:
        """Parses a client frame (masked).

        Returns: (opcode, unmasked_payload, bytes_consumed) or None if incomplete.
        """
        if len(data) < 2:
            return None
        byte1, byte2 = data[0], data[1]
        opcode = byte1 & 0x0F
        masked = bool(byte2 & 0x80)
        payload_len = byte2 & 0x7F
        offset = 2

        if payload_len == 126:
            if len(data) < offset + 2:
                return None
            payload_len = struct.unpack(">H", data[offset : offset + 2])[0]
            offset += 2
        elif payload_len == 127:
            if len(data) < offset + 8:
                return None
            payload_len = struct.unpack(">Q", data[offset : offset + 8])[0]
            offset += 8

        mask_key = None
        if masked:
            if len(data) < offset + 4:
                return None
            mask_key = data[offset : offset + 4]
            offset += 4

        if len(data) < offset + payload_len:
            return None

        raw_payload = data[offset : offset + payload_len]
        if masked and mask_key:
            unmasked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(raw_payload))
            payload = bytes(unmasked)
        else:
            payload = raw_payload

        return opcode, payload, offset + payload_len


# ============================================================================
# 5. Runtime Proxy Application Dispatcher
# ============================================================================


class MockRuntimeProxyApp:
    """Unified Runtime Proxy Application supporting REST and WebSocket routes."""

    def __init__(self):
        self.endpoints: Dict[str, MockRuntimeProxyEndpoint] = {}
        self._lock = threading.RLock()

    def register_endpoint(self, endpoint_id: str, token: str) -> MockRuntimeProxyEndpoint:
        with self._lock:
            ep = MockRuntimeProxyEndpoint(endpoint_id, token)
            self.endpoints[endpoint_id] = ep
            return ep

    def get_endpoint(self, endpoint_id: str) -> Optional[MockRuntimeProxyEndpoint]:
        with self._lock:
            return self.endpoints.get(endpoint_id)

    def get_endpoint_by_token(self, token: Optional[str]) -> Optional[MockRuntimeProxyEndpoint]:
        """Lookup an endpoint by runtime proxy token in a thread-safe manner."""
        if not token:
            return None
        with self._lock:
            for candidate in self.endpoints.values():
                if candidate.token == token:
                    return candidate
            return None

    def dispatch_rest(
        self,
        method: str,
        path: str,
        query_params: Dict[str, List[str]],
        body_bytes: bytes,
        headers: Dict[str, str],
    ) -> Tuple[int, Dict[str, str], bytes]:
        """Handles REST API requests: /api/contents/{path}."""
        token = query_params.get("colab-runtime-proxy-token", [None])[0] or headers.get(
            "x-colab-runtime-proxy-token"
        )

        endpoint_id = "default"
        ep_match = re.match(r"^/tun/m/([^/]+)(/api/contents.*)", path)
        if ep_match:
            endpoint_id = ep_match.group(1)
            contents_path = ep_match.group(2)
        else:
            contents_path = path

        ep = self.get_endpoint(endpoint_id)
        if ep is None and token:
            ep = self.get_endpoint_by_token(token)
        if ep is None:
            with self._lock:
                if len(self.endpoints) == 1:
                    ep = next(iter(self.endpoints.values()))

        if ep is None or not ep.validate_token(token):
            return (
                401,
                {"Content-Type": "application/json; charset=utf-8"},
                json.dumps({"error": "Unauthorized: invalid or expired proxy token"}).encode(),
            )

        prefix = "/api/contents"
        if not contents_path.startswith(prefix):
            return (
                404,
                {"Content-Type": "application/json; charset=utf-8"},
                json.dumps({"error": "Endpoint not found"}).encode(),
            )

        subpath = unquote(contents_path[len(prefix) :].lstrip("/"))

        try:
            if method == "GET":
                content_flag = query_params.get("content", ["0"])[0] == "1"
                result = ep.vfs.get(subpath, include_content=content_flag)
                body = json.dumps(result).encode("utf-8")
                return 200, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))}, body

            elif method == "PUT":
                payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
                remote_path = payload.get("path", subpath)
                item_type = payload.get("type", "file")
                if item_type == "directory":
                    result = ep.vfs.mkdir(remote_path)
                else:
                    b64_content = payload.get("content", "")
                    chunk = int(payload.get("chunk", 1))
                    result = ep.vfs.write_file(remote_path, b64_content, chunk=chunk)
                body = json.dumps(result).encode("utf-8")
                return 200, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))}, body

            elif method == "DELETE":
                ep.vfs.delete(subpath)
                return 204, {}, b""

            else:
                return (
                    405,
                    {"Content-Type": "application/json; charset=utf-8"},
                    json.dumps({"error": "Method Not Allowed"}).encode(),
                )

        except FileNotFoundError as e:
            body = json.dumps({"error": str(e)}).encode()
            return 404, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))}, body
        except PermissionError as e:
            body = json.dumps({"error": str(e)}).encode()
            return 403, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))}, body
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            return 500, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))}, body


# ============================================================================
# 6. Standalone HTTP & WebSocket Server
# ============================================================================


class MockRuntimeProxyHandler(BaseHTTPRequestHandler):
    """Handles both HTTP REST and RFC 6455 WebSocket upgrade requests."""

    protocol_version = "HTTP/1.1"
    app: MockRuntimeProxyApp

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        upgrade = self.headers.get("Upgrade", "").lower()
        if upgrade == "websocket":
            self._handle_websocket()
        else:
            self._dispatch_rest("GET")

    def do_PUT(self) -> None:
        self._dispatch_rest("PUT")

    def do_DELETE(self) -> None:
        self._dispatch_rest("DELETE")

    def _dispatch_rest(self, method: str) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        content_len = int(self.headers.get("Content-Length", 0))
        body_in = self.rfile.read(content_len) if content_len > 0 else b""
        headers_dict = {k.lower(): v for k, v in self.headers.items()}

        status, hdrs, body = self.app.dispatch_rest(method, parsed.path, qs, body_in, headers_dict)
        if "Content-Length" not in hdrs and "content-length" not in hdrs:
            hdrs["Content-Length"] = str(len(body))
        self.send_response(status)
        for k, v in hdrs.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _handle_websocket(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        token = qs.get("colab-runtime-proxy-token", [None])[0] or self.headers.get(
            "X-Colab-Runtime-Proxy-Token"
        )

        ep_match = re.match(r"^/tun/m/([^/]+)/colab/(tty|ssh)", parsed.path)
        if ep_match:
            endpoint_id, ws_type = ep_match.group(1), ep_match.group(2)
        else:
            endpoint_id = "default"
            ws_type = "tty" if "/colab/tty" in parsed.path else "ssh"

        ep = self.app.get_endpoint(endpoint_id)
        if ep is None and token:
            ep = self.app.get_endpoint_by_token(token)
        if ep is None:
            with self.app._lock:
                if len(self.app.endpoints) == 1:
                    ep = next(iter(self.app.endpoints.values()))

        if ep is None or not ep.validate_token(token):
            err_bytes = b"Unauthorized: invalid or expired proxy token\n"
            self.send_response(401)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(err_bytes)))
            self.end_headers()
            self.wfile.write(err_bytes)
            self.wfile.flush()
            return

        if ws_type == "ssh":
            pubkey = self.headers.get("X-Colab-Ssh-Pubkey")
            status, msg = ep.acquire_ssh_lock(pubkey)
            if status != 101:
                msg_bytes = msg.encode("utf-8") + b"\n"
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(msg_bytes)))
                self.end_headers()
                self.wfile.write(msg_bytes)
                self.wfile.flush()
                return

            sec_key = self.headers.get("Sec-WebSocket-Key", "")
            accept_key = RFC6455WebSocket.compute_accept_key(sec_key)
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key)
            self.end_headers()

            # Send OpenSSH banner frame in binary
            banner = b"SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6\r\n"
            self.wfile.write(RFC6455WebSocket.encode_frame(banner, opcode=0x2))
            self.wfile.flush()

            try:
                buffer = bytearray()
                while True:
                    data = self.connection.recv(4096)
                    if not data:
                        break
                    buffer.extend(data)
                    while True:
                        frame = RFC6455WebSocket.decode_frame(bytes(buffer))
                        if not frame:
                            break
                        opcode, payload, consumed = frame
                        buffer = buffer[consumed:]
                        if opcode == 0x8:  # Close frame
                            return
                        elif opcode in (0x1, 0x2):
                            # Echo back binary packets for test verification
                            self.wfile.write(RFC6455WebSocket.encode_frame(payload, opcode=opcode))
                            self.wfile.flush()
            except Exception:
                pass
            finally:
                ep.release_ssh_lock()

        elif ws_type == "tty":
            sec_key = self.headers.get("Sec-WebSocket-Key", "")
            accept_key = RFC6455WebSocket.compute_accept_key(sec_key)
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept_key)
            self.end_headers()
            self.wfile.flush()
            time.sleep(0.02)

            session = MockTerminalSession(ep.vfs)
            self.wfile.write(
                RFC6455WebSocket.encode_frame(
                    json.dumps({"data": session.prompt}).encode("utf-8"), opcode=0x1
                )
            )
            self.wfile.flush()

            try:
                buffer = bytearray()
                while not session.closed:
                    data = self.connection.recv(4096)
                    if not data:
                        break
                    buffer.extend(data)
                    while True:
                        frame = RFC6455WebSocket.decode_frame(bytes(buffer))
                        if not frame:
                            break
                        opcode, payload, consumed = frame
                        buffer = buffer[consumed:]
                        if opcode == 0x8:
                            return
                        if opcode == 0x1:
                            try:
                                msg = json.loads(payload.decode("utf-8"))
                            except Exception:
                                continue
                            if "cols" in msg and "rows" in msg:
                                session.handle_resize(msg["cols"], msg["rows"])
                            elif "data" in msg:
                                out_chunks = session.handle_input(msg["data"])
                                for chunk in out_chunks:
                                    self.wfile.write(
                                        RFC6455WebSocket.encode_frame(
                                            json.dumps({"data": chunk}).encode("utf-8"), opcode=0x1
                                        )
                                    )
                                    self.wfile.flush()
            except Exception:
                pass


class MockRuntimeProxyServer:
    """Threaded HTTP and WebSocket server running the Mock Colab Runtime Proxy."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, app: Optional[MockRuntimeProxyApp] = None):
        self.host = host
        self.app = app or MockRuntimeProxyApp()

        class BoundHandler(MockRuntimeProxyHandler):
            pass

        BoundHandler.app = self.app
        self._httpd = ThreadingHTTPServer((host, port), BoundHandler)
        self.port = self._httpd.server_port
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> MockRuntimeProxyServer:
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
        with self.app._lock:
            self.app.endpoints.clear()

    def __enter__(self) -> MockRuntimeProxyServer:
        return self.start()

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()

    async def __aenter__(self) -> MockRuntimeProxyServer:
        return self.start()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.stop()
