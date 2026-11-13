"""Comprehensive Test Suite for Zero-Dependency Mock Colab Server Suite.

Verifies:
1. MockGoogleOAuthServer: code exchange, refresh flow, userinfo, revocation, fault injection, dynamic codes.
2. MockControlPlaneServer: CCU info, assignments, two-step assign, two-step unassign, XSSI prefix,
   XSRF token validation, shape rules, concurrency limit (412), quota exhaustion (400), 502 reconciliation.
3. MockRuntimeProxyServer: REST VFS (GET list, PUT base64 chunked, GET download, DELETE),
   WebSocket /colab/ssh (pubkey validation, single-connection lock HTTP 429), WebSocket /colab/tty (ANSI emulator).
4. MockControlHub: Unified lifecycle, cross-server state synchronization, programmatic fault injection.
"""

from __future__ import annotations

import base64
import json
import socket
import unittest
import urllib.error
import urllib.parse
import urllib.request

from backend.tests.mock_colab.mock_control_plane import (
    HIGH_MEM_ACCELERATORS,
    XSSI_PREFIX,
    MockControlPlaneServer,
    MockControlPlaneState,
)
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import (
    MockGoogleOAuthServer,
    MockOAuthAccount,
    MockOAuthState,
)
from backend.tests.mock_colab.mock_runtime_proxy import (
    MockRuntimeProxyApp,
    MockRuntimeProxyServer,
    RFC6455WebSocket,
    VirtualFileSystem,
)


