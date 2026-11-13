"""Tier 1 Feature Tests: SSH Gateway WebSocket Upstream Protocol and Locks (F7)."""

import re
import socket
import time
from typing import Dict, Optional, Tuple
import unittest

from backend.tests.mock_colab.mock_runtime_proxy import MockRuntimeProxyServer


class TestSSHGateway(unittest.TestCase):
    """Test SSH upstream protocol: Ed25519/ECDSA acceptance, RSA rejection, and single-conn 429 lock."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockRuntimeProxyServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.reset()
        self.endpoint_id = "ep-tier1-ssh"
        self.token = "token-tier1-ssh-secret"
        self.server.app.register_endpoint(self.endpoint_id, self.token)
        self.host = self.server.host
        self.port = self.server.port

    def _send_handshake(self, headers: Dict[str, str], token: Optional[str] = None) -> Tuple[int, str, str, socket.socket]:
        """Initiates handshake and returns (status_code, headers_str, body_str, socket).

        Caller is responsible for closing the returned socket if status_code == 101.
        """
        tok = token if token is not None else self.token
        sock = socket.create_connection((self.host, self.port), timeout=3.0)
        lines = [
            f"GET /tun/m/{self.endpoint_id}/colab/ssh?colab-runtime-proxy-token={tok} HTTP/1.1",
            f"Host: {self.host}:{self.port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
        ]
        for k, v in headers.items():
            lines.append(f"{k}: {v}")
        req_bytes = "\r\n".join(lines).encode("utf-8") + b"\r\n\r\n"
        sock.sendall(req_bytes)

        sock.settimeout(2.0)
        buf = bytearray()
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\r\n\r\n" in buf:
                    if b" 101 " in buf:
                        break
                    # For non-101 responses, wait for full Content-Length body
                    headers_raw = bytes(buf).split(b"\r\n\r\n", 1)[0].decode("utf-8", errors="ignore")
                    cl_match = re.search(r"Content-Length:\s*(\d+)", headers_raw, re.IGNORECASE)
                    if cl_match:
                        expected_len = int(cl_match.group(1))
                        current_body_len = len(bytes(buf).split(b"\r\n\r\n", 1)[1])
                        if current_body_len >= expected_len:
                            break
                    else:
                        break
            except socket.timeout:
                break

        raw = bytes(buf).decode("utf-8", errors="ignore")
        parts = raw.split("\r\n\r\n", 1)
        headers_part = parts[0]
        body_part = parts[1] if len(parts) > 1 else ""
        status_line = headers_part.splitlines()[0] if headers_part else ""
        status_code = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0

        if status_code != 101:
            sock.close()

        return status_code, headers_part, body_part, sock

    def test_ssh_pubkey_handshake_ed25519_and_ecdsa(self):
        """Verify Ed25519 and ECDSA public keys are accepted with HTTP 101 Switching Protocols."""
        # Test Ed25519 key
        ed25519_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"
        status, headers, _, sock = self._send_handshake({"X-Colab-Ssh-Pubkey": ed25519_key})
        self.assertEqual(status, 101, f"Expected 101 for Ed25519 key, got {status}")
        self.assertIn("Upgrade: websocket", headers)
        sock.close()

        # Allow socket release
        time.sleep(0.05)

        # Test ECDSA key
        ecdsa_key = "ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTY... dev@colab"
        status, headers, _, sock = self._send_handshake({"X-Colab-Ssh-Pubkey": ecdsa_key})
        self.assertEqual(status, 101, f"Expected 101 for ECDSA key, got {status}")
        self.assertIn("Upgrade: websocket", headers)
        sock.close()

    def test_ssh_rsa_key_rejected_http_400(self):
        """Verify legacy RSA public keys and missing pubkeys are rejected with HTTP 400."""
        # 1. RSA Key
        rsa_key = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC7vbvF8mQ... dev@colab"
        status, _, body, _ = self._send_handshake({"X-Colab-Ssh-Pubkey": rsa_key})
        self.assertEqual(status, 400, f"Expected 400 for RSA key, got {status}")
        self.assertIn("RSA keys (ssh-rsa) are NOT accepted", body)

        # 2. Missing pubkey header
        status_missing, _, body_missing, _ = self._send_handshake({})
        self.assertEqual(status_missing, 400, f"Expected 400 for missing pubkey header, got {status_missing}")
        self.assertIn("missing pubkey header", body_missing)

    def test_ssh_single_connection_lock_http_429(self):
        """Verify single-connection SSH lock: 2nd concurrent connection receives 429 until 1st disconnects."""
        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"

        # 1. First connection succeeds (101)
        status1, _, _, sock1 = self._send_handshake({"X-Colab-Ssh-Pubkey": pubkey})
        self.assertEqual(status1, 101, "First connection must succeed with 101")

        try:
            # 2. Second concurrent connection must be rejected with 429 Too Many Requests
            status2, _, body2, _ = self._send_handshake({"X-Colab-Ssh-Pubkey": pubkey})
            self.assertEqual(status2, 429, f"Concurrent 2nd connection must receive 429, got {status2}")
            self.assertIn("Already-active SSH session", body2)
        finally:
            # 3. Close the first connection
            sock1.close()

        # Allow server thread to finish cleanup
        time.sleep(0.05)

        # 4. Third connection after lock release must now succeed (101)
        status3, _, _, sock3 = self._send_handshake({"X-Colab-Ssh-Pubkey": pubkey})
        self.assertEqual(status3, 101, "Connection after lock release must succeed with 101")
        sock3.close()

    def test_ssh_unauthorized_token_rejected_http_401(self):
        """Verify connecting to /colab/ssh with invalid or missing token is rejected with HTTP 401."""
        status, _, body, _ = self._send_handshake(
            {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGf3PzV5Q8N1L0K2... dev@colab"},
            token="invalid-expired-proxy-token",
        )
        self.assertEqual(status, 401, f"Expected HTTP 401 Unauthorized for invalid proxy token, got {status}")
        self.assertIn("Unauthorized", body)



if __name__ == "__main__":
    unittest.main()
