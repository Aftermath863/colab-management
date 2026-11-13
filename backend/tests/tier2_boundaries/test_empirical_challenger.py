"""Empirical Challenger Stress Tests for Milestone 1.

Rigorously verifies Colab Protocol Invariants, WebSocket TTY bidirectional framing,
and edge-case boundary conditions:
1. XSSI prefix stripping and JSON parsing under adversarial payloads.
2. UUID transformation to 44-character web-safe strings (property testing).
3. High-RAM Shape rules: omitting shape=hm for L4, V5E1, V6E1; passing for T4, A100.
4. Two-step assign handshake (GET probe -> POST commit with X-Goog-Colab-Token).
5. Two-step unassign handshake (GET probe -> POST commit with X-Goog-Colab-Token).
6. Direct Colab Web Notebook URL generation format and encoding invariants.
7. Terminal WebSocket TTY bi-directional JSON framing (data, resize, errors).
"""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import unittest
import urllib.parse
import uuid
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    HIGH_MEM_ONLY_ACCELERATORS,
    SessionCreateRequest,
)
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services.colab_client import (
    COLAB_CLIENT_AGENT_HEADER,
    COLAB_CLIENT_AGENT_VALUE,
    COLAB_XSRF_TOKEN_HEADER,
    XSSI_PREFIX,
    ColabClient,
    ColabClientError,
    QuotaExceededError,
    TooManyAssignmentsError,
    generate_colab_notebook_url,
    strip_xssi_prefix,
    uuid_to_web_safe_base64,
)
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestEmpiricalChallenger(unittest.TestCase):
    """Adversarial and empirical protocol stress testing suite."""

    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        cls.orig_control_url = settings.CONTROL_PLANE_URL
        cls.orig_token_url = settings.OAUTH_TOKEN_URL
        cls.orig_userinfo_url = settings.OAUTH_USERINFO_URL
        cls.orig_admin_pass = settings.ADMIN_PASSWORD

        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]
        settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
        settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri
        settings.ADMIN_PASSWORD = "test_challenger_secret"

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        settings.OAUTH_TOKEN_URL = cls.orig_token_url
        settings.OAUTH_USERINFO_URL = cls.orig_userinfo_url
        settings.ADMIN_PASSWORD = cls.orig_admin_pass
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "challenger_test.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.client = ColabClient(base_url=settings.CONTROL_PLANE_URL)
        self.test_access_token = "ya29.challenger_mock_token"

        # Register mock account in OAuth mock and DB
        self.account_id = "acc_challenger_1"
        self.email = "challenger.test@gmail.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Challenger Tester",
            sub="sub_challenger_99",
            code="code_challenger",
            access_token=self.test_access_token,
            refresh_token="1//mock_refresh_challenger",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": self.test_access_token,
            "refresh_token": "1//mock_refresh_challenger",
            "expires_at": 9999999999,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, priority, status)
                VALUES (?, ?, 'Challenger Tester', ?, 150.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.fastapi_client = TestClient(app)
        login_resp = self.fastapi_client.post("/api/auth/login", json={"password": settings.ADMIN_PASSWORD})
        self.assertEqual(login_resp.status_code, 200)
        self.jwt_token = login_resp.json()["token"]
        self.auth_headers = {"Authorization": f"Bearer {self.jwt_token}"}

    def tearDown(self):
        self.tmpdir.cleanup()

    # =========================================================================
    # 1. XSSI Prefix Stripping & JSON Parsing Stress Testing
    # =========================================================================

    def test_xssi_prefix_stripping_and_parsing(self):
        """Stress-test strip_xssi_prefix and JSON parsing with edge cases."""
        # 1. Nominal Google JSON vulnerability protection prefix
        raw = ")]}'\n{\"status\": \"ok\", \"count\": 42}"
        self.assertEqual(strip_xssi_prefix(raw), "{\"status\": \"ok\", \"count\": 42}")

        # 2. Array payload with XSSI prefix
        raw_list = ")]}'\n[{\"endpoint\": \"ep-1\"}, {\"endpoint\": \"ep-2\"}]"
        self.assertEqual(strip_xssi_prefix(raw_list), "[{\"endpoint\": \"ep-1\"}, {\"endpoint\": \"ep-2\"}]")
        self.assertEqual(json.loads(strip_xssi_prefix(raw_list)), [{"endpoint": "ep-1"}, {"endpoint": "ep-2"}])

        # 3. Payload without XSSI prefix (should remain unchanged)
        plain = "{\"plain\": true}"
        self.assertEqual(strip_xssi_prefix(plain), plain)

        # 4. Whitespace, newlines, empty bodies
        self.assertEqual(strip_xssi_prefix(")]}'\n"), "")
        self.assertEqual(strip_xssi_prefix(")]}'\n   \n\t"), "   \n\t")
        self.assertEqual(strip_xssi_prefix(""), "")

        # 5. Unicode, escaped quotes, emojis inside XSSI protected payload
        unicode_payload = r")]}'" + "\n" + r'{"unicode": "🚀 深度学习 \t\r\n\"quoted\""}'
        cleaned = strip_xssi_prefix(unicode_payload)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["unicode"], "🚀 深度学习 \t\r\n\"quoted\"")

        # 6. ColabClient._parse_response behavior
        class FakeResponse:
            def __init__(self, text: str, status_code: int = 200):
                self.text = text
                self.status_code = status_code

        # Empty body returns empty dict
        self.assertEqual(self.client._parse_response(FakeResponse(")]}'\n")), {})
        self.assertEqual(self.client._parse_response(FakeResponse("   ")), {})

        # Malformed JSON raises ColabClientError with status code
        with self.assertRaises(ColabClientError) as cm:
            self.client._parse_response(FakeResponse(")]}'\n{malformed: json!}", status_code=500))
        self.assertEqual(cm.exception.status_code, 500)
        self.assertIn("malformed: json!", cm.exception.response_body)

    # =========================================================================
    # 2. UUID Transformation to 44-Character Web-Safe String
    # =========================================================================

    def test_uuid_transformation_properties(self):
        """Property-based validation that UUID is strictly transformed to 44 chars."""
        # 1. Deterministic known test vectors
        fixed_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        out1 = uuid_to_web_safe_base64(fixed_uuid)
        self.assertEqual(out1, "12345678_1234_5678_1234_567812345678........")
        self.assertEqual(len(out1), 44)

        # String input representation
        out2 = uuid_to_web_safe_base64("c305d233-0187-4184-a128-4ef74c431478")
        self.assertEqual(out2, "c305d233_0187_4184_a128_4ef74c431478........")
        self.assertEqual(len(out2), 44)

        # 2. Property testing across 500 randomized UUIDs
        for _ in range(500):
            test_u = uuid.uuid4()
            res = uuid_to_web_safe_base64(test_u)

            # Invariant: Must be exactly 44 characters
            self.assertEqual(len(res), 44, f"Failed length 44 invariant for {test_u}")

            # Invariant: Must not contain hyphens
            self.assertNotIn("-", res, f"Hyphen found in transformed UUID: {res}")

            # Invariant: Must contain exactly 4 underscores replacing hyphens
            self.assertEqual(res.count("_"), 4)

            # Invariant: Must be padded with exactly 8 dots at the end
            self.assertTrue(res.endswith("........"), f"Expected 8 dot padding, got: {res[-10:]}")

            # Invariant: Characters must belong to standard web-safe set [0-9a-fA-F_.]
            self.assertTrue(re.match(r"^[0-9a-fA-F_\.]+$", res), f"Invalid characters in: {res}")

    # =========================================================================
    # 3. High-RAM Shape Rules (L4, V5E1, V6E1 vs T4, A100)
    # =========================================================================

    def test_high_ram_shape_rules_on_wire(self):
        """Verify shape=hm is strictly omitted for L4, V5E1, V6E1 and passed for T4, A100."""
        # Accelerators that must NEVER include shape=hm
        forbidden_hm_accelerators = ["L4", "V5E1", "V6E1", "l4", "v5e1", "v6e1"]

        for acc in forbidden_hm_accelerators:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())

            # Even if caller passes shape="hm", colab_client must strictly omit shape=hm
            res = self.client.assign(
                access_token=self.test_access_token,
                notebook_hash=nbh,
                accelerator=acc,
                shape="hm",
            )
            self.assertIn("endpoint", res)

            # Inspect all recorded requests sent to mock control plane
            reqs = self.hub.control_plane.state.request_log
            self.assertGreaterEqual(len(reqs), 2)
            for req in reqs:
                if req["path"] == "/tun/m/assign":
                    # Invariant: 'shape' MUST NOT be in query parameters
                    self.assertNotIn(
                        "shape",
                        req["query"],
                        f"shape=hm was erroneously sent for forbidden high-RAM accelerator {acc} in {req}",
                    )

        # Accelerators that MUST include shape=hm when requested
        allowed_hm_accelerators = ["T4", "A100", "H100", "G4"]

        for acc in allowed_hm_accelerators:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())

            res = self.client.assign(
                access_token=self.test_access_token,
                notebook_hash=nbh,
                accelerator=acc,
                shape="hm",
            )
            self.assertIn("endpoint", res)

            reqs = self.hub.control_plane.state.request_log
            self.assertGreaterEqual(len(reqs), 2)
            for req in reqs:
                if req["path"] == "/tun/m/assign":
                    # Invariant: 'shape' MUST be present with value ['hm']
                    self.assertIn(
                        "shape",
                        req["query"],
                        f"shape=hm was missing for supported accelerator {acc} in {req}",
                    )
                    self.assertEqual(req["query"]["shape"], ["hm"])

        # Accelerators with shape="standard" must NEVER include shape parameter
        for acc in ["T4", "L4", "A100", "V5E1"]:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())

            self.client.assign(
                access_token=self.test_access_token,
                notebook_hash=nbh,
                accelerator=acc,
                shape="standard",
            )
            reqs = self.hub.control_plane.state.request_log
            for req in reqs:
                if req["path"] == "/tun/m/assign":
                    self.assertNotIn("shape", req["query"])

    def test_session_create_request_model_coercion(self):
        """Verify SessionCreateRequest model validator sets shape='hm' for High-RAM-only L4/V5E1/V6E1."""
        for acc in HIGH_MEM_ONLY_ACCELERATORS:
            req_hm = SessionCreateRequest(name="test-shape", accelerator=acc, shape="hm")
            self.assertEqual(req_hm.shape, "hm", f"Model should retain hm for {acc}")
            req_std = SessionCreateRequest(name="test-shape-std", accelerator=acc, shape="standard")
            self.assertEqual(req_std.shape, "hm", f"Model failed to coerce shape='standard' to 'hm' for {acc}")

        req_cpu = SessionCreateRequest(name="test-shape", accelerator="CPU", shape="hm")
        self.assertEqual(req_cpu.shape, "hm")

        req_t4 = SessionCreateRequest(name="test-shape", accelerator="T4", shape="hm")
        self.assertEqual(req_t4.shape, "hm")

        req_a100 = SessionCreateRequest(name="test-shape", accelerator="A100", shape="hm")
        self.assertEqual(req_a100.shape, "hm")

    # =========================================================================
    # 4. Assign Handshake Invariants (GET probe -> POST commit with XSRF)
    # =========================================================================

    def test_assign_handshake_two_phase_protocol(self):
        """Verify strict two-phase assign handshake protocol and headers."""
        self.hub.control_plane.state.request_log.clear()
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        result = self.client.assign(
            access_token=self.test_access_token,
            notebook_hash=nbh,
            accelerator="T4",
            shape="standard",
        )
        self.assertIn("endpoint", result)

        log = self.hub.control_plane.state.request_log
        self.assertEqual(len(log), 2)

        # Step 1: Probe GET
        req_get = log[0]
        self.assertEqual(req_get["method"], "GET")
        self.assertEqual(req_get["path"], "/tun/m/assign")
        self.assertEqual(req_get["query"]["nbh"], [nbh])
        self.assertEqual(req_get["query"]["accelerator"], ["T4"])
        self.assertEqual(req_get["query"]["variant"], ["GPU"])
        self.assertEqual(req_get["query"]["authuser"], ["0"])
        # No XSRF token on GET
        self.assertEqual(req_get["xsrf"], "")

        # Step 2: Commit POST
        req_post = log[1]
        self.assertEqual(req_post["method"], "POST")
        self.assertEqual(req_post["path"], "/tun/m/assign")
        self.assertEqual(req_post["query"]["nbh"], [nbh])
        # Crucial invariant: POST MUST include X-Goog-Colab-Token header
        self.assertTrue(req_post["xsrf"].startswith("xsrf-assign-"), f"Missing or invalid XSRF token: {req_post['xsrf']}")

    def test_assign_reconciliation_probe_avoids_duplicate_post(self):
        """Verify that if GET probe already finds active assignment, commit POST is skipped."""
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        # First assign: executes GET then POST
        res1 = self.client.assign(access_token=self.test_access_token, notebook_hash=nbh)
        ep = res1["endpoint"]

        # Clear log and call assign again with identical notebook hash
        self.hub.control_plane.state.request_log.clear()
        res2 = self.client.assign(access_token=self.test_access_token, notebook_hash=nbh)

        # Invariant: Reconciled existing assignment, only GET was called
        log = self.hub.control_plane.state.request_log
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["method"], "GET")
        self.assertEqual(res2["endpoint"], ep)

    def test_assign_error_mapping_400_and_412(self):
        """Verify HTTP 400 maps to QuotaExceededError and 412 maps to TooManyAssignmentsError."""
        # 1. Quota unavailable accelerator -> 400
        self.hub.control_plane.state.quota_unavailable_accelerators.add("H100")
        with self.assertRaises(QuotaExceededError) as cm_400:
            self.client.assign(
                access_token=self.test_access_token,
                notebook_hash=uuid_to_web_safe_base64(uuid.uuid4()),
                accelerator="H100",
            )
        self.assertEqual(cm_400.exception.status_code, 400)

        # 2. Too many assignments -> 412
        self.hub.control_plane.state.fail_next_assign = {"status": 412, "body": {"error": "Concurrently active limit exceeded"}}
        with self.assertRaises(TooManyAssignmentsError) as cm_412:
            self.client.assign(
                access_token=self.test_access_token,
                notebook_hash=uuid_to_web_safe_base64(uuid.uuid4()),
                accelerator="T4",
            )
        self.assertEqual(cm_412.exception.status_code, 412)

    # =========================================================================
    # 5. Unassign Handshake Invariants (GET probe -> POST commit with XSRF)
    # =========================================================================

    def test_unassign_handshake_two_phase_protocol(self):
        """Verify two-step unassign handshake: GET /tun/m/unassign/{ep} -> POST with X-Goog-Colab-Token."""
        # Provision an assignment first
        res = self.client.assign(
            access_token=self.test_access_token,
            notebook_hash=uuid_to_web_safe_base64(uuid.uuid4()),
        )
        endpoint = res["endpoint"]

        self.hub.control_plane.state.request_log.clear()
        unassign_ok = self.client.unassign(self.test_access_token, endpoint)
        self.assertTrue(unassign_ok)

        log = self.hub.control_plane.state.request_log
        self.assertEqual(len(log), 2)

        # Step 1: GET token
        get_unassign = log[0]
        self.assertEqual(get_unassign["method"], "GET")
        self.assertEqual(get_unassign["path"], f"/tun/m/unassign/{endpoint}")
        self.assertEqual(get_unassign["xsrf"], "")

        # Step 2: POST unassign with token
        post_unassign = log[1]
        self.assertEqual(post_unassign["method"], "POST")
        self.assertEqual(post_unassign["path"], f"/tun/m/unassign/{endpoint}")
        self.assertTrue(post_unassign["xsrf"].startswith("xsrf-unassign-"))

        # Verify assignment is removed from control plane
        assignments = self.client.list_assignments(self.test_access_token)
        endpoints = [a.get("endpoint") for a in assignments]
        self.assertNotIn(endpoint, endpoints)

    def test_unassign_idempotency_on_404(self):
        """Verify unassigning a non-existent or already-unassigned endpoint returns True cleanly."""
        res = self.client.unassign(self.test_access_token, "ep-non-existent-999")
        self.assertTrue(res)

    # =========================================================================
    # 6. Direct Colab Web URL Format & URL-Encoding Invariants
    # =========================================================================

    def test_generate_colab_notebook_url_invariants(self):
        """Verify exact structure, URL-encoded dbu, and unencoded #datalabBackendUrl."""
        # 1. Standard email and endpoint
        email = "test.user@gmail.com"
        endpoint = "t-abcdef123456"
        url = generate_colab_notebook_url(email, endpoint)

        expected = (
            "https://colab.research.google.com/notebooks/empty.ipynb?"
            "authuser=test.user%40gmail.com&dbu=%2Ftun%2Fm%2Ft-abcdef123456"
            "#datalabBackendUrl=https://colab.research.google.com/tun/m/t-abcdef123456"
        )
        self.assertEqual(url, expected)

        # 2. Email with plus tag and special characters
        email2 = "researcher+lab@sub.university.edu"
        endpoint2 = "ep_9876_xyz"
        url2 = generate_colab_notebook_url(email2, endpoint2)

        self.assertIn("authuser=researcher%2Blab%40sub.university.edu", url2)
        self.assertIn("dbu=%2Ftun%2Fm%2Fep_9876_xyz", url2)
        self.assertTrue(url2.endswith("#datalabBackendUrl=https://colab.research.google.com/tun/m/ep_9876_xyz"))

        # Invariant checks:
        # - dbu MUST be percent-encoded: '%2Ftun%2Fm%2F...' NOT '/tun/m/...'
        self.assertIn("dbu=%2Ftun%2Fm%2F", url2)
        self.assertNotIn("dbu=/tun/m/", url2)
        # - #datalabBackendUrl must NOT be percent-encoded
        self.assertIn("#datalabBackendUrl=https://colab.research.google.com/tun/m/", url2)

    # =========================================================================
    # 7. WebSocket TTY Bidirectional JSON Framing & Resizing
    # =========================================================================

    def test_websocket_tty_bidirectional_framing_and_resizing(self):
        """Empirically stress-test WebSocket TTY bidirectional JSON frames:
        - Outbound resize frames: {"cols": C, "rows": R}
        - Outbound data keystroke frames: {"data": "..."}
        - Inbound output data frames: {"data": "..."}
        - Authentication protection (invalid/missing token)
        """
        # 1. Create an active session via REST API
        sess_payload = {
            "name": "tty-stress-session",
            "accelerator": "T4",
            "shape": "standard",
            "lifecycle_policy": "interactive",
            "account_id": self.account_id,
        }
        create_resp = self.fastapi_client.post("/api/sessions", json=sess_payload, headers=self.auth_headers)
        self.assertEqual(create_resp.status_code, 201)
        sess_data = create_resp.json()
        session_id = sess_data["id"]

        # 2. Unauthorized attempt: missing token
        with self.assertRaises(Exception):
            with self.fastapi_client.websocket_connect(f"/api/sessions/{session_id}/terminal") as ws:
                pass

        # 3. Unauthorized attempt: corrupted JWT token
        with self.assertRaises(Exception):
            with self.fastapi_client.websocket_connect(f"/api/sessions/{session_id}/terminal?token=bad.jwt.token") as ws:
                pass

        # 4. Successful full-duplex session connection
        ws_url = f"/api/sessions/{session_id}/terminal?token={self.jwt_token}"
        with self.fastapi_client.websocket_connect(ws_url) as ws:
            # Step A: Receive initial shell prompt / banner frame from Mock Colab runtime
            initial_frame = ws.receive_text()
            msg = json.loads(initial_frame)
            self.assertIn("data", msg, "Initial frame must contain 'data' key")
            self.assertTrue(len(msg["data"]) > 0)

            # Step B: Send terminal window resize frame {"cols": 120, "rows": 40}
            resize_frame = json.dumps({"cols": 120, "rows": 40})
            ws.send_text(resize_frame)

            # Step C: Send input keystroke frame {"data": "echo CHALLENGER_OK\r"}
            ws.send_text(json.dumps({"data": "echo CHALLENGER_OK\r"}))

            # Step D: Read back echoed and output frames until CHALLENGER_OK is observed
            received_data = ""
            for _ in range(35):
                frame = ws.receive_text()
                parsed = json.loads(frame)
                self.assertIn("data", parsed)
                received_data += parsed["data"]
                if "CHALLENGER_OK" in received_data:
                    break

            self.assertIn("CHALLENGER_OK", received_data, f"Did not observe terminal output: {received_data}")

            # Step E: Send additional terminal resize frame
            ws.send_text(json.dumps({"cols": 80, "rows": 24}))

        # 5. Clean teardown: DELETE session
        del_resp = self.fastapi_client.delete(f"/api/sessions/{session_id}", headers=self.auth_headers)
        self.assertEqual(del_resp.status_code, 204)


if __name__ == "__main__":
    unittest.main()
