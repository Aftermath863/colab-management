"""Phase 3 Empirical Concurrency, Race Condition, and Stress Verification Suite.

Authored by teamwork_challenger_m3_1 (Concurrency & Stress Challenger).

This test suite empirically stress-tests the Phase 3 services under adversarial concurrency:
1. Concurrent telemetry samples ingestion across threads without SQLite locks.
2. Job runner FIFO queue under concurrency load and queue limits.
3. Job cancellation race conditions (QUEUED vs RUNNING).
4. Ephemeral session teardown guarantee under failures/exceptions.
5. Transient webhook failure handling and exponential backoff (with real loopback HTTP server).
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import http.server
import json
import os
import socket
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import unittest
import uuid

import httpx

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import JobCreate, SessionCreateRequest
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient
from backend.app.services.job_runner import JobRunnerService
from backend.app.services.notification import NotificationService
from backend.app.services.telemetry import TelemetryService
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount


def _drain_and_close_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Helper to cleanly await all background tasks before closing loop."""
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:
        pass
    finally:
        loop.close()


# ============================================================================
# Helpers: Ephemeral Loopback Webhook Server for Stress Testing
# ============================================================================

class EphemeralWebhookHandler(http.server.BaseHTTPRequestHandler):
    """Real HTTP request handler for webhook testing with customizable fault injection."""

    def log_message(self, format, *args):
        # Suppress noisy HTTP server logs during tests
        pass

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        req_time = time.time()

        server: "EphemeralWebhookServer" = self.server  # type: ignore
        server.record_request(self.path, self.headers, body, req_time)

        rule = server.get_next_response(self.path)
        status_code, resp_body = rule

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(resp_body.encode("utf-8") if isinstance(resp_body, str) else resp_body)


