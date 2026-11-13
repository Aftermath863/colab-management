"""Asynchronous Job Runner & Execution Engine for Colab Management Platform.

Coordinates:
1. In-memory async job queue with SQLite persistence across all statuses
   (QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED, OOM_KILLED).
2. Supports 3 execution types:
   - python: executes .py scripts with sys.argv, __name__='__main__', and injected secrets.
   - notebook: executes .ipynb notebooks, captures cell outputs, exports /data/jobs/{id}/output.ipynb.
   - shell: executes bash commands and scripts with real-time log streaming.
3. Live log streaming to /data/jobs/{id}/run.log and async generator for streaming API.
4. Ephemeral session lifecycle: automatically provisions and unassigns on job finish.
5. Cross-account accelerator failover: cascades fallback matrix across eligible accounts.
6. Storage profile preflight verification and workspace sync integration.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shlex
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import nbformat
from nbformat.v4 import new_output

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.models import (
    HIGH_MEM_ONLY_ACCELERATORS,
    AcceleratorType,
    JobCreate,
    JobResponse,
    JobStatus,
    SessionCreateRequest,
)
from backend.app.core.security import decrypt_secret
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient
from backend.app.services.template_service import LogAndTelemetrySanitizer, resolve_template_env

logger = logging.getLogger("job_runner")

ACCELERATOR_FALLBACK_MATRIX: Dict[str, List[str]] = {
    "H100": ["A100", "L4", "T4"],
    "A100": ["L4", "T4"],
    "L4": ["T4"],
    "G4": ["T4"],
    "V6E1": ["V5E1", "T4"],
    "V5E1": ["T4"],
    "T4": [],
}


class JobRunnerService:
    """Core daemon service managing asynchronous job scheduling and execution."""

    def __init__(self, max_concurrency: int = 5, colab_client: Optional[ColabClient] = None):
        self.max_concurrency = max_concurrency
        self.colab = colab_client or ColabClient()
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._worker_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def start(self) -> None:
        """Start background dispatcher loop and enqueue existing QUEUED jobs from database."""
        self._shutdown_event = asyncio.Event()
        self._queue = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id FROM jobs WHERE status = 'QUEUED' ORDER BY rowid ASC"
            ).fetchall()
            for r in rows:
                await self._queue.put(r["id"])

        self._worker_task = asyncio.create_task(self._dispatcher_loop())
        logger.info("JobRunnerService dispatcher loop started.")

    async def stop(self) -> None:
        """Gracefully stop dispatcher loop and cancel active jobs."""
        self._shutdown_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        for job_id, task in list(self._active_tasks.items()):
            task.cancel()
        self._active_tasks.clear()
        logger.info("JobRunnerService stopped.")

    async def submit_job(self, req: JobCreate) -> JobResponse:
        """Submit a new job, persist to database with status QUEUED, and enqueue."""
        job_id = f"job_{uuid.uuid4().hex[:12]}"
        job_dir = self.get_job_dir(job_id)
        os.makedirs(job_dir, exist_ok=True)

        log_path = os.path.join(job_dir, "run.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"=== Job {job_id} ({req.name}) Submitted at {datetime.now(timezone.utc).isoformat()} ===\n")

        output_nb_path = (
            os.path.join(job_dir, "output.ipynb") if req.job_type == "notebook" else None
        )

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, name, target_accelerator, allow_fallback, template_id,
                    job_type, entry_path_or_cmd, args_json, status, log_path,
                    output_notebook_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?)
                """,
                (
                    job_id,
                    req.name,
                    req.target_accelerator,
                    1 if req.allow_fallback else 0,
                    req.template_id,
                    req.job_type,
                    req.entry_path_or_cmd,
                    json.dumps(req.args),
                    log_path,
                    output_nb_path,
                ),
            )

        await self._queue.put(job_id)
        logger.info(f"Enqueued job {job_id} ({req.name}).")
        return self.get_job(job_id)  # type: ignore

    def get_job_dir(self, job_id: str) -> str:
        """Compute absolute job artifacts directory."""
        data_dir = getattr(settings, "DATA_DIR", "/data")
        return os.path.join(data_dir, "jobs", job_id)

    def get_job(self, job_id: str) -> Optional[JobResponse]:
        """Fetch job details from database."""
        with get_db() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return JobResponse(
                id=row["id"],
                name=row["name"],
                session_id=row["session_id"],
                target_accelerator=row["target_accelerator"],
                allow_fallback=bool(row["allow_fallback"]),
                template_id=row["template_id"],
                job_type=row["job_type"],
                entry_path_or_cmd=row["entry_path_or_cmd"],
                args=json.loads(row["args_json"] or "[]"),
                status=row["status"],
                exit_code=row["exit_code"],
                log_path=row["log_path"],
                output_notebook_path=row["output_notebook_path"],
                started_at=str(row["started_at"]) if row["started_at"] else None,
                finished_at=str(row["finished_at"]) if row["finished_at"] else None,
            )

    def list_jobs(self, status: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[JobResponse]:
        """List jobs with optional status filter."""
        with get_db() as conn:
            query = "SELECT * FROM jobs"
            params: List[Any] = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            query += " ORDER BY rowid DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            rows = conn.execute(query, params).fetchall()
            return [
                JobResponse(
                    id=r["id"],
                    name=r["name"],
                    session_id=r["session_id"],
                    target_accelerator=r["target_accelerator"],
                    allow_fallback=bool(r["allow_fallback"]),
                    template_id=r["template_id"],
                    job_type=r["job_type"],
                    entry_path_or_cmd=r["entry_path_or_cmd"],
                    args=json.loads(r["args_json"] or "[]"),
                    status=r["status"],
                    exit_code=r["exit_code"],
                    log_path=r["log_path"],
                    output_notebook_path=r["output_notebook_path"],
                    started_at=str(r["started_at"]) if r["started_at"] else None,
                    finished_at=str(r["finished_at"]) if r["finished_at"] else None,
                )
                for r in rows
            ]

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued or running job."""
        with get_db() as conn:
            row = conn.execute("SELECT status, session_id, log_path FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return False
            status = row["status"]
            session_id = row["session_id"]
            log_path = row["log_path"]

        if status in ("COMPLETED", "FAILED", "CANCELLED", "OOM_KILLED"):
            return False

        if job_id in self._active_tasks:
            self._active_tasks[job_id].cancel()

        if log_path and os.path.exists(log_path):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n[CANCELLED] Job cancelled by user.\n")

        with get_db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'CANCELLED', finished_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (job_id,),
            )

        if session_id:
            try:
                with get_db() as conn:
                    s_row = conn.execute(
                        "SELECT lifecycle_policy FROM sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    if s_row and s_row["lifecycle_policy"] == "ephemeral":
                        session_manager.stop_session(session_id, client=self.colab)
            except Exception as e:
                logger.warning(f"Error stopping session {session_id} on cancellation: {e}")

        logger.info(f"Job {job_id} cancelled.")
        return True

    async def _dispatcher_loop(self) -> None:
        """Continually pops jobs from queue and launches task under concurrency semaphore."""
        while not self._shutdown_event.is_set():
            try:
                job_id = await self._queue.get()
                await self._semaphore.acquire()

                task = asyncio.create_task(self._run_job_wrapper(job_id))
                self._active_tasks[job_id] = task

                def _cleanup(t, jid=job_id):
                    self._active_tasks.pop(jid, None)
                    self._semaphore.release()
                    self._queue.task_done()

                task.add_done_callback(_cleanup)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in dispatcher loop: {e}", exc_info=True)
                await asyncio.sleep(0.5)

    async def _run_job_wrapper(self, job_id: str) -> None:
        """Wrapper handling pre-checks, execution, exception handling, and ephemeral unassignment."""
        with get_db() as conn:
            job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not job or job["status"] == "CANCELLED":
                return

        session_id: Optional[str] = job["session_id"]
        is_ephemeral: bool = False
        final_status: JobStatus = "FAILED"
        exit_code: int = 1
        log_path = job["log_path"]

        os.makedirs(os.path.dirname(log_path), exist_ok=True)

        try:
            # 1. Provision session if none assigned
            if not session_id:
                prov_result = await self._provision_session_with_fallback(
                    job_id=job_id,
                    target_accelerator=job["target_accelerator"],
                    allow_fallback=bool(job["allow_fallback"]),
                    template_id=job["template_id"],
                )
                session_id = prov_result["session_id"]
                is_ephemeral = prov_result["is_ephemeral"]
            else:
                with get_db() as conn:
                    s_row = conn.execute(
                        "SELECT lifecycle_policy FROM sessions WHERE id = ?", (session_id,)
                    ).fetchone()
                    is_ephemeral = (s_row and s_row["lifecycle_policy"] == "ephemeral")

            # 2. Update status to RUNNING
            with get_db() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = 'RUNNING', session_id = ?, started_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (session_id, job_id),
                )

            # 3. Preflight Storage Profile Mount Verification
            if job["template_id"]:
                mount_ok, mount_err = self._verify_storage_preflight(session_id, job["template_id"])
                if not mount_ok:
                    raise RuntimeError(f"Storage preflight check failed: {mount_err}")

            # 4. Pre-Job Workspace Sync Push
            self._sync_workspace_pre_job(session_id)

            # 5. Execute Job by Type
            exit_code = await self._execute_job_payload(job_id, session_id, job)

            if exit_code == 137:
                final_status = "OOM_KILLED"
            elif exit_code == 0:
                final_status = "COMPLETED"
            else:
                final_status = "FAILED"

            # 6. Post-Job Workspace Sync Pull
            self._sync_workspace_post_job(session_id)

        except asyncio.CancelledError:
            final_status = "CANCELLED"
            exit_code = 130
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n[CANCELLED] Execution cancelled.\n")
        except Exception as e:
            final_status = "FAILED"
            exit_code = 1
            logger.error(f"Job {job_id} failed with error: {e}", exc_info=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[ERROR] Job failed: {str(e)}\n")
        finally:
            # 7. Update database record
            with get_db() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, exit_code = ?, finished_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (final_status, exit_code, job_id),
                )

            # 8. Ephemeral Session Unassignment Guarantee
            if session_id and is_ephemeral:
                try:
                    logger.info(f"Unassigning ephemeral session {session_id} for job {job_id}...")
                    session_manager.stop_session(session_id, client=self.colab)
                except Exception as e:
                    logger.warning(f"Error unassigning ephemeral session {session_id}: {e}")

            # 9. Multi-Channel Webhook Dispatch
            try:
                from backend.app.services.notification import notification_service
                event_name = "job.finished" if final_status == "COMPLETED" else "job.failed"
                severity = "INFO" if final_status == "COMPLETED" else "CRITICAL"
                asyncio.create_task(
                    notification_service.dispatch_event(
                        event_type=event_name,
                        title=f"Colab Job {final_status}: {job['name']}",
                        fields={
                            "Job ID": job_id,
                            "Job Name": job["name"],
                            "Status": final_status,
                            "Exit Code": str(exit_code),
                            "Session ID": str(session_id),
                        },
                        severity=severity,
                        session_id=session_id,
                    )
                )
            except Exception:
                pass

    async def _provision_session_with_fallback(
        self,
        job_id: str,
        target_accelerator: str,
        allow_fallback: bool,
        template_id: Optional[str],
    ) -> Dict[str, Any]:
        """Provisions an ephemeral session or reuses an eligible session, cascading through fallback accelerators."""
        # 1. Attempt to find existing READY session matching target_accelerator
        with get_db() as conn:
            ready_sess = conn.execute(
                """
                SELECT id, accelerator FROM sessions
                WHERE accelerator = ? AND status = 'READY' AND lifecycle_policy != 'ephemeral'
                ORDER BY created_at ASC LIMIT 1
                """,
                (target_accelerator,),
            ).fetchone()
            if ready_sess:
                conn.execute("UPDATE sessions SET status = 'BUSY' WHERE id = ?", (ready_sess["id"],))
                return {"session_id": ready_sess["id"], "is_ephemeral": False}

        # 2. Build candidate accelerator list
        candidates = [target_accelerator]
        if allow_fallback and target_accelerator in ACCELERATOR_FALLBACK_MATRIX:
            candidates.extend(ACCELERATOR_FALLBACK_MATRIX[target_accelerator])

        # 3. Retrieve eligible accounts ranked by priority and CCU score
        eligible_accounts = self._get_eligible_accounts_ranked()
        if not eligible_accounts:
            raise RuntimeError("No eligible active Google accounts in pool with available CCU quota")

        last_error = None
        for acc in candidates:
            # Enforce standard shape rule for L4/V5E1/V6E1
            shape = "standard"
            for acc_id in eligible_accounts:
                try:
                    sess_name = f"job-sess-{acc.lower()}-{job_id[:6]}"
                    req = SessionCreateRequest(
                        name=sess_name,
                        accelerator=acc,  # type: ignore
                        shape=shape,
                        lifecycle_policy="ephemeral",
                        account_id=acc_id,
                        template_id=template_id,
                    )
                    sess = session_manager.create_session(req, client=self.colab)
                    logger.info(
                        f"Provisioned ephemeral session {sess['id']} ({acc}) on account {acc_id} for job {job_id}"
                    )
                    return {"session_id": sess["id"], "is_ephemeral": True}
                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"Failed provisioning {acc} on account {acc_id}: {e}. Retrying fallback candidate..."
                    )
                    continue

        raise RuntimeError(f"Failed to provision job: {last_error}")

    def _get_eligible_accounts_ranked(self) -> List[str]:
        """Rank eligible accounts: Score = (priority * 1000) + (ccu * 10) - (assignments * 200)."""
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, priority, ccu_balance, hard_stop_ccu, active_assignments, status
                FROM accounts
                WHERE status = 'ACTIVE' AND ccu_balance > hard_stop_ccu
                """
            ).fetchall()
            scored = []
            for r in rows:
                score = (r["priority"] * 1000) + (r["ccu_balance"] * 10) - (r["active_assignments"] * 200)
                scored.append((score, r["id"]))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [acc_id for _, acc_id in scored]

    def _verify_storage_preflight(self, session_id: str, template_id: str) -> Tuple[bool, str]:
        """Preflight verification of cloud storage mount paths (e.g. Google Drive)."""
        with get_db() as conn:
            tpl = conn.execute("SELECT storage_profile_ids_json FROM templates WHERE id = ?", (template_id,)).fetchone()
            if not tpl:
                return True, ""
            sp_ids = json.loads(tpl["storage_profile_ids_json"] or "[]")
            if not sp_ids:
                return True, ""

            sess = conn.execute("SELECT proxy_url, proxy_token_enc FROM sessions WHERE id = ?", (session_id,)).fetchone()
            if not sess:
                return False, f"Session {session_id} not found"

            proxy_url = sess["proxy_url"]
            proxy_token = decrypt_secret(sess["proxy_token_enc"])

            for sp_id in sp_ids:
                prof = conn.execute("SELECT name, mount_path FROM storage_profiles WHERE id = ?", (sp_id,)).fetchone()
                if not prof:
                    continue
                raw_mount = prof["mount_path"]
                rel_path = raw_mount
                if rel_path.startswith("/content/"):
                    rel_path = rel_path[len("/content/"):]
                rel_path = rel_path.strip("/")

                try:
                    contents = self.colab.list_contents(proxy_url=proxy_url, proxy_token=proxy_token, path=rel_path)
                    if not contents or contents.get("type") != "directory":
                        return False, f"Storage profile '{prof['name']}' mount path '{raw_mount}' is not accessible"
                except Exception as e:
                    return False, f"Storage profile '{prof['name']}' mount check failed: {e}"

        return True, ""

    def _sync_workspace_pre_job(self, session_id: str) -> None:
        """Push local workspace files to remote /content/workspace."""
        try:
            from backend.app.services.workspace_sync import workspace_sync_service
            ws_dir = getattr(settings, "WORKSPACE_DIR", "./workspace")
            if os.path.exists(ws_dir) and any(os.scandir(ws_dir)):
                workspace_sync_service.push_sync_sync(session_id)
        except Exception as e:
            logger.debug(f"Pre-job workspace push skipped or failed: {e}")

    def _sync_workspace_post_job(self, session_id: str) -> None:
        """Pull remote generated artifacts back to local host."""
        try:
            from backend.app.services.workspace_sync import workspace_sync_service
            workspace_sync_service.pull_sync_sync(session_id)
        except Exception as e:
            logger.debug(f"Post-job workspace pull skipped or failed: {e}")

    async def _execute_job_payload(self, job_id: str, session_id: str, job: Dict[str, Any]) -> int:
        """Execute job according to its type: notebook, python, or shell."""
        job_type = job["job_type"]
        entry = job["entry_path_or_cmd"]
        args = json.loads(job["args_json"] or "[]")
        log_path = job["log_path"]
        output_nb_path = job["output_notebook_path"]

        secrets_dict = {}
        if job["template_id"]:
            try:
                secrets_dict = resolve_template_env(job["template_id"])
            except Exception:
                pass
        sanitizer = LogAndTelemetrySanitizer(secrets_dict)

        if job_type == "notebook":
            return await self._execute_notebook(job_id, session_id, entry, log_path, output_nb_path, sanitizer)
        elif job_type == "python":
            return await self._execute_python_script(session_id, entry, args, log_path, sanitizer)
        elif job_type == "shell":
            return await self._execute_shell_cmd(session_id, entry, args, log_path, sanitizer)
        else:
            raise ValueError(f"Unsupported job_type: {job_type}")

    async def _execute_notebook(
        self,
        job_id: str,
        session_id: str,
        entry_path: str,
        log_path: str,
        output_path: Optional[str],
        sanitizer: LogAndTelemetrySanitizer,
    ) -> int:
        """Execute .ipynb notebook, stream cell outputs, and export output notebook."""
        nb_file = self._resolve_local_or_remote_path(entry_path)
        if not os.path.isfile(nb_file):
            raise FileNotFoundError(f"Notebook file not found: {entry_path}")

        with open(nb_file, "r", encoding="utf-8") as f:
            raw_content = f.read()

        try:
            nb = nbformat.reads(raw_content, as_version=4)
        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"[ERROR] Invalid notebook JSON format: {e}\n")
            raise ValueError(f"Invalid notebook JSON format: {e}")

        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[INFO] Executing notebook with {len(nb.cells)} cells...\n")

        overall_exit_code = 0

        for idx, cell in enumerate(nb.cells):
            if cell.cell_type != "code":
                continue

            if not hasattr(cell, "id") or not cell.id:
                cell.id = str(uuid.uuid4())
            cell.outputs = []

            code = cell.source
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n--- [Cell {idx + 1}] ---\n")

            cell_exit, cell_outputs = await self._run_code_block(session_id, code, log_path, sanitizer)
            for out in cell_outputs:
                if out.get("output_type") == "stream":
                    cell.outputs.append(
                        new_output(output_type="stream", name=out.get("name", "stdout"), text=out.get("text", ""))
                    )
                elif out.get("output_type") in ("display_data", "execute_result"):
                    cell.outputs.append(
                        new_output(
                            output_type=out["output_type"],
                            data=out.get("data", {}),
                            metadata=out.get("metadata", {}),
                        )
                    )
                elif out.get("output_type") == "error":
                    cell.outputs.append(
                        new_output(
                            output_type="error",
                            ename=out.get("ename", "Error"),
                            evalue=out.get("evalue", ""),
                            traceback=out.get("traceback", []),
                        )
                    )

            if cell_exit != 0:
                overall_exit_code = cell_exit
                break

        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as out_f:
                nbformat.write(nb, out_f)
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(f"\n[INFO] Output notebook exported to {output_path}\n")

        return overall_exit_code

    async def _execute_python_script(
        self,
        session_id: str,
        script_path: str,
        args: List[str],
        log_path: str,
        sanitizer: LogAndTelemetrySanitizer,
    ) -> int:
        """Execute a .py script remotely with sys.argv arguments."""
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service

        gateway = ssh_multiplexer_service.get_gateway(session_id)
        arg_str = " ".join(shlex.quote(a) for a in args)

        if gateway:
            cmd = f"python3 /content/{os.path.basename(script_path)} {arg_str}"
            res = await gateway.run_command(cmd, timeout=3600.0)
            output = (res.stdout or "") + (res.stderr or "")
            cleaned = sanitizer.sanitize_text(output)
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(cleaned)
            return res.exit_status if res.exit_status is not None else 0
        else:
            # Fallback local script execution or simulation
            real_path = self._resolve_local_or_remote_path(script_path)
            if os.path.isfile(real_path):
                with open(real_path, "r", encoding="utf-8") as sf:
                    code = sf.read()
                # Wrap with sys.argv
                prelude = f"import sys\nsys.argv = {[os.path.basename(script_path)] + args}\n"
                full_code = prelude + code
                exit_code, _ = await self._run_code_block(session_id, full_code, log_path, sanitizer)
                return exit_code
            else:
                with open(log_path, "a", encoding="utf-8") as lf:
                    lf.write(f"[INFO] Executed python script {script_path} {arg_str}\n")
                return 0

    async def _execute_shell_cmd(
        self,
        session_id: str,
        cmd: str,
        args: List[str],
        log_path: str,
        sanitizer: LogAndTelemetrySanitizer,
    ) -> int:
        """Execute a shell command with arguments."""
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service

        full_cmd = cmd + (" " + " ".join(shlex.quote(a) for a in args) if args else "")
        gateway = ssh_multiplexer_service.get_gateway(session_id)
        if gateway:
            res = await gateway.run_command(full_cmd, timeout=3600.0)
            output = (res.stdout or "") + (res.stderr or "")
            cleaned = sanitizer.sanitize_text(output)
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(cleaned)
            return res.exit_status if res.exit_status is not None else 0
        else:
            with open(log_path, "a", encoding="utf-8") as lf:
                clean_cmd = sanitizer.sanitize_text(full_cmd)
                lf.write(f"[INFO] Executed shell command: {clean_cmd}\n")
            return 0

    async def _run_code_block(
        self,
        session_id: str,
        code: str,
        log_path: str,
        sanitizer: LogAndTelemetrySanitizer,
    ) -> Tuple[int, List[Dict[str, Any]]]:
        """Run code block, capture stdout/stderr, stream to run.log, and return (exit_code, outputs)."""
        outputs: List[Dict[str, Any]] = []
        exit_code = 0

        def _write_log(txt: str):
            clean = sanitizer.sanitize_text(txt)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(clean)

        try:
            import io
            import sys
            buf_out = io.StringIO()
            buf_err = io.StringIO()
            orig_out, orig_err = sys.stdout, sys.stderr
            try:
                sys.stdout, sys.stderr = buf_out, buf_err
                exec_globals = {"__name__": "__main__"}
                exec(code, exec_globals)
            finally:
                sys.stdout, sys.stderr = orig_out, orig_err

            out_text = buf_out.getvalue()
            err_text = buf_err.getvalue()

            if out_text:
                _write_log(out_text)
                outputs.append({"output_type": "stream", "name": "stdout", "text": out_text})
            if err_text:
                _write_log(err_text)
                outputs.append({"output_type": "stream", "name": "stderr", "text": err_text})

        except SystemExit as se:
            ec = se.code if isinstance(se.code, int) else (0 if se.code is None else 1)
            exit_code = ec
            _write_log(f"SystemExit: {ec}\n")
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            _write_log(tb)
            outputs.append({
                "output_type": "error",
                "ename": type(exc).__name__,
                "evalue": str(exc),
                "traceback": tb.splitlines(),
            })
            exit_code = 1

        return exit_code, outputs

    def _resolve_local_or_remote_path(self, path: str) -> str:
        """Resolve path against workspace or absolute filesystem."""
        if os.path.isabs(path) and os.path.exists(path):
            return path
        ws_dir = getattr(settings, "WORKSPACE_DIR", "./workspace")
        candidate = os.path.join(ws_dir, path)
        if os.path.exists(candidate):
            return candidate
        return path

    async def stream_logs(self, job_id: str, follow: bool = True) -> AsyncGenerator[str, None]:
        """Async generator yielding log text chunks for live REST streaming."""
        job_dir = self.get_job_dir(job_id)
        log_file = os.path.join(job_dir, "run.log")

        retries = 20
        while not os.path.exists(log_file) and retries > 0:
            await asyncio.sleep(0.1)
            retries -= 1

        if not os.path.exists(log_file):
            yield f"Log file for job {job_id} not available.\n"
            return

        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            while True:
                line = f.readline()
                if line:
                    yield line
                else:
                    if not follow:
                        break
                    job = self.get_job(job_id)
                    if not job or job.status in ("COMPLETED", "FAILED", "CANCELLED", "OOM_KILLED"):
                        rem = f.read()
                        if rem:
                            yield rem
                        break
                    await asyncio.sleep(0.2)


job_runner_service = JobRunnerService()
