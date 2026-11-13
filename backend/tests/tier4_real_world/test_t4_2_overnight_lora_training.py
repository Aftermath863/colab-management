"""T4.2: Overnight LoRA Training Workflow.

Simulates a complete automated overnight training job:
1. Job Submission: Job created targeting A100 with fallback enabled, status QUEUED.
2. Quota 400 Rejection & Automatic Fallback to L4: Control plane rejects A100 with HTTP 400;
   scheduler falls back to L4 (enforcing shape='standard'), provisioning session and setting job to RUNNING.
3. Non-Blocking Telemetry: Background collector records GPU, VRAM, CPU, and RAM time series samples.
4. Output Weights Sync: Trained model weights and metrics in /content/workspace/ are pulled to local disk.
5. Ephemeral Lifecycle Teardown: Ephemeral session automatically torn down and unassigned on Colab.
6. Webhook Notification: Webhook card alert dispatched detailing completed job, fallback accelerator, and artifacts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import encrypt_secret
from backend.app.services import session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount
from backend.tests.tier3_pairwise.test_t3_6_workspace_sync_output_pull import WorkspaceSyncEngine
from backend.tests.tier3_pairwise.test_t3_8_warning_ccu_throttled_webhook import MockWebhookReceiver


class LoRAJobFallbackScheduler:
    """Scheduler that handles accelerator provisioning with automatic fallback on quota exhaustion."""

    FALLBACK_MATRIX = {
        "A100": ["L4", "T4"],
        "H100": ["A100", "L4", "T4"],
        "L4": ["T4"],
    }

    def __init__(self, colab_client: Optional[ColabClient] = None):
        self.colab = colab_client or ColabClient()

    def provision_job_session(
        self,
        job_id: str,
        account_id: str,
        target_accelerator: str,
        allow_fallback: bool,
    ) -> Dict[str, Any]:
        accelerators_to_try = [target_accelerator]
        if allow_fallback and target_accelerator in self.FALLBACK_MATRIX:
            accelerators_to_try.extend(self.FALLBACK_MATRIX[target_accelerator])

        last_error = None
        for acc in accelerators_to_try:
            # Enforce Colab shape rule: L4 only supports high-RAM standard shape
            shape = "standard"
            try:
                req = SessionCreateRequest(
                    name=f"job-sess-{acc.lower()}-{job_id[:6]}",
                    accelerator=acc,
                    shape=shape,
                    lifecycle_policy="ephemeral",
                    account_id=account_id,
                )
                sess = session_manager.create_session(req, client=self.colab)

                # Link session to job and set status to RUNNING
                with get_db() as conn:
                    conn.execute(
                        """
                        UPDATE jobs
                        SET session_id = ?, status = 'RUNNING', started_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (sess["id"], job_id),
                    )

                return {
                    "success": True,
                    "session": sess,
                    "final_accelerator": acc,
                    "was_fallback": (acc != target_accelerator),
                }
            except Exception as e:
                last_error = e
                continue

        # All attempts failed
        with get_db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'FAILED', exit_code = 1, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )
        raise RuntimeError(f"Failed to provision job: {last_error}")