class EphemeralWebhookServer(http.server.HTTPServer):
    """Threaded loopback HTTP server tracking requests and returning programmed status codes."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        super().__init__((host, port), EphemeralWebhookHandler)
        self.requests: List[Dict[str, Any]] = []
        self._rules: List[Tuple[int, str]] = []
        self._default_rule: Tuple[int, str] = (200, json.dumps({"status": "ok"}))
        self._lock = threading.Lock()

    def set_rules(self, rules: List[Tuple[int, str]], default_rule: Tuple[int, str] = (200, '{"status": "ok"}')):
        with self._lock:
            self._rules = list(rules)
            self._default_rule = default_rule

    def record_request(self, path: str, headers: Any, body: bytes, req_time: float):
        with self._lock:
            self.requests.append({
                "path": path,
                "headers": dict(headers),
                "body": body.decode("utf-8", errors="replace"),
                "time": req_time,
            })

    def get_next_response(self, path: str) -> Tuple[int, str]:
        with self._lock:
            if self._rules:
                return self._rules.pop(0)
            return self._default_rule


class EphemeralWebhookManager:
    """Manages background lifecycle of the test webhook server."""

    def __init__(self):
        self.server: Optional[EphemeralWebhookServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> str:
        self.server = EphemeralWebhookServer()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_port
        return f"http://127.0.0.1:{port}/webhook"

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread:
            self.thread.join(timeout=2.0)
            self.thread = None


# ============================================================================
# Challenge 1: Telemetry Concurrency & WAL Stress
# ============================================================================

class TestChallengerTelemetryWALStress(unittest.TestCase):
    """Stress tests concurrent telemetry ingestion across threads/tasks without SQLite lock errors."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "challenger_wal.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Seed 3 parent sessions in database (to satisfy FOREIGN KEY constraint)
        self.session_ids = [f"sess_wal_stress_{i}" for i in range(3)]
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES ('acc_wal_test', 'wal.test@example.com', 'dummy_enc', 50.0, 'ACTIVE')
                """
            )
            for sid in self.session_ids:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        id, name, account_id, notebook_hash, endpoint, proxy_url,
                        proxy_token_enc, token_expires_at, accelerator, status
                    ) VALUES (?, ?, 'acc_wal_test', 'hash123', 'endpoint123', 'http://127.0.0.1/proxy',
                              'token_enc', CURRENT_TIMESTAMP, 'T4', 'READY')
                    """,
                    (sid, f"Session {sid}"),
                )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_concurrent_telemetry_ingestion_threads_no_locks(self):
        """20 concurrent threads inserting 500 samples simultaneously produce 0 SQLite lock errors."""
        total_threads = 20
        samples_per_thread = 25  # 500 total samples
        errors: List[Exception] = []

        def worker_task(thread_idx: int):
            try:
                sid = self.session_ids[thread_idx % len(self.session_ids)]
                for i in range(samples_per_thread):
                    gpu_util = float((thread_idx * 10 + i) % 100)
                    vram_used = float(1024 + i * 50)
                    with get_db(self.db_path) as conn:
                        conn.execute(
                            """
                            INSERT INTO telemetry_samples (
                                session_id, kernel_state, cpu_pct, ram_used_mb, ram_total_mb,
                                disk_used_gb, disk_total_gb, gpu_name, gpu_util_pct,
                                vram_used_mb, vram_total_mb
                            ) VALUES (?, 'busy', ?, ?, 16384.0, 15.0, 100.0, 'Tesla T4', ?, ?, 15360.0)
                            """,
                            (sid, 10.0 + i % 30, 2048.0 + i, gpu_util, vram_used),
                        )
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=total_threads) as executor:
            futures = [executor.submit(worker_task, idx) for idx in range(total_threads)]
            for f in as_completed(futures):
                f.result()

        # Zero lock errors or operational errors
        self.assertEqual(len(errors), 0, f"Encountered {len(errors)} lock errors: {errors[:3]}")

        # Invariant: exactly 500 samples persisted across all sessions
        with get_db(self.db_path) as conn:
            count = conn.execute("SELECT count(*) FROM telemetry_samples").fetchone()[0]
        self.assertEqual(count, total_threads * samples_per_thread)

    def test_concurrent_writers_readers_and_pruners(self):
        """Simultaneous high-concurrency writes, reads, and pruning calls do not deadlock or fail."""
        service = TelemetryService(max_samples_per_session=50)
        target_session = self.session_ids[0]
        errors: List[Exception] = []

        # Preload 80 samples for target_session
        with get_db(self.db_path) as conn:
            for i in range(80):
                conn.execute(
                    """
                    INSERT INTO telemetry_samples (session_id, gpu_name, vram_used_mb, vram_total_mb, gpu_util_pct)
                    VALUES (?, 'Tesla T4', 2048.0, 15360.0, 50.0)
                    """,
                    (target_session,),
                )

        def writer_loop():
            try:
                for i in range(30):
                    with get_db(self.db_path) as conn:
                        conn.execute(
                            """
                            INSERT INTO telemetry_samples (session_id, gpu_name, vram_used_mb, vram_total_mb, gpu_util_pct)
                            VALUES (?, 'Tesla T4', ?, 15360.0, 75.0)
                            """,
                            (target_session, 4000.0 + i),
                        )
                    time.sleep(0.002)
            except Exception as e:
                errors.append(e)

        def reader_loop():
            try:
                for _ in range(40):
                    with get_db(self.db_path) as conn:
                        rows = conn.execute(
                            "SELECT id, vram_used_mb FROM telemetry_samples WHERE session_id = ? ORDER BY id DESC LIMIT 10",
                            (target_session,),
                        ).fetchall()
                        self.assertTrue(len(rows) > 0)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def pruner_loop():
            try:
                for _ in range(15):
                    service._prune_samples(target_session)
                    time.sleep(0.005)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer_loop),
            threading.Thread(target=writer_loop),
            threading.Thread(target=reader_loop),
            threading.Thread(target=reader_loop),
            threading.Thread(target=pruner_loop),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Concurrent read/write/prune threw errors: {errors}")

        # Final pruning check
        service._prune_samples(target_session)
        with get_db(self.db_path) as conn:
            final_count = conn.execute(
                "SELECT count(*) FROM telemetry_samples WHERE session_id = ?", (target_session,)
            ).fetchone()[0]
        self.assertLessEqual(final_count, 50)


