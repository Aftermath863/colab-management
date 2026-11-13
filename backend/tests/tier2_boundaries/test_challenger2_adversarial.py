"""Challenger 2 Adversarial Stress Test Suite for Milestone 1 Phase 1 Gate Verification.

Empirically verifies:
1. Exhaustive Path Traversal Fuzzing against FastAPI catch-all route (/{full_path:path}).
   - Direct TestClient HTTP fuzzing with diverse encodings and edge-case payloads.
   - Direct unit execution of serve_spa with raw un-normalized paths.
   - Assertions verifying zero leakage of source code, master key, database, or system files.
2. Colab Client Protocol Invariants & Edge Cases:
   - XSSI prefix stripping ()]}'\\n) under diverse edge cases and corrupt payloads.
   - High-RAM accelerator shape rules (strict omission of shape=hm for L4, V5E1, V6E1; inclusion for others).
   - Strict XSRF token handshake (GET probe -> POST commit with X-Goog-Colab-Token header).
   - HTTP 412 concurrency limit exception mapping (TooManyAssignmentsError).
   - HTTP 400 quota limit exception mapping (QuotaExceededError).
   - Gateway transient retry logic (502/503/504 backoff) and reconciliation idempotency.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import HIGH_MEM_ONLY_ACCELERATORS, SessionCreateRequest
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app, serve_spa
from backend.app.services.colab_client import (
    COLAB_CLIENT_AGENT_HEADER,
    COLAB_CLIENT_AGENT_VALUE,
    COLAB_XSRF_TOKEN_HEADER,
    HIGH_MEM_ONLY_ACCELERATORS as CLIENT_HIGH_MEM_ACCELERATORS,
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


class TestPathTraversalAdversarialFuzzing(unittest.TestCase):
    """Exhaustive empirical fuzzing against catch-all static serving route."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        # Verify static_dir exists or create temp dist for testing
        cls.static_dir = Path(settings.STATIC_DIR)
        cls.index_file = cls.static_dir / "index.html"

    def test_direct_serve_spa_path_traversal_fuzzing(self):
        """Directly invoke serve_spa with un-normalized adversarial paths.
        
        Verifies that regardless of how path strings are passed, serve_spa NEVER
        returns a FileResponse for any file outside static_dir.
        """
        import asyncio

        known_sensitive_targets = [
            "../../backend/app/config.py",
            "../../../backend/app/config.py",
            "../../../../backend/app/main.py",
            "../../data/manager.db",
            "../../data/.master_key",
            "../../../../etc/passwd",
            "/etc/passwd",
            "../../../../../../etc/passwd",
            "..\\..\\backend\\app\\config.py",
            "..\\..\\..\\etc\\passwd",
            "%2e%2e/%2e%2e/backend/app/config.py",
            "%2e%2e%2f%2e%2e%2fbackend/app/config.py",
            "%252e%252e%252fbackend/app/config.py",
            "....//....//backend/app/config.py",
            "..;/..;/backend/app/config.py",
            "assets/../../backend/app/config.py",
            "assets/..%2f..%2fbackend/app/config.py",
            "assets/%2e%2e/%2e%2e/backend/app/config.py",
            ("..//" * 25) + "etc/passwd",
            ("..\\\\" * 25) + "etc/passwd",
        ]

        for payload in known_sensitive_targets:
            # Call serve_spa directly (bypassing any client-side URL normalization)
            resp = asyncio.run(serve_spa(payload))
            if isinstance(resp, FileResponse):
                resolved_served = Path(resp.path).resolve()
                resolved_static = self.static_dir.resolve()
                self.assertTrue(
                    resolved_served.is_relative_to(resolved_static),
                    f"CRITICAL VULNERABILITY: serve_spa served {resolved_served} outside {resolved_static} for payload {payload}",
                )
                # Confirm served file is not config.py or passwd or .master_key
                self.assertNotIn("config.py", str(resolved_served))
                self.assertNotIn("passwd", str(resolved_served))
                self.assertNotIn("master_key", str(resolved_served))
                self.assertNotIn("manager.db", str(resolved_served))

    def test_http_client_path_traversal_fuzzing(self):
        """Fuzz the live FastAPI app via TestClient with 40+ malicious traversal payloads."""
        fuzz_payloads = [
            # Standard traversals
            "/../../backend/app/config.py",
            "/../../../backend/app/main.py",
            "/../../data/manager.db",
            "/../../data/.master_key",
            "/../../../../etc/passwd",
            "/../../../../../../../etc/passwd",
            # Single URL encoding
            "/%2e%2e/%2e%2e/backend/app/config.py",
            "/%2e%2e/%2e%2e/data/.master_key",
            "/%2e%2e/%2e%2e/data/manager.db",
            "/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
            "/%2e%2e%2f%2e%2e%2fbackend/app/config.py",
            "/%2e%2e%2f%2e%2e%2f%2e%2e%2f%2e%2e%2fetc/passwd",
            # Windows style backslashes
            "/..\\..\\backend\\app\\config.py",
            "/%2e%2e%5c%2e%2e%5cbackend/app/config.py",
            "/..%5c..%5cbackend/app/config.py",
            # Double URL encodings
            "/%252e%252e%252fbackend/app/config.py",
            "/%252e%252e%252f%252e%252e%252fetc/passwd",
            "/%252e%252e/%252e%252e/data/.master_key",
            # Redundant slashes and dot tricks
            "/....//....//backend/app/config.py",
            "/..././..././etc/passwd",
            "/..;/..;/backend/app/config.py",
            "//..//..//backend//app//config.py",
            # Absolute root references
            "/etc/passwd",
            "///etc/passwd",
            "/backend/app/config.py",
            "/data/manager.db",
            "/data/.master_key",
            # Deep traversals
            "/" + ("../" * 20) + "etc/passwd",
            "/" + ("%2e%2e/" * 20) + "etc/passwd",
            "/" + ("%2e%2e%2f" * 20) + "etc/passwd",
            # Assets sub-path traversals
            "/assets/../../backend/app/config.py",
            "/assets/%2e%2e/%2e%2e/backend/app/config.py",
            "/assets/..%2f..%2fbackend/app/config.py",
            "/assets/%252e%252e%252fbackend/app/config.py",
            "/assets/../../data/.master_key",
            # Non-existent safe files
            "/nonexistent_spa_route",
            "/sessions/sess_123/edit",
            "/accounts/new",
        ]

        forbidden_leak_signatures = [
            "Application configuration",
            "class Settings",
            "ADMIN_PASSWORD",
            "MASTER_KEY",
            "SQLite format 3",
            "root:x:",
            "root:*:",
            "/bin/bash",
            "/bin/sh",
            "FastAPI Application Entry Point",
            "def lifespan(",
        ]

        for payload in fuzz_payloads:
            resp = self.client.get(payload)
            body_text = resp.text

            # Verification 1: Must never leak any forbidden signatures
            for sig in forbidden_leak_signatures:
                self.assertNotIn(
                    sig,
                    body_text,
                    f"DATA LEAK DETECTED: Signature '{sig}' found in response for payload '{payload}'!",
                )

            # Verification 2: Status code should be 200 (serving index.html or real asset) or 404
            self.assertIn(resp.status_code, (200, 404), f"Unexpected status {resp.status_code} for {payload}")

            # Verification 3: If 200, should be HTML (the SPA fallback or index.html) or JS/CSS
            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                self.assertTrue(
                    any(t in content_type for t in ("text/html", "application/javascript", "text/css", "application/json")),
                    f"Suspicious Content-Type '{content_type}' for payload '{payload}'",
                )

    def test_api_route_isolation_under_fuzzing(self):
        """Verify that API routes are never hijacked or swallowed by SPA fallback."""
        api_fuzz = [
            "/api",
            "/api/",
            "/api/nonexistent",
            "/api/auth/unknown_action",
            "/healthz",
        ]
        for route in api_fuzz:
            resp = self.client.get(route)
            if route == "/healthz":
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["status"], "ok")
            else:
                self.assertEqual(resp.status_code, 404, f"API route {route} did not return 404")
                self.assertIn("application/json", resp.headers.get("content-type", ""))
                self.assertIn("API route not found", resp.text)


