"""Tier 2 Boundary Tests: Session Provisioning & Accelerator Shape Rules (T2.3).

Validates:
1. HTTP 412 concurrency limit handling (TooManyAssignmentsError) and rollback.
2. HTTP 400 quota exhaustion mapping (QuotaExceededError) per accelerator.
3. High-RAM shape omission for L4, V5E1, V6E1 (shape=hm omitted on wire).
4. Standard shape never includes shape query parameter on wire across all accelerators.
5. Rapid cancellation / network failure during assign rolls back without ghost sessions.
6. Two-phase probe reconciliation: GET probe returns existing assignment without redundant POST.
"""

from __future__ import annotations

import json
import unittest
import uuid
from unittest.mock import MagicMock, patch

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.models import SessionCreateRequest
from backend.app.services import session_manager
from backend.app.services.colab_client import (
    HIGH_MEM_ONLY_ACCELERATORS,
    ColabClient,
    ColabClientError,
    QuotaExceededError,
    TooManyAssignmentsError,
    uuid_to_web_safe_base64,
)
from backend.tests.common.base_test import BaseE2ESyncTestCase


class TestProvisioningShapeBoundaries(BaseE2ESyncTestCase):
    """Tier 2 Boundary test cases for Colab Provisioning and Hardware Shape Rules."""

    seed_default_account: bool = True

    def test_concurrency_limit_412_handling_and_retry_exhaustion(self):
        """HTTP 412 concurrency limit raises TooManyAssignmentsError; no ghost session or assignment leak."""
        self.hub.trigger_concurrency_limit(max_allowed=0)
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        req = SessionCreateRequest(
            name="test-412-limit",
            accelerator="T4",
            shape="standard",
            account_id=self.default_account_id,
        )

        with self.assertRaises(TooManyAssignmentsError) as ctx:
            session_manager.create_session(req, client=colab)

        self.assertEqual(ctx.exception.status_code, 412)

        # Database must have zero sessions created
        rows = self.query_db("SELECT count(*) as cnt FROM sessions")
        self.assertEqual(rows[0]["cnt"], 0)

        # Account active_assignments must remain 0
        acc_rows = self.query_db("SELECT active_assignments FROM accounts WHERE id = ?", (self.default_account_id,))
        self.assertEqual(acc_rows[0]["active_assignments"], 0)

    def test_quota_exhaustion_400_handling_with_accelerator(self):
        """HTTP 400 quota exhaustion maps to QuotaExceededError and prevents session provisioning."""
        self.hub.trigger_quota_exhaustion("A100")
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        req = SessionCreateRequest(
            name="test-400-quota",
            accelerator="A100",
            shape="standard",
            account_id=self.default_account_id,
        )

        with self.assertRaises(QuotaExceededError) as ctx:
            session_manager.create_session(req, client=colab)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Quota exceeded", str(ctx.exception))

        rows = self.query_db("SELECT count(*) as cnt FROM sessions")
        self.assertEqual(rows[0]["cnt"], 0)

    def test_high_ram_shape_omission_for_l4_v5e1_v6e1(self):
        """High-RAM shape (shape=hm) is strictly omitted for L4, V5E1, V6E1 on wire, but included for T4/A100."""
        # 1. Test model-level coercion for high-RAM accelerators
        for acc in ["L4", "V5E1", "V6E1"]:
            req = SessionCreateRequest(name=f"test-{acc}", accelerator=acc, shape="hm")
            self.assertEqual(req.shape, "standard", f"Model must coerce shape='hm' to 'standard' for {acc}")

        # 2. Test wire-level parameter filtering via ColabClient
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        token = "ya29.mock_test_token"

        for acc in ["L4", "V5E1", "V6E1"]:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())
            res = colab.assign(access_token=token, notebook_hash=nbh, accelerator=acc, shape="hm")
            self.assertIn("endpoint", res)

            reqs = self.hub.control_plane.state.request_log
            self.assertGreaterEqual(len(reqs), 2)
            for req_entry in reqs:
                if req_entry["path"] == "/tun/m/assign":
                    self.assertNotIn("shape", req_entry["query"], f"shape=hm must not appear in query for {acc}")

        # In contrast, for T4, shape=hm must be present on wire
        self.hub.control_plane.state.request_log.clear()
        nbh_t4 = uuid_to_web_safe_base64(uuid.uuid4())
        res_t4 = colab.assign(access_token=token, notebook_hash=nbh_t4, accelerator="T4", shape="hm")
        self.assertIn("endpoint", res_t4)
        reqs_t4 = self.hub.control_plane.state.request_log
        self.assertGreaterEqual(len(reqs_t4), 2)
        for req_entry in reqs_t4:
            if req_entry["path"] == "/tun/m/assign":
                self.assertIn("shape", req_entry["query"])
                self.assertEqual(req_entry["query"]["shape"], ["hm"])

    def test_standard_shape_never_includes_shape_param(self):
        """Standard shape requests never include shape= query parameter for any accelerator."""
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        token = "ya29.mock_test_token"
        accelerators = ["T4", "L4", "A100", "H100", "G4", "V5E1", "V6E1"]

        for acc in accelerators:
            self.hub.control_plane.state.request_log.clear()
            nbh = uuid_to_web_safe_base64(uuid.uuid4())
            res = colab.assign(access_token=token, notebook_hash=nbh, accelerator=acc, shape="standard")
            self.assertIn("endpoint", res)

            reqs = self.hub.control_plane.state.request_log
            for req_entry in reqs:
                if req_entry["path"] == "/tun/m/assign":
                    self.assertNotIn("shape", req_entry["query"], f"shape query param found for {acc}")

    def test_rapid_cancellation_during_assign_rollback(self):
        """Failure or cancellation during assignment rolls back without creating ghost records."""
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        req = SessionCreateRequest(
            name="test-cancel-rollback",
            accelerator="T4",
            shape="standard",
            account_id=self.default_account_id,
        )

        with patch.object(colab, "assign", side_effect=ColabClientError("Network connection reset")):
            with self.assertRaises(ColabClientError):
                session_manager.create_session(req, client=colab)

        # Database must have zero sessions
        rows = self.query_db("SELECT count(*) as cnt FROM sessions")
        self.assertEqual(rows[0]["cnt"], 0)

        # Account active assignments remains 0
        acc_rows = self.query_db("SELECT active_assignments FROM accounts WHERE id = ?", (self.default_account_id,))
        self.assertEqual(acc_rows[0]["active_assignments"], 0)

    def test_assign_two_phase_probe_avoids_duplicate_provisioning(self):
        """GET probe finds existing assignment and returns it without issuing redundant POST."""
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        token = "ya29.mock_test_token"
        nbh = uuid_to_web_safe_base64(uuid.uuid4())

        # 1. First assign performs GET probe (empty) then POST commit
        self.hub.control_plane.state.request_log.clear()
        res1 = colab.assign(access_token=token, notebook_hash=nbh, accelerator="T4", shape="standard")
        ep1 = res1.get("endpoint")
        self.assertIsNotNone(ep1)

        # Count requests in first assign: should be GET followed by POST
        log1 = list(self.hub.control_plane.state.request_log)
        self.assertEqual(len(log1), 2)
        self.assertEqual(log1[0]["method"], "GET")
        self.assertEqual(log1[1]["method"], "POST")

        # 2. Second assign with same notebook_hash must reconcile via GET probe
        self.hub.control_plane.state.request_log.clear()
        res2 = colab.assign(access_token=token, notebook_hash=nbh, accelerator="T4", shape="standard")
        self.assertEqual(res2.get("endpoint"), ep1)

        # Invariant: Reconciled existing assignment, ONLY GET was called
        log2 = list(self.hub.control_plane.state.request_log)
        self.assertEqual(len(log2), 1)
        self.assertEqual(log2[0]["method"], "GET")


if __name__ == "__main__":
    unittest.main()