# ============================================================================
# Challenge 2: Job Runner Queue Concurrency & FIFO Ordering
# ============================================================================

class TestChallengerJobRunnerQueueConcurrency(unittest.TestCase):
    """Stress tests the Job Runner FIFO queue, concurrency semaphore limits, and queue draining."""

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
        self.db_path = os.path.join(self.tmpdir.name, "challenger_jobs.db")
        settings.DATABASE_PATH = self.db_path
        self.orig_data_dir = getattr(settings, "DATA_DIR", "./data")
        self.orig_workspace_dir = getattr(settings, "WORKSPACE_DIR", "./data/workspace")
        settings.DATA_DIR = self.tmpdir.name
        settings.WORKSPACE_DIR = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(settings.WORKSPACE_DIR, exist_ok=True)
        init_db(self.db_path)

        # Seed active Google account
        self.account_id = "acc_queue_stress"
        self.email = "queue.stress@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Queue Stress Account",
            sub="sub_qs_1",
            code="code_qs",
            access_token="ya29.mock_qs_token",
            refresh_token="1//mock_refresh_qs",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_qs_token",
            "refresh_token": "1//mock_refresh_qs",
            "expires_at": 9999999999,
        }
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Queue Tester', ?, 200.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        # Shut down any lingering SSH multiplexer gateways to free OS ports 2220-2229
        try:
            from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(ssh_multiplexer_service.shutdown())
            finally:
                loop.close()
        except Exception:
            pass

        settings.DATA_DIR = self.orig_data_dir
        settings.WORKSPACE_DIR = self.orig_workspace_dir
        self.tmpdir.cleanup()

    def test_fifo_queue_concurrency_semaphore_limit(self):
        """Under a limit of 3 concurrent jobs, active jobs never exceed 3, and all 9 jobs execute in FIFO order."""
        max_concurrency = 3
        total_jobs = 9

        colab_client = ColabClient(base_url=self.hub.urls["control_plane"])
        service = JobRunnerService(max_concurrency=max_concurrency, colab_client=colab_client)

        active_jobs = 0
        max_observed_active = 0
        start_order: List[int] = []
        lock = asyncio.Lock()

        # Monkey patch _execute_job_payload to simulate realistic asynchronous work and measure concurrency
        async def tracked_payload(job_id: str, session_id: str, job: Dict[str, Any]) -> int:
            nonlocal active_jobs, max_observed_active, start_order
            job_idx = int(job["name"].split("_")[-1])

            async with lock:
                active_jobs += 1
                if active_jobs > max_observed_active:
                    max_observed_active = active_jobs
                start_order.append(job_idx)

            # Simulated execution delay
            await asyncio.sleep(0.04)

            async with lock:
                active_jobs -= 1
            return 0

        service._execute_job_payload = tracked_payload  # type: ignore

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def run_scenario():
                await service.start()

                # Submit all 12 jobs in rapid sequence
                job_ids = []
                for idx in range(total_jobs):
                    req = JobCreate(
                        name=f"job_burst_{idx}",
                        target_accelerator="T4",
                        allow_fallback=True,
                        job_type="shell",
                        entry_path_or_cmd="echo ok",
                    )
                    resp = await service.submit_job(req)
                    job_ids.append(resp.id)

                # Wait for queue to drain completely
                await service._queue.join()

                # Wait for all active tasks to finalize
                if service._active_tasks:
                    await asyncio.gather(*list(service._active_tasks.values()), return_exceptions=True)

                await service.stop()
                return job_ids

            job_ids = loop.run_until_complete(run_scenario())
        finally:
            _drain_and_close_loop(loop)

        # Verification Invariant 1: Concurrency semaphore strictly limited active jobs <= 3
        self.assertLessEqual(max_observed_active, max_concurrency,
                             f"Active jobs ({max_observed_active}) exceeded semaphore limit ({max_concurrency})")
        self.assertGreaterEqual(max_observed_active, 2, "Concurrency did not actually execute parallel tasks")

        # Verification Invariant 2: FIFO submission order was respected
        # First 3 jobs must be [0, 1, 2] in some order, and earlier jobs must precede later ones
        self.assertEqual(len(start_order), total_jobs)
        self.assertEqual(set(start_order[:3]), {0, 1, 2}, f"First batch was not 0, 1, 2: {start_order[:3]}")
        # Job i must start before Job i + 3
        for i in range(total_jobs - max_concurrency):
            pos_i = start_order.index(i)
            pos_later = start_order.index(i + max_concurrency)
            self.assertLess(pos_i, pos_later, f"FIFO violation: Job {i} started at {pos_i} after Job {i + max_concurrency} at {pos_later}")

        # Verification Invariant 3: All 12 jobs transitioned to COMPLETED
        with get_db(self.db_path) as conn:
            statuses = [
                conn.execute("SELECT status FROM jobs WHERE id = ?", (jid,)).fetchone()["status"]
                for jid in job_ids
            ]
        self.assertTrue(all(s == "COMPLETED" for s in statuses), f"Not all jobs completed: {statuses}")