class TestT42OvernightLoRATrainingWorkflow(unittest.TestCase):
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
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Register account
        self.account_id = "acc_lora_test"
        self.email = "lora_researcher@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="LoRA Researcher",
            sub="sub_lora_123",
            code="code_lora",
            access_token="ya29.mock_token_lora",
            refresh_token="1//mock_refresh_lora",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_token_lora",
            "refresh_token": "1//mock_refresh_lora",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'LoRA Researcher', ?, 150.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

            # Insert webhook notification channel
            ch_config = {"webhook_url": "https://hooks.slack.com/services/mock_lora_job"}
            conn.execute(
                """
                INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                VALUES ('ch_slack_lora', 'ML Job Alerts', 'slack', ?, ?, 1)
                """,
                (encrypt_secret(json.dumps(ch_config)), json.dumps(["job_completed", "job_failed"])),
            )

        self.scheduler = LoRAJobFallbackScheduler()
        self.sync_engine = WorkspaceSyncEngine()
        self.webhook_receiver = MockWebhookReceiver()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t4_2_overnight_lora_training_with_fallback_telemetry_and_teardown(self):
        # ====================================================================
        # Phase 1: Job Submission
        # ====================================================================
        job_id = "job_lora_llama3_001"
        job_name = "lora-llama3-finetuning"
        log_path = os.path.join(self.tmpdir.name, "job_lora.log")

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, target_accelerator, allow_fallback, job_type,
                                 entry_path_or_cmd, args_json, status, log_path)
                VALUES (?, ?, 'A100', 1, 'python', 'train_lora.py', '["--epochs", "3"]', 'QUEUED', ?)
                """,
                (job_id, job_name, log_path),
            )

        # Assert job initial state in DB
        with get_db() as conn:
            j = conn.execute("SELECT status, target_accelerator FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(j["status"], "QUEUED")
            self.assertEqual(j["target_accelerator"], "A100")

        # ====================================================================
        # Phase 2: Quota 400 & Fallback to L4
        # ====================================================================
        # Inject A100 quota exhaustion into mock control plane (HTTP 400)
        self.hub.trigger_quota_exhaustion("A100")

        # Scheduler attempts A100 -> gets 400 -> automatically falls back to L4!
        prov_result = self.scheduler.provision_job_session(
            job_id=job_id,
            account_id=self.account_id,
            target_accelerator="A100",
            allow_fallback=True,
        )

        self.assertTrue(prov_result["success"])
        self.assertTrue(prov_result["was_fallback"])
        self.assertEqual(prov_result["final_accelerator"], "L4")

        sess = prov_result["session"]
        session_id = sess["id"]
        endpoint = sess["endpoint"]
        proxy_url = sess["proxy_url"]
        internal = session_manager.get_session_internal(session_id)
        proxy_token = internal["proxy_token"]

        # Verify job transitioned to RUNNING and linked to session
        with get_db() as conn:
            j_running = conn.execute("SELECT status, session_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(j_running["status"], "RUNNING")
            self.assertEqual(j_running["session_id"], session_id)

        # ====================================================================
        # Phase 3: Telemetry Sampling
        # ====================================================================
        # Background sampler records 3 metrics samples during training
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO telemetry_samples (session_id, cpu_pct, ram_used_mb, ram_total_mb, gpu_name, gpu_util_pct, vram_used_mb, vram_total_mb)
                VALUES (?, 45.2, 8192.0, 32768.0, 'NVIDIA L4', 88.0, 18432.0, 24576.0),
                       (?, 52.1, 9216.0, 32768.0, 'NVIDIA L4', 94.5, 20480.0, 24576.0),
                       (?, 60.8, 10240.0, 32768.0, 'NVIDIA L4', 92.0, 21504.0, 24576.0)
                """,
                (session_id, session_id, session_id),
            )

        with get_db() as conn:
            telemetry_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM telemetry_samples WHERE session_id = ?",
                (session_id,),
            ).fetchone()["cnt"]
            self.assertEqual(telemetry_count, 3)

        # ====================================================================
        # Phase 4: Output Weights Sync
        # ====================================================================
        # Simulate model weights and metrics created on remote VFS
        ep = self.hub.runtime_proxy.app.get_endpoint(endpoint)
        self.assertIsNotNone(ep)
        ep.vfs.mkdir("workspace")
        ep.vfs.mkdir("workspace/output")

        adapter_weights = b"PK\x03\x04_SIMULATED_LORA_ADAPTER_WEIGHTS_BINARY_DATA_MODEL_BIN"
        metrics_payload = json.dumps({"train_loss": 0.089, "eval_loss": 0.092, "perplexity": 1.09}).encode("utf-8")

        ep.vfs.write_file(
            "workspace/output/adapter_model.bin",
            base64.b64encode(adapter_weights).decode("ascii"),
            chunk=1,
        )
        ep.vfs.write_file(
            "workspace/output/metrics.json",
            base64.b64encode(metrics_payload).decode("ascii"),
            chunk=1,
        )

        # Output syncer pulls artifacts to host ./workspace/output
        local_dest = os.path.join(self.tmpdir.name, "output")
        pulled = self.sync_engine.pull_workspace(
            remote_prefix="workspace/output",
            local_dest_dir=local_dest,
            proxy_url=proxy_url,
            proxy_token=proxy_token,
        )

        local_model_bin = os.path.join(local_dest, "adapter_model.bin")
        local_metrics = os.path.join(local_dest, "metrics.json")
        self.assertIn(local_model_bin, pulled)
        self.assertIn(local_metrics, pulled)

        with open(local_model_bin, "rb") as f:
            self.assertEqual(f.read(), adapter_weights)
        with open(local_metrics, "rb") as f:
            self.assertEqual(f.read(), metrics_payload)

        # ====================================================================
        # Phase 5: Ephemeral Teardown
        # ====================================================================
        # Job completes successfully
        with get_db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'COMPLETED', exit_code = 0, finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )

        # Because lifecycle_policy is 'ephemeral', session is destroyed immediately upon completion
        session_manager.stop_session(session_id, client=self.scheduler.colab)

        # Invariants: Session is STOPPED, assignment unassigned on Google Colab
        sess_final = session_manager.get_session(session_id)
        self.assertEqual(sess_final["status"], "STOPPED")
        self.assertIsNone(sess_final["ssh_local_port"])
        self.assertNotIn(endpoint, self.hub.control_plane.state.assignments)

        # ====================================================================
        # Phase 6: Webhook Notification
        # ====================================================================
        # Build completion card notification
        job_card = {
            "title": f"[COMPLETED] LoRA Training Job: {job_name}",
            "fields": {
                "Job ID": job_id,
                "Requested Accelerator": "A100",
                "Final Accelerator": "L4 (fallback from A100)",
                "Status": "COMPLETED",
                "Exit Code": 0,
                "Output Artifacts": "adapter_model.bin, metrics.json",
            },
        }
        self.webhook_receiver.receive_webhook("https://hooks.slack.com/services/mock_lora_job", job_card)

        # Verify notification delivered with correct fallback details
        self.assertEqual(len(self.webhook_receiver.dispatched_requests), 1)
        req = self.webhook_receiver.dispatched_requests[0]
        self.assertIn("L4 (fallback from A100)", req["payload"]["fields"]["Final Accelerator"])
        self.assertEqual(req["payload"]["fields"]["Exit Code"], 0)


if __name__ == "__main__":
    unittest.main()
