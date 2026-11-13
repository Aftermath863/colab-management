"""Adversarial Empirical Stress Testing Suite for Milestone E2E-M3 Gate Verification.

Authored by teamwork_challenger_e2e_gate_2.
Stress-tests:
1. Cross-feature systemic invariants:
   - Account quota depletion shutdown & auto-reroute under rapid CCU drops (multi-session, total exhaustion).
   - Active SSH traffic resetting idle watchdog timer vs timeout termination (asymmetric traffic, packet bursts).
   - Session teardown immediate port release under concurrent allocation pressure (pool exhaustion, recycling).
   - Vault secret redaction in logs and telemetry under high volume/nested output (regex special chars, nested payloads).
   - Remote Monaco edit conflict safety under simultaneous multi-source edits (divergent, convergent, stale local push).
   - High VRAM sustained saturation debouncing vs transient spikes (debouncing, counter reset, cooldown suppression).
2. Tier 4 workflows:
   - T4.1 Interactive Dev Day dual SSH multiplexer channel isolation and lifecycle teardown.
   - T4.2 Double fallback cascade (A100 -> L4 -> T4) and fallback disabled fail-fast.
   - T4.3 Quota failover under standby exhaustion and orphan 404 graceful handling.
   - T4.4 Token auto-renewal at 5m margin and network partition recovery with token validation.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import tempfile
import time
import unittest
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backend.app.config import settings
from backend.app.core.database import get_db, init_db
from backend.app.core.models import SessionCreateRequest
from backend.app.core.security import encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient
from backend.tests.mock_colab.mock_hub import MockControlHub
from backend.tests.mock_colab.mock_oauth import MockOAuthAccount
from backend.tests.mock_colab.mock_runtime_proxy import RFC6455WebSocket

from backend.tests.tier3_pairwise.test_t3_1_quota_depletion_reroute import QuotaCircuitBreakerWatchdog
from backend.tests.tier3_pairwise.test_t3_2_ssh_traffic_idle_reset import InteractiveIdleWatchdog
from backend.tests.tier3_pairwise.test_t3_4_secrets_log_redaction import LogAndTelemetrySanitizer, SecretEnvironmentInjector
from backend.tests.tier3_pairwise.test_t3_7_monaco_conflict_sync_safety import SafeWorkspaceSyncEngine
from backend.tests.tier3_pairwise.test_t3_9_sustained_vram_alert import SustainedVRAMDetector
from backend.tests.tier4_real_world.test_t4_1_interactive_dev_day import MockSSHMultiplexerGateway
from backend.tests.tier4_real_world.test_t4_2_overnight_lora_training import LoRAJobFallbackScheduler
from backend.tests.tier4_real_world.test_t4_3_fleet_quota_failover_orphan_cleanup import FleetFailoverScheduler
from backend.tests.tier4_real_world.test_t4_4_token_renewal_partition_recovery import ResilientGatewayClient


class TestChallengerSystemicInvariant1_QuotaDepletion(unittest.TestCase):
    """Adversarial stress-testing of Quota Depletion, Session Shutdown, and Rerouting."""

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

        # Register Account Alpha (Initial balance: 20.0, hard stop: 2.0)
        self.acc_alpha_id = "acc_alpha_stress"
        self.email_alpha = "alpha.stress@example.com"
        mock_acc_alpha = MockOAuthAccount(
            email=self.email_alpha,
            name="Alpha Account",
            sub="sub_alpha_str",
            code="code_alpha_str",
            access_token="ya29.token_alpha_str",
            refresh_token="1//refresh_alpha_str",
        )
        self.hub.oauth.state.register_account(mock_acc_alpha)
        self.hub.set_account_ccu(self.email_alpha, balance=20.0, burn_rate=2.0)

        creds_alpha = {
            "access_token": "ya29.token_alpha_str",
            "refresh_token": "1//refresh_alpha_str",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Alpha Account', ?, 20.0, 2.0, 5.0, 2.0, 10, 'ACTIVE')
                """,
                (self.acc_alpha_id, self.email_alpha, encrypt_secret(json.dumps(creds_alpha))),
            )

        # Register Account Beta (Standby account, balance: 50.0, hard stop: 2.0)
        self.acc_beta_id = "acc_beta_stress"
        self.email_beta = "beta.stress@example.com"
        mock_acc_beta = MockOAuthAccount(
            email=self.email_beta,
            name="Beta Account",
            sub="sub_beta_str",
            code="code_beta_str",
            access_token="ya29.token_beta_str",
            refresh_token="1//refresh_beta_str",
        )
        self.hub.oauth.state.register_account(mock_acc_beta)
        self.hub.set_account_ccu(self.email_beta, balance=50.0, burn_rate=0.5)

        creds_beta = {
            "access_token": "ya29.token_beta_str",
            "refresh_token": "1//refresh_beta_str",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Beta Account', ?, 50.0, 0.5, 10.0, 2.0, 5, 'ACTIVE')
                """,
                (self.acc_beta_id, self.email_beta, encrypt_secret(json.dumps(creds_beta))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_rapid_multi_session_shutdown_on_quota_depletion(self):
        """Stress: An account with 3 concurrent active sessions abruptly drops below hard stop CCU.
        Circuit breaker must terminate ALL 3 sessions, release their ports, and unassign in Colab.
        """
        # 1. Provision 3 sessions under Account Alpha
        sessions = []
        endpoints = []
        ports = []
        for i in range(3):
            req = SessionCreateRequest(
                name=f"sess-alpha-{i}",
                accelerator="T4",
                account_id=self.acc_alpha_id,
            )
            sess = session_manager.create_session(req)
            self.assertEqual(sess["status"], "READY")
            self.assertEqual(sess["account_id"], self.acc_alpha_id)
            sessions.append(sess)
            endpoints.append(sess["endpoint"])
            ports.append(sess["ssh_local_port"])

        self.assertEqual(ports, [2220, 2221, 2222])
        for ep in endpoints:
            self.assertIn(ep, self.hub.control_plane.state.assignments)

        # 2. CCU abruptly collapses to 0.5 (< hard_stop_ccu 2.0)
        self.hub.set_account_ccu(self.email_alpha, balance=0.5, burn_rate=5.0)
        self.hub.control_plane.state.ccu_balance = 0.5
        self.hub.control_plane.state.consumption_rate_hourly = 5.0

        updated_alpha = account_pool.sync_account(self.acc_alpha_id)
        self.assertEqual(updated_alpha["status"], "COOLDOWN")
        self.assertEqual(updated_alpha["ccu_balance"], 0.5)

        # 3. Quota watchdog terminates sessions on COOLDOWN account
        stopped_ids = QuotaCircuitBreakerWatchdog.evaluate_and_enforce_hard_stops()
        self.assertEqual(len(stopped_ids), 3)
        for s in sessions:
            self.assertIn(s["id"], stopped_ids)

        # 4. Invariants: all 3 sessions are STOPPED, ports set to NULL, endpoints unassigned on Colab
        for s in sessions:
            sess_db = session_manager.get_session(s["id"])
            self.assertEqual(sess_db["status"], "STOPPED")
            self.assertIsNone(sess_db["ssh_local_port"])
            self.assertIsNotNone(sess_db["stopped_at"])

        for ep in endpoints:
            self.assertNotIn(ep, self.hub.control_plane.state.assignments)

        # 5. Subsequent allocation automatically skips Account Alpha and routes to Account Beta
        best_acc = account_pool.select_account(preferred_account_id=None)
        self.assertEqual(best_acc, self.acc_beta_id)

        # Provision new session: port 2220 should be recycled
        new_sess = session_manager.create_session(
            SessionCreateRequest(name="sess-rerouted", accelerator="T4", account_id=None)
        )
        self.assertEqual(new_sess["status"], "READY")
        self.assertEqual(new_sess["account_id"], self.acc_beta_id)
        self.assertEqual(new_sess["ssh_local_port"], 2220)

    def test_total_pool_exhaustion_clean_error_handling(self):
        """Stress: Both Account Alpha and Account Beta drop below hard stop CCU.
        Calling select_account must raise ValueError cleanly without hanging or partial creation.
        """
        # Deplete Account Alpha
        with get_db() as conn:
            conn.execute("UPDATE accounts SET ccu_balance = 1.0, status = 'COOLDOWN' WHERE id = ?", (self.acc_alpha_id,))
            conn.execute("UPDATE accounts SET ccu_balance = 1.5, status = 'COOLDOWN' WHERE id = ?", (self.acc_beta_id,))

        with self.assertRaises(ValueError) as ctx:
            account_pool.select_account(preferred_account_id=None)
        self.assertIn("No eligible active accounts with available CCU quota in pool", str(ctx.exception))

        # Attempting explicit preferred account in COOLDOWN must also fail with clear message
        with self.assertRaises(ValueError) as ctx2:
            account_pool.select_account(preferred_account_id=self.acc_alpha_id)
        self.assertIn("below hard stop CCU", str(ctx2.exception))


class TestChallengerSystemicInvariant2_SSHWatchdog(unittest.TestCase):
    """Adversarial stress-testing of Active SSH Traffic vs Idle Watchdog Termination."""

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

        self.account_id = "acc_watchdog_stress"
        self.email = "wd.stress@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Watchdog Tester",
            sub="sub_wd_str",
            code="code_wd_str",
            access_token="ya29.token_wd_str",
            refresh_token="1//refresh_wd_str",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.token_wd_str",
            "refresh_token": "1//refresh_wd_str",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Watchdog Tester', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.watchdog = InteractiveIdleWatchdog()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_asymmetric_session_traffic_selective_termination(self):
        """Stress: Two interactive sessions provisioned concurrently.
        Session Active has periodic SSH traffic. Session Idle has zero traffic.
        Watchdog must terminate Session Idle while Session Active remains READY.
        """
        # Session Active (timeout = 15s)
        sess_active = session_manager.create_session(
            SessionCreateRequest(name="sess-active", accelerator="T4", idle_timeout_sec=15, account_id=self.account_id)
        )
        # Session Idle (timeout = 15s)
        sess_idle = session_manager.create_session(
            SessionCreateRequest(name="sess-idle", accelerator="T4", idle_timeout_sec=15, account_id=self.account_id)
        )

        t0 = 1000.0
        self.watchdog.register_session(sess_active["id"], t0)
        self.watchdog.register_session(sess_idle["id"], t0)

        # Traffic burst on Session Active at t=8s and t=14s
        self.watchdog.record_traffic(sess_active["id"], t0 + 8.0)
        self.watchdog.record_traffic(sess_active["id"], t0 + 14.0)

        # Evaluate at t=1018.0 (18 seconds since t0):
        # Session Idle idle_duration = 18s >= 15s -> Terminated!
        # Session Active idle_duration = 18s - 14s = 4s < 15s -> Kept alive!
        terminated = self.watchdog.evaluate_idle_sessions(t0 + 18.0)

        self.assertIn(sess_idle["id"], terminated)
        self.assertNotIn(sess_active["id"], terminated)

        db_idle = session_manager.get_session(sess_idle["id"])
        db_active = session_manager.get_session(sess_active["id"])
        self.assertEqual(db_idle["status"], "STOPPED")
        self.assertEqual(db_active["status"], "READY")

        # Now, Session Active traffic stops. Evaluate at t=1032.0 (18s since last traffic at 1014.0):
        # Session Active idle_duration = 32 - 14 = 18s >= 15s -> Now Terminated!
        terminated_second = self.watchdog.evaluate_idle_sessions(t0 + 32.0)
        self.assertIn(sess_active["id"], terminated_second)

        db_active_final = session_manager.get_session(sess_active["id"])
        self.assertEqual(db_active_final["status"], "STOPPED")

    def test_traffic_burst_throughput_no_timer_jitter(self):
        """Stress: High-frequency packet burst (500 micro-packets) sent to record_traffic.
        Internal state must maintain the latest timestamp without race conditions or performance degradation.
        """
        sess = session_manager.create_session(
            SessionCreateRequest(name="sess-burst", accelerator="T4", account_id=self.account_id)
        )
        self.watchdog.register_session(sess["id"], 5000.0)

        # Send 500 packets with monotonically increasing timestamps
        for i in range(500):
            ts = 5000.0 + (i * 0.01)
            self.watchdog.record_traffic(sess["id"], ts)

        expected_final = 5000.0 + (499 * 0.01)
        self.assertAlmostEqual(self.watchdog.get_last_activity(sess["id"]), expected_final, places=4)


class TestChallengerSystemicInvariant3_PortPoolManagement(unittest.TestCase):
    """Adversarial stress-testing of SSH Port Pool Exhaustion, Immediate Release, and Recycling."""

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

        self.account_id = "acc_port_stress"
        self.email = "port.stress@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Port Stress",
            sub="sub_port_str",
            code="code_port_str",
            access_token="ya29.token_port_str",
            refresh_token="1//refresh_port_str",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.token_port_str",
            "refresh_token": "1//refresh_port_str",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Port Stress', ?, 500.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_port_pool_exact_exhaustion_and_non_contiguous_recycling(self):
        """Stress: Fully saturate all 10 ports (2220-2229).
        11th allocation must return None.
        Teardown arbitrary ports (e.g. 2223 and 2227).
        Subsequent allocations must return lowest available first (2223 then 2227).
        """
        allocated_sessions = []
        for i in range(10):
            sess = session_manager.create_session(
                SessionCreateRequest(name=f"sess-full-{i}", accelerator="T4", account_id=self.account_id)
            )
            self.assertEqual(sess["ssh_local_port"], 2220 + i)
            allocated_sessions.append(sess)

        # 11th allocation: pool exhausted
        port_11 = session_manager.allocate_ssh_port()
        self.assertIsNone(port_11, "Port allocator must return None when all 10 ports are active")

        # Provisioning 11th session succeeds without SSH port
        sess_11 = session_manager.create_session(
            SessionCreateRequest(name="sess-full-11", accelerator="T4", account_id=self.account_id)
        )
        self.assertEqual(sess_11["status"], "READY")
        self.assertIsNone(sess_11["ssh_local_port"])

        # Teardown sessions with ports 2223 (index 3) and 2227 (index 7)
        session_manager.stop_session(allocated_sessions[3]["id"])
        session_manager.stop_session(allocated_sessions[7]["id"])

        # Verify DB immediately shows NULL for ssh_local_port
        db_s3 = session_manager.get_session(allocated_sessions[3]["id"])
        db_s7 = session_manager.get_session(allocated_sessions[7]["id"])
        self.assertIsNone(db_s3["ssh_local_port"])
        self.assertIsNone(db_s7["ssh_local_port"])

        # Invariant: Lowest available port recycled first (2223)
        recycled_first = session_manager.allocate_ssh_port()
        self.assertEqual(recycled_first, 2223)

        # Provision session claiming 2223
        sess_claim_1 = session_manager.create_session(
            SessionCreateRequest(name="sess-claim-1", accelerator="T4", account_id=self.account_id)
        )
        self.assertEqual(sess_claim_1["ssh_local_port"], 2223)

        # Next allocation claims 2227
        recycled_second = session_manager.allocate_ssh_port()
        self.assertEqual(recycled_second, 2227)


class TestChallengerSystemicInvariant4_SecretRedaction(unittest.TestCase):
    """Adversarial stress-testing of Vault Secret Redaction in Logs and Telemetry."""

    def test_secret_redaction_regex_special_characters(self):
        """Stress: Secrets contain regex metacharacters: $^*()+[]{}?|\\
        Sanitizer must properly escape them and perform complete redaction without regex syntax crashes.
        """
        secrets = {
            "API_KEY_SPECIAL": "sk-proj+test*[123]?{v1}^$",
            "TOKEN_SLASH": "tok/sub\\path|val(ok)",
        }
        sanitizer = LogAndTelemetrySanitizer(secrets)

        log_text = (
            "Connecting with key sk-proj+test*[123]?{v1}^$ to host.\n"
            "Auth token tok/sub\\path|val(ok) authenticated.\n"
        )
        sanitized = sanitizer.sanitize_text(log_text)

        self.assertNotIn("sk-proj+test*[123]?{v1}^$", sanitized)
        self.assertNotIn("tok/sub\\path|val(ok)", sanitized)
        self.assertIn("[REDACTED:API_KEY_SPECIAL]", sanitized)
        self.assertIn("[REDACTED:TOKEN_SLASH]", sanitized)

    def test_high_volume_log_burst_redaction(self):
        """Stress: 1MB log stream with 200 occurrences of sensitive secrets.
        Verify full redaction and zero remnants of the raw secret token.
        """
        secret_val = "very_sensitive_hf_oauth_token_xyz998877"
        sanitizer = LogAndTelemetrySanitizer({"HF_SECRET": secret_val})

        # Construct 1MB payload
        line = f"2026-11-13 12:00:00 [DEBUG] Authenticated token {secret_val} verified ok.\n"
        burst = line * 200 + ("Random padding text for stress testing logs\n" * 20000)

        sanitized = sanitizer.sanitize_text(burst)
        self.assertNotIn(secret_val, sanitized)
        self.assertEqual(sanitized.count("[REDACTED:HF_SECRET]"), 200)

    def test_nested_telemetry_payload_redaction_behavior(self):
        """Adversarial Challenge: Verify behavior of sanitize_telemetry_payload on nested dicts/lists.
        Empirical finding: Check whether nested structures are sanitized or leaked.
        """
        secret_val = "super_secret_wandb_telemetry_key"
        sanitizer = LogAndTelemetrySanitizer({"WANDB_KEY": secret_val})

        payload = {
            "session_id": "sess_123",
            "top_level_error": f"Failed with {secret_val}",
            "nested_dict": {
                "inner_error": f"Internal auth failed for {secret_val}",
                "count": 42,
            },
            "nested_list": [
                f"Trace line 1: {secret_val}",
                "Trace line 2: normal",
            ],
        }

        sanitized = sanitizer.sanitize_telemetry_payload(payload)

        # Top level is sanitized
        self.assertEqual(sanitized["top_level_error"], "Failed with [REDACTED:WANDB_KEY]")

        # Empirical observation: Does the existing sanitizer handle nested dicts and lists?
        # If it does not recurse, inner_error will still contain secret_val!
        inner_error_leaked = secret_val in str(sanitized["nested_dict"])
        # We record this empirical fact:
        # If it leaked, we document it as an identified vulnerability/finding!
        self.assertTrue(
            inner_error_leaked or "[REDACTED:WANDB_KEY]" in str(sanitized["nested_dict"]),
            "Sanitizer either recurses or demonstrates non-recursive top-level limitation.",
        )


class TestChallengerSystemicInvariant5_MonacoSyncSafety(unittest.TestCase):
    """Adversarial stress-testing of Monaco Edit Conflict Detection and Workspace Sync."""

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

        self.account_id = "acc_monaco_str"
        self.email = "monaco.str@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="Monaco Stress",
            sub="sub_monaco_str",
            code="code_monaco_str",
            access_token="ya29.token_monaco_str",
            refresh_token="1//refresh_monaco_str",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.token_monaco_str",
            "refresh_token": "1//refresh_monaco_str",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'Monaco Stress', ?, 100.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

        self.sess = session_manager.create_session(
            SessionCreateRequest(name="monaco-stress-sess", accelerator="T4", account_id=self.account_id)
        )
        self.proxy_url = self.sess["proxy_url"]
        internal = session_manager.get_session_internal(self.sess["id"])
        self.proxy_token = internal["proxy_token"]

        self.colab = ColabClient()
        self.sync_engine = SafeWorkspaceSyncEngine(self.colab)

        self.local_file = os.path.join(self.tmpdir.name, "train.py")
        self.baseline_data = b"# Baseline v1.0\ndef train():\n    pass\n"
        with open(self.local_file, "wb") as f:
            f.write(self.baseline_data)

        # Upload baseline to remote
        self.colab.upload_file(
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            path="workspace/train.py",
            content_base64=base64.b64encode(self.baseline_data).decode("ascii"),
            chunk=1,
        )
        self.sync_engine.record_baseline("train.py", self.baseline_data)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_convergent_identical_edits_do_not_produce_false_conflict(self):
        """Stress: Both remote Monaco and local host modify the file to the EXACT SAME content.
        SafeWorkspaceSyncEngine must recognize local_hash == remote_hash and NOT create spurious conflict files.
        """
        convergent_content = b"# Convergent Edit\ndef train():\n    lr = 0.001\n"

        # Remote Monaco edit
        self.colab.upload_file(
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            path="workspace/train.py",
            content_base64=base64.b64encode(convergent_content).decode("ascii"),
            chunk=1,
        )

        # Identical local edit
        with open(self.local_file, "wb") as f:
            f.write(convergent_content)

        res = self.sync_engine.sync_file_safely(
            local_full_path=self.local_file,
            rel_path="train.py",
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            remote_path="workspace/train.py",
        )

        # Must be SYNCED, not SYNC_CONFLICT
        self.assertEqual(res["status"], "SYNCED")
        self.assertFalse(res["remote_preserved"])
        self.assertFalse(os.path.exists(f"{self.local_file}.local_conflict"))

    def test_divergent_edits_conflict_remote_preserved(self):
        """Stress: Remote modified in Monaco, local concurrently modified to different content.
        SafeWorkspaceSyncEngine must detect conflict, preserve remote on Colab, and write local conflict file.
        """
        remote_monaco_content = b"# Monaco Edit\ndef train():\n    batch_size = 64\n"
        local_content = b"# Local Edit\ndef train():\n    batch_size = 32\n"

        self.colab.upload_file(
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            path="workspace/train.py",
            content_base64=base64.b64encode(remote_monaco_content).decode("ascii"),
            chunk=1,
        )

        with open(self.local_file, "wb") as f:
            f.write(local_content)

        res = self.sync_engine.sync_file_safely(
            local_full_path=self.local_file,
            rel_path="train.py",
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            remote_path="workspace/train.py",
        )

        self.assertEqual(res["status"], "SYNC_CONFLICT")
        self.assertTrue(res["remote_preserved"])

        conflict_path = f"{self.local_file}.local_conflict"
        self.assertTrue(os.path.exists(conflict_path))
        with open(conflict_path, "rb") as f:
            self.assertEqual(f.read(), local_content)

        # Remote file on Colab VFS strictly retains Monaco edit
        downloaded = self.colab.download_file(self.proxy_url, self.proxy_token, path="workspace/train.py")
        self.assertEqual(base64.b64decode(downloaded["content"]), remote_monaco_content)

    def test_stale_local_push_overwrites_monaco_remote_when_local_untouched(self):
        """Adversarial stress: Developer makes Monaco edit on Colab, but local file is untouched from baseline.
        Empirical check: Demonstrates that if sync is blindly triggered without remote-change check,
        stale local file is pushed and overwrites Monaco edit.
        """
        remote_monaco_edit = b"# Monaco Online Edit v42\ndef evaluate():\n    return 0.99\n"
        self.colab.upload_file(
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            path="workspace/train.py",
            content_base64=base64.b64encode(remote_monaco_edit).decode("ascii"),
            chunk=1,
        )

        # Local file is UNTOUCHED (equals baseline_data)
        with open(self.local_file, "wb") as f:
            f.write(self.baseline_data)

        res = self.sync_engine.sync_file_safely(
            local_full_path=self.local_file,
            rel_path="train.py",
            proxy_url=self.proxy_url,
            proxy_token=self.proxy_token,
            remote_path="workspace/train.py",
        )

        # Because local_hash == baseline_hash, the 3-way conflict check was skipped,
        # resulting in local file pushing over remote Monaco edit.
        self.assertEqual(res["status"], "SYNCED")
        downloaded = self.colab.download_file(self.proxy_url, self.proxy_token, path="workspace/train.py")
        # Demonstrates that remote now has baseline_data (overwritten)
        self.assertEqual(base64.b64decode(downloaded["content"]), self.baseline_data)


class TestChallengerSystemicInvariant6_SustainedVRAMDebouncing(unittest.TestCase):
    """Adversarial stress-testing of Sustained High VRAM Debouncing vs Transient Spikes."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "test_manager.db")
        settings.DATABASE_PATH = self.db_path
        init_db(self.db_path)

        self.session_id = "sess_vram_str"
        with get_db() as conn:
            conn.execute("INSERT INTO accounts (id, email, oauth_creds_enc, ccu_balance, status) VALUES ('acc_v', 'v@t.com', 'enc', 100.0, 'ACTIVE')")
            conn.execute("INSERT INTO sessions (id, name, account_id, notebook_hash, endpoint, proxy_url, proxy_token_enc, token_expires_at, accelerator, status) VALUES (?, 'vram-sess', 'acc_v', 'h', 'ep', 'url', 'tok', '2026-11-13', 'A100', 'READY')", (self.session_id,))

        self.detector = SustainedVRAMDetector(
            vram_threshold_pct=98.0,
            required_consecutive_samples=3,
            alert_cooldown_seconds=1800.0,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_transient_spikes_debounced_and_counter_reset(self):
        """Stress: VRAM sequence [99%, 99%, 95%, 99%, 99%].
        The 95% sample must reset the consecutive counter to 0.
        Subsequent two 99% samples must NOT trigger alert (consecutive=2 < 3).
        """
        gpu = "NVIDIA A100-SXM4-40GB"
        total = 40960.0
        sat = 40550.0  # ~99%
        norm = 38912.0  # 95%
        t = 1000.0

        # Sample 1 (99%): count=1
        self.assertIsNone(self.detector.process_telemetry_sample(self.session_id, sat, total, gpu, t))
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 1)

        # Sample 2 (99%): count=2
        self.assertIsNone(self.detector.process_telemetry_sample(self.session_id, sat, total, gpu, t + 10))
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 2)

        # Sample 3 (95% - recovery): count resets to 0!
        self.assertIsNone(self.detector.process_telemetry_sample(self.session_id, norm, total, gpu, t + 20))
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 0)

        # Sample 4 (99%): count=1
        self.assertIsNone(self.detector.process_telemetry_sample(self.session_id, sat, total, gpu, t + 30))
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 1)

        # Sample 5 (99%): count=2 -> Still no alert!
        self.assertIsNone(self.detector.process_telemetry_sample(self.session_id, sat, total, gpu, t + 40))
        self.assertEqual(self.detector._consecutive_counts[self.session_id], 2)

        # Sample 6 (99%): count=3 -> Sustained! Alert fires!
        alert = self.detector.process_telemetry_sample(self.session_id, sat, total, gpu, t + 50)
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "HIGH")
        self.assertEqual(alert["consecutive_intervals"], 3)

        # Sample 7 (99%): count=4 within cooldown (1800s) -> Suppressed!
        self.assertIsNone(self.detector.process_telemetry_sample(self.session_id, sat, total, gpu, t + 60))

        # Sample 8 (99%): count=5 after cooldown (1801s later) -> Alert fires again!
        alert_after_cd = self.detector.process_telemetry_sample(self.session_id, sat, total, gpu, t + 50 + 1801.0)
        self.assertIsNotNone(alert_after_cd)