# ============================================================================
# Challenge 3: Job Cancellation Race Conditions (QUEUED vs RUNNING)
# ============================================================================

class TestChallengerJobCancellationRaces(unittest.TestCase):
    """Stress tests race conditions during job cancellation across QUEUED, RUNNING, and EPHEMERAL states."""

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
        self.db_path = os.path.join(self.tmpdir.name, "challenger_cancels.db")
        settings.DATABASE_PATH = self.db_path
        self.orig_data_dir = getattr(settings, "DATA_DIR", "./data")
        self.orig_workspace_dir = getattr(settings, "WORKSPACE_DIR", "./data/workspace")
        settings.DATA_DIR = self.tmpdir.name
        settings.WORKSPACE_DIR = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(settings.WORKSPACE_DIR, exist_ok=True)
        init_db(self.db_path)

        # Seed account
        self.account_id = "acc_cancel_stress"
        self.email = "cancel.stress@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Cancel Stress Account",
            sub="sub_cs_1",
            code="code_cs",
            access_token="ya29.mock_cs_token",
            refresh_token="1//mock_refresh_cs",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_cs_token",
            "refresh_token": "1//mock_refresh_cs",
            "expires_at": 9999999999,
        }
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Cancel Tester', ?, 200.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        try:
            from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(ssh_multiplexer_service.shutdown())
            finally:
                loop.close()
        except Exception:
            pass

        settings.DATA_DIR = self.orig_data_dir
        settings.WORKSPACE_DIR = self.orig_workspace_dir
        self.tmpdir.cleanup()

    def test_cancel_while_queued_preempts_execution_cleanly(self):
        """Cancelling a QUEUED job before dispatch marks CANCELLED and never provisions or executes."""
        colab_client = ColabClient(base_url=self.hub.urls["control_plane"])
        service = JobRunnerService(max_concurrency=1, colab_client=colab_client)

        executed_jobs: List[str] = []

        async def tracked_payload(job_id: str, session_id: str, job: Dict[str, Any]) -> int:
            executed_jobs.append(job_id)
            await asyncio.sleep(0.05)
            return 0

        service._execute_job_payload = tracked_payload  # type: ignore

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def run_scenario():
                # 1. Submit Job 1 and Job 2 (Job 2 is queued behind Job 1)
                req1 = JobCreate(name="Job_Long_1", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="echo 1")
                req2 = JobCreate(name="Job_Queued_2", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="echo 2")
                req3 = JobCreate(name="Job_Follower_3", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="echo 3")

                j1 = await service.submit_job(req1)
                j2 = await service.submit_job(req2)
                j3 = await service.submit_job(req3)

                # 2. Cancel Job 2 while strictly in QUEUED state
                cancel_ok = await service.cancel_job(j2.id)
                self.assertTrue(cancel_ok)

                # 3. Start dispatcher now
                await service.start()

                # Wait until Job 1 and Job 3 are done
                await service._queue.join()
                if service._active_tasks:
                    await asyncio.gather(*list(service._active_tasks.values()), return_exceptions=True)

                await service.stop()
                return j1.id, j2.id, j3.id

            j1_id, j2_id, j3_id = loop.run_until_complete(run_scenario())
        finally:
            _drain_and_close_loop(loop)

        # Job 2 must NEVER have reached _execute_job_payload
        self.assertNotIn(j2_id, executed_jobs)
        self.assertIn(j1_id, executed_jobs)
        self.assertIn(j3_id, executed_jobs)

        # In DB, Job 2 remains CANCELLED with finished_at set
        j2_db = service.get_job(j2_id)
        self.assertEqual(j2_db.status, "CANCELLED")
        self.assertIsNotNone(j2_db.finished_at)
        self.assertIsNone(j2_db.session_id)

    def test_cancel_while_running_aborts_task_and_releases_semaphore(self):
        """Cancelling a RUNNING job raises CancelledError, updates status=CANCELLED, and unblocks queue."""
        colab_client = ColabClient(base_url=self.hub.urls["control_plane"])
        service = JobRunnerService(max_concurrency=1, colab_client=colab_client)

        job_running_event = asyncio.Event()
        job_cancelled_event = asyncio.Event()

        async def hung_payload(job_id: str, session_id: str, job: Dict[str, Any]) -> int:
            if job["name"] == "Hung_Job":
                job_running_event.set()
                try:
                    await asyncio.sleep(60.0)  # Hung execution
                except asyncio.CancelledError:
                    job_cancelled_event.set()
                    raise
            return 0

        service._execute_job_payload = hung_payload  # type: ignore

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def run_scenario():
                await service.start()

                req_hung = JobCreate(name="Hung_Job", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="sleep 60")
                req_next = JobCreate(name="Next_Job", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="echo next")

                hung_job = await service.submit_job(req_hung)
                next_job = await service.submit_job(req_next)

                # Wait until Hung_Job is actively running
                await job_running_event.wait()

                # Cancel Hung_Job
                cancel_ok = await service.cancel_job(hung_job.id)
                self.assertTrue(cancel_ok)

                # Wait for cancellation event
                await job_cancelled_event.wait()

                # Now Next_Job must acquire the released semaphore and finish
                await service._queue.join()
                if service._active_tasks:
                    await asyncio.gather(*list(service._active_tasks.values()), return_exceptions=True)

                await service.stop()
                return hung_job.id, next_job.id

            hung_id, next_id = loop.run_until_complete(run_scenario())
        finally:
            _drain_and_close_loop(loop)

        hung_db = service.get_job(hung_id)
        next_db = service.get_job(next_id)

        self.assertEqual(hung_db.status, "CANCELLED")
        self.assertEqual(hung_db.exit_code, 130)
        self.assertEqual(next_db.status, "COMPLETED")

    def test_double_cancellation_and_terminal_state_cancel(self):
        """Calling cancel_job on a terminal job (COMPLETED/CANCELLED) returns False without crashing."""
        colab_client = ColabClient(base_url=self.hub.urls["control_plane"])
        service = JobRunnerService(max_concurrency=2, colab_client=colab_client)

        req = JobCreate(name="Short_Job", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="echo ok")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            job = loop.run_until_complete(service.submit_job(req))
            c1 = loop.run_until_complete(service.cancel_job(job.id))
            c2 = loop.run_until_complete(service.cancel_job(job.id))
        finally:
            _drain_and_close_loop(loop)

        self.assertTrue(c1, "First cancel should return True")
        self.assertFalse(c2, "Second cancel on already CANCELLED job should return False")


