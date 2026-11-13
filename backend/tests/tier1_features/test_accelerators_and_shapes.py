"""Tier 1 Tests: Complete Colab Accelerator Catalog (incl. CPU-only) & High-RAM Shape Rules."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import unittest

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    HIGH_MEM_ONLY_ACCELERATORS,
    JobCreate,
    SessionCreateRequest,
)
from backend.app.core.security import encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient
from backend.app.services.job_runner import JobRunnerService
from backend.tests.mock_colab.mock_control_plane import MockAssignment
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestAcceleratorsAndShapes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hub = MockControlHub()
        cls.hub.start()

        cls.orig_control_url = settings.CONTROL_PLANE_URL
        cls.orig_token_url = settings.OAUTH_TOKEN_URL
        cls.orig_userinfo_url = settings.OAUTH_USERINFO_URL

        settings.CONTROL_PLANE_URL = cls.hub.urls["control_plane"]
        settings.OAUTH_TOKEN_URL = cls.hub.oauth.token_uri
        settings.OAUTH_USERINFO_URL = cls.hub.oauth.userinfo_uri

    @classmethod
    def tearDownClass(cls):
        settings.CONTROL_PLANE_URL = cls.orig_control_url
        settings.OAUTH_TOKEN_URL = cls.orig_token_url
        settings.OAUTH_USERINFO_URL = cls.orig_userinfo_url
        cls.hub.stop()

    def setUp(self):
        self.hub.reset()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_acc_shape.db")
        self.orig_db_path = settings.DATABASE_PATH
        self.orig_data_dir = getattr(settings, "DATA_DIR", "./data")
        self.orig_workspace_dir = getattr(settings, "WORKSPACE_DIR", "./data/workspace")
        settings.DATABASE_PATH = self.db_path
        settings.DATA_DIR = self.tmpdir.name
        settings.WORKSPACE_DIR = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(settings.WORKSPACE_DIR, exist_ok=True)
        init_db(self.db_path)

        self.account_id = "acc_shape_01"
        self.email = "shape.tester@example.com"
        self.hub.oauth.state.register_account(
            MockOAuthAccount(
                email=self.email,
                name="Shape Tester",
                sub="sub_shape_01",
                code="code_shape_01",
                access_token="ya29.shape_token",
                refresh_token="1//shape_refresh",
            )
        )
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (
                    id, email, display_name, oauth_creds_enc,
                    ccu_balance, burn_rate_hourly, active_assignments,
                    warning_ccu, hard_stop_ccu, priority, status
                ) VALUES (?, ?, 'Shape Tester', ?, 50.0, 0.5, 0, 10.0, 2.0, 5, 'ACTIVE')
                """,
                (
                    self.account_id,
                    self.email,
                    encrypt_secret(
                        '{"access_token": "ya29.shape_token", "refresh_token": "1//shape_refresh", "expires_at": 9999999999}'
                    ),
                ),
            )

    def tearDown(self):
        settings.DATABASE_PATH = self.orig_db_path
        settings.DATA_DIR = self.orig_data_dir
        settings.WORKSPACE_DIR = self.orig_workspace_dir
        self.tmpdir.cleanup()

    def test_all_eight_accelerators_and_none_normalization_in_models(self):
        all_accs = ["CPU", "T4", "G4", "L4", "A100", "H100", "V5E1", "V6E1"]
        for acc in all_accs:
            s_req = SessionCreateRequest(name=f"s-{acc}", accelerator=acc, shape="standard")  # type: ignore
            expected_shape = "hm" if acc in HIGH_MEM_ONLY_ACCELERATORS else "standard"
            self.assertEqual(s_req.accelerator, acc)
            self.assertEqual(s_req.shape, expected_shape)

            j_req = JobCreate(
                name=f"j-{acc}",
                target_accelerator=acc,  # type: ignore
                target_shape="standard",
                job_type="shell",
                entry_path_or_cmd="echo ok",
            )
            self.assertEqual(j_req.target_accelerator, acc)
            self.assertEqual(j_req.target_shape, expected_shape)

        # Wire accelerator 'NONE' normalizes to 'CPU'
        none_sess = SessionCreateRequest(name="s-none", accelerator="none", shape="hm")  # type: ignore
        self.assertEqual(none_sess.accelerator, "CPU")
        self.assertEqual(none_sess.shape, "hm")

        none_job = JobCreate(
            name="j-none",
            target_accelerator="NONE",  # type: ignore
            target_shape="hm",
            job_type="shell",
            entry_path_or_cmd="echo cpu",
        )
        self.assertEqual(none_job.target_accelerator, "CPU")
        self.assertEqual(none_job.target_shape, "hm")

    def test_cpu_wire_protocol_standard_and_high_ram_sessions(self):
        colab = ColabClient(base_url=self.hub.urls["control_plane"])

        # 1. CPU standard -> variant=DEFAULT, accelerator=NONE, no shape query param
        self.hub.control_plane.state.request_log.clear()
        sess_std = session_manager.create_session(
            SessionCreateRequest(name="cpu-std", accelerator="CPU", shape="standard", account_id=self.account_id),
            client=colab,
        )
        self.assertEqual(sess_std["accelerator"], "CPU")
        self.assertEqual(sess_std["shape"], "standard")
        assign_reqs = [r for r in self.hub.control_plane.state.request_log if r["path"] == "/tun/m/assign"]
        self.assertEqual(len(assign_reqs), 2)
        for req_entry in assign_reqs:
            self.assertEqual(req_entry["query"].get("variant"), ["DEFAULT"])
            self.assertEqual(req_entry["query"].get("accelerator"), ["NONE"])
            self.assertNotIn("shape", req_entry["query"])

        # 2. CPU high-RAM -> variant=DEFAULT, accelerator=NONE, shape=['hm']
        self.hub.control_plane.state.request_log.clear()
        sess_hm = session_manager.create_session(
            SessionCreateRequest(name="cpu-hm", accelerator="CPU", shape="hm", account_id=self.account_id),
            client=colab,
        )
        self.assertEqual(sess_hm["accelerator"], "CPU")
        self.assertEqual(sess_hm["shape"], "hm")
        assign_reqs_hm = [r for r in self.hub.control_plane.state.request_log if r["path"] == "/tun/m/assign"]
        self.assertEqual(len(assign_reqs_hm), 2)
        for req_entry in assign_reqs_hm:
            self.assertEqual(req_entry["query"].get("variant"), ["DEFAULT"])
            self.assertEqual(req_entry["query"].get("accelerator"), ["NONE"])
            self.assertEqual(req_entry["query"].get("shape"), ["hm"])

    def test_high_mem_only_session_stores_hm_while_wire_omits_shape_hm(self):
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        self.hub.control_plane.state.strict_shape_validation = True

        for acc in ["L4", "V5E1", "V6E1"]:
            self.hub.control_plane.state.request_log.clear()
            req = SessionCreateRequest(
                name=f"auto-hm-{acc.lower()}",
                accelerator=acc,  # type: ignore
                shape="standard",
                account_id=self.account_id,
            )
            sess = session_manager.create_session(req, client=colab)
            self.assertEqual(sess["shape"], "hm")

            assign_reqs = [r for r in self.hub.control_plane.state.request_log if r["path"] == "/tun/m/assign"]
            self.assertGreaterEqual(len(assign_reqs), 2)
            for req_entry in assign_reqs:
                self.assertNotIn("shape", req_entry["query"])

    def test_orphan_discovery_normalizes_cpu_none_and_high_ram_shape(self):
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        self.hub.control_plane.state.assignments["ep-orphan-cpu"] = MockAssignment(
            endpoint="ep-orphan-cpu",
            accelerator="NONE",
            variant=0,
            machine_shape=1,
            nbh="orphan-nbh-cpu",
            proxy_url=f"{self.hub.urls['control_plane']}/tun/m/ep-orphan-cpu",
        )
        account_pool.sync_account(self.account_id, client=colab)

        orphans = session_manager.list_sessions(status_filter="ORPHAN")
        self.assertEqual(len(orphans), 1)
        self.assertEqual(orphans[0]["accelerator"], "CPU")
        self.assertEqual(orphans[0]["shape"], "hm")

    def test_init_db_migrates_legacy_jobs_table_without_target_shape(self):
        legacy_db = os.path.join(self.tmpdir.name, "legacy_manager.db")
        conn = sqlite3.connect(legacy_db)
        try:
            conn.executescript(
                """
                CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    session_id TEXT,
                    target_accelerator TEXT NOT NULL,
                    allow_fallback BOOLEAN NOT NULL DEFAULT 1,
                    template_id TEXT,
                    job_type TEXT NOT NULL,
                    entry_path_or_cmd TEXT NOT NULL,
                    args_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'QUEUED',
                    exit_code INTEGER,
                    log_path TEXT NOT NULL,
                    output_notebook_path TEXT,
                    started_at TIMESTAMP,
                    finished_at TIMESTAMP
                );
                INSERT INTO jobs (id, name, target_accelerator, job_type, entry_path_or_cmd, log_path)
                VALUES ('job_old_l4', 'legacy l4 job', 'L4', 'shell', 'echo hi', '/tmp/j.log');
                """
            )
            conn.commit()
        finally:
            conn.close()

        init_db(legacy_db)
        verify = sqlite3.connect(legacy_db)
        verify.row_factory = sqlite3.Row
        try:
            cols = {r[1] for r in verify.execute("PRAGMA table_info(jobs)").fetchall()}
            self.assertIn("target_shape", cols)
            row = verify.execute("SELECT target_shape FROM jobs WHERE id = 'job_old_l4'").fetchone()
            self.assertEqual(row["target_shape"], "hm")
        finally:
            verify.close()

    def test_job_runner_provisions_cpu_high_ram_and_l4_high_ram(self):
        colab = ColabClient(base_url=self.hub.urls["control_plane"])
        runner = JobRunnerService(colab_client=colab)

        cpu_job_req = JobCreate(
            name="CPU High-RAM Shell Job",
            target_accelerator="CPU",
            target_shape="hm",
            allow_fallback=False,
            job_type="shell",
            entry_path_or_cmd="echo cpu-hm-ok",
        )
        l4_job_req = JobCreate(
            name="L4 Job Auto-HM",
            target_accelerator="L4",
            target_shape="standard",
            allow_fallback=False,
            job_type="shell",
            entry_path_or_cmd="echo l4-hm-ok",
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            cpu_job = loop.run_until_complete(runner.submit_job(cpu_job_req))
            self.assertEqual(cpu_job.target_accelerator, "CPU")
            self.assertEqual(cpu_job.target_shape, "hm")
            loop.run_until_complete(runner._run_job_wrapper(cpu_job.id))
            cpu_done = runner.get_job(cpu_job.id)
            assert cpu_done is not None
            self.assertEqual(cpu_done.status, "COMPLETED")

            with get_db(self.db_path) as conn:
                cpu_sess = conn.execute(
                    "SELECT accelerator, shape FROM sessions WHERE id = ?", (cpu_done.session_id,)
                ).fetchone()
                self.assertEqual(cpu_sess["accelerator"], "CPU")
                self.assertEqual(cpu_sess["shape"], "hm")

            l4_job = loop.run_until_complete(runner.submit_job(l4_job_req))
            self.assertEqual(l4_job.target_shape, "hm")
            loop.run_until_complete(runner._run_job_wrapper(l4_job.id))
            l4_done = runner.get_job(l4_job.id)
            assert l4_done is not None
            self.assertEqual(l4_done.status, "COMPLETED")

            with get_db(self.db_path) as conn:
                l4_sess = conn.execute(
                    "SELECT accelerator, shape FROM sessions WHERE id = ?", (l4_done.session_id,)
                ).fetchone()
                self.assertEqual(l4_sess["accelerator"], "L4")
                self.assertEqual(l4_sess["shape"], "hm")
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
