"""T3.9: Sustained High VRAM x Alert Trigger.

Validates that non-blocking background telemetry sampling detects persistent GPU VRAM saturation:
1. Transient VRAM spikes (1-2 intervals) are debounced without false-positive alert spam.
2. A drop in VRAM resets the consecutive saturation counter.
3. Sustained saturation (>= 3 consecutive intervals at >= 98% VRAM utilization) fires
   a high-priority alert card detailing session ID, GPU model, and VRAM utilization.
4. Subsequent saturated samples are suppressed by alert cooldown.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.security import encrypt_secret


class SustainedVRAMDetector:
    """Evaluates telemetry samples and triggers debounced alerts for sustained VRAM saturation."""

    def __init__(
        self,
        vram_threshold_pct: float = 98.0,
        required_consecutive_samples: int = 3,
        alert_cooldown_seconds: float = 1800.0,
    ):
        self.vram_threshold_pct = vram_threshold_pct
        self.required_consecutive = required_consecutive_samples
        self.alert_cooldown = alert_cooldown_seconds

        # session_id -> consecutive count
        self._consecutive_counts: Dict[str, int] = {}
        # session_id -> last alert timestamp
        self._last_alert_times: Dict[str, float] = {}

    def process_telemetry_sample(
        self,
        session_id: str,
        vram_used_mb: float,
        vram_total_mb: float,
        gpu_name: str,
        timestamp: float,
    ) -> Optional[Dict[str, Any]]:
        # Persist telemetry sample into database
        util_pct = (vram_used_mb / vram_total_mb) * 100.0 if vram_total_mb > 0 else 0.0

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO telemetry_samples (session_id, gpu_name, vram_used_mb, vram_total_mb, gpu_util_pct)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, gpu_name, vram_used_mb, vram_total_mb, util_pct),
            )

        if util_pct >= self.vram_threshold_pct:
            current_count = self._consecutive_counts.get(session_id, 0) + 1
            self._consecutive_counts[session_id] = current_count
        else:
            # Recovery: reset consecutive count
            self._consecutive_counts[session_id] = 0
            return None

        # Check if consecutive threshold met
        if current_count >= self.required_consecutive:
            last_alert = self._last_alert_times.get(session_id)
            if last_alert is not None and (timestamp - last_alert) < self.alert_cooldown:
                # Suppressed by cooldown
                return None

            self._last_alert_times[session_id] = timestamp
            alert_card = {
                "event": "sustained_high_vram",
                "severity": "HIGH",
                "session_id": session_id,
                "gpu_name": gpu_name,
                "vram_util_pct": f"{util_pct:.1f}%",
                "vram_used_mb": vram_used_mb,
                "vram_total_mb": vram_total_mb,
                "consecutive_intervals": current_count,
            }
            return alert_card

        return None


class TestT39SustainedVRAMAlertTrigger(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        # Set up prerequisite DB account and session
        self.session_id = "sess_vram_test_99"
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status)
                VALUES ('acc_vram', 'vram@test.com', 'enc', 100.0, 'ACTIVE')
                """
            )
            conn.execute(
                """
                INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url,
                                     proxy_token_enc, token_expires_at, accelerator, status)
                VALUES (?, 'vram-session', 'acc_vram', 'hash', 'ep', 'url', 'tok', '2026-11-13', 'A100', 'READY')
                """,
                (self.session_id,),
            )

        self.detector = SustainedVRAMDetector(
            vram_threshold_pct=98.0,
            required_consecutive_samples=3,
            alert_cooldown_seconds=1800.0,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t3_9_high_vram_alert_triggers_only_after_sustained_saturation(self):
        t0 = 5000.0
        gpu = "NVIDIA A100-SXM4-40GB"
        total_vram = 40960.0  # 40GB in MB
        saturated_vram = 40550.0  # ~99.0%
        normal_vram = 32768.0  # 80.0%

        # 1. Sample 1: 99% VRAM (consecutive = 1) -> No alert
        alert = self.detector.process_telemetry_sample(
            self.session_id, saturated_vram, total_vram, gpu, timestamp=t0
        )
        self.assertIsNone(alert)
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 1)

        # 2. Sample 2: 99% VRAM (consecutive = 2) -> No alert
        alert = self.detector.process_telemetry_sample(
            self.session_id, saturated_vram, total_vram, gpu, timestamp=t0 + 10.0
        )
        self.assertIsNone(alert)
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 2)

        # 3. Sample 3: Temporary drop to 80% (cool-off) -> Consecutive count resets to 0
        alert = self.detector.process_telemetry_sample(
            self.session_id, normal_vram, total_vram, gpu, timestamp=t0 + 20.0
        )
        self.assertIsNone(alert)
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 0)

        # 4. Now simulate sustained saturation across 3 consecutive intervals:
        # Sample 4: 99% (consecutive = 1)
        alert = self.detector.process_telemetry_sample(
            self.session_id, saturated_vram, total_vram, gpu, timestamp=t0 + 30.0
        )
        self.assertIsNone(alert)
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 1)

        # Sample 5: 99% (consecutive = 2)
        alert = self.detector.process_telemetry_sample(
            self.session_id, saturated_vram, total_vram, gpu, timestamp=t0 + 40.0
        )
        self.assertIsNone(alert)
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 2)

        # Sample 6: 99% (consecutive = 3) -> Threshold met!
        alert = self.detector.process_telemetry_sample(
            self.session_id, saturated_vram, total_vram, gpu, timestamp=t0 + 50.0
        )
        self.assertIsNotNone(alert)

        # Invariant: Alert payload contains required diagnostic metadata
        self.assertEqual(alert["event"], "sustained_high_vram")
        self.assertEqual(alert["severity"], "HIGH")
        self.assertEqual(alert["session_id"], self.session_id)
        self.assertEqual(alert["gpu_name"], gpu)
        self.assertEqual(alert["vram_util_pct"], "99.0%")
        self.assertEqual(alert["consecutive_intervals"], 3)

        # 5. Sample 7: 99% (consecutive = 4, timestamp t0 + 60s)
        # Suppressed by 1800s cooldown window
        alert_suppressed = self.detector.process_telemetry_sample(
            self.session_id, saturated_vram, total_vram, gpu, timestamp=t0 + 60.0
        )
        self.assertIsNone(alert_suppressed)
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 4)

        # 6. Verify 7 total samples persisted into database
        with get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM telemetry_samples WHERE session_id = ?",
                (self.session_id,),
            ).fetchone()["cnt"]
            self.assertEqual(count, 7)


if __name__ == "__main__":
    unittest.main()