# ============================================================================
# Challenge 4: Ephemeral Session Teardown Guarantee Under Failures
# ============================================================================

class TestChallengerEphemeralTeardownUnderFailures(unittest.TestCase):
    """Stress tests that ephemeral sessions are strictly unassigned and stopped under all failure modes."""

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
        self.db_path = os.path.join(self.tmpdir.name, "challenger_teardown.db")
        settings.DATABASE_PATH = self.db_path
        self.orig_data_dir = getattr(settings, "DATA_DIR", "./data")
        self.orig_workspace_dir = getattr(settings, "WORKSPACE_DIR", "./data/workspace")
        settings.DATA_DIR = self.tmpdir.name
        settings.WORKSPACE_DIR = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(settings.WORKSPACE_DIR, exist_ok=True)
        init_db(self.db_path)

        # Seed account
        self.account_id = "acc_teardown_stress"
        self.email = "teardown.stress@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Teardown Stress Account",
            sub="sub_td_1",
            code="code_td",
            access_token="ya29.mock_td_token",
            refresh_token="1//mock_refresh_td",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.mock_td_token",
            "refresh_token": "1//mock_refresh_td",
            "expires_at": 9999999999,
        }
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Teardown Tester', ?, 200.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        try:
            from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(ssh_multiplexer_service.shutdown())
            finally:
                loop.close()
        except Exception:
            pass

        settings.DATA_DIR = self.orig_data_dir
        settings.WORKSPACE_DIR = self.orig_workspace_dir
        self.tmpdir.cleanup()

    def test_ephemeral_teardown_on_unhandled_exception(self):
        """When user code or payload raises an unhandled exception, ephemeral session is strictly unassigned and STOPPED."""
        colab_client = ColabClient(base_url=self.hub.urls["control_plane"])
        service = JobRunnerService(max_concurrency=2, colab_client=colab_client)

        async def crashing_payload(job_id: str, session_id: str, job: Dict[str, Any]) -> int:
            raise RuntimeError("CRITICAL USER CODE KERNEL EXPLOSION")

        service._execute_job_payload = crashing_payload  # type: ignore

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            req = JobCreate(name="Exception_Job", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="echo crash")
            job = loop.run_until_complete(service.submit_job(req))
            loop.run_until_complete(service._run_job_wrapper(job.id))
        finally:
            _drain_and_close_loop(loop)

        updated_job = service.get_job(job.id)
        self.assertEqual(updated_job.status, "FAILED")
        self.assertIsNotNone(updated_job.session_id)

        # Verify ephemeral session was STOPPED in DB
        with get_db(self.db_path) as conn:
            sess_row = conn.execute("SELECT status, lifecycle_policy FROM sessions WHERE id = ?", (updated_job.session_id,)).fetchone()
            acc_row = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (self.account_id,)).fetchone()

        self.assertEqual(sess_row["status"], "STOPPED")
        self.assertEqual(sess_row["lifecycle_policy"], "ephemeral")
        self.assertEqual(acc_row["active_assignments"], 0, "active_assignments was not decremented")

    def test_ephemeral_teardown_on_oom_kill(self):
        """When job process exits with code 137 (OOM killed), status=OOM_KILLED and ephemeral session is torn down."""
        colab_client = ColabClient(base_url=self.hub.urls["control_plane"])
        service = JobRunnerService(max_concurrency=2, colab_client=colab_client)

        async def oom_payload(job_id: str, session_id: str, job: Dict[str, Any]) -> int:
            return 137  # Standard Linux SIGKILL / OOM

        service._execute_job_payload = oom_payload  # type: ignore

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            req = JobCreate(name="OOM_Job", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="allocate_all_vram")
            job = loop.run_until_complete(service.submit_job(req))
            loop.run_until_complete(service._run_job_wrapper(job.id))
        finally:
            _drain_and_close_loop(loop)

        updated_job = service.get_job(job.id)
        self.assertEqual(updated_job.status, "OOM_KILLED")
        self.assertEqual(updated_job.exit_code, 137)

        # Verify ephemeral session was STOPPED in DB
        with get_db(self.db_path) as conn:
            sess_row = conn.execute("SELECT status FROM sessions WHERE id = ?", (updated_job.session_id,)).fetchone()
            acc_row = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (self.account_id,)).fetchone()

        self.assertEqual(sess_row["status"], "STOPPED")
        self.assertEqual(acc_row["active_assignments"], 0)

    def test_ephemeral_teardown_burst_no_orphans(self):
        """Burst of 5 consecutive failing ephemeral jobs leaves ZERO active or orphaned ephemeral sessions."""
        colab_client = ColabClient(base_url=self.hub.urls["control_plane"])
        service = JobRunnerService(max_concurrency=2, colab_client=colab_client)

        async def fail_payload(job_id: str, session_id: str, job: Dict[str, Any]) -> int:
            return 1

        service._execute_job_payload = fail_payload  # type: ignore

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for i in range(5):
                req = JobCreate(name=f"Burst_Fail_{i}", target_accelerator="T4", allow_fallback=True, job_type="shell", entry_path_or_cmd="exit 1")
                job = loop.run_until_complete(service.submit_job(req))
                loop.run_until_complete(service._run_job_wrapper(job.id))
                # Allow background gateway register/close tasks to drain
                loop.run_until_complete(asyncio.sleep(0.05))
        finally:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

        # Invariant: 0 ephemeral sessions remain in any active status
        with get_db(self.db_path) as conn:
            active_ephemerals = conn.execute(
                """
                SELECT count(*) FROM sessions
                WHERE lifecycle_policy = 'ephemeral'
                  AND status NOT IN ('STOPPED', 'FAILED', 'LOST')
                """
            ).fetchone()[0]
            acc_assignments = conn.execute("SELECT active_assignments FROM accounts WHERE id = ?", (self.account_id,)).fetchone()[0]

        self.assertEqual(active_ephemerals, 0, f"Found {active_ephemerals} orphaned ephemeral sessions!")
        self.assertEqual(acc_assignments, 0, f"Account active_assignments leaked: {acc_assignments}")


