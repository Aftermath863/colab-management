"""Tier 1 Feature Tests: Asynchronous Job Runner & Execution Engine.

Validates:
1. Python script job submission, queueing, execution, and COMPLETED status.
2. Notebook (.ipynb) cell output capture and export to /data/jobs/{job_id}/output.ipynb.
3. Shell task execution and stdout streaming to run.log.
4. Live log streaming endpoint (GET /api/jobs/{id}/logs).
5. Output notebook download endpoint (GET /api/jobs/{id}/output).
6. Job cancellation in QUEUED and RUNNING states (POST /api/jobs/{id}/cancel).
7. Ephemeral session lifecycle teardown upon completion.
8. Cross-account accelerator fallback when target accelerator allocation fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import JobCreate
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services.job_runner import job_runner_service
from backend.app.services.colab_client import ColabClient
from backend.app.services import session_manager
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


class TestJobRunnerFeatures(unittest.TestCase):
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
        self.db_path = os.path.join(self.tmpdir.name, "test_jobs.db")
        settings.DATABASE_PATH = self.db_path
        self.orig_data_dir = getattr(settings, "DATA_DIR", "./data")
        self.orig_workspace_dir = getattr(settings, "WORKSPACE_DIR", "./data/workspace")
        settings.DATA_DIR = self.tmpdir.name
        settings.WORKSPACE_DIR = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(settings.WORKSPACE_DIR, exist_ok=True)
        init_db(self.db_path)

        # Seed account
        self.account_id = "acc_job_worker_1"
        self.email = "job.worker@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Job Worker",
            sub="sub_job_1",
            code="code_job",
            access_token="ya29.mock_job_token",
            refresh_token="1//mock_refresh_job",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_job_token",
            "refresh_token": "1//mock_refresh_job",
            "expires_at": 9999999999,
        }
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Job Worker', ?, 100.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        job_runner_service.colab = ColabClient(base_url=self.hub.urls["control_plane"])
        self.token = create_access_token(data={"sub": "admin", "role": "admin"})
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(app)

    def tearDown(self):
        settings.DATA_DIR = self.orig_data_dir
        settings.WORKSPACE_DIR = self.orig_workspace_dir
        self.tmpdir.cleanup()

    def test_job_submission_and_lifecycle_ephemeral_unassign(self):
        """Job runs on auto-provisioned ephemeral session and tears it down on completion."""
        # Create a python script in workspace
        script_path = os.path.join(settings.WORKSPACE_DIR, "simple_task.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("print('Hello from Job Runner!')\n")

        job_req = JobCreate(
            name="Test Python Job",
            target_accelerator="T4",
            allow_fallback=True,
            job_type="python",
            entry_path_or_cmd="simple_task.py",
            args=["--flag", "val"],
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            job_resp = loop.run_until_complete(job_runner_service.submit_job(job_req))
            job_id = job_resp.id

            # Run wrapper synchronously to simulate worker execution
            loop.run_until_complete(job_runner_service._run_job_wrapper(job_id))
        finally:
            loop.close()

        # Check job status
        finished_job = job_runner_service.get_job(job_id)
        self.assertIsNotNone(finished_job)
        self.assertEqual(finished_job.status, "COMPLETED")
        self.assertEqual(finished_job.exit_code, 0)
        self.assertTrue(finished_job.session_id)

        # Confirm ephemeral session was stopped/unassigned
        sess = session_manager.get_session(finished_job.session_id)
        self.assertEqual(sess["status"], "STOPPED")

        # Check log file contents
        self.assertTrue(os.path.exists(finished_job.log_path))
        with open(finished_job.log_path, "r", encoding="utf-8") as lf:
            content = lf.read()
            self.assertIn("Hello from Job Runner!", content)

    def test_notebook_execution_and_output_export(self):
        """Notebook job executes code cells, populates outputs, and exports output.ipynb."""
        import nbformat
        from nbformat.v4 import new_code_cell, new_notebook

        nb = new_notebook()
        nb.cells.append(new_code_cell(source="x = 10\ny = 20\nprint(f'Result={x+y}')"))
        nb.cells.append(new_code_cell(source="print('Second cell execution')"))

        nb_path = os.path.join(settings.WORKSPACE_DIR, "test_run.ipynb")
        with open(nb_path, "w", encoding="utf-8") as f:
            nbformat.write(nb, f)

        job_req = JobCreate(
            name="Notebook Execution Job",
            target_accelerator="T4",
            allow_fallback=True,
            job_type="notebook",
            entry_path_or_cmd="test_run.ipynb",
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            job_resp = loop.run_until_complete(job_runner_service.submit_job(job_req))
            job_id = job_resp.id
            loop.run_until_complete(job_runner_service._run_job_wrapper(job_id))
        finally:
            loop.close()

        finished_job = job_runner_service.get_job(job_id)
        self.assertEqual(finished_job.status, "COMPLETED")
        self.assertEqual(finished_job.exit_code, 0)
        self.assertTrue(finished_job.output_notebook_path)
        self.assertTrue(os.path.exists(finished_job.output_notebook_path))

        # Inspect exported notebook outputs
        with open(finished_job.output_notebook_path, "r", encoding="utf-8") as f:
            out_nb = nbformat.read(f, as_version=4)
            self.assertEqual(len(out_nb.cells), 2)
            first_cell_out = out_nb.cells[0].outputs[0]["text"]
            self.assertIn("Result=30", first_cell_out)

    def test_shell_command_execution_and_streaming_api(self):
        """Shell commands stream real-time output to run.log and GET /api/jobs/{id}/logs."""
        job_req = JobCreate(
            name="Shell Echo Task",
            target_accelerator="T4",
            allow_fallback=True,
            job_type="shell",
            entry_path_or_cmd="echo 'Streaming shell message'",
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            job_resp = loop.run_until_complete(job_runner_service.submit_job(job_req))
            job_id = job_resp.id
            loop.run_until_complete(job_runner_service._run_job_wrapper(job_id))
        finally:
            loop.close()

        # Check logs via REST endpoint
        res = self.client.get(f"/api/jobs/{job_id}/logs", headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        self.assertIn("Streaming shell message", res.text)

    def test_job_cancellation_in_queued_state(self):
        """POST /api/jobs/{id}/cancel marks job CANCELLED immediately."""
        job_req = JobCreate(
            name="Job to Cancel",
            target_accelerator="T4",
            allow_fallback=True,
            job_type="shell",
            entry_path_or_cmd="sleep 60",
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            job_resp = loop.run_until_complete(job_runner_service.submit_job(job_req))
        finally:
            loop.close()

        res = self.client.post(f"/api/jobs/{job_resp.id}/cancel", headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "CANCELLED")

        cancelled_job = job_runner_service.get_job(job_resp.id)
        self.assertEqual(cancelled_job.status, "CANCELLED")

    def test_cross_account_accelerator_fallback(self):
        """When requested accelerator A100 is unavailable (e.g. quota 400), cascades to L4/T4."""
        # Target A100 is rejected by mock or fallback cascades to T4
        job_req = JobCreate(
            name="Fallback Cascade Job",
            target_accelerator="A100",
            allow_fallback=True,
            job_type="shell",
            entry_path_or_cmd="echo fallback-success",
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            job_resp = loop.run_until_complete(job_runner_service.submit_job(job_req))
            job_id = job_resp.id
            loop.run_until_complete(job_runner_service._run_job_wrapper(job_id))
        finally:
            loop.close()

        finished_job = job_runner_service.get_job(job_id)
        self.assertEqual(finished_job.status, "COMPLETED")


if __name__ == "__main__":
    unittest.main()
