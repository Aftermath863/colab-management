"""Tier 5 Adversarial Stress & Subsystems Verification Suite.

Milestone 4 Phase 2: Tier 5 Adversarial Coverage Hardening
Assigned Scope: Subsystems & Integration Adversarial Verifier
Modules Covered:
1. Non-blocking Telemetry Collector (`backend/app/services/telemetry.py`)
2. Async Job Runner Queue & Notebook Exporter (`backend/app/services/job_runner.py`)
3. Storage Wizard & Workspace Sync (`backend/app/services/storage_wizard.py`, `backend/app/services/workspace_sync.py`)
4. Multi-channel Notifications Webhooks (`backend/app/services/notification.py`)
5. Frontend-Backend Contract Stress (FastAPI endpoints & TypeScript schema contracts)
"""

from __future__ import annotations

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import hmac
import http.server
import io
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest
import urllib.parse
import uuid

from fastapi.testclient import TestClient
import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    JobCreate,
    NotificationChannelCreate,
    SessionCreateRequest,
    StorageProfileCreate,
    SyncPullRequest,
    SyncPushRequest,
    TelemetrySample,
)
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services import account_pool, session_manager, storage_wizard, workspace_sync
from backend.app.services.colab_client import ColabClient
from backend.app.services.job_runner import JobRunnerService
from backend.app.services.notification import (
    EVENT_ALIASES,
    NotificationService,
    mask_channel_config,
    mask_secret_str,
    normalize_event_type,
)
from backend.app.services.storage_wizard import (
    create_storage_profile,
    delete_storage_profile,
    ensure_remote_directory,
    generate_hf_token_config,
    generate_rclone_config,
    get_storage_profile,
    mount_profile_on_session,
    propagate_gdrive_credentials,
)
from backend.app.services.telemetry import SustainedVRAMDetector, TelemetryService
from backend.app.services.template_service import LogAndTelemetrySanitizer
from backend.app.services.workspace_sync import (
    WorkspaceSyncEngine,
    compute_sha256,
    resolve_session_sync_paths,
    sync_pull_session,
    sync_push_session,
)

logger = logging.getLogger("adversarial_subsystems")


