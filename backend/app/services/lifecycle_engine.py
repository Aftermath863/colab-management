"""Session Lifecycle, Protection Engine & Watchdog Service.

Implements:
1. Interactive session idle detection & auto-stop (default 1800s timeout).
2. Keep-alive anti-disconnect ping loop (60s probe to prevent VM drop).
3. Ephemeral job teardown upon async job completion.
4. Account Quota Circuit Breaker (Warning at 10.0 CCU, Hard-stop at 2.0 CCU).
5. Runtime Proxy Token Auto-Renewal at 5-minute margin.
"""

from __future__ import annotations

import asyncio
import calendar
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from backend.app.config import settings
from backend.app.core.database import get_db
from backend.app.core.security import decrypt_secret, encrypt_secret
from backend.app.services import account_pool, session_manager
from backend.app.services.colab_client import ColabClient

logger = logging.getLogger("lifecycle_engine")


class InteractiveIdleWatchdog:
    """Monitors interactive sessions and enforces idle timeouts.

    Compatible with T3.2 and T4.1 test suites.
    """

    def __init__(self):
        self._last_activity: Dict[str, float] = {}

    def register_session(self, session_id: str, initial_timestamp: Optional[float] = None) -> None:
        self._last_activity[session_id] = initial_timestamp if initial_timestamp is not None else time.time()

    def record_traffic(self, session_id: str, timestamp: Optional[float] = None) -> None:
        """Called by SSH gateway or TTY bridge when bytes/packets are transferred."""
        self._last_activity[session_id] = timestamp if timestamp is not None else time.time()

    def unregister_session(self, session_id: str) -> None:
        self._last_activity.pop(session_id, None)

    def get_last_activity(self, session_id: str) -> Optional[float]:
        return self._last_activity.get(session_id)

    def evaluate_idle_sessions(
        self, current_timestamp: Optional[float] = None, client: Optional[ColabClient] = None
    ) -> List[str]:
        """Check all active interactive sessions and terminate those exceeding idle_timeout_sec."""
        now = current_timestamp if current_timestamp is not None else time.time()
        terminated: List[str] = []

        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, lifecycle_policy, idle_timeout_sec, status
                FROM sessions
                WHERE lifecycle_policy = 'interactive'
                  AND status IN ('READY', 'BOOTSTRAPPING', 'BUSY')
                """
            ).fetchall()

        for row in rows:
            sess_id = row["id"]
            timeout_sec = row["idle_timeout_sec"]
            last_active = self._last_activity.get(sess_id, now)
            idle_duration = now - last_active

            if idle_duration >= timeout_sec:
                logger.info(
                    f"Session {sess_id} idle for {idle_duration:.1f}s >= {timeout_sec}s. Terminating."
                )
                try:
                    session_manager.stop_session(sess_id, client=client)
                    terminated.append(sess_id)
                    self.unregister_session(sess_id)
                except Exception as e:
                    logger.error(f"Failed to auto-stop idle session {sess_id}: {e}")

        return terminated


class QuotaCircuitBreakerWatchdog:
    """Evaluates account CCU health and terminates active sessions on cooldown accounts.

    Compatible with T3.1 and Challenger test suites.
    """

    @staticmethod
    def evaluate_and_enforce_hard_stops(client: Optional[ColabClient] = None) -> List[str]:
        stopped_session_ids: List[str] = []
        with get_db() as conn:
            # Check for accounts where CCU balance is at or below hard_stop_ccu and update to COOLDOWN
            conn.execute(
                """
                UPDATE accounts
                SET status = 'COOLDOWN'
                WHERE ccu_balance <= hard_stop_ccu
                  AND status = 'ACTIVE'
                """
            )
            # Find active sessions on accounts in COOLDOWN or AUTH_EXPIRED
            rows = conn.execute(
                """
                SELECT s.id, s.account_id, a.status as account_status, a.ccu_balance, a.hard_stop_ccu
                FROM sessions s
                JOIN accounts a ON s.account_id = a.id
                WHERE a.status IN ('COOLDOWN', 'AUTH_EXPIRED')
                  AND s.status NOT IN ('STOPPED', 'FAILED', 'LOST')
                """
            ).fetchall()

        for row in rows:
            sess_id = row["id"]
            logger.warning(
                f"Circuit breaker stopping session {sess_id} on account {row['account_id']} "
                f"(status: {row['account_status']}, balance: {row['ccu_balance']} CCU)"
            )
            try:
                session_manager.stop_session(sess_id, client=client)
                stopped_session_ids.append(sess_id)
            except Exception as e:
                logger.error(f"Circuit breaker failed to stop session {sess_id}: {e}")

        return stopped_session_ids


class LifecycleEngine:
    """Master background daemon coordinating all session lifecycle policies."""

    def __init__(self, check_interval_sec: float = 15.0):
        self.check_interval_sec = check_interval_sec
        self.idle_watchdog = InteractiveIdleWatchdog()
        self.quota_watchdog = QuotaCircuitBreakerWatchdog()
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

        # Track keep-alive ping timestamps: session_id -> last_ping_time
        self._last_keep_alive_pings: Dict[str, float] = {}
        # Track warning notification timestamps: account_id -> last_warning_time
        self._last_warning_times: Dict[str, float] = {}

    def record_activity(self, session_id: str, ts: Optional[float] = None) -> None:
        """Record activity event from TTY, SSH, or REST file API."""
        self.idle_watchdog.record_traffic(session_id, ts)

    def register_session(self, session_id: str, initial_timestamp: Optional[float] = None) -> None:
        self.idle_watchdog.register_session(session_id, initial_timestamp)

    def unregister_session(self, session_id: str) -> None:
        self.idle_watchdog.unregister_session(session_id)
        self._last_keep_alive_pings.pop(session_id, None)

    async def start(self) -> None:
        """Start background evaluation loop."""
        if self._running:
            return
        self._running = True
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Session Lifecycle Engine daemon started.")

    async def stop(self) -> None:
        """Stop background evaluation loop gracefully."""
        if not self._running:
            return
        self._running = False
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Session Lifecycle Engine daemon stopped.")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self.tick()
            except Exception as e:
                logger.error(f"Error in Lifecycle Engine evaluation tick: {e}", exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.check_interval_sec)
                break
            except asyncio.TimeoutError:
                pass

    async def tick(self, client: Optional[ColabClient] = None) -> Dict[str, Any]:
        """Execute a single evaluation pass across all policies."""
        now = time.time()
        colab = client or ColabClient()

        # 1. Evaluate interactive idle timeouts
        terminated_idle = self.idle_watchdog.evaluate_idle_sessions(current_timestamp=now, client=colab)

        # 2. Evaluate keep-alive anti-disconnect pings
        pinged_sessions = await self._evaluate_keep_alive_pings(now, client=colab)

        # 3. Evaluate ephemeral session completions
        terminated_ephemeral = await self._evaluate_ephemeral_sessions(client=colab)

        # 4. Evaluate account quota circuit breaker & warnings
        stopped_by_quota, warned_accounts = await self._evaluate_quota_circuit_breaker(now, client=colab)

        # 5. Evaluate proxy token auto-renewal
        renewed_tokens = await self._evaluate_proxy_token_renewals(now, client=colab)

        return {
            "terminated_idle": terminated_idle,
            "pinged_sessions": pinged_sessions,
            "terminated_ephemeral": terminated_ephemeral,
            "stopped_by_quota": stopped_by_quota,
            "warned_accounts": warned_accounts,
            "renewed_tokens": renewed_tokens,
        }

    async def _evaluate_keep_alive_pings(self, now: float, client: ColabClient) -> List[str]:
        """Send anti-disconnect ping every 60s to keep-alive sessions."""
        pinged: List[str] = []
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT id, proxy_url, proxy_token_enc
                FROM sessions
                WHERE lifecycle_policy = 'keep_alive'
                  AND status IN ('READY', 'BUSY')
                """
            ).fetchall()

        for row in rows:
            sess_id = row["id"]
            last_ping = self._last_keep_alive_pings.get(sess_id, 0.0)
            if (now - last_ping) < 60.0:
                continue

            proxy_url = row["proxy_url"]
            try:
                proxy_token = decrypt_secret(row["proxy_token_enc"])
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None,
                    lambda: client.list_contents(proxy_url, proxy_token, path=""),
                )
                self._last_keep_alive_pings[sess_id] = now
                pinged.append(sess_id)
            except Exception as e:
                logger.debug(f"Keep-alive ping for session {sess_id} failed: {e}")

        return pinged

    async def _evaluate_ephemeral_sessions(self, client: ColabClient) -> List[str]:
        """Auto-teardown ephemeral sessions after job completion."""
        terminated: List[str] = []
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT s.id as session_id, j.id as job_id, j.status as job_status
                FROM sessions s
                JOIN jobs j ON j.session_id = s.id
                WHERE s.lifecycle_policy = 'ephemeral'
                  AND s.status NOT IN ('STOPPED', 'FAILED', 'LOST')
                  AND j.status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'OOM_KILLED')
                """
            ).fetchall()

        for row in rows:
            sess_id = row["session_id"]
            logger.info(
                f"Ephemeral session {sess_id} job {row['job_id']} reached terminal status '{row['job_status']}'. Tearing down."
            )
            try:
                session_manager.stop_session(sess_id, client=client)
                terminated.append(sess_id)
                self.unregister_session(sess_id)
            except Exception as e:
                logger.error(f"Failed to teardown ephemeral session {sess_id}: {e}")

        return terminated

    async def _evaluate_quota_circuit_breaker(
        self, now: float, client: ColabClient
    ) -> tuple[List[str], List[str]]:
        """Enforce hard-stop circuit breaker (<= 2.0 CCU) and warning alerts (<= 10.0 CCU)."""
        stopped = self.quota_watchdog.evaluate_and_enforce_hard_stops(client=client)
        warned: List[str] = []

        with get_db() as conn:
            accounts = conn.execute(
                """
                SELECT id, email, ccu_balance, warning_ccu, hard_stop_ccu, status
                FROM accounts
                WHERE status = 'ACTIVE'
                """
            ).fetchall()

        for acc in accounts:
            acc_id = acc["id"]
            bal = acc["ccu_balance"]
            warn_limit = acc["warning_ccu"]
            hard_limit = acc["hard_stop_ccu"]

            # Quota Warning Check
            if bal <= warn_limit and bal > hard_limit:
                last_warn = self._last_warning_times.get(acc_id, 0.0)
                if (now - last_warn) >= 3600.0:
                    self._last_warning_times[acc_id] = now
                    warned.append(acc_id)
                    logger.warning(
                        f"Account {acc['email']} ({acc_id}) CCU balance ({bal:.2f}) dropped below warning threshold ({warn_limit:.2f})"
                    )
                    try:
                        from backend.app.services.notification import notification_service
                        asyncio.create_task(
                            notification_service.dispatch_event(
                                event_type="quota.warning",
                                title=f"Colab Quota Warning: {acc['email']}",
                                fields={
                                    "Account": acc["email"],
                                    "CCU Balance": f"{bal:.2f}",
                                    "Warning Threshold": f"{warn_limit:.2f}",
                                },
                                severity="WARNING",
                                account_id=acc_id,
                            )
                        )
                    except Exception:
                        pass

        return stopped, warned

    async def _evaluate_proxy_token_renewals(self, now: float, client: ColabClient) -> List[str]:
        """Renew proxy tokens for sessions nearing expiry (within 300s)."""
        renewed: List[str] = []
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT s.id, s.account_id, s.endpoint, s.token_expires_at
                FROM sessions s
                WHERE s.status IN ('READY', 'BUSY', 'BOOTSTRAPPING')
                """
            ).fetchall()

        for row in rows:
            sess_id = row["id"]
            acc_id = row["account_id"]
            endpoint = row["endpoint"]
            expires_at_val = row["token_expires_at"]

            if not expires_at_val:
                continue

            try:
                if isinstance(expires_at_val, str):
                    # Handle both ISO and space separated timestamps
                    clean_ts = expires_at_val.replace("T", " ").replace("Z", "")
                    if "." in clean_ts:
                        clean_ts = clean_ts.split(".")[0]
                    dt = datetime.strptime(clean_ts, "%Y-%m-%d %H:%M:%S")
                    expiry_ts = calendar.timegm(dt.utctimetuple())
                else:
                    expiry_ts = float(expires_at_val)
            except Exception:
                continue

            if (expiry_ts - now) <= 300.0:
                logger.info(f"Session {sess_id} token expires in {expiry_ts - now:.1f}s. Renewing assignment.")
                try:
                    access_token = account_pool.get_valid_access_token(acc_id)
                    assignments = client.list_assignments(access_token)
                    target = next((a for a in assignments if a.get("endpoint") == endpoint), None)
                    if target and "runtimeProxyInfo" in target:
                        p_info = target["runtimeProxyInfo"]
                        new_token = p_info["token"]
                        new_url = p_info.get("url", "")
                        new_exp_in = p_info.get("tokenExpiresInSeconds", 3600)
                        new_expiry_str = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.gmtime(now + new_exp_in)
                        )
                        with get_db() as conn:
                            conn.execute(
                                """
                                UPDATE sessions
                                SET proxy_url = ?, proxy_token_enc = ?, token_expires_at = ?
                                WHERE id = ?
                                """,
                                (new_url, encrypt_secret(new_token), new_expiry_str, sess_id),
                            )
                        renewed.append(sess_id)
                        logger.info(f"Renewed proxy token for session {sess_id} successfully.")
                except Exception as e:
                    logger.error(f"Failed to renew proxy token for session {sess_id}: {e}")

        return renewed


# Global Singleton Instance
lifecycle_engine = LifecycleEngine()