class TestMockGoogleOAuthServer(unittest.TestCase):
    """Test cases for MockGoogleOAuthServer."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockGoogleOAuthServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.reset()

    def _post(self, path: str, data: dict) -> tuple[int, dict]:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(f"{self.server.base_url}{path}", data=body)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path: str, headers: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(f"{self.server.base_url}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_authorization_code_exchange_success(self):
        status, data = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "mock_code_account_1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["access_token"], "ya29.mock_access_token_account_1")
        self.assertEqual(data["refresh_token"], "1//mock_refresh_token_account_1")
        self.assertEqual(data["token_type"], "Bearer")
        self.assertIn("openid", data["scope"])
        self.assertIn("mock_id_token_", data["id_token"])

    def test_refresh_token_flow_success(self):
        status, data = self._post(
            "/token",
            {"grant_type": "refresh_token", "refresh_token": "1//mock_refresh_token_account_1"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["access_token"].startswith("ya29.mock_refreshed_"))
        self.assertEqual(data["token_type"], "Bearer")

    def test_userinfo_retrieval_success(self):
        status, data = self._get(
            "/userinfo",
            headers={"Authorization": "Bearer ya29.mock_access_token_account_1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(data["email"], "colab-dev@gmail.com")
        self.assertTrue(data["email_verified"])
        self.assertEqual(data["name"], "Colab Test Dev")

    def test_userinfo_missing_auth_header(self):
        status, data = self._get("/userinfo")
        self.assertEqual(status, 401)
        self.assertEqual(data["error"]["status"], "UNAUTHENTICATED")

    def test_invalid_auth_code_rejection(self):
        self.server.state.allow_dynamic_codes = False
        status, data = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "non_existent_code"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_grant")

    def test_revoked_account_token_rejection(self):
        status, data = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "mock_code_revoked"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_grant")

    def test_token_revocation_endpoint(self):
        status, _ = self._post("/revoke", {"token": "1//mock_refresh_token_account_1"})
        self.assertEqual(status, 200)
        # Refresh should now fail with 400
        ref_status, ref_data = self._post(
            "/token",
            {"grant_type": "refresh_token", "refresh_token": "1//mock_refresh_token_account_1"},
        )
        self.assertEqual(ref_status, 400)
        self.assertEqual(ref_data["error"], "invalid_grant")

    def test_expired_access_token_userinfo_rejection(self):
        self.server.state.expire_access_token("ya29.mock_access_token_account_1")
        status, data = self._get(
            "/userinfo",
            headers={"Authorization": "Bearer ya29.mock_access_token_account_1"},
        )
        self.assertEqual(status, 401)

    def test_magic_error_injection(self):
        status, data = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "error:invalid_grant"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "invalid_grant")

        status500, data500 = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "error:500"},
        )
        self.assertEqual(status500, 500)

    def test_programmatic_fault_injection(self):
        self.server.state.inject_fault("/token", 503, {"error": "service_unavailable"})
        status, data = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "mock_code_account_1"},
        )
        self.assertEqual(status, 503)
        self.assertEqual(data["error"], "service_unavailable")

        self.server.state.clear_faults()
        status2, _ = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "mock_code_account_1"},
        )
        self.assertEqual(status2, 200)

    def test_dynamic_account_synthesis(self):
        self.server.state.allow_dynamic_codes = True
        status, data = self._post(
            "/token",
            {"grant_type": "authorization_code", "code": "unique_random_code_xyz"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["access_token"].startswith("ya29.mock_access_token_dynamic_"))

    def test_health_check(self):
        status, data = self._get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(data["status"], "healthy")


class TestMockControlPlaneServer(unittest.TestCase):
    """Test cases for MockControlPlaneServer."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockControlPlaneServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.reset()

    def _strip_xssi(self, raw: str) -> dict:
        self.assertTrue(raw.startswith(XSSI_PREFIX), f"Payload missing XSSI prefix: {raw[:30]}")
        return json.loads(raw[len(XSSI_PREFIX) :])

    def _get(self, path: str, headers: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(f"{self.server.url}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, self._strip_xssi(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, self._strip_xssi(e.read().decode("utf-8"))

    def _post(self, path: str, data: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        body = json.dumps(data or {}).encode("utf-8")
        hdrs = headers or {}
        req = urllib.request.Request(f"{self.server.url}{path}", data=body, headers=hdrs)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, self._strip_xssi(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, self._strip_xssi(e.read().decode("utf-8"))

    def test_ccu_info_endpoint(self):
        status, data = self._get("/tun/m/ccu-info?authuser=0")
        self.assertEqual(status, 200)
        self.assertEqual(data["currentBalance"], 100.0)
        self.assertEqual(data["consumptionRateHourly"], 1.5)
        self.assertEqual(data["assignmentsCount"], 0)

    def test_assignments_empty_and_populated(self):
        status, data = self._get("/tun/m/assignments?authuser=0")
        self.assertEqual(status, 200)
        self.assertEqual(data["assignments"], [])

        # Inject orphan
        self.server.state.inject_orphan_assignment(
            endpoint="ep-test-01", accelerator="A100", variant=1, machine_shape=1
        )
        status2, data2 = self._get("/tun/m/assignments?authuser=0")
        self.assertEqual(status2, 200)
        self.assertEqual(len(data2["assignments"]), 1)
        a = data2["assignments"][0]
        self.assertEqual(a["endpoint"], "ep-test-01")
        self.assertEqual(a["accelerator"], "A100")
        self.assertEqual(a["variant"], 1)  # Integer!
        self.assertEqual(a["machineShape"], 1)  # Integer!

    def test_two_step_assign_happy_path(self):
        nbh = "test_nbh_abc123"
        # Step 1: Probe GET /tun/m/assign
        status1, data1 = self._get(f"/tun/m/assign?nbh={nbh}&variant=GPU&accelerator=T4")
        self.assertEqual(status1, 200)
        self.assertEqual(data1["acc"], "T4")
        self.assertEqual(data1["variant"], "GPU")  # String in GET response!
        self.assertTrue("token" in data1)
        xsrf = data1["token"]

        # Step 2: Commit POST /tun/m/assign with X-Goog-Colab-Token
        status2, data2 = self._post(
            f"/tun/m/assign?nbh={nbh}&variant=GPU&accelerator=T4",
            headers={"X-Goog-Colab-Token": xsrf},
        )
        self.assertEqual(status2, 200)
        self.assertEqual(data2["accelerator"], "T4")
        self.assertEqual(data2["variant"], 1)  # Integer in POST response!
        self.assertTrue(data2["endpoint"].startswith("ep-"))
        self.assertTrue(data2["runtimeProxyInfo"]["token"].startswith("proxy-token-"))

    def test_assign_missing_or_invalid_xsrf_token_rejected(self):
        nbh = "test_nbh_noxsrf"
        # POST without header
        status, data = self._post(f"/tun/m/assign?nbh={nbh}&variant=GPU&accelerator=T4")
        self.assertEqual(status, 403)
        self.assertIn("Invalid or missing XSRF token", data["error"])

        # POST with bogus header
        status2, data2 = self._post(
            f"/tun/m/assign?nbh={nbh}&variant=GPU&accelerator=T4",
            headers={"X-Goog-Colab-Token": "bogus_token"},
        )
        self.assertEqual(status2, 403)

    def test_high_ram_shape_rules(self):
        # L4, V5E1, V6E1 must reject shape=hm with HTTP 400
        for acc in HIGH_MEM_ACCELERATORS:
            status, data = self._get(f"/tun/m/assign?nbh=nbh_{acc}&accelerator={acc}&shape=hm")
            self.assertEqual(status, 400, f"{acc} with shape=hm should return 400")

        # T4 with shape=hm is valid
        status_t4, data_t4 = self._get("/tun/m/assign?nbh=nbh_t4&accelerator=T4&shape=hm")
        self.assertEqual(status_t4, 200)

        # L4 without shape=hm is valid
        status_l4, data_l4 = self._get("/tun/m/assign?nbh=nbh_l4&accelerator=L4")
        self.assertEqual(status_l4, 200)

    def test_case_insensitive_high_ram_shape_rules(self):
        """Verify that lowercase/mixed-case high-RAM accelerators (l4, v5e1, v6e1) reject shape=hm with HTTP 400.

        Also verifies:
        - GET /tun/m/assign returns HTTP 400 with 'Invalid parameter: shape=hm is not supported'
        - POST /tun/m/assign returns HTTP 400 with 'Invalid shape=hm'
        - Lowercase accelerators without shape=hm succeed and assign machineShape=1 (HIGH_RAM)
        - Assignment listings reflect canonical uppercase accelerator names and machineShape=1.
        """
        lowercase_accs = ["l4", "v5e1", "v6e1"]

        for acc in lowercase_accs:
            # 1. Probe GET with shape=hm must return HTTP 400
            status_get, data_get = self._get(
                f"/tun/m/assign?nbh=nbh_lower_{acc}&accelerator={acc}&shape=hm"
            )
            self.assertEqual(
                status_get,
                400,
                f"GET /tun/m/assign with accelerator={acc} and shape=hm must return 400",
            )
            self.assertIn("not supported", data_get.get("error", "").lower())

            # Also check uppercase shape=HM
            status_get_hm, data_get_hm = self._get(
                f"/tun/m/assign?nbh=nbh_lower_HM_{acc}&accelerator={acc}&shape=HM"
            )
            self.assertEqual(
                status_get_hm,
                400,
                f"GET /tun/m/assign with accelerator={acc} and shape=HM must return 400",
            )

            # 2. Probe GET without shape=hm must succeed with HTTP 200 and return XSRF token
            status_ok, data_ok = self._get(
                f"/tun/m/assign?nbh=nbh_ok_{acc}&accelerator={acc}"
            )
            self.assertEqual(
                status_ok,
                200,
                f"GET /tun/m/assign with accelerator={acc} (no shape) must succeed with 200",
            )
            xsrf = data_ok["token"]

            # 3. Commit POST with shape=hm must return HTTP 400
            status_post_bad, data_post_bad = self._post(
                f"/tun/m/assign?nbh=nbh_post_bad_{acc}&accelerator={acc}&shape=hm",
                headers={"X-Goog-Colab-Token": xsrf},
            )
            self.assertEqual(
                status_post_bad,
                400,
                f"POST /tun/m/assign with accelerator={acc} and shape=hm must return 400",
            )
            self.assertIn("invalid shape=hm", data_post_bad.get("error", "").lower())

            # 4. Commit POST without shape=hm must succeed with HTTP 200
            status_get2, data_get2 = self._get(
                f"/tun/m/assign?nbh=nbh_commit_{acc}&accelerator={acc}"
            )
            self.assertEqual(status_get2, 200)
            xsrf2 = data_get2["token"]

            status_post_ok, data_post_ok = self._post(
                f"/tun/m/assign?nbh=nbh_commit_{acc}&accelerator={acc}",
                headers={"X-Goog-Colab-Token": xsrf2},
            )
            self.assertEqual(status_post_ok, 200)
            self.assertTrue(data_post_ok["endpoint"].startswith("ep-"))

        # 5. Verify assignments listing reflects machineShape=1 for all high-RAM accelerators
        status_list, data_list = self._get("/tun/m/assignments?authuser=0")
        self.assertEqual(status_list, 200)
        assignments = data_list.get("assignments", [])
        self.assertEqual(len(assignments), len(lowercase_accs))
        for a in assignments:
            self.assertEqual(
                a["machineShape"],
                1,
                f"Accelerator {a.get('accelerator')} must have machineShape=1 (HIGH_RAM)",
            )
            self.assertIn(a["accelerator"], HIGH_MEM_ACCELERATORS)

    def test_quota_exhaustion_returns_http_400(self):
        self.server.state.trigger_quota_exhaustion("A100")
        status, data = self._get("/tun/m/assign?nbh=nbh_a100&accelerator=A100")
        self.assertEqual(status, 400)
        self.assertIn("quota", data["error"].lower())

        self.server.state.clear_quota_exhaustion()
        status2, data2 = self._get("/tun/m/assign?nbh=nbh_a100&accelerator=A100")
        self.assertEqual(status2, 200)

    def test_concurrency_limit_returns_http_412(self):
        self.server.state.trigger_concurrency_limit(max_allowed=1)
        # Create 1st assignment
        self.server.state.inject_orphan_assignment("ep-active-1")

        # Attempt 2nd assignment
        status1, data1 = self._get("/tun/m/assign?nbh=nbh_2&accelerator=T4")
        self.assertEqual(status1, 200)
        xsrf = data1["token"]

        status2, data2 = self._post(
            "/tun/m/assign?nbh=nbh_2&accelerator=T4",
            headers={"X-Goog-Colab-Token": xsrf},
        )
        self.assertEqual(status2, 412)
        self.assertIn("Too many active assignments", data2["error"])

    def test_502_reconciliation_flow(self):
        nbh = "nbh_502_reconcile"
        self.server.state.trigger_transient_error(count=1, status_code=502, reconcile=True)

        # Step 1: Probe GET
        _, data1 = self._get(f"/tun/m/assign?nbh={nbh}&accelerator=T4")
        xsrf = data1["token"]

        # Step 2: POST returns 502
        status2, _ = self._post(
            f"/tun/m/assign?nbh={nbh}&accelerator=T4",
            headers={"X-Goog-Colab-Token": xsrf},
        )
        self.assertEqual(status2, 502)

        # Step 3: Reconcile probe GET returns existing assignment without duplicate POST
        status3, data3 = self._get(f"/tun/m/assign?nbh={nbh}&accelerator=T4")
        self.assertEqual(status3, 200)
        self.assertTrue("endpoint" in data3)
        self.assertTrue("runtimeProxyInfo" in data3)

    def test_two_step_unassign_happy_path(self):
        self.server.state.inject_orphan_assignment("ep-to-delete")

        # Step 1: GET token
        status1, data1 = self._get("/tun/m/unassign/ep-to-delete")
        self.assertEqual(status1, 200)
        self.assertTrue("token" in data1)
        xsrf = data1["token"]

        # Step 2: POST token
        status2, data2 = self._post(
            "/tun/m/unassign/ep-to-delete",
            headers={"X-Goog-Colab-Token": xsrf},
        )
        self.assertEqual(status2, 200)
        self.assertEqual(data2, {})

        # Confirm deleted
        status3, _ = self._get("/tun/m/unassign/ep-to-delete")
        self.assertEqual(status3, 404)

    def test_credentials_propagation_endpoint(self):
        status1, data1 = self._get("/tun/m/credentials-propagation/ep-1")
        self.assertEqual(status1, 200)
        self.assertTrue("token" in data1)

        # dryrun=true
        status2, data2 = self._post("/tun/m/credentials-propagation/ep-1?dryrun=true")
        self.assertEqual(status2, 200)
        self.assertTrue(data2["success"])

        # dryrun=false
        status3, data3 = self._post("/tun/m/credentials-propagation/ep-1?dryrun=false")
        self.assertEqual(status3, 200)
        self.assertEqual(data3["status"], "propagated")


class TestMockRuntimeProxyServer(unittest.TestCase):
    """Test cases for MockRuntimeProxyServer (REST VFS, WebSocket TTY, and SSH 429 lock)."""

    @classmethod
    def setUpClass(cls):
        cls.server = MockRuntimeProxyServer()
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.reset()
        self.endpoint_id = "ep-test-vfs"
        self.token = "token-secret-xyz"
        self.server.app.register_endpoint(self.endpoint_id, self.token)

    def _url(self, subpath: str, params: dict | None = None) -> str:
        q = {"colab-runtime-proxy-token": self.token}
        if params:
            q.update(params)
        qs = urllib.parse.urlencode(q)
        return f"{self.server.url}/tun/m/{self.endpoint_id}/api/contents{subpath}?{qs}"

    def test_rest_vfs_directory_listing(self):
        req = urllib.request.Request(self._url("/"))
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["type"], "directory")
            names = [c["name"] for c in data["content"]]
            self.assertIn("sample_data", names)
            self.assertIn("workspace", names)

    def test_rest_vfs_file_upload_and_download(self):
        content_bytes = b"print('Hello Mock Colab!')\n"
        b64_content = base64.b64encode(content_bytes).decode("ascii")

        # PUT upload
        upload_payload = json.dumps({
            "name": "hello.py",
            "path": "workspace/hello.py",
            "type": "file",
            "format": "base64",
            "content": b64_content,
            "chunk": 1,
        }).encode("utf-8")

        req = urllib.request.Request(
            self._url("/workspace/hello.py"),
            data=upload_payload,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)

        # GET download (content=1)
        req_dl = urllib.request.Request(self._url("/workspace/hello.py", {"content": "1"}))
        with urllib.request.urlopen(req_dl) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode())
            self.assertEqual(data["format"], "base64")
            self.assertEqual(base64.b64decode(data["content"]), content_bytes)

    def test_rest_vfs_chunked_upload(self):
        chunk1 = base64.b64encode(b"Part 1 ").decode()
        chunk2 = base64.b64encode(b"Part 2\n").decode()

        # Chunk 1
        p1 = json.dumps({"path": "workspace/chunked.txt", "content": chunk1, "chunk": 1}).encode()
        req1 = urllib.request.Request(self._url("/workspace/chunked.txt"), data=p1, method="PUT")
        with urllib.request.urlopen(req1) as resp:
            self.assertEqual(resp.status, 200)

        # Chunk 2
        p2 = json.dumps({"path": "workspace/chunked.txt", "content": chunk2, "chunk": 2}).encode()
        req2 = urllib.request.Request(self._url("/workspace/chunked.txt"), data=p2, method="PUT")
        with urllib.request.urlopen(req2) as resp:
            self.assertEqual(resp.status, 200)

        # Verify assembled download
        req_dl = urllib.request.Request(self._url("/workspace/chunked.txt", {"content": "1"}))
        with urllib.request.urlopen(req_dl) as resp:
            data = json.loads(resp.read().decode())
            self.assertEqual(base64.b64decode(data["content"]), b"Part 1 Part 2\n")

    def test_rest_vfs_delete(self):
        # Create file first
        p = json.dumps({"path": "workspace/to_del.txt", "content": base64.b64encode(b"del").decode(), "chunk": 1}).encode()
        urllib.request.urlopen(urllib.request.Request(self._url("/workspace/to_del.txt"), data=p, method="PUT"))

        # DELETE
        req_del = urllib.request.Request(self._url("/workspace/to_del.txt"), method="DELETE")
        with urllib.request.urlopen(req_del) as resp:
            self.assertEqual(resp.status, 204)

        # Confirm 404
        try:
            urllib.request.urlopen(urllib.request.Request(self._url("/workspace/to_del.txt")))
            self.fail("Expected HTTP 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_rest_vfs_path_traversal_rejection(self):
        try:
            req = urllib.request.Request(self._url("/../../etc/passwd"))
            urllib.request.urlopen(req)
            self.fail("Expected HTTP 403")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_rest_vfs_unauthorized_token_rejection(self):
        url = f"{self.server.url}/tun/m/{self.endpoint_id}/api/contents/?colab-runtime-proxy-token=invalid_token"
        try:
            urllib.request.urlopen(urllib.request.Request(url))
            self.fail("Expected HTTP 401")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def _read_http_response(self, sock: socket.socket) -> str:
        sock.settimeout(2.0)
        buf = bytearray()
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\r\n\r\n" in buf:
                    header_part, body_part = bytes(buf).split(b"\r\n\r\n", 1)
                    clen = None
                    for line in header_part.split(b"\r\n"):
                        if line.lower().startswith(b"content-length:"):
                            clen = int(line.split(b":", 1)[1].strip())
                    if clen is not None:
                        if len(body_part) >= clen:
                            break
                    else:
                        break
            except socket.timeout:
                break
        return bytes(buf).decode("utf-8", errors="ignore")

    def test_ssh_websocket_pubkey_validation(self):
        host = self.server.host
        port = self.server.port

        # Case 1: Missing pubkey -> HTTP 400
        sock = socket.create_connection((host, port))
        req = (
            f"GET /tun/m/{self.endpoint_id}/colab/ssh?colab-runtime-proxy-token={self.token} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(req.encode())
        res = self._read_http_response(sock)
        sock.close()
        self.assertIn("400 Bad Request", res)
        self.assertIn("missing pubkey", res)

        # Case 2: ssh-rsa key -> HTTP 400
        sock2 = socket.create_connection((host, port))
        req_rsa = (
            f"GET /tun/m/{self.endpoint_id}/colab/ssh?colab-runtime-proxy-token={self.token} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "X-Colab-Ssh-Pubkey: ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQC... user@host\r\n\r\n"
        )
        sock2.sendall(req_rsa.encode())
        res2 = self._read_http_response(sock2)
        sock2.close()
        self.assertIn("400 Bad Request", res2)
        self.assertIn("unsupported key type", res2)

    def test_ssh_websocket_single_connection_lock_http_429(self):
        host = self.server.host
        port = self.server.port
        req_ed25519 = (
            f"GET /tun/m/{self.endpoint_id}/colab/ssh?colab-runtime-proxy-token={self.token} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "X-Colab-Ssh-Pubkey: ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@colab\r\n\r\n"
        )

        # Connect 1st client -> HTTP 101 Switching Protocols
        sock1 = socket.create_connection((host, port))
        sock1.sendall(req_ed25519.encode())
        res1 = sock1.recv(1024).decode(errors="ignore")
        self.assertIn("101 Switching Protocols", res1)

        # Connect 2nd client -> MUST be rejected with HTTP 429 Too Many Requests
        sock2 = socket.create_connection((host, port))
        sock2.sendall(req_ed25519.encode())
        res2 = self._read_http_response(sock2)
        sock2.close()
        self.assertIn("429 Too Many Requests", res2)
        self.assertIn("Already-active SSH session", res2)

        # Disconnect 1st client -> Re-trying 2nd client now succeeds!
        sock1.close()
        # Allow short margin for server thread cleanup
        import time
        time.sleep(0.1)

        sock3 = socket.create_connection((host, port))
        sock3.sendall(req_ed25519.encode())
        res3 = sock3.recv(1024).decode(errors="ignore")
        sock3.close()
        self.assertIn("101 Switching Protocols", res3)

    def test_tty_websocket_handshake_and_commands(self):
        host = self.server.host
        port = self.server.port

        sock = socket.create_connection((host, port))
        handshake = (
            f"GET /tun/m/{self.endpoint_id}/colab/tty?colab-runtime-proxy-token={self.token} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(handshake.encode())
        res = sock.recv(1024).decode(errors="ignore")
        self.assertIn("101 Switching Protocols", res)

        # Send resize frame: {"cols": 120, "rows": 40}
        # Masked client frame builder
        def send_text_frame(s, text):
            payload = text.encode("utf-8")
            header = bytearray([0x81, 0x80 | len(payload)])  # Text opcode 0x1, masked
            mask = b"\x01\x02\x03\x04"
            masked_payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
            s.sendall(bytes(header) + mask + masked_payload)

        send_text_frame(sock, json.dumps({"cols": 120, "rows": 40}))

        # Send command: "whoami\n"
        send_text_frame(sock, json.dumps({"data": "whoami\n"}))

        # Receive responses
        buf = sock.recv(2048)
        self.assertTrue(len(buf) > 0)
        sock.close()

    def test_multi_session_concurrent_websockets(self):
        """Verify that concurrent WebSocket connections (/colab/ssh and /colab/tty) across
        distinct active sessions succeed via token routing without 401 Unauthorized.
        """
        host = self.server.host
        port = self.server.port

        # Register two distinct active sessions
        sess1_ep = "ep-session-1"
        sess1_token = "proxy-tok-session-1-alpha"
        sess2_ep = "ep-session-2"
        sess2_token = "proxy-tok-session-2-beta"

        self.server.app.register_endpoint(sess1_ep, sess1_token)
        self.server.app.register_endpoint(sess2_ep, sess2_token)

        open_sockets = []

        def _connect_ws(path: str, headers: dict | None = None) -> tuple[socket.socket, str]:
            sock = socket.create_connection((host, port))
            open_sockets.append(sock)
            hdrs = [
                f"GET {path} HTTP/1.1",
                f"Host: {host}:{port}",
                "Upgrade: websocket",
                "Connection: Upgrade",
                "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
                "Sec-WebSocket-Version: 13",
            ]
            if headers:
                for k, v in headers.items():
                    hdrs.append(f"{k}: {v}")
            req = "\r\n".join(hdrs) + "\r\n\r\n"
            sock.sendall(req.encode("utf-8"))
            resp = self._read_http_response(sock)
            return sock, resp

        try:
            # 1. Connect Session 1 SSH (native colab cli path: /colab/ssh?colab-runtime-proxy-token=...)
            s1_ssh, r1 = _connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={sess1_token}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user1@colab"},
            )
            self.assertIn("101 Switching Protocols", r1, f"Session 1 SSH failed: {r1}")

            # 2. Concurrently connect Session 2 SSH (MUST succeed with 101, not 401 or 429)
            s2_ssh, r2 = _connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={sess2_token}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user2@colab"},
            )
            self.assertIn("101 Switching Protocols", r2, f"Session 2 SSH failed: {r2}")

            # 3. Concurrently connect Session 1 TTY (native colab cli path: /colab/tty?colab-runtime-proxy-token=...)
            s1_tty, r3 = _connect_ws(
                f"/colab/tty?colab-runtime-proxy-token={sess1_token}"
            )
            self.assertIn("101 Switching Protocols", r3, f"Session 1 TTY failed: {r3}")

            # 4. Concurrently connect Session 2 TTY (MUST succeed with 101, not 401)
            s2_tty, r4 = _connect_ws(
                f"/colab/tty?colab-runtime-proxy-token={sess2_token}"
            )
            self.assertIn("101 Switching Protocols", r4, f"Session 2 TTY failed: {r4}")

            # 5. Verify intra-session SSH lock: 2nd concurrent connection to Session 1 MUST return HTTP 429
            s1_ssh_dup, r5 = _connect_ws(
                f"/colab/ssh?colab-runtime-proxy-token={sess1_token}",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... dup@colab"},
            )
            self.assertIn("429 Too Many Requests", r5)
            self.assertIn("Already-active SSH session", r5)

            # 6. Verify unknown token returns HTTP 401
            s_unauth, r6 = _connect_ws(
                "/colab/ssh?colab-runtime-proxy-token=invalid_unknown_token",
                {"X-Colab-Ssh-Pubkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... unauth@colab"},
            )
            self.assertIn("401 Unauthorized", r6)

            # 7. Verify multi-session REST VFS also resolves by token
            vfs_url = f"{self.server.url}/api/contents/?colab-runtime-proxy-token={sess2_token}"
            with urllib.request.urlopen(vfs_url) as resp:
                self.assertEqual(resp.status, 200)
                vfs_data = json.loads(resp.read().decode())
                self.assertEqual(vfs_data["type"], "directory")

        finally:
            for s in open_sockets:
                try:
                    s.close()
                except Exception:
                    pass


class TestMockControlHub(unittest.TestCase):
    """Test cases for MockControlHub cross-server coordinator and fault injector."""

    def test_hub_lifecycle_and_coordination(self):
        with MockControlHub() as hub:
            self.assertTrue(hub.is_running)
            self.assertIn("oauth", hub.urls)
            self.assertIn("control_plane", hub.urls)
            self.assertIn("runtime_proxy", hub.urls)

            # Assign instance via Control Plane
            cp_url = hub.urls["control_plane"]
            nbh = "hub_test_nbh"

            # Probe GET
            req1 = urllib.request.Request(f"{cp_url}/tun/m/assign?nbh={nbh}&variant=GPU&accelerator=T4")
            with urllib.request.urlopen(req1) as resp:
                raw = resp.read().decode()
                token_data = json.loads(raw[len(XSSI_PREFIX) :])
                xsrf = token_data["token"]

            # Commit POST
            req2 = urllib.request.Request(
                f"{cp_url}/tun/m/assign?nbh={nbh}&variant=GPU&accelerator=T4",
                data=b"{}",
                headers={"X-Goog-Colab-Token": xsrf},
            )
            with urllib.request.urlopen(req2) as resp:
                raw2 = resp.read().decode()
                assign_data = json.loads(raw2[len(XSSI_PREFIX) :])
                endpoint = assign_data["endpoint"]
                proxy_token = assign_data["runtimeProxyInfo"]["token"]

            # Verify that Runtime Proxy automatically registered the endpoint!
            rp_ep = hub.runtime_proxy_server.app.get_endpoint(endpoint)
            self.assertIsNotNone(rp_ep)
            self.assertEqual(rp_ep.token, proxy_token)

            # Access VFS via Runtime Proxy URL with proxy token
            rp_url = hub.urls["runtime_proxy"]
            vfs_req = urllib.request.Request(
                f"{rp_url}/tun/m/{endpoint}/api/contents/?colab-runtime-proxy-token={proxy_token}"
            )
            with urllib.request.urlopen(vfs_req) as resp:
                self.assertEqual(resp.status, 200)

            # Test Fault Injection: trigger_ssh_collision
            hub.trigger_ssh_collision(endpoint)
            self.assertTrue(rp_ep.active_ssh_connection)
            hub.release_ssh_collision(endpoint)
            self.assertFalse(rp_ep.active_ssh_connection)

            # Test Fault Injection: expire_proxy_token
            hub.expire_proxy_token(endpoint)
            try:
                urllib.request.urlopen(vfs_req)
                self.fail("Expected HTTP 401 after expiring proxy token")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 401)

            # Test Unassign coordination
            req_un1 = urllib.request.Request(f"{cp_url}/tun/m/unassign/{endpoint}")
            with urllib.request.urlopen(req_un1) as resp:
                un_xsrf = json.loads(resp.read().decode()[len(XSSI_PREFIX) :])["token"]

            req_un2 = urllib.request.Request(
                f"{cp_url}/tun/m/unassign/{endpoint}",
                data=b"{}",
                headers={"X-Goog-Colab-Token": un_xsrf},
            )
            with urllib.request.urlopen(req_un2) as resp:
                self.assertEqual(resp.status, 200)

            # Verify that Runtime Proxy automatically unregistered the endpoint!
            self.assertIsNone(hub.runtime_proxy_server.app.get_endpoint(endpoint))

        # After exiting context, hub stopped
        self.assertFalse(hub.is_running)


class TestMockControlPlaneASGI(unittest.TestCase):
    """Test ASGI 3.0 interface for MockControlPlane."""

    def test_asgi_get_ccu_info(self):
        import asyncio
        from backend.tests.mock_colab.mock_control_plane import create_asgi_app

        app = create_asgi_app()
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/tun/m/ccu-info",
            "query_string": b"authuser=0",
            "headers": [(b"accept", b"application/json")],
        }

        messages = []

        async def mock_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def mock_send(msg):
            messages.append(msg)

        async def runner():
            await app(scope, mock_receive, mock_send)

        asyncio.run(runner())

        start_msg = next(m for m in messages if m["type"] == "http.response.start")
        body_msg = next(m for m in messages if m["type"] == "http.response.body")

        self.assertEqual(start_msg["status"], 200)
        raw_body = body_msg["body"].decode("utf-8")
        self.assertTrue(raw_body.startswith(XSSI_PREFIX))
        data = json.loads(raw_body[len(XSSI_PREFIX) :])
        self.assertEqual(data["currentBalance"], 100.0)


class TestNativeColabCliIntegration(unittest.TestCase):
    """Verifies end-to-end compatibility with installed google-colab-cli."""

    def test_colab_cli_client_lifecycle(self):
        try:
            import requests
            import uuid
            from colab_cli.client import Client, Prod, Accelerator, Variant
        except ImportError:
            self.skipTest("google-colab-cli or requests not installed")

        with MockControlPlaneServer() as s:
            c = Client(Prod(domain=s.url, api=s.url), session=requests.Session())
            ccu = c.get_consumption_user_info()
            self.assertEqual(ccu.paid_compute_units_balance, 100.0)

            nbh = uuid.uuid4()
            assign = c.assign(nbh, Variant.GPU, Accelerator.T4)
            self.assertTrue(assign.endpoint.startswith("ep-"))

            assignments = c.list_assignments()
            self.assertEqual(len(assignments), 1)
            self.assertEqual(assignments[0].endpoint, assign.endpoint)

            c.unassign(assign.endpoint)
            assignments_after = c.list_assignments()
            self.assertEqual(len(assignments_after), 0)


class TestAsyncContextManagers(unittest.IsolatedAsyncioTestCase):
    """Verifies asynchronous context manager interfaces across all mock servers."""

    async def test_async_context_managers(self):
        async with MockGoogleOAuthServer() as oauth:
            self.assertTrue(oauth.base_url.startswith("http://127.0.0.1:"))

        async with MockControlPlaneServer() as cp:
            self.assertTrue(cp.url.startswith("http://127.0.0.1:"))

        async with MockRuntimeProxyServer() as rp:
            self.assertTrue(rp.url.startswith("http://127.0.0.1:"))

        async with MockControlHub() as hub:
            self.assertTrue(hub.is_running)


if __name__ == "__main__":
    unittest.main()