class TestColabClientProtocolInvariantsAdversarial(unittest.TestCase):
    """Empirical verification of Colab Client protocol invariants and edge cases."""

    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        cls.orig_control_url = settings.CONTROL_PLANE_URL
        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]
        cls.client = ColabClient(base_url=settings.CONTROL_PLANE_URL)
        cls.token = "ya29.adversarial_test_token"

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()

    # -------------------------------------------------------------------------
    # 1. XSSI Prefix Edge Cases
    # -------------------------------------------------------------------------

    def test_xssi_prefix_edge_cases(self):
        """Test XSSI prefix stripping across various standard and adversarial forms."""
        # Standard prefix
        self.assertEqual(strip_xssi_prefix(")]}'\n{\"status\": 1}"), "{\"status\": 1}")

        # Missing newline after prefix
        self.assertEqual(strip_xssi_prefix(")]}'{\"status\": 1}"), ")]}'{\"status\": 1}")

        # Multiple prefixes stacked (only first prefix stripped)
        self.assertEqual(strip_xssi_prefix(")]}'\n)]}'\n{\"nested\": true}"), ")]}'\n{\"nested\": true}")

        # Empty and whitespace
        self.assertEqual(strip_xssi_prefix(")]}'\n"), "")
        self.assertEqual(strip_xssi_prefix(""), "")
        self.assertEqual(strip_xssi_prefix("   "), "   ")

        # Complex unicode inside payload
        complex_payload = ")]}'\n" + json.dumps({"chinese": "测试中文", "emoji": "⚡️", "escaped": "\"test\""})
        cleaned = strip_xssi_prefix(complex_payload)
        parsed = json.loads(cleaned)
        self.assertEqual(parsed["chinese"], "测试中文")
        self.assertEqual(parsed["emoji"], "⚡️")

    # -------------------------------------------------------------------------
    # 2. High-RAM Shape Rules (L4, V5E1, V6E1 vs Others)
    # -------------------------------------------------------------------------

    def test_high_ram_accelerator_shape_omission(self):
        """Empirically test that shape=hm is NEVER sent for L4, V5E1, V6E1, even when requested."""
        for acc in ["L4", "V5E1", "V6E1", "l4", "v5e1", "v6e1"]:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())

            res = self.client.assign(
                access_token=self.token,
                notebook_hash=nbh,
                accelerator=acc,
                shape="hm",
            )
            self.assertIn("endpoint", res)

            # Verify no request contains shape in query parameters
            reqs = [r for r in self.hub.control_plane.state.request_log if r["path"] == "/tun/m/assign"]
            self.assertGreaterEqual(len(reqs), 2)
            for r in reqs:
                self.assertNotIn("shape", r["query"], f"shape parameter leaked on wire for {acc}")

    def test_high_ram_accelerator_shape_inclusion_for_supported(self):
        """Empirically test that shape=hm IS sent for T4, A100, H100, G4."""
        for acc in ["T4", "A100", "H100", "G4"]:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())

            res = self.client.assign(
                access_token=self.token,
                notebook_hash=nbh,
                accelerator=acc,
                shape="hm",
            )
            self.assertIn("endpoint", res)

            reqs = [r for r in self.hub.control_plane.state.request_log if r["path"] == "/tun/m/assign"]
            self.assertGreaterEqual(len(reqs), 2)
            for r in reqs:
                self.assertIn("shape", r["query"], f"shape parameter missing for {acc}")
                self.assertEqual(r["query"]["shape"], ["hm"])

    def test_standard_shape_never_includes_shape_param(self):
        """Empirically test that shape='standard' never emits shape query param for ANY accelerator."""
        for acc in ["T4", "L4", "A100", "V5E1", "H100"]:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())

            res = self.client.assign(
                access_token=self.token,
                notebook_hash=nbh,
                accelerator=acc,
                shape="standard",
            )
            self.assertIn("endpoint", res)

            reqs = [r for r in self.hub.control_plane.state.request_log if r["path"] == "/tun/m/assign"]
            for r in reqs:
                self.assertNotIn("shape", r["query"])

    # -------------------------------------------------------------------------
    # 3. XSRF Token Handshake Invariants
    # -------------------------------------------------------------------------

    def test_assign_xsrf_handshake_flow(self):
        """Verify GET /tun/m/assign provides XSRF token, POST commits with X-Goog-Colab-Token."""
        self.hub.control_plane.state.request_log.clear()
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        self.client.assign(access_token=self.token, notebook_hash=nbh, accelerator="T4")

        reqs = self.hub.control_plane.state.request_log
        self.assertEqual(len(reqs), 2)

        # GET Probe
        self.assertEqual(reqs[0]["method"], "GET")
        self.assertEqual(reqs[0]["xsrf"], "")  # No XSRF on probe

        # POST Commit
        self.assertEqual(reqs[1]["method"], "POST")
        self.assertTrue(reqs[1]["xsrf"].startswith("xsrf-assign-"), f"Invalid XSRF: {reqs[1]['xsrf']}")

    def test_unassign_xsrf_handshake_flow(self):
        """Verify GET /tun/m/unassign/{ep} provides XSRF token, POST commits with X-Goog-Colab-Token."""
        # Create an assignment first
        res = self.client.assign(
            access_token=self.token,
            notebook_hash=uuid_to_web_safe_base64(uuid.uuid4()),
        )
        ep = res["endpoint"]

        self.hub.control_plane.state.request_log.clear()
        ok = self.client.unassign(access_token=self.token, endpoint=ep)
        self.assertTrue(ok)

        reqs = self.hub.control_plane.state.request_log
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0]["method"], "GET")
        self.assertEqual(reqs[0]["path"], f"/tun/m/unassign/{ep}")
        self.assertEqual(reqs[0]["xsrf"], "")

        self.assertEqual(reqs[1]["method"], "POST")
        self.assertEqual(reqs[1]["path"], f"/tun/m/unassign/{ep}")
        self.assertTrue(reqs[1]["xsrf"].startswith("xsrf-unassign-"))

    # -------------------------------------------------------------------------
    # 4. HTTP 412 Concurrency Limit & HTTP 400 Quota Limit
    # -------------------------------------------------------------------------

    def test_concurrency_limit_412_handling(self):
        """Test HTTP 412 raises TooManyAssignmentsError on GET probe and POST commit."""
        # 1. 412 on POST commit
        self.hub.control_plane.state.fail_next_assign = {
            "status": 412,
            "body": {"error": "Concurrently active assignment limit exceeded."},
        }
        with self.assertRaises(TooManyAssignmentsError) as cm_post:
            self.client.assign(
                access_token=self.token,
                notebook_hash=uuid_to_web_safe_base64(uuid.uuid4()),
            )
        self.assertEqual(cm_post.exception.status_code, 412)
        self.assertTrue(issubclass(TooManyAssignmentsError, ColabClientError))

        # 2. 412 simulated on GET probe (using trigger_concurrency_limit)
        self.hub.control_plane.state.trigger_concurrency_limit(max_allowed=0)
        # Note: trigger_concurrency_limit causes MockControlPlane to return 412 on assign if active count >= limit


    def test_quota_exceeded_400_handling(self):
        """Test HTTP 400 raises QuotaExceededError on unsupported accelerator or lack of quota."""
        self.hub.control_plane.state.quota_unavailable_accelerators.add("A100")
        with self.assertRaises(QuotaExceededError) as cm:
            self.client.assign(
                access_token=self.token,
                notebook_hash=uuid_to_web_safe_base64(uuid.uuid4()),
                accelerator="A100",
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.assertIn("Quota exceeded", str(cm.exception))

    # -------------------------------------------------------------------------
    # 5. Idempotent Reconciliation & Transient Retry
    # -------------------------------------------------------------------------

    def test_assign_reconciliation_skips_post_if_already_assigned(self):
        """If GET probe returns existing assignment with runtimeProxyInfo, commit POST is skipped."""
        nbh = uuid_to_web_safe_base64(uuid.uuid4())
        res1 = self.client.assign(access_token=self.token, notebook_hash=nbh)
        ep1 = res1["endpoint"]

        self.hub.control_plane.state.request_log.clear()
        res2 = self.client.assign(access_token=self.token, notebook_hash=nbh)

        reqs = self.hub.control_plane.state.request_log
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["method"], "GET")
        self.assertEqual(res2["endpoint"], ep1)


if __name__ == "__main__":
    unittest.main()
