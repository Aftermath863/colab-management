"""Empirical Adversarial Stress Harness for M2 R2 Security & Boundary Defenses.

Tests:
1. Zip Slip directory traversal in pull_workspace and sync_pull_session
2. Symlink following / external exfiltration in push_workspace and sync_push_session
3. workspace_subpath boundary containment within settings.WORKSPACE_DIR
4. Deep recursive sanitization of nested telemetry payloads
5. Suffix-leakage prevention via descending length sorting in sanitize_text
6. Precise model file vs non-model large file discrimination in is_large_model_file
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
import unittest
from typing import Any, Dict, List
from unittest.mock import MagicMock

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import (
    SessionCreateRequest,
    SyncPullRequest,
    SyncPushRequest,
)
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.services import session_manager, workspace_sync
from backend.app.services.colab_client import ColabClient
from backend.app.services.template_service import LogAndTelemetrySanitizer
from backend.app.services.workspace_sync import (
    DEFAULT_MAX_FILE_SIZE_BYTES,
    LARGE_MODEL_PATTERNS,
    WorkspaceSyncEngine,
    resolve_session_sync_paths,
    sync_pull_session,
    sync_push_session,
)


class AdversarialSecurityBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "harness.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.workspace_dir = os.path.join(self.tmpdir.name, "workspace")
        os.makedirs(self.workspace_dir, exist_ok=True)
        settings.WORKSPACE_DIR = self.workspace_dir

        self.account_id = "acc_adv_test"
        creds = {
            "access_token": "ya29.adv_token",
            "refresh_token": "1//adv_refresh",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, 'adv@example.com', 'Adv Tester', ?, 50.0, 1.0, 5.0, 1.0, 1, 'ACTIVE')
                """,
                (self.account_id, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    # =========================================================================
    # Test 1: Zip Slip directory traversal in pull_workspace and sync_pull_session
    # =========================================================================

    def test_adv_pull_workspace_comprehensive_traversal_payloads(self):
        """Stress-test pull_workspace against a wide spectrum of Zip Slip traversal payloads."""
        mock_client = MagicMock(spec=ColabClient)

        traversal_attack_names = [
            "../../escaped_root.txt",
            "../../../etc/passwd",
            "../../../../var/log/syslog",
            "nested/../../../../tmp/evil.py",
            "subdir/../../external.txt",
            "/tmp/absolute_evil.txt",
            "....//....//escaped.txt",
            "..\\..\\windows_style.txt",
        ]

        mock_contents = [
            {"name": "normal.py", "path": "workspace/normal.py", "type": "file", "size": 100}
        ]
        for idx, attack_name in enumerate(traversal_attack_names):
            mock_contents.append({
                "name": os.path.basename(attack_name),
                "path": f"workspace/{attack_name}",
                "type": "file",
                "size": 100 + idx,
            })

        mock_client.list_contents.return_value = {
            "type": "directory",
            "content": mock_contents,
        }
        mock_client.download_file.return_value = {
            "name": "sample",
            "format": "text",
            "content": "PAYLOAD_CONTENT_MALICIOUS",
        }

        pull_target_dir = os.path.join(self.tmpdir.name, "target_ws")
        os.makedirs(pull_target_dir, exist_ok=True)

        engine = WorkspaceSyncEngine(mock_client)
        downloaded = engine.pull_workspace(
            local_dir=pull_target_dir,
            proxy_url="http://mock-proxy",
            proxy_token="token",
            remote_prefix="workspace",
        )

        # 1. Normal file must be downloaded
        self.assertIn("normal.py", downloaded)
        self.assertTrue(os.path.exists(os.path.join(pull_target_dir, "normal.py")))

        # 2. None of the traversal files must exist outside pull_target_dir
        canonical_target = os.path.realpath(os.path.abspath(pull_target_dir))
        for root, dirs, files in os.walk(self.tmpdir.name):
            for file in files:
                full_path = os.path.join(root, file)
                canonical_file = os.path.realpath(os.path.abspath(full_path))
                is_outside = (
                    canonical_file != canonical_target
                    and not canonical_file.startswith(canonical_target + os.sep)
                )
                if is_outside:
                    with open(full_path, "r", errors="ignore") as f:
                        content = f.read()
                    self.assertNotEqual(
                        content,
                        "PAYLOAD_CONTENT_MALICIOUS",
                        f"Directory traversal succeeded! Malicious file written outside target at {full_path}",
                    )

    def _create_mock_session_row(self, session_id: str, workspace_subpath: str = "") -> str:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, idle_timeout_sec, ssh_local_port,
                    workspace_subpath, remote_workdir, template_id, status, created_at
                ) VALUES (
                    ?, 'adv-sess', ?, 'hash123', 'endpoint123', 'http://mock-proxy:1113',
                    ?, ?, 'T4', 'standard',
                    'interactive', 3600, 2220,
                    ?, '/content', NULL, 'READY', ?
                )
                """,
                (
                    session_id,
                    self.account_id,
                    encrypt_secret("mock_proxy_token"),
                    int(time.time()) + 3600,
                    workspace_subpath,
                    int(time.time()),
                ),
            )
        return session_id

    def test_adv_sync_pull_session_traversal_reporting(self):
        """Verify sync_pull_session skips traversal attacks and reports path_traversal_rejected."""
        session_id = self._create_mock_session_row("adv_pull_sess")

        mock_client = MagicMock(spec=ColabClient)
        mock_client.list_contents.return_value = {
            "type": "directory",
            "content": [
                {"name": "valid.py", "path": "workspace/valid.py", "type": "file", "size": 50},
                {"name": "../../pwn.sh", "path": "workspace/../../pwn.sh", "type": "file", "size": 90},
                {"name": "/etc/shadow", "path": "/etc/shadow", "type": "file", "size": 120},
            ],
        }
        mock_client.download_file.return_value = {
            "name": "sample",
            "format": "text",
            "content": "MALICIOUS_CONTENT",
        }

        req = SyncPullRequest(dry_run=False, max_file_size_mb=50)
        result = asyncio.run(sync_pull_session(session_id, req, client=mock_client))

        self.assertTrue(result["success"])
        self.assertEqual(result["files_downloaded"], 1)
        self.assertGreaterEqual(result["files_skipped"], 2)

        skip_reasons = [d.get("reason") for d in result["details"] if d.get("action") == "skipped"]
        self.assertIn("path_traversal_rejected", skip_reasons)

    # =========================================================================
    # Test 2: Symlinks pointing outside workspace in push_workspace / sync_push_session
    # =========================================================================

    def test_adv_push_symlink_defense_external_vs_internal(self):
        """Verify external symlinks are strictly rejected while internal symlinks are preserved."""
        mock_client = MagicMock(spec=ColabClient)

        # 1. External secret file outside workspace
        external_secret = os.path.join(self.tmpdir.name, "external_secret.key")
        with open(external_secret, "w") as f:
            f.write("SUPER_SECRET_HOST_KEY")

        # 2. Internal valid file inside workspace
        internal_file = os.path.join(self.workspace_dir, "internal_code.py")
        with open(internal_file, "w") as f:
            f.write("print('Hello Colab')")

        # 3. External symlink inside workspace -> external_secret
        symlink_external = os.path.join(self.workspace_dir, "leak_external.key")
        os.symlink(external_secret, symlink_external)

        # 4. Internal symlink inside workspace -> internal_code.py
        symlink_internal = os.path.join(self.workspace_dir, "link_internal.py")
        os.symlink(internal_file, symlink_internal)

        engine = WorkspaceSyncEngine(mock_client)
        uploaded = engine.push_workspace(
            local_dir=self.workspace_dir,
            proxy_url="http://mock-proxy",
            proxy_token="token",
            remote_prefix="workspace",
        )

        uploaded_str = " ".join(uploaded)
        # External symlink MUST NOT be uploaded
        self.assertNotIn("leak_external.key", uploaded_str)
        # Internal symlink MUST be uploaded
        self.assertIn("link_internal.py", uploaded_str)
        self.assertIn("internal_code.py", uploaded_str)

    def test_adv_sync_push_session_broken_symlink_handling(self):
        """Verify broken / dangling symlink in push_workspace and existing external symlink in sync_push_session."""
        mock_client = MagicMock(spec=ColabClient)

        # 1. Broken symlink in push_workspace
        broken_link = os.path.join(self.workspace_dir, "dangling.link")
        os.symlink(os.path.join(self.tmpdir.name, "nonexistent_target.txt"), broken_link)

        engine = WorkspaceSyncEngine(mock_client)
        # push_workspace must cleanly ignore broken symlinks without FileNotFoundError
        uploaded = engine.push_workspace(
            local_dir=self.workspace_dir,
            proxy_url="http://mock-proxy",
            proxy_token="token",
            remote_prefix="workspace",
        )
        self.assertNotIn("dangling.link", uploaded)

        # Clean up broken link before sync_push_session to test clean symlink skipping
        os.remove(broken_link)

        # 2. Existing external symlink in sync_push_session
        session_id = self._create_mock_session_row("adv_push_sess")
        external_secret = os.path.join(self.tmpdir.name, "secret_for_push.txt")
        with open(external_secret, "w") as f:
            f.write("SECRET_EXTERNAL_CONTENT")

        valid_ext_link = os.path.join(self.workspace_dir, "leak_attempt.txt")
        os.symlink(external_secret, valid_ext_link)

        req = SyncPushRequest(dry_run=False, max_file_size_mb=50)
        result = asyncio.run(sync_push_session(session_id, req, client=mock_client))
        self.assertTrue(result["success"])
        # leak_attempt.txt must be skipped
        skipped_paths = [d["path"] for d in result["details"] if d["action"] == "skipped"]
        self.assertIn("leak_attempt.txt", skipped_paths)
        # colab.upload_file should never have been called with secret content
        self.assertFalse(mock_client.upload_file.called)

        # 3. Document empirically that broken symlink in sync_push_session raises FileNotFoundError
        broken_link_2 = os.path.join(self.workspace_dir, "dangling_push.link")
        os.symlink(os.path.join(self.tmpdir.name, "missing.txt"), broken_link_2)
        with self.assertRaises(FileNotFoundError):
            asyncio.run(sync_push_session(session_id, req, client=mock_client))

    # =========================================================================
    # Test 3: workspace_subpath boundary containment
    # =========================================================================

    def test_adv_workspace_subpath_boundary_enforcement(self):
        """Stress-test resolve_session_sync_paths against diverse traversal subpaths."""
        session_id = self._create_mock_session_row("adv_subpath_sess")

        traversal_attempts = [
            "../../etc",
            "../../../",
            "/etc",
            "/var/run",
            "sub/../../..",
            "sub/../../../../tmp",
        ]

        for bad_subpath in traversal_attempts:
            with get_db() as conn:
                conn.execute(
                    "UPDATE sessions SET workspace_subpath = ? WHERE id = ?",
                    (bad_subpath, session_id),
                )
            with self.assertRaises(ValueError, msg=f"Subpath '{bad_subpath}' was not rejected!"):
                resolve_session_sync_paths(session_id)

        # Valid subpaths must succeed
        valid_subpaths = ["", "my_project", "team/repo_a", "experiments/run_1"]
        for good_subpath in valid_subpaths:
            with get_db() as conn:
                conn.execute(
                    "UPDATE sessions SET workspace_subpath = ? WHERE id = ?",
                    (good_subpath, session_id),
                )
            local_dir, remote_pfx, _, _ = resolve_session_sync_paths(session_id)
            canonical_ws = os.path.realpath(os.path.abspath(settings.WORKSPACE_DIR))
            self.assertTrue(
                local_dir == canonical_ws or local_dir.startswith(canonical_ws + os.sep),
                f"Resolved local_dir '{local_dir}' escaped workspace '{canonical_ws}'!",
            )

    # =========================================================================
    # Test 4: Recursive sanitization of nested telemetry payloads
    # =========================================================================

    def test_adv_recursive_telemetry_sanitization_deep_hierarchy(self):
        """Stress-test telemetry sanitizer with arbitrary nesting depth and data types."""
        secret_api = "sk-live-adv999888777"
        secret_db = "postgres://admin:pass4321@db.host:5432/prod"
        sanitizer = LogAndTelemetrySanitizer({
            "API_KEY": secret_api,
            "DB_URI": secret_db,
        })

        complex_payload = {
            "timestamp": 1234567890,
            "is_active": True,
            "metrics": {
                "gpu": {
                    "device": "NVIDIA T4",
                    "info": f"Authenticated using {secret_api}",
                    "details": [
                        {"conn": secret_db, "code": 200},
                        {"tags": ["gpu", f"key={secret_api}", 42, None]},
                    ],
                },
                "history": [
                    [[[{"deepest_level": f"Check secret {secret_db} here"}]]],
                ],
            },
            "unrelated_int": 9999,
            "empty_dict": {},
            "empty_list": [],
        }

        cleaned = sanitizer.sanitize_telemetry_payload(complex_payload)

        # Assert no secret strings exist anywhere in the serialized cleaned output
        cleaned_json = str(cleaned)
        self.assertNotIn(secret_api, cleaned_json)
        self.assertNotIn(secret_db, cleaned_json)

        # Assert replacements occurred properly
        self.assertIn("[REDACTED:API_KEY]", cleaned_json)
        self.assertIn("[REDACTED:DB_URI]", cleaned_json)

        # Assert structural preservation
        self.assertEqual(cleaned["unrelated_int"], 9999)
        self.assertEqual(cleaned["is_active"], True)
        self.assertEqual(cleaned["empty_dict"], {})
        self.assertEqual(cleaned["empty_list"], [])

    # =========================================================================
    # Test 5: Secret keys/values sorted descending by length in sanitize_text
    # =========================================================================

    def test_adv_secret_sorting_descending_by_length_prevents_partial_leak(self):
        """Verify multi-tier prefix overlapping secrets never leak cleartext suffixes."""
        # 4-tier overlapping secrets
        overlapping = {
            "SHORT_1": "token",
            "SHORT_2": "token123",
            "MEDIUM": "token123456",
            "LONG": "token123456789",
        }
        # Intentionally construct sanitizer with dict in reverse order (shortest first)
        sanitizer = LogAndTelemetrySanitizer({
            "SHORT_1": "token",
            "SHORT_2": "token123",
            "MEDIUM": "token123456",
            "LONG": "token123456789",
        })

        test_string = "Log entry with token123456789 and secondary token123."
        sanitized = sanitizer.sanitize_text(test_string)

        # Must not contain suffix "456789" or "789" or "123"
        self.assertNotIn("token123456789", sanitized)
        self.assertNotIn("456789", sanitized)
        self.assertNotIn("token123", sanitized)
        self.assertIn("[REDACTED:LONG]", sanitized)
        self.assertIn("[REDACTED:SHORT_2]", sanitized)

    # =========================================================================
    # Test 6: is_large_model_file precise model weight vs non-model discrimination
    # =========================================================================

    def test_adv_is_large_model_file_boundary_matrix(self):
        """Matrix test for model patterns vs non-model files across sizes."""
        engine = WorkspaceSyncEngine()
        limit = 50 * 1024 * 1024  # 50MB

        # Model files
        model_names = [
            "model.pt", "model.PT", "model.Pt",
            "weights.bin", "model.safetensors", "model.SAFETENSORS",
            "network.onnx", "checkpoint.ckpt", "model.h5",
            "weights.pkl", "frozen.pb", "model.tflite",
            "yolo.weights", "pytorch_model.bin", "llama-7b.gguf",
        ]
        for name in model_names:
            # Below limit -> False
            self.assertFalse(engine.is_large_model_file(name, limit - 100, limit))
            # Exactly limit -> False
            self.assertFalse(engine.is_large_model_file(name, limit, limit))
            # Above limit -> True
            self.assertTrue(engine.is_large_model_file(name, limit + 1, limit))

        # Non-model files
        non_model_names = [
            "dataset.csv", "big_data.parquet", "archive.zip", "disk.iso",
            "video.mp4", "train.py", "requirements.txt", "model_spec.json",
            "data.bin.txt", "weights.pt.bak",
        ]
        for name in non_model_names:
            # Even at 1GB, must NOT be marked as large model file
            one_gb = 1024 * 1024 * 1024
            self.assertFalse(
                engine.is_large_model_file(name, one_gb, limit),
                f"Non-model file '{name}' was falsely marked as large model file!",
            )


if __name__ == "__main__":
    unittest.main()
