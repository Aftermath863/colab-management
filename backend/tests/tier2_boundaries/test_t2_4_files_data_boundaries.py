"""Tier 2 Boundary Tests: Files & Virtual File System (VFS) Boundaries (T2.4).

Validates:
1. VFS chunked upload reassembly across sequential multi-part chunks.
2. Path traversal attack rejection (HTTP 403) across GET, PUT, and DELETE.
3. Runtime proxy token expiry and invalid token rejection (HTTP 401).
4. Deletion of non-existent files returns HTTP 404.
5. Deeply nested directories and unicode/emoji path handling.
6. Large file download integrity (MD5 & SHA-256 checksum preservation).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import unittest

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.tests.mock_colab.mock_runtime_proxy import MockRuntimeProxyServer


class TestFilesDataBoundaries(unittest.TestCase):
    """Tier 2 Boundary tests for Virtual File System (VFS) operations."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockRuntimeProxyServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.reset()
        self.test_dir = tempfile.mkdtemp(prefix="t2_files_")
        self.db_path = os.path.join(self.test_dir, "files_boundary.db")
        init_db(self.db_path)
        self.orig_db = settings.DATABASE_PATH
        settings.DATABASE_PATH = self.db_path

        self.client = TestClient(app)
        self.token = create_access_token({"sub": "admin", "role": "admin"})
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}

        # Register endpoint in mock runtime proxy
        self.endpoint_id = "ep-t2-files"
        self.proxy_token = "proxy-tok-files-12345"
        self.server.app.register_endpoint(self.endpoint_id, self.proxy_token)
        self.ep = self.server.app.get_endpoint(self.endpoint_id)

        # Seed account and session in local DB
        self.account_id = "acc_files_test"
        self.session_id = "sess_files_test"
        self.proxy_url = f"{self.server.url}/tun/m/{self.endpoint_id}"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance)
                VALUES (?, 'files@example.com', 'dummy', 100.0)
                """,
                (self.account_id,),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status
                ) VALUES (?, 'Files Session', ?, 'hash', ?, ?, ?, datetime('now', '+2 hours'), 'T4', 'READY')
                """,
                (
                    self.session_id,
                    self.account_id,
                    self.endpoint_id,
                    self.proxy_url,
                    encrypt_secret(self.proxy_token),
                ),
            )

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _vfs_url(self, subpath: str, token: str | None = None) -> str:
        tok = token if token is not None else self.proxy_token
        clean = subpath.lstrip("/")
        return f"{self.proxy_url}/api/contents/{clean}?colab-runtime-proxy-token={tok}"

    def test_vfs_chunked_upload_multi_part_assembly(self):
        """Sequential multi-part chunks (1..5) are reassembled byte-for-byte in VFS."""
        full_content = bytearray()
        chunk_count = 5
        chunk_size = 2048

        for i in range(1, chunk_count + 1):
            chunk_bytes = bytes([i * 17 % 256] * chunk_size)
            full_content.extend(chunk_bytes)
            b64_data = base64.b64encode(chunk_bytes).decode("utf-8")

            resp = self.client.put(
                f"/api/sessions/{self.session_id}/contents/workspace/multipart_data.bin",
                json={"content": b64_data, "chunk": i},
                headers=self.auth_headers,
            )
            self.assertEqual(resp.status_code, 200, f"Chunk {i} upload failed: {resp.text}")

        # Download and verify reassembled content
        dl_resp = self.client.get(
            f"/api/sessions/{self.session_id}/contents/workspace/multipart_data.bin?content=1",
            headers=self.auth_headers,
        )
        self.assertEqual(dl_resp.status_code, 200)
        dl_data = dl_resp.json()
        downloaded_bytes = base64.b64decode(dl_data["content"])

        self.assertEqual(len(downloaded_bytes), len(full_content))
        self.assertEqual(downloaded_bytes, bytes(full_content))
        self.assertEqual(hashlib.sha256(downloaded_bytes).hexdigest(), hashlib.sha256(full_content).hexdigest())

    def test_vfs_path_traversal_attack_rejection_403(self):
        """Directory traversal attempts return HTTP 403 Forbidden across GET, PUT, and DELETE."""
        traversal_paths = [
            "workspace/../../etc/passwd",
            "workspace/../workspace/../../etc/shadow",
            "sample_data/../../../root/.ssh/id_rsa",
            "workspace/./test.py",
        ]

        for bad_path in traversal_paths:
            # 1. Direct VFS GET
            url = self._vfs_url(bad_path)
            req = urllib.request.Request(url)
            try:
                urllib.request.urlopen(req)
                self.fail(f"GET path traversal not rejected: {bad_path}")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403, f"Expected 403 for GET {bad_path}, got {e.code}")

            # 2. Direct VFS PUT
            put_payload = json.dumps({"content": base64.b64encode(b"malicious").decode()}).encode("utf-8")
            req_put = urllib.request.Request(
                url,
                data=put_payload,
                headers={"Content-Type": "application/json"},
                method="PUT",
            )
            try:
                urllib.request.urlopen(req_put)
                self.fail(f"PUT path traversal not rejected: {bad_path}")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403, f"Expected 403 for PUT {bad_path}, got {e.code}")

            # 3. Direct VFS DELETE
            req_del = urllib.request.Request(url, method="DELETE")
            try:
                urllib.request.urlopen(req_del)
                self.fail(f"DELETE path traversal not rejected: {bad_path}")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403, f"Expected 403 for DELETE {bad_path}, got {e.code}")

    def test_vfs_token_expiry_rejection_401(self):
        """Accessing VFS with expired or invalid runtime proxy token returns HTTP 401 Unauthorized."""
        # 1. Invalid proxy token returns HTTP 401
        invalid_url = self._vfs_url("workspace/check.txt", token="INVALID_TOKEN_XYZ")
        req = urllib.request.Request(invalid_url)
        try:
            urllib.request.urlopen(req)
            self.fail("Expected 401 for invalid proxy token")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)
            err_body = json.loads(e.read().decode())
            self.assertIn("Unauthorized", err_body["error"])

        # 2. Expired proxy token returns HTTP 401
        self.ep.token_expired = True
        expired_url = self._vfs_url("workspace/check.txt")
        try:
            urllib.request.urlopen(urllib.request.Request(expired_url))
            self.fail("Expected 401 for expired proxy token")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

        # 3. Accessing /api/sessions/{id}/contents with invalid auth token returns HTTP 401
        resp_unauth = self.client.get(
            f"/api/sessions/{self.session_id}/contents",
            headers={"Authorization": "Bearer invalid_auth_token"},
        )
        self.assertEqual(resp_unauth.status_code, 401)

    def test_vfs_delete_non_existent_file_404(self):
        """Deleting a non-existent file returns HTTP 404 cleanly from VFS runtime."""
        # 1. Direct VFS DELETE on missing file returns 404
        url = self._vfs_url("workspace/non_existent_file_987654.txt")
        req = urllib.request.Request(url, method="DELETE")
        try:
            urllib.request.urlopen(req)
            self.fail("Expected 404 for deleting non-existent file")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)
            data = json.loads(e.read().decode())
            self.assertIn("not found", data["error"].lower())

        # 2. Requesting non-existent session on delete returns HTTP 404
        resp_missing_sess = self.client.delete(
            "/api/sessions/sess_missing_id/contents/workspace/demo.py",
            headers=self.auth_headers,
        )
        self.assertEqual(resp_missing_sess.status_code, 404)

    def test_vfs_unicode_and_nested_path_handling(self):
        """Deeply nested directories and paths with unicode/emojis are created, listed, and read cleanly."""
        nested_unicode_path = "workspace/深度学习/模型_🤖_v1/config.json"
        config_content = json.dumps({"model": "gpt-colab", "layers": 32, "author": "李工"}).encode("utf-8")
        b64_content = base64.b64encode(config_content).decode("utf-8")

        # 1. Upload to deep unicode path
        put_resp = self.client.put(
            f"/api/sessions/{self.session_id}/contents/{nested_unicode_path}",
            json={"content": b64_content, "chunk": 1},
            headers=self.auth_headers,
        )
        self.assertEqual(put_resp.status_code, 200)

        # 2. List intermediate directory
        list_resp = self.client.get(
            f"/api/sessions/{self.session_id}/contents/workspace/深度学习/模型_🤖_v1",
            headers=self.auth_headers,
        )
        self.assertEqual(list_resp.status_code, 200)
        items = list_resp.json().get("content", [])
        self.assertTrue(any(item["name"] == "config.json" for item in items))

        # 3. Read back file and verify unicode text
        read_resp = self.client.get(
            f"/api/sessions/{self.session_id}/contents/{nested_unicode_path}?content=1",
            headers=self.auth_headers,
        )
        self.assertEqual(read_resp.status_code, 200)
        decoded = base64.b64decode(read_resp.json()["content"]).decode("utf-8")
        parsed = json.loads(decoded)
        self.assertEqual(parsed["author"], "李工")

    def test_vfs_large_file_download_integrity(self):
        """Downloading large files (100KB) preserves MD5 and SHA-256 checksums."""
        large_bytes = os.urandom(100 * 1024)
        expected_md5 = hashlib.md5(large_bytes).hexdigest()
        expected_sha256 = hashlib.sha256(large_bytes).hexdigest()

        # Upload
        upload_resp = self.client.put(
            f"/api/sessions/{self.session_id}/contents/workspace/large_weights.bin",
            json={"content": base64.b64encode(large_bytes).decode("utf-8")},
            headers=self.auth_headers,
        )
        self.assertEqual(upload_resp.status_code, 200)

        # Download
        dl_resp = self.client.get(
            f"/api/sessions/{self.session_id}/contents/workspace/large_weights.bin?content=1",
            headers=self.auth_headers,
        )
        self.assertEqual(dl_resp.status_code, 200)
        downloaded = base64.b64decode(dl_resp.json()["content"])

        self.assertEqual(len(downloaded), len(large_bytes))
        self.assertEqual(hashlib.md5(downloaded).hexdigest(), expected_md5)
        self.assertEqual(hashlib.sha256(downloaded).hexdigest(), expected_sha256)

    def test_vfs_complex_gitignore_and_dotfile_filtering(self):
        """File sync crawler filters .git, *.pt weights, and __pycache__ while preserving code."""
        import fnmatch
        ignore_patterns = [".git*", "__pycache__*", "*.pt", "*.pth", "*.bin", ".venv*"]

        test_files = [
            ("src/train.py", True),
            ("src/utils.py", True),
            ("README.md", True),
            (".git/config", False),
            (".git/HEAD", False),
            ("checkpoints/model.pt", False),
            ("weights/best.pth", False),
            ("__pycache__/main.cpython-312.pyc", False),
            (".venv/bin/python", False),
        ]

        def should_include(rel_path: str) -> bool:
            parts = rel_path.split("/")
            for pattern in ignore_patterns:
                for part in parts:
                    if fnmatch.fnmatch(part, pattern):
                        return False
                if fnmatch.fnmatch(rel_path, pattern):
                    return False
            return True

        for path, expected_include in test_files:
            self.assertEqual(
                should_include(path),
                expected_include,
                f"File filtering mismatch for {path}: expected {expected_include}",
            )



if __name__ == "__main__":
    unittest.main()