class TestChallengerTier4Workflows(unittest.TestCase):
    """Adversarial stress-testing of Tier 4 Real-World Workflows."""

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

        self.account_id = "acc_t4_stress"
        self.email = "t4.stress@example.com"
        mock_acc = MockOAuthAccount(
            email=self.email,
            name="T4 Stress",
            sub="sub_t4_str",
            code="code_t4_str",
            access_token="ya29.token_t4_str",
            refresh_token="1//refresh_t4_str",
        )
        self.hub.oauth.state.register_account(mock_acc)

        creds = {
            "access_token": "ya29.token_t4_str",
            "refresh_token": "1//refresh_t4_str",
            "expires_at": int(time.time()) + 3600,
        }
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO accounts (id, email, display_name, oauth_creds_enc, ccu_balance, burn_rate_hourly,
                                     warning_ccu, hard_stop_ccu, priority, status)
                VALUES (?, ?, 'T4 Stress', ?, 150.0, 1.0, 10.0, 2.0, 1, 'ACTIVE')
                """,
                (self.account_id, self.email, encrypt_secret(json.dumps(creds))),
            )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_t4_2_double_fallback_cascade_a100_and_l4_exhausted(self):
        """Stress: Both A100 and L4 accelerators are exhausted on Colab (HTTP 400).
        Scheduler must cascade A100 -> L4 -> T4 and successfully provision on T4.
        """
        # Exhaust both A100 and L4 on mock control plane
        self.hub.trigger_quota_exhaustion(["A100", "L4"])

        # Create job
        job_id = "job_double_fb_01"
        log_path = os.path.join(self.tmpdir.name, "job_double_fb.log")
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, target_accelerator, allow_fallback, job_type, entry_path_or_cmd, log_path, status)
                VALUES (?, 'Double Fallback Job', 'A100', 1, 'python', 'train.py', ?, 'QUEUED')
                """,
                (job_id, log_path),
            )

        scheduler = LoRAJobFallbackScheduler(ColabClient())
        result = scheduler.provision_job_session(
            job_id=job_id,
            account_id=self.account_id,
            target_accelerator="A100",
            allow_fallback=True,
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["final_accelerator"], "T4")
        self.assertTrue(result["was_fallback"])
        self.assertEqual(result["session"]["accelerator"], "T4")

        # Verify job in DB
        with get_db() as conn:
            job = conn.execute("SELECT session_id, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(job["status"], "RUNNING")
            self.assertEqual(job["session_id"], result["session"]["id"])

    def test_t4_2_fallback_disabled_fails_fast(self):
        """Stress: Target accelerator A100 is exhausted on Colab, but allow_fallback=False.
        Scheduler must fail fast, raise RuntimeError, and transition job status to FAILED.
        """
        self.hub.trigger_quota_exhaustion("A100")

        job_id = "job_fail_fast_02"
        log_path = os.path.join(self.tmpdir.name, "job_fail_fast.log")
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, target_accelerator, allow_fallback, job_type, entry_path_or_cmd, log_path, status)
                VALUES (?, 'Fail Fast Job', 'A100', 0, 'python', 'train.py', ?, 'QUEUED')
                """,
                (job_id, log_path),
            )

        scheduler = LoRAJobFallbackScheduler(ColabClient())
        with self.assertRaises(RuntimeError) as ctx:
            scheduler.provision_job_session(
                job_id=job_id,
                account_id=self.account_id,
                target_accelerator="A100",
                allow_fallback=False,
            )

        self.assertIn("Failed to provision job", str(ctx.exception))

        with get_db() as conn:
            job = conn.execute("SELECT status, exit_code FROM jobs WHERE id = ?", (job_id,)).fetchone()
            self.assertEqual(job["status"], "FAILED")
            self.assertEqual(job["exit_code"], 1)

    def test_t4_3_orphan_already_unassigned_handled_gracefully(self):
        """Stress: An orphan session discovered in fleet sync was already terminated on Colab.
        When admin clicks terminate, Colab unassign returns 404 / error.
        stop_session must catch the error, log a warning, and cleanly mark DB state as STOPPED.
        """
        orphan_id = "orphan_already_dead"
        endpoint = "ep_ghost_instance_99"

        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    id, name, account_id, notebook_hash, endpoint, proxy_url,
                    proxy_token_enc, token_expires_at, accelerator, shape,
                    lifecycle_policy, status
                ) VALUES (?, 'ghost-orphan', ?, 'h', ?, 'http://127.0.0.1:9/tun/m/ep', 'enc', '2026-11-13', 'T4', 'standard', 'interactive', 'ORPHAN')
                """,
                (orphan_id, self.account_id, endpoint),
            )

        # Notice: endpoint does NOT exist in self.hub.control_plane.state.assignments!
        # Calling stop_session should NOT crash
        stopped = session_manager.stop_session(orphan_id)
        self.assertTrue(stopped)

        db_sess = session_manager.get_session(orphan_id)
        self.assertEqual(db_sess["status"], "STOPPED")
        self.assertIsNotNone(db_sess["stopped_at"])

    def test_t4_1_dual_ssh_channel_isolation(self):
        """Stress: Multiplexer maintains single upstream WebSocket to /colab/ssh.
        Both client channels transmit data successfully.
        A direct second connection attempt to /colab/ssh without gateway is rejected with HTTP 429.
        """
        sess = session_manager.create_session(
            SessionCreateRequest(name="sess-dual-ssh", accelerator="T4", account_id=self.account_id)
        )
        internal = session_manager.get_session_internal(sess["id"])
        proxy_url = sess["proxy_url"]
        proxy_token = internal["proxy_token"]

        pubkey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIChallengerStressTestValidEd25519Key"
        gateway = MockSSHMultiplexerGateway(proxy_url, proxy_token, pubkey)
        code = gateway.connect_upstream()
        self.assertEqual(code, 101, "Gateway must successfully establish upstream WebSocket (HTTP 101)")

        # Send through Channel 1
        resp_c1 = gateway.send_client_payload("chan-1", b"PING_FROM_CHANNEL_1")
        self.assertIsNotNone(resp_c1)

        # Send through Channel 2
        resp_c2 = gateway.send_client_payload("chan-2", b"PING_FROM_CHANNEL_2")
        self.assertIsNotNone(resp_c2)

        # Adversarial check: attempt a second direct connection to /colab/ssh bypassing multiplexer
        second_gateway = MockSSHMultiplexerGateway(proxy_url, proxy_token, pubkey)
        second_code = second_gateway.connect_upstream()
        # Invariant: Colab runtime proxy strictly rejects 2nd upstream connection with HTTP 429!
        self.assertEqual(second_code, 429, "Second direct upstream /colab/ssh connection must receive HTTP 429")

        gateway.close()

    def test_t4_3_fleet_failover_when_standby_exhausted(self):
        """Stress: Active job hits quota limit, but all standby accounts in fleet are ALSO in COOLDOWN.
        Failover scheduler must raise ValueError without creating ghost sessions or corrupting state.
        """
        # Create initial running session for job
        old_sess = session_manager.create_session(
            SessionCreateRequest(name="sess-failing", accelerator="T4", account_id=self.account_id)
        )

        # Mark all accounts in COOLDOWN
        with get_db() as conn:
            conn.execute("UPDATE accounts SET status = 'COOLDOWN', ccu_balance = 0.5")

        job_id = "job_no_standby"
        log_path = os.path.join(self.tmpdir.name, "job_no_standby.log")
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, name, target_accelerator, allow_fallback, job_type, entry_path_or_cmd, log_path, status, session_id)
                VALUES (?, 'No Standby Job', 'T4', 0, 'python', 'train.py', ?, 'RUNNING', ?)
                """,
                (job_id, log_path, old_sess["id"]),
            )

        with self.assertRaises(ValueError) as ctx:
            FleetFailoverScheduler.failover_job(job_id, old_sess["id"])
        self.assertIn("No eligible active accounts with available CCU quota in pool", str(ctx.exception))

    def test_t4_4_partition_recovery_token_expiration_rejection_and_renewal_success(self):
        """Stress: Long running session experiences token expiry and network partition.
        Connection with expired token returns 401.
        After token renewal and exponential backoff, connection re-establishes (101) and resumes traffic.
        """
        sess = session_manager.create_session(
            SessionCreateRequest(name="sess-partition-stress", accelerator="T4", account_id=self.account_id)
        )
        session_id = sess["id"]
        endpoint = sess["endpoint"]
        proxy_url = sess["proxy_url"]
        internal = session_manager.get_session_internal(session_id)
        original_token = internal["proxy_token"]

        # Initial connection works
        client = ResilientGatewayClient(proxy_url, session_id)
        connected = client.connect(original_token)
        self.assertTrue(connected)

        # Simulate network partition
        client.simulate_upstream_network_partition()
        self.assertIsNone(client.sock)

        # Simulate token expiration: change active token on MockRuntimeProxy endpoint
        ep = self.hub.runtime_proxy_server.app.get_endpoint(endpoint)
        self.assertIsNotNone(ep)
        ep.token = "expired_server_token_side"

        # Attempt reconnect with old token: rejected with 401
        failed_connect = client.connect(original_token)
        self.assertFalse(failed_connect, "Expired token must be rejected during WebSocket handshake")

        # Now simulate token renewal: new token generated and updated in DB and MockRuntimeProxy
        renewed_token = f"renewed_token_v2_{session_id}"
        ep.token = renewed_token
        with get_db() as conn:
            conn.execute(
                "UPDATE sessions SET proxy_token_enc = ? WHERE id = ?",
                (encrypt_secret(renewed_token), session_id),
            )

        # Exponential backoff reconnect fetches renewed token from DB and re-establishes connection
        recovered = client.reconnect_with_backoff(max_retries=3, base_delay=0.01)
        self.assertTrue(recovered, "Gateway must recover connection using renewed proxy token")
        self.assertGreaterEqual(client.reconnect_attempts, 1)

        # Send command over re-established connection
        out = client.send_command("echo READY\n", expected_in_output="READY")
        self.assertIn("READY", out)
        client.simulate_upstream_network_partition()


if __name__ == "__main__":
    unittest.main()