# ============================================================================
# Challenge 5: Transient Webhook Failure & Exponential Backoff
# ============================================================================

class TestChallengerWebhookRetryBackoffResilience(unittest.TestCase):
    """Stress tests real loopback HTTP webhook retry with exponential backoff and network failure isolation."""

    @classmethod
    def setUpClass(cls):
        cls.webhook_mgr = EphemeralWebhookManager()
        cls.webhook_url = cls.webhook_mgr.start()

    @classmethod
    def tearDownClass(cls):
        cls.webhook_mgr.stop()

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "challenger_webhooks.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)
        self.service = NotificationService(cooldown_seconds=3600.0)

        # Clear recorded requests on mock webhook server
        if self.webhook_mgr.server:
            self.webhook_mgr.server.requests.clear()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_transient_failure_500_503_exponential_backoff_success(self):
        """Transient HTTP 500 then 503 triggers exponential backoff delay and succeeds on 3rd attempt."""
        # Program server to respond with 500 on 1st, 503 on 2nd, and 200 on 3rd
        self.webhook_mgr.server.set_rules([
            (500, json.dumps({"error": "Internal Error 1"})),
            (503, json.dumps({"error": "Service Unavailable 2"})),
            (200, json.dumps({"success": True})),
        ])

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            t0 = time.time()
            success, msg = loop.run_until_complete(
                self.service._send_with_retry(
                    url=self.webhook_url,
                    headers={"Content-Type": "application/json"},
                    payload={"event": "stress.test"},
                    max_retries=3,
                    base_delay=0.04,
                )
            )
            elapsed = time.time() - t0
        finally:
            _drain_and_close_loop(loop)

        # Invariant 1: Final delivery succeeded
        self.assertTrue(success, f"Webhook delivery failed: {msg}")
        self.assertIn("HTTP 200", msg)

        # Invariant 2: Exactly 3 HTTP requests received
        requests = self.webhook_mgr.server.requests
        self.assertEqual(len(requests), 3, f"Expected 3 attempts, got {len(requests)}")

        # Invariant 3: Exponential backoff delay was observed:
        # attempt 1 -> delay ~0.04s -> attempt 2 -> delay ~0.08s -> attempt 3
        # Total expected backoff delay >= 0.12s
        self.assertGreaterEqual(elapsed, 0.10, f"Elapsed time {elapsed:.3f}s was too short for backoff (>=0.10s expected)")

    def test_permanent_failure_exhaustion_does_not_crash(self):
        """Permanent HTTP 500 failure exhausts max_retries, returns (False, msg), and does not raise exception."""
        self.webhook_mgr.server.set_rules([
            (500, json.dumps({"error": "Perm Fail 1"})),
            (500, json.dumps({"error": "Perm Fail 2"})),
            (500, json.dumps({"error": "Perm Fail 3"})),
        ])

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success, msg = loop.run_until_complete(
                self.service._send_with_retry(
                    url=self.webhook_url,
                    headers={"Content-Type": "application/json"},
                    payload={"event": "stress.perm_fail"},
                    max_retries=3,
                    base_delay=0.01,
                )
            )
        finally:
            _drain_and_close_loop(loop)

        self.assertFalse(success)
        self.assertIn("HTTP 500", msg)
        self.assertEqual(len(self.webhook_mgr.server.requests), 3)

    def test_connection_refused_network_error_handled_gracefully(self):
        """Unreachable host/port (connection refused) is caught and reported cleanly as Network Error."""
        # Find an unused closed port on localhost
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            unused_port = s.getsockname()[1]

        dead_url = f"http://127.0.0.1:{unused_port}/dead"

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success, msg = loop.run_until_complete(
                self.service._send_with_retry(
                    url=dead_url,
                    headers={"Content-Type": "application/json"},
                    payload={"event": "stress.dead"},
                    max_retries=2,
                    base_delay=0.01,
                )
            )
        finally:
            _drain_and_close_loop(loop)

        self.assertFalse(success)
        self.assertIn("Network Error", msg)

    def test_multi_channel_isolation_failures_do_not_block_healthy_channels(self):
        """When Channel A (500) and Channel B (unreachable) fail, Channel C (healthy) still receives alert."""
        self.webhook_mgr.server.set_rules([
            (500, '{"error": "failing_channel_a"}'),
            (500, '{"error": "failing_channel_a"}'),
            (500, '{"error": "failing_channel_a"}'),
            (200, '{"success": "healthy_channel_c"}'),
        ])

        # Find dead port for Channel B
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            dead_port = s.getsockname()[1]
        dead_url = f"http://127.0.0.1:{dead_port}/dead"

        with get_db(self.db_path) as conn:
            # Channel A: failing with 500
            conn.execute(
                """
                INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                VALUES ('ch_failing_a', 'Channel A (Failing)', 'webhook', ?, '["job.finished"]', 1)
                """,
                (encrypt_secret(json.dumps({"webhook_url": self.webhook_url})),),
            )
            # Channel B: dead port connection refusal
            conn.execute(
                """
                INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                VALUES ('ch_dead_b', 'Channel B (Dead)', 'webhook', ?, '["job.finished"]', 1)
                """,
                (encrypt_secret(json.dumps({"webhook_url": dead_url})),),
            )
            # Channel C: healthy
            conn.execute(
                """
                INSERT INTO notification_channels (id, name, provider, config_enc, events_json, enabled)
                VALUES ('ch_healthy_c', 'Channel C (Healthy)', 'webhook', ?, '["job.finished"]', 1)
                """,
                (encrypt_secret(json.dumps({"webhook_url": self.webhook_url})),),
            )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            results = loop.run_until_complete(
                self.service.dispatch_event(
                    event_type="job.finished",
                    title="Multi Channel Stress Test",
                    fields={"Job": "job_multi_1", "Result": "PASS"},
                    severity="INFO",
                    session_id="sess_multi_1",
                )
            )
        finally:
            _drain_and_close_loop(loop)

        self.assertEqual(len(results), 3, f"Expected results from all 3 channels, got {results}")

        res_map = {r["channel_id"]: r for r in results}
        self.assertFalse(res_map["ch_failing_a"]["success"], "Channel A should have failed")
        self.assertFalse(res_map["ch_dead_b"]["success"], "Channel B should have failed")
        self.assertTrue(res_map["ch_healthy_c"]["success"], "Channel C must succeed despite A and B failing")


if __name__ == "__main__":
    unittest.main()
