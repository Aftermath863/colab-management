"""Tier 1 Feature Tests: Non-Blocking Telemetry & Metrics Sampling.

Validates:
1. SustainedVRAMDetector debouncing (3 consecutive intervals >= 98% required).
2. SustainedVRAMDetector 1800s cooldown suppression.
3. Telemetry persistence to SQLite `telemetry_samples` table.
4. Vault secret redaction on telemetry string fields.
5. Retention auto-pruning (max 1000 samples per session).
6. REST endpoints: GET /api/sessions/{id}/telemetry and POST /api/sessions/{id}/telemetry.
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
from backend.app.core.models import TelemetrySample
from backend.app.core.security import create_access_token, encrypt_secret
from backend.app.main import app
from backend.app.services.telemetry import SustainedVRAMDetector, TelemetryService


class TestTelemetryFeatures(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_telemetry.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Seed account & session
        self.account_id = "acc_telem_1"
        self.session_id = "sess_telem_1"

        with get_db(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES (?, 'telem@example.com', 'dummy_enc', 50.0, 'ACTIVE')
                """,
                (self.account_id,),
            )
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, status
                ) VALUES (?, 'Telem Session', ?, 'nbh_1', 'ep_1', 'https://proxy.google.com',
                          'dummy_token', '2030-01-01 00:00:00', 'T4', 'READY')
                """,
                (self.session_id, self.account_id),
            )

        # Auth Token
        self.token = create_access_token(data={"sub": "admin", "role": "admin"})
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.client = TestClient(app)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_sustained_vram_detector_debouncing_and_cooldown(self):
        """VRAM saturation requires 3 consecutive samples >= 98% to alert, and observes 1800s cooldown."""
        detector = SustainedVRAMDetector(
            vram_threshold_pct=98.0,
            required_consecutive_samples=3,
            alert_cooldown_seconds=1800.0,
        )

        t0 = 1000.0
        vram_total = 16384.0
        vram_high = 16100.0  # ~98.26%

        # Sample 1: high -> No alert (count = 1)
        alert1 = detector.process_telemetry_sample(self.session_id, vram_high, vram_total, "Tesla T4", t0)
        self.assertIsNone(alert1)

        # Sample 2: high -> No alert (count = 2)
        alert2 = detector.process_telemetry_sample(self.session_id, vram_high, vram_total, "Tesla T4", t0 + 15)
        self.assertIsNone(alert2)

        # Sample 3: high -> Alert triggered! (count = 3 >= 3)
        alert3 = detector.process_telemetry_sample(self.session_id, vram_high, vram_total, "Tesla T4", t0 + 30)
        self.assertIsNotNone(alert3)
        self.assertEqual(alert3["event"], "sustained_high_vram")
        self.assertEqual(alert3["consecutive_intervals"], 3)
        self.assertEqual(alert3["severity"], "HIGH")

        # Sample 4: high within cooldown (e.g. at t0 + 60s < 1800s) -> Suppressed
        alert4 = detector.process_telemetry_sample(self.session_id, vram_high, vram_total, "Tesla T4", t0 + 60)
        self.assertIsNone(alert4)

        # Sample 5: normal usage drops below 98% -> Resets count to 0
        vram_normal = 8000.0
        alert5 = detector.process_telemetry_sample(self.session_id, vram_normal, vram_total, "Tesla T4", t0 + 90)
        self.assertIsNone(alert5)
        self.assertEqual(detector._consecutive_counts[self.session_id], 0)

    def test_telemetry_service_sample_persistence_and_secret_redaction(self):
        """Telemetry sample string fields have Vault secrets redacted before persistence."""
        # Insert a secret into database
        secret_key = "HF_API_TOKEN"
        raw_token = "hf_secret_token_value_9999"
        with get_db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO secrets (id, key_name, value_enc, description) VALUES ('sec_1', ?, ?, 'HuggingFace')",
                (secret_key, encrypt_secret(raw_token)),
            )

        service = TelemetryService(poll_interval_sec=1.0, max_samples_per_session=5)

        # Sanitize text test directly
        leaked_text = f"Warning: token {raw_token} encountered on GPU Colab T4"
        clean_text = service._sanitize_text(leaked_text)
        self.assertNotIn(raw_token, clean_text)
        self.assertIn(f"[REDACTED:{secret_key}]", clean_text)

        # Test single session sampling
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            sample = loop.run_until_complete(service.sample_session(self.session_id, "T4"))
        finally:
            loop.close()

        self.assertIsNotNone(sample)
        self.assertEqual(sample.session_id, self.session_id)

        # Verify sample is in DB
        with get_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM telemetry_samples WHERE session_id = ?", (self.session_id,)
            ).fetchall()
            self.assertGreaterEqual(len(rows), 1)

    def test_telemetry_sample_auto_pruning(self):
        """Old samples exceeding max_samples_per_session are pruned automatically."""
        service = TelemetryService(poll_interval_sec=1.0, max_samples_per_session=5)

        # Insert 10 samples
        with get_db(self.db_path) as conn:
            for i in range(10):
                conn.execute(
                    """
                    INSERT INTO telemetry_samples (session_id, cpu_pct, ram_used_mb, ram_total_mb)
                    VALUES (?, ?, 1024, 16384)
                    """,
                    (self.session_id, float(i)),
                )

        # Prune
        service._prune_samples(self.session_id)

        with get_db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM telemetry_samples WHERE session_id = ?", (self.session_id,)
            ).fetchall()
            self.assertEqual(len(rows), 5)
            # Latest 5 (cpu_pct 5.0 to 9.0) should remain
            cpu_vals = [r["cpu_pct"] for r in rows]
            self.assertIn(9.0, cpu_vals)
            self.assertNotIn(0.0, cpu_vals)

    def test_telemetry_rest_api_endpoints(self):
        """GET /api/sessions/{id}/telemetry and POST /api/sessions/{id}/telemetry operate correctly."""
        # 1. Post a telemetry sample
        payload = {
            "session_id": self.session_id,
            "kernel_state": "busy",
            "cpu_pct": 45.5,
            "ram_used_mb": 4096.0,
            "ram_total_mb": 16384.0,
            "disk_used_gb": 22.0,
            "disk_total_gb": 100.0,
            "gpu_name": "Tesla T4",
            "gpu_util_pct": 85.0,
            "vram_used_mb": 12000.0,
            "vram_total_mb": 15360.0,
        }
        res = self.client.post(
            f"/api/sessions/{self.session_id}/telemetry",
            headers=self.auth_headers,
            json=payload,
        )
        self.assertEqual(res.status_code, 201)
        created = res.json()
        self.assertEqual(created["cpu_pct"], 45.5)

        # 2. Get telemetry
        res = self.client.get(
            f"/api/sessions/{self.session_id}/telemetry",
            headers=self.auth_headers,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["session_id"], self.session_id)
        self.assertIsNotNone(data["latest"])
        self.assertEqual(data["latest"]["gpu_util_pct"], 85.0)
        self.assertGreaterEqual(len(data["history"]), 1)

        # 3. Non-existent session returns 404
        res404 = self.client.get(
            "/api/sessions/sess_non_existent/telemetry",
            headers=self.auth_headers,
        )
        self.assertEqual(res404.status_code, 404)


if __name__ == "__main__":
    unittest.main()