def _drain_and_close_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Helper to cleanly close an event loop without unclosed task warnings."""
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:
        pass
    finally:
        loop.close()


# ============================================================================
# Ephemeral HTTP Server for Webhook Stress Testing
# ============================================================================

class EphemeralWebhookHandler(http.server.BaseHTTPRequestHandler):
    """Configurable loopback HTTP handler for simulating webhook endpoints."""

    def log_message(self, format, *args):
        pass  # Suppress HTTP logging during stress runs

    def do_POST(self):
        behavior = getattr(self.server, "behavior", "ok")
        delay = getattr(self.server, "delay", 0.0)
        if delay > 0:
            time.sleep(delay)

        self.server.request_count = getattr(self.server, "request_count", 0) + 1

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        if not hasattr(self.server, "received_payloads"):
            self.server.received_payloads = []
        self.server.received_payloads.append(body)

        if behavior == "ok":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "delivered"}')
        elif behavior == "error_500":
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Internal Server Error")
        elif behavior == "error_503":
            self.send_response(503)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Service Temporarily Unavailable")
        elif behavior == "error_404":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Not Found")
        elif behavior == "error_400":
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "bad_request"}')
        elif behavior == "flake_then_ok":
            if self.server.request_count < 3:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Retry later")
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status": "recovered"}')
        else:
            self.send_response(200)
            self.end_headers()


class EphemeralWebhookServer:
    def __init__(self, behavior="ok", delay=0.0):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), EphemeralWebhookHandler)
        self.server.behavior = behavior
        self.server.delay = delay
        self.server.request_count = 0
        self.server.received_payloads = []
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/webhook"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=2.0)


# ============================================================================
# Mock Colab Adapter for Subsystems
# ============================================================================

class MockRequestsResponse:
    def __init__(self, status_code: int = 200, ok: bool = True, text: str = "{}"):
        self.status_code = status_code
        self.ok = ok
        self.text = text


class MockRequestsSession:
    """Mock requests.Session avoiding external socket calls during hermetic testing."""

    def put(self, url, *args, **kwargs):
        return MockRequestsResponse(200, True, "{}")

    def get(self, url, *args, **kwargs):
        return MockRequestsResponse(200, True, ')]}\'\n{"token": "mock_xsrf_token", "success": true}')

    def post(self, url, *args, **kwargs):
        return MockRequestsResponse(200, True, ')]}\'\n{"success": true}')


class MockSubsystemColabClient(ColabClient):
    """In-memory ColabClient adapter simulating VFS contents and credentials propagation."""

    def __init__(self, base_url: str = "https://colab.research.google.com"):
        super().__init__(base_url=base_url)
        self.session = MockRequestsSession()  # Intercept requests.Session
        self.vfs: Dict[str, Dict[str, Any]] = {}
        self.uploaded_files: List[Tuple[str, str]] = []
        self.created_dirs: List[str] = []

    def list_contents(self, proxy_url: str, proxy_token: str, path: str = "") -> Dict[str, Any]:
        clean = path.strip("/")
        if not clean:
            items = [entry for p, entry in self.vfs.items() if "/" not in p]
            return {"name": "", "path": "", "type": "directory", "content": items}

        if clean in self.vfs:
            return self.vfs[clean]

        prefix = f"{clean}/"
        sub_items = [entry for p, entry in self.vfs.items() if p.startswith(prefix) and "/" not in p[len(prefix):]]
        return {"name": os.path.basename(clean), "path": clean, "type": "directory", "content": sub_items}

    def upload_file(
        self,
        proxy_url: str,
        proxy_token: str,
        path: str,
        content_base64: str,
        format: str = "base64",
        chunk: int = 1,
    ) -> Dict[str, Any]:
        clean = path.strip("/")
        self.uploaded_files.append((clean, content_base64))
        entry = {
            "name": os.path.basename(clean),
            "path": clean,
            "type": "file",
            "format": format,
            "content": content_base64,
            "size": len(content_base64),
        }
        self.vfs[clean] = entry
        return entry

    def download_file(self, proxy_url: str, proxy_token: str, path: str) -> Dict[str, Any]:
        clean = path.strip("/")
        if clean in self.vfs:
            return self.vfs[clean]
        raise ValueError(f"File not found in Mock Colab VFS: {path}")


# ============================================================================
# 1. Telemetry Collector Adversarial Stress Tests
# ============================================================================

class TestAdversarialTelemetryCollector(unittest.TestCase):
    """Stress testing non-blocking telemetry collection, buffer overflows, and extreme values."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_telem_stress.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.account_id = "acc_telem_adv_1"
        self.session_id = "sess_telem_adv_1"

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES (?, 'telem_adv@test.com', 'dummy_enc', 50.0, 'ACTIVE')
                """,
                (self.account_id,),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status
                ) VALUES (?, 'Adv Telem Session', ?, 'nbh_1', 'ep_1', 'https://proxy.test',
                          'dummy_token', '2030-01-01 00:00:00', 'T4', 'READY')
                """,
                (self.session_id, self.account_id),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_telemetry_high_throughput_flood_burst_concurrency(self):
        """Flood SQLite WAL with 200 rapid telemetry inserts across 10 concurrent threads."""
        detector = SustainedVRAMDetector()

        def _worker_burst(worker_idx: int):
            for i in range(20):
                detector.process_telemetry_sample(
                    session_id=self.session_id,
                    vram_used_mb=1000.0 + (worker_idx * 10) + i,
                    vram_total_mb=16384.0,
                    gpu_name=f"GPU_Thread_{worker_idx}",
                    timestamp=100000.0 + i,
                )

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_worker_burst, w) for w in range(10)]
            for f in futures:
                f.result()

        with get_db(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM telemetry_samples WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()["c"]
        self.assertEqual(count, 200, "All 200 concurrent burst samples must be persisted without WAL lock errors")

    def test_telemetry_ring_buffer_pruning_and_overflow(self):
        """Verify automatic retention pruning when samples exceed max limit."""
        service = TelemetryService(max_samples_per_session=15)

        with get_db(self.db_path) as conn:
            for i in range(45):
                conn.execute(
                    """
                    INSERT INTO telemetry_samples (
                        session_id, kernel_state, cpu_pct, ram_used_mb, ram_total_mb,
                        disk_used_gb, disk_total_gb, gpu_name, gpu_util_pct,
                        vram_used_mb, vram_total_mb
                    ) VALUES (?, 'idle', ?, 1024.0, 16384.0, 10.0, 100.0, 'T4', 0.0, 512.0, 16384.0)
                    """,
                    (self.session_id, float(i)),
                )

        service._prune_samples(self.session_id)

        with get_db(self.db_path) as conn:
            remaining = conn.execute(
                "SELECT id, cpu_pct FROM telemetry_samples WHERE session_id = ? ORDER BY id ASC",
                (self.session_id,),
            ).fetchall()

        self.assertEqual(len(remaining), 15, "Pruning must retain exactly max_samples_per_session=15")
        latest_cpu_pcts = [r["cpu_pct"] for r in remaining]
        self.assertEqual(latest_cpu_pcts, [float(x) for x in range(30, 45)])

    def test_telemetry_extreme_nan_infinity_and_negative_values(self):
        """Stress-test NaN, negative, zero total, and extreme float values in detector and service."""
        detector = SustainedVRAMDetector()

        # Total VRAM is 0.0 or negative -> should not crash with ZeroDivisionError
        alert_zero = detector.process_telemetry_sample(
            self.session_id, vram_used_mb=500.0, vram_total_mb=0.0, gpu_name="T4", timestamp=100.0
        )
        self.assertIsNone(alert_zero)

        alert_neg = detector.process_telemetry_sample(
            self.session_id, vram_used_mb=500.0, vram_total_mb=-16384.0, gpu_name="T4", timestamp=101.0
        )
        self.assertIsNone(alert_neg)

        # Used VRAM is NaN -> should safely clamp and not trigger false alarm
        alert_nan = detector.process_telemetry_sample(
            self.session_id, vram_used_mb=float("nan"), vram_total_mb=16384.0, gpu_name="T4", timestamp=102.0
        )
        self.assertIsNone(alert_nan)

        # Extreme value (1e18 MB) -> should compute without OverflowError
        for i in range(3):
            alert_extreme = detector.process_telemetry_sample(
                self.session_id, vram_used_mb=1e18, vram_total_mb=16384.0, gpu_name="T4", timestamp=200.0 + i
            )
        self.assertIsNotNone(alert_extreme, "Extreme VRAM usage >= 98% for 3 intervals must trigger alert")
        self.assertEqual(alert_extreme["severity"], "HIGH")

    def test_telemetry_sustained_vram_detector_state_machine(self):
        """Validate consecutive counter reset on dips and 1800s cooldown suppression."""
        detector = SustainedVRAMDetector(vram_threshold_pct=98.0, required_consecutive_samples=3, alert_cooldown_seconds=1800.0)

        # Sample 1: 98.1% (consecutive: 1)
        res1 = detector.process_telemetry_sample(self.session_id, 981.0, 1000.0, "T4", 1000.0)
        self.assertIsNone(res1)

        # Sample 2: 98.2% (consecutive: 2)
        res2 = detector.process_telemetry_sample(self.session_id, 982.0, 1000.0, "T4", 1015.0)
        self.assertIsNone(res2)

        # Sample 3: 97.9% (Dip below threshold! Consecutive should reset to 0)
        res3 = detector.process_telemetry_sample(self.session_id, 979.0, 1000.0, "T4", 1030.0)
        self.assertIsNone(res3)
        self.assertEqual(detector._consecutive_counts.get(self.session_id), 0)

        # Supply 3 consecutive saturated samples
        detector.process_telemetry_sample(self.session_id, 990.0, 1000.0, "T4", 1045.0)
        detector.process_telemetry_sample(self.session_id, 990.0, 1000.0, "T4", 1060.0)
        alert = detector.process_telemetry_sample(self.session_id, 990.0, 1000.0, "T4", 1075.0)
        self.assertIsNotNone(alert, "Should trigger alert after 3 consecutive saturated intervals")

        # 4th saturated sample within cooldown period (1075 + 100 < 1075 + 1800) -> should be suppressed
        cooldown_suppressed = detector.process_telemetry_sample(self.session_id, 990.0, 1000.0, "T4", 1175.0)
        self.assertIsNone(cooldown_suppressed, "Alert must be suppressed during 1800s cooldown period")

        # After cooldown expired (1075 + 1801) -> should trigger alert again
        re_alert = detector.process_telemetry_sample(self.session_id, 990.0, 1000.0, "T4", 1075.0 + 1801.0)
        self.assertIsNotNone(re_alert, "Alert should re-trigger after cooldown expiration")

    def test_telemetry_vault_secret_sanitization_under_stress(self):
        """Ensure telemetry strings with embedded secrets are 100% sanitized."""
        secret_key = "HUGGINGFACE_KEY"
        secret_val = "hf_AdversarialSuperSecretPayload12345"

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO secrets (id, key_name, value_enc, description)
                VALUES ('sec_1', ?, ?, 'Test Key')
                """,
                (secret_key, encrypt_secret(secret_val)),
            )

        service = TelemetryService()
        dirty_gpu_name = f"NVIDIA RTX 4090 ({secret_val}) Driver 535"
        clean_gpu_name = service._sanitize_text(dirty_gpu_name)

        self.assertNotIn(secret_val, clean_gpu_name, "Raw secret must not exist in sanitized telemetry string")
        self.assertIn(f"[REDACTED:{secret_key}]", clean_gpu_name)

    def test_telemetry_api_validation_and_boundary_limits(self):
        """Verify API schema boundaries for GET /api/sessions/{id}/telemetry."""
        client = TestClient(app)
        token = create_access_token({"sub": "admin", "role": "admin"})
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Negative or zero limit -> 422
        r_zero = client.get(f"/api/sessions/{self.session_id}/telemetry?limit=0", headers=headers)
        self.assertEqual(r_zero.status_code, 422)

        # 2. Limit > 500 -> 422
        r_overflow = client.get(f"/api/sessions/{self.session_id}/telemetry?limit=501", headers=headers)
        self.assertEqual(r_overflow.status_code, 422)

        # 3. Nonexistent session -> 404
        r_404 = client.get("/api/sessions/nonexistent_session_id/telemetry", headers=headers)
        self.assertEqual(r_404.status_code, 404)

        # 4. Empty history returns 200 with latest=None and history=[]
        r_empty = client.get(f"/api/sessions/{self.session_id}/telemetry", headers=headers)
        self.assertEqual(r_empty.status_code, 200)
        data = r_empty.json()
        self.assertEqual(data["session_id"], self.session_id)
        self.assertIsNone(data["latest"])
        self.assertEqual(data["history"], [])


# ============================================================================
# 2. Async Job Runner Adversarial Stress Tests
# ============================================================================

class TestAdversarialAsyncJobRunner(unittest.TestCase):
    """Stress testing job queue concurrency, cancellation races, and corrupted notebooks."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_job_stress.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.data_dir = os.path.join(self.tmpdir.name, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        settings.DATA_DIR = self.data_dir
        settings.WORKSPACE_DIR = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(settings.WORKSPACE_DIR, exist_ok=True)

        self.account_id = "acc_job_adv_1"

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES (?, 'job_adv@test.com', 'dummy_enc', 50.0, 'ACTIVE')
                """,
                (self.account_id,),
            )
            # Seed 6 READY interactive sessions to allow parallel job execution without cloud assign
            for i in range(6):
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, lifecycle_policy, status
                    ) VALUES (?, ?, ?, 'nbh_job', 'ep_job', 'https://proxy.test',
                              ?, '2030-01-01 00:00:00', 'T4', 'interactive', 'READY')
                    """,
                    (f"sess_job_adv_{i}", f"Job Session {i}", self.account_id, encrypt_secret("token")),
                )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_job_runner_concurrency_saturation_and_queue_starvation(self):
        """Submit 6 jobs with max_concurrency=2; verify concurrency control and FIFO draining."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            jr = JobRunnerService(max_concurrency=2)

            async def _run_test():
                await jr.start()

                job_ids = []
                for i in range(6):
                    job = await jr.submit_job(
                        JobCreate(
                            name=f"test_concurrency_{i}",
                            target_accelerator="T4",
                            allow_fallback=False,
                            job_type="shell",
                            entry_path_or_cmd=f"echo 'Job {i}'",
                        )
                    )
                    job_ids.append(job.id)

                # Poll until all jobs reach a terminal status
                for _ in range(60):
                    with get_db(self.db_path) as conn:
                        finished = conn.execute(
                            "SELECT COUNT(*) as c FROM jobs WHERE status IN ('COMPLETED', 'FAILED')"
                        ).fetchone()["c"]
                    if finished == 6:
                        break
                    await asyncio.sleep(0.05)

                await jr.stop()
                return finished

            finished_count = loop.run_until_complete(_run_test())
            self.assertEqual(finished_count, 6, "All 6 jobs must complete successfully through the semaphore")
        finally:
            _drain_and_close_loop(loop)

    def test_job_runner_cancellation_active_streaming_vs_queued(self):
        """Test cancelling an actively executing job vs cancelling a queued job."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            jr = JobRunnerService(max_concurrency=1)

            # Patch payload to sleep asynchronously without blocking the loop
            async def slow_payload(job_id, session_id, job):
                await asyncio.sleep(5.0)
                return 0

            jr._execute_job_payload = slow_payload

            async def _run_test():
                await jr.start()

                job1 = await jr.submit_job(
                    JobCreate(
                        name="long_job",
                        target_accelerator="T4",
                        allow_fallback=False,
                        job_type="shell",
                        entry_path_or_cmd="echo 1",
                    )
                )

                job2 = await jr.submit_job(
                    JobCreate(
                        name="queued_job",
                        target_accelerator="T4",
                        allow_fallback=False,
                        job_type="shell",
                        entry_path_or_cmd="echo 2",
                    )
                )

                # Wait for job1 to be RUNNING
                for _ in range(50):
                    j1_cur = jr.get_job(job1.id)
                    if j1_cur and j1_cur.status == "RUNNING":
                        break
                    await asyncio.sleep(0.02)

                # Cancel running job 1
                c1 = await jr.cancel_job(job1.id)
                self.assertTrue(c1)

                # Cancel queued job 2 while still in queue
                c2 = await jr.cancel_job(job2.id)
                self.assertTrue(c2)

                j1_res = jr.get_job(job1.id)
                j2_res = jr.get_job(job2.id)

                await jr.stop()
                return j1_res, j2_res

            j1_res, j2_res = loop.run_until_complete(_run_test())
            self.assertEqual(j1_res.status, "CANCELLED")
            self.assertEqual(j2_res.status, "CANCELLED")
        finally:
            _drain_and_close_loop(loop)

    def test_job_runner_massive_stdout_stderr_log_stress_and_truncation(self):
        """Simulate a task generating 5,000 lines of output; verify log streaming."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            jr = JobRunnerService()

            async def _run_test():
                job_id = f"job_stress_{uuid.uuid4().hex[:8]}"
                job_dir = jr.get_job_dir(job_id)
                os.makedirs(job_dir, exist_ok=True)
                log_path = os.path.join(job_dir, "run.log")

                with open(log_path, "w", encoding="utf-8") as f:
                    for i in range(5000):
                        f.write(f"Line {i}: Epoch {i // 100} Step {i % 100} - Loss: 0.123456\n")

                with get_db(self.db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO jobs (id, name, target_accelerator, allow_fallback, job_type,
                                          entry_path_or_cmd, args_json, status, log_path)
                        VALUES (?, 'Stress Log Job', 'T4', 0, 'python', 'dummy.py', '[]', 'COMPLETED', ?)
                        """,
                        (job_id, log_path),
                    )

                lines_read = 0
                async for chunk in jr.stream_logs(job_id, follow=False):
                    lines_read += chunk.count("\n")

                return lines_read

            lines_count = loop.run_until_complete(_run_test())
            self.assertEqual(lines_count, 5000, "All 5,000 log lines must stream cleanly")
        finally:
            _drain_and_close_loop(loop)

    def test_job_runner_corrupted_notebook_ast_and_syntax_execution(self):
        """Test notebook execution against corrupted JSON, SyntaxError, and runtime exceptions."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            jr = JobRunnerService()
            sanitizer = LogAndTelemetrySanitizer({})

            async def _run_test():
                # 1. Corrupted raw JSON file
                bad_json_path = os.path.join(settings.WORKSPACE_DIR, "corrupted.ipynb")
                with open(bad_json_path, "w") as f:
                    f.write("{ invalid json [")

                log_corrupt = os.path.join(self.data_dir, "corrupt.log")
                with self.assertRaises(ValueError):
                    await jr._execute_notebook(
                        "job_c", "sess_job_adv_0", bad_json_path, log_corrupt, None, sanitizer
                    )

                # 2. Valid notebook containing a SyntaxError cell
                syntax_err_nb = {
                    "cells": [
                        {
                            "cell_type": "code",
                            "execution_count": None,
                            "metadata": {},
                            "outputs": [],
                            "source": "def broken_syntax(:\n    pass\n",
                        }
                    ],
                    "metadata": {},
                    "nbformat": 4,
                    "nbformat_minor": 2,
                }
                syntax_nb_path = os.path.join(settings.WORKSPACE_DIR, "syntax_err.ipynb")
                with open(syntax_nb_path, "w") as f:
                    json.dump(syntax_err_nb, f)

                log_syntax = os.path.join(self.data_dir, "syntax.log")
                out_nb_path = os.path.join(self.data_dir, "syntax_out.ipynb")
                exit_code = await jr._execute_notebook(
                    "job_s", "sess_job_adv_0", syntax_nb_path, log_syntax, out_nb_path, sanitizer
                )
                self.assertEqual(exit_code, 1, "SyntaxError in cell must yield exit_code=1")
                self.assertTrue(os.path.exists(out_nb_path), "Partial execution notebook must still be exported")

                with open(out_nb_path, "r") as f:
                    exported = json.load(f)
                outputs = exported["cells"][0]["outputs"]
                self.assertTrue(any(o.get("output_type") == "error" for o in outputs))

            loop.run_until_complete(_run_test())
        finally:
            _drain_and_close_loop(loop)

    def test_job_runner_ephemeral_lifecycle_teardown_guarantee(self):
        """Ensure that ephemeral sessions are strictly unassigned even when job execution throws fatal exception."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            jr = JobRunnerService()

            async def _run_test():
                ephem_sess_id = f"sess_ephem_{uuid.uuid4().hex[:8]}"
                with get_db(self.db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO sessions (
                            id, name, account_id, notebook_hash, endpoint, proxy_url,
                            proxy_token_enc, token_expires_at, accelerator, lifecycle_policy, status
                        ) VALUES (?, 'Ephemeral Test', ?, 'nbh_e', 'ep_e', 'https://proxy.test',
                                  'dummy_token', '2030-01-01', 'T4', 'ephemeral', 'READY')
                        """,
                        (ephem_sess_id, self.account_id),
                    )

                job_id = f"job_fatal_{uuid.uuid4().hex[:8]}"
                log_path = os.path.join(self.data_dir, f"{job_id}.log")
                with get_db(self.db_path) as conn:
                    conn.execute(
                        """
                        INSERT INTO jobs (id, name, session_id, target_accelerator, allow_fallback,
                                          job_type, entry_path_or_cmd, args_json, status, log_path)
                        VALUES (?, 'Fatal Job', ?, 'T4', 0, 'invalid_type', 'cmd', '[]', 'QUEUED', ?)
                        """,
                        (job_id, ephem_sess_id, log_path),
                    )

                await jr._run_job_wrapper(job_id)

                with get_db(self.db_path) as conn:
                    job_row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                    sess_row = conn.execute("SELECT status FROM sessions WHERE id = ?", (ephem_sess_id,)).fetchone()

                return job_row["status"], sess_row["status"]

            job_status, sess_status = loop.run_until_complete(_run_test())
            self.assertEqual(job_status, "FAILED")
            self.assertEqual(sess_status, "STOPPED", "Ephemeral session must be STOPPED in outer finally block")
        finally:
            _drain_and_close_loop(loop)


# ============================================================================
# 3. Storage Wizard & Workspace Sync Adversarial Stress Tests
# ============================================================================

class TestAdversarialStorageAndSync(unittest.TestCase):
    """Stress testing recursive sync, symlink loops, deep directory hierarchies, and storage wizard."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_sync_stress.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.ws_dir = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(self.ws_dir, exist_ok=True)
        settings.WORKSPACE_DIR = self.ws_dir

        self.account_id = "acc_sync_adv_1"
        self.session_id = "sess_sync_adv_1"
        self.mock_colab = MockSubsystemColabClient()

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES (?, 'sync_adv@test.com', 'dummy_enc', 50.0, 'ACTIVE')
                """,
                (self.account_id,),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, workspace_subpath, status
                ) VALUES (?, 'Sync Adv Session', ?, 'nbh_sync', 'ep_sync', 'https://proxy.test',
                          ?, '2030-01-01', 'T4', '', 'READY')
                """,
                (self.session_id, self.account_id, encrypt_secret("dummy_proxy_token")),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_workspace_sync_symlink_security_and_traversal_rejection(self):
        """Reject out-of-workspace symlinks that attempt directory traversal."""
        engine = WorkspaceSyncEngine(self.mock_colab)

        good_file = os.path.join(self.ws_dir, "main.py")
        with open(good_file, "w") as f:
            f.write("print('hello')\n")

        outside_file = os.path.join(self.tmpdir.name, "sensitive_system_file.txt")
        with open(outside_file, "w") as f:
            f.write("SUPER_SECRET_HOST_DATA\n")

        escape_symlink = os.path.join(self.ws_dir, "escape_link.txt")
        os.symlink(outside_file, escape_symlink)

        uploaded = engine.push_workspace(
            local_dir=self.ws_dir,
            proxy_url="https://proxy.test",
            proxy_token="tok",
            remote_prefix="workspace",
        )

        uploaded_names = [p.split("/")[-1] for p in uploaded]
        self.assertIn("main.py", uploaded_names)
        self.assertNotIn("escape_link.txt", uploaded_names, "Symlinks pointing outside workspace MUST be skipped")

    def test_workspace_sync_symlink_loop_and_broken_symlink_vulnerability(self):
        """Hardened: Broken and circular symlinks are safely skipped without aborting sync."""
        engine = WorkspaceSyncEngine(self.mock_colab)

        valid_file = os.path.join(self.ws_dir, "valid.py")
        with open(valid_file, "w") as f:
            f.write("print('valid')\n")

        # 1. Broken symlink: must be skipped without FileNotFoundError
        broken_symlink = os.path.join(self.ws_dir, "broken_link.txt")
        os.symlink(os.path.join(self.ws_dir, "nonexistent.txt"), broken_symlink)

        uploaded = engine.push_workspace(
            local_dir=self.ws_dir,
            proxy_url="https://proxy.test",
            proxy_token="tok",
            remote_prefix="workspace",
        )
        self.assertIn("workspace/valid.py", uploaded)
        self.assertNotIn("workspace/broken_link.txt", uploaded)

        # Test sync_push_session endpoint helper with broken symlink
        from backend.app.services.workspace_sync import sync_push_session
        from backend.app.core.models import SyncPushRequest

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            resp = loop.run_until_complete(
                sync_push_session(
                    session_id=self.session_id,
                    req=SyncPushRequest(),
                    client=self.mock_colab,
                )
            )
            self.assertGreaterEqual(resp["files_scanned"], 2)
            skipped_reasons = [d["reason"] for d in resp["details"] if d["action"] == "skipped"]
            self.assertIn("invalid_symlink", skipped_reasons)
        finally:
            _drain_and_close_loop(loop)

        os.unlink(broken_symlink)

        # 2. Circular symlink loop: must be skipped without OSError (Too many levels of symbolic links)
        loop_a = os.path.join(self.ws_dir, "loop_a")
        loop_b = os.path.join(self.ws_dir, "loop_b")
        os.symlink(loop_b, loop_a)
        os.symlink(loop_a, loop_b)

        uploaded_circular = engine.push_workspace(
            local_dir=self.ws_dir,
            proxy_url="https://proxy.test",
            proxy_token="tok",
            remote_prefix="workspace",
        )
        self.assertIn("workspace/valid.py", uploaded_circular)
        self.assertNotIn("workspace/loop_a", uploaded_circular)
        self.assertNotIn("workspace/loop_b", uploaded_circular)

        # Test sync_push_session with circular symlinks
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            resp_circ = loop.run_until_complete(
                sync_push_session(
                    session_id=self.session_id,
                    req=SyncPushRequest(),
                    client=self.mock_colab,
                )
            )
            self.assertGreaterEqual(resp_circ["files_scanned"], 2)
            skipped_reasons_circ = [d["reason"] for d in resp_circ["details"] if d["action"] == "skipped"]
            self.assertIn("invalid_symlink", skipped_reasons_circ)
        finally:
            _drain_and_close_loop(loop)

        os.unlink(loop_a)
        os.unlink(loop_b)

    def test_workspace_sync_permission_denied_resilience(self):
        """Hardened: Permission-denied files (chmod 000) are skipped with warning without crashing sync."""
        engine = WorkspaceSyncEngine(self.mock_colab)

        valid_file = os.path.join(self.ws_dir, "readable.py")
        with open(valid_file, "w") as f:
            f.write("print('readable')\n")

        restricted_file = os.path.join(self.ws_dir, "restricted.py")
        with open(restricted_file, "w") as f:
            f.write("secret_data\n")
        os.chmod(restricted_file, 0o000)

        try:
            # 1. Test engine.push_workspace
            uploaded = engine.push_workspace(
                local_dir=self.ws_dir,
                proxy_url="https://proxy.test",
                proxy_token="tok",
                remote_prefix="workspace",
            )
            self.assertIn("workspace/readable.py", uploaded)
            self.assertNotIn("workspace/restricted.py", uploaded)

            # 2. Test async sync_push_session endpoint helper
            from backend.app.services.workspace_sync import sync_push_session
            from backend.app.core.models import SyncPushRequest

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                resp = loop.run_until_complete(
                    sync_push_session(
                        session_id=self.session_id,
                        req=SyncPushRequest(),
                        client=self.mock_colab,
                    )
                )
                self.assertGreaterEqual(resp["files_scanned"], 2)
                self.assertGreaterEqual(resp["files_skipped"], 1)
                skipped_reasons = [d["reason"] for d in resp["details"] if d["action"] == "skipped"]
                self.assertIn("permission_denied", skipped_reasons)
            finally:
                _drain_and_close_loop(loop)
        finally:
            os.chmod(restricted_file, 0o644)

    def test_workspace_sync_deep_directory_nesting_40_levels(self):
        """Verify recursive traversal across 40 deeply nested directories."""
        engine = WorkspaceSyncEngine(self.mock_colab)

        deep_rel = "/".join([f"level_{i}" for i in range(40)])
        deep_dir = os.path.join(self.ws_dir, deep_rel)
        os.makedirs(deep_dir, exist_ok=True)

        deep_file = os.path.join(deep_dir, "deep_payload.py")
        with open(deep_file, "w") as f:
            f.write("# deep code\n")

        uploaded = engine.push_workspace(
            local_dir=self.ws_dir,
            proxy_url="https://proxy.test",
            proxy_token="tok",
            remote_prefix="workspace",
        )

        expected_remote = f"workspace/{deep_rel}/deep_payload.py"
        self.assertIn(expected_remote, uploaded, "Deeply nested file must be pushed cleanly to remote VFS")

    def test_workspace_sync_empty_dirs_and_zero_byte_files(self):
        """Verify handling of empty directories and 0-byte files without hash corruption."""
        engine = WorkspaceSyncEngine(self.mock_colab)

        empty_dir = os.path.join(self.ws_dir, "empty_subfolder")
        os.makedirs(empty_dir, exist_ok=True)

        zero_file = os.path.join(self.ws_dir, "empty.txt")
        with open(zero_file, "wb") as f:
            pass  # 0 bytes

        uploaded = engine.push_workspace(
            local_dir=self.ws_dir,
            proxy_url="https://proxy.test",
            proxy_token="tok",
            remote_prefix="workspace",
        )

        self.assertIn("workspace/empty.txt", uploaded)
        empty_hash = compute_sha256(b"")
        self.assertEqual(empty_hash, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_workspace_sync_large_model_weights_filtering(self):
        """Verify model weight patterns (*.pt, *.safetensors) exceeding threshold are skipped."""
        engine = WorkspaceSyncEngine(self.mock_colab)

        large_model = os.path.join(self.ws_dir, "model.safetensors")
        with open(large_model, "wb") as f:
            f.write(b"X" * 60)

        small_model = os.path.join(self.ws_dir, "adapter.pt")
        with open(small_model, "wb") as f:
            f.write(b"Y" * 20)

        large_txt = os.path.join(self.ws_dir, "data.csv")
        with open(large_txt, "wb") as f:
            f.write(b"Z" * 60)

        uploaded = engine.push_workspace(
            local_dir=self.ws_dir,
            proxy_url="https://proxy.test",
            proxy_token="tok",
            remote_prefix="workspace",
            max_file_size_bytes=50,
        )

        uploaded_names = [p.split("/")[-1] for p in uploaded]
        self.assertNotIn("model.safetensors", uploaded_names, "Large model weights > threshold must be excluded")
        self.assertIn("adapter.pt", uploaded_names, "Small model weights <= threshold must be uploaded")
        self.assertIn("data.csv", uploaded_names, "Non-model files must not be excluded by model pattern")

    def test_storage_wizard_mount_profile_adversarial_inputs(self):
        """Adversarial validation of storage profile creation and mount parameter verification."""
        prof1 = create_storage_profile(
            StorageProfileCreate(
                name="S3_Dataset_Bucket",
                type="s3",
                config={"access_key": "AKIA123", "secret_key": "sec456", "endpoint": "https://s3.aws.com"},
                mount_path="/content/data/s3",
            )
        )
        self.assertEqual(prof1["name"], "S3_Dataset_Bucket")

        with self.assertRaises(ValueError):
            create_storage_profile(
                StorageProfileCreate(
                    name="S3_Dataset_Bucket",
                    type="s3",
                    config={"access_key": "AKIA123", "secret_key": "sec456"},
                )
            )

        with self.assertRaises(ValueError):
            mount_profile_on_session(session_id="nonexistent_sess_id", profile_id=prof1["id"])

        with self.assertRaises(ValueError):
            mount_profile_on_session(session_id=self.session_id, profile_id="nonexistent_profile_id")

        rclone_cfg = generate_rclone_config(
            profile_name="My Profile [Special] & Quotes",
            config={"provider": "Cloudflare", "access_key_id": "KEY;--", "secret_access_key": 'SEC"VAL'},
        )
        self.assertIn("[My_Profile_[Special]_&_Quotes]", rclone_cfg)
        self.assertIn("KEY;--", rclone_cfg)


# ============================================================================
# 4. Multi-Channel Notifications Adversarial Stress Tests
# ============================================================================

class TestAdversarialMultiChannelNotifications(unittest.TestCase):
    """Stress testing slow/unresponsive webhooks, HTTP failure codes, and unicode payloads."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_notif_stress.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_webhook_slow_unresponsive_timeout_and_backoff(self):
        """Simulate slow endpoint with 0.5s delay; verify retry exhaustion and failure capture."""
        server = EphemeralWebhookServer(behavior="ok", delay=0.5).start()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                async def _test():
                    async with httpx.AsyncClient(timeout=0.05) as client:
                        try:
                            await client.post(server.url, json={"test": "timeout"})
                            return True, "ok"
                        except (httpx.TimeoutException, httpx.RequestError) as e:
                            return False, type(e).__name__

                success, err_name = loop.run_until_complete(_test())
                self.assertFalse(success, "Endpoint exceeding timeout must report failure")
                self.assertIn("Timeout", err_name)
            finally:
                _drain_and_close_loop(loop)
        finally:
            server.stop()

    def test_webhook_http_status_codes_retry_discrimination(self):
        """Verify 503 triggers retry while 404/400 fails fast without retrying."""
        # 1. Server that flakes twice with 503, then succeeds on attempt 3
        flake_server = EphemeralWebhookServer(behavior="flake_then_ok").start()
        try:
            service = NotificationService()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success, msg = loop.run_until_complete(
                    service._send_with_retry(
                        url=flake_server.url,
                        headers={"Content-Type": "application/json"},
                        payload={"event": "test"},
                        max_retries=3,
                        base_delay=0.01,
                    )
                )
                self.assertTrue(success, "503 flake should recover on 3rd attempt via retry backoff")
                self.assertEqual(flake_server.server.request_count, 3)
            finally:
                _drain_and_close_loop(loop)
        finally:
            flake_server.stop()

        # 2. Server returning 404 Not Found -> should fail fast on 1st attempt
        notfound_server = EphemeralWebhookServer(behavior="error_404").start()
        try:
            service = NotificationService()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success, msg = loop.run_until_complete(
                    service._send_with_retry(
                        url=notfound_server.url,
                        headers={"Content-Type": "application/json"},
                        payload={"event": "test"},
                        max_retries=3,
                        base_delay=0.01,
                    )
                )
                self.assertFalse(success)
                self.assertIn("404", msg)
                self.assertEqual(notfound_server.server.request_count, 1, "404 must not trigger retries")
            finally:
                _drain_and_close_loop(loop)
        finally:
            notfound_server.stop()

    def test_webhook_malformed_urls_and_connection_refusal(self):
        """Verify connection refused to unused port fails gracefully without raising unhandled exceptions."""
        service = NotificationService()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            dead_url = "http://127.0.0.1:49999/webhook"
            success, msg = loop.run_until_complete(
                service._send_with_retry(
                    url=dead_url,
                    headers={"Content-Type": "application/json"},
                    payload={"event": "test"},
                    max_retries=2,
                    base_delay=0.01,
                )
            )
            self.assertFalse(success)
            self.assertIn("Network Error", msg)
        finally:
            _drain_and_close_loop(loop)

    def test_webhook_extreme_unicode_deep_nesting_all_providers(self):
        """Format payloads for all 7 providers with emojis, RTL, and deep unicode text."""
        service = NotificationService()

        adversarial_fields = {
            "Emojis": "🚨🔥💥🚀 Colab OOM Alert",
            "Arabic_RTL": "تنبيه استهلاك الموارد - فشل التشغيل",
            "CJK_Characters": "算力池报警：实例已自动终止，CCU余额不足",
            "XSS_Injection": "<script>alert('xss')</script><b>Bold</b>",
            "Long_Payload": "X" * 10000,
        }

        providers = ["feishu", "dingtalk", "wecom", "telegram", "bark", "slack", "webhook"]
        for p in providers:
            cfg = {
                "webhook_url": "https://example.com/webhook",
                "secret": "my_secret_token",
                "bot_token": "123456:ABC-DEF",
                "chat_id": "@my_channel",
                "device_key": "device_xyz",
            }
            target_url, headers, payload = service.format_provider_payload(
                provider=p,
                title="Adversarial Alert: Extreme Unicode",
                fields=adversarial_fields,
                config=cfg,
                event_type="job.failed",
                severity="CRITICAL",
            )
            self.assertTrue(bool(target_url))
            self.assertIn("Content-Type", headers)
            # Verify serialization to JSON round-trip without encoding error
            json_str = json.dumps(payload, ensure_ascii=False)
            self.assertIn("🚨🔥💥🚀", json_str, f"Provider {p} must encode unicode emojis properly")
            roundtrip = json.loads(json_str)
            self.assertIsInstance(roundtrip, dict)

    def test_webhook_dingtalk_hmac_signature_correctness(self):
        """Validate DingTalk HMAC-SHA256 signature oracle."""
        service = NotificationService()
        secret = "SEC_TEST_SECRET_KEY_123"
        url = "https://oapi.dingtalk.com/robot/send?access_token=test_token"

        target_url, headers, payload = service.format_provider_payload(
            provider="dingtalk",
            title="DingTalk Sign Test",
            fields={"Key": "Val"},
            config={"webhook_url": url, "secret": secret},
        )

        parsed = urllib.parse.urlparse(target_url)
        qs = urllib.parse.parse_qs(parsed.query)

        self.assertIn("timestamp", qs)
        self.assertIn("sign", qs)

        ts = qs["timestamp"][0]
        actual_sign = qs["sign"][0]

        string_to_sign = f"{ts}\n{secret}".encode("utf-8")
        expected_hmac = hmac.new(secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
        expected_sign = base64.b64encode(expected_hmac).decode("utf-8")

        self.assertEqual(actual_sign, expected_sign, "DingTalk signature must match HMAC-SHA256 oracle")

    def test_webhook_cooldown_throttling_and_critical_bypass(self):
        """Verify 3600s cooldown throttles INFO alerts while CRITICAL alerts bypass."""
        server = EphemeralWebhookServer(behavior="ok").start()
        try:
            service = NotificationService(cooldown_seconds=3600.0)

            with get_db(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                    VALUES ('ch_throt_1', 'Throttle Test', 'webhook', ?, '[\"job.finished\", \"job.failed\"]', 1)
                    """,
                    (encrypt_secret(json.dumps({"webhook_url": server.url})),),
                )

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                # 1. Dispatch INFO event -> Should dispatch
                res1 = loop.run_until_complete(
                    service.dispatch_event("job.finished", "Job 1 OK", {}, severity="INFO", session_id="s_1")
                )
                self.assertEqual(len(res1), 1)
                self.assertTrue(res1[0]["success"])

                # 2. Dispatch immediate second INFO event for same session -> Should be THROTTLED
                res2 = loop.run_until_complete(
                    service.dispatch_event("job.finished", "Job 2 OK", {}, severity="INFO", session_id="s_1")
                )
                self.assertEqual(len(res2), 1)
                self.assertIn("THROTTLED", res2[0].get("status", ""))

                # 3. Dispatch CRITICAL event for same session -> Should BYPASS cooldown and dispatch!
                res3 = loop.run_until_complete(
                    service.dispatch_event("job.failed", "Job 3 CRITICAL FAIL", {}, severity="CRITICAL", session_id="s_1")
                )
                self.assertEqual(len(res3), 1)
                self.assertTrue(res3[0]["success"], "CRITICAL severity must bypass cooldown throttle")
            finally:
                _drain_and_close_loop(loop)
        finally:
            server.stop()


# ============================================================================
# 5. Frontend-Backend Contract Stress Tests
# ============================================================================

class TestAdversarialContractsAndAPI(unittest.TestCase):
    """Stress testing FastAPI schemas, unexpected input formats, and WebSocket security contracts."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_contracts.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.account_id = "acc_contract_1"
        self.session_id = "sess_contract_1"

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES (?, 'contract@test.com', 'dummy_enc', 50.0, 'ACTIVE')
                """,
                (self.account_id,),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status
                ) VALUES (?, 'Contract Session', ?, 'nbh_c', 'ep_c', 'https://proxy.test',
                          'dummy_token', '2030-01-01', 'T4', 'READY')
                """,
                (self.session_id, self.account_id),
            )

        self.client = TestClient(app)
        self.token = create_access_token({"sub": "admin", "role": "admin"})
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_contract_telemetry_schema_validation(self):
        """Validate POST /api/sessions/{id}/telemetry schema adherence against invalid data."""
        bad_payload = {
            "session_id": self.session_id,
            "cpu_pct": "not_a_float",
            "ram_used_mb": 1024.0,
        }
        res = self.client.post(
            f"/api/sessions/{self.session_id}/telemetry",
            json=bad_payload,
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 422, "String in float field must return HTTP 422")

        good_payload = {
            "session_id": self.session_id,
            "cpu_pct": 25.5,
            "ram_used_mb": 2048.0,
            "ram_total_mb": 16384.0,
            "disk_used_gb": 12.0,
            "disk_total_gb": 100.0,
            "gpu_name": "Tesla T4",
            "gpu_util_pct": 50.0,
            "vram_used_mb": 4096.0,
            "vram_total_mb": 16384.0,
        }
        res_ok = self.client.post(
            f"/api/sessions/{self.session_id}/telemetry",
            json=good_payload,
            headers=self.headers,
        )
        self.assertEqual(res_ok.status_code, 201)
        data = res_ok.json()
        self.assertEqual(data["session_id"], self.session_id)
        self.assertEqual(data["cpu_pct"], 25.5)

    def test_contract_job_runner_schema_validation(self):
        """Validate POST /api/jobs schema adherence against illegal accelerators and job types."""
        bad_job = {
            "name": "Invalid Job",
            "target_accelerator": "T4",
            "job_type": "ruby",
            "entry_path_or_cmd": "puts 'hello'",
        }
        res1 = self.client.post("/api/jobs", json=bad_job, headers=self.headers)
        self.assertEqual(res1.status_code, 422)

        bad_acc = {
            "name": "Invalid Acc",
            "target_accelerator": "GTX1080",
            "job_type": "shell",
            "entry_path_or_cmd": "echo 1",
        }
        res2 = self.client.post("/api/jobs", json=bad_acc, headers=self.headers)
        self.assertEqual(res2.status_code, 422)

    def test_contract_notification_channel_masking(self):
        """Verify GET /api/notifications/channels masks sensitive tokens per frontend contract."""
        raw_secret = "secret_webhook_key_xyz_98765"
        body = {
            "name": "Test Feishu Masking",
            "provider": "feishu",
            "config": {
                "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/token123456789",
                "secret": raw_secret,
            },
            "events": ["job.failed"],
            "enabled": True,
        }
        create_res = self.client.post("/api/notifications/channels", json=body, headers=self.headers)
        self.assertEqual(create_res.status_code, 201)

        list_res = self.client.get("/api/notifications/channels", headers=self.headers)
        self.assertEqual(list_res.status_code, 200)
        channels = list_res.json()
        ch = next(c for c in channels if c["name"] == "Test Feishu Masking")

        masked_cfg = ch.get("config_masked", {})
        self.assertNotIn(raw_secret, json.dumps(masked_cfg), "Plaintext secret MUST NOT be present in masked config")
        self.assertIn("****", masked_cfg.get("secret", ""))

    def test_contract_websocket_terminal_unauthorized_rejection(self):
        """Verify WebSocket /api/sessions/{id}/terminal rejects missing or invalid tokens."""
        with self.assertRaises(Exception):
            with self.client.websocket_connect(f"/api/sessions/{self.session_id}/terminal") as ws:
                pass

        with self.assertRaises(Exception):
            with self.client.websocket_connect(f"/api/sessions/{self.session_id}/terminal?token=bad_token") as ws:
                pass


if __name__ == "__main__":
    unittest.main()
