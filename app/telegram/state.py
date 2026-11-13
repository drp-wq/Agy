"""Thread- and process-safe persistent Telegram state (dedup, rate limit, cursor, alert history)."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.telegram.models import TelegramAlertEvent


def _default_state() -> dict[str, Any]:
    return {
        "update_offset": 0,
        "dedup_history": {},
        "sent_timestamps": [],
        "last_readiness_summary": None,
        "last_exchange_health": {},
        "last_risk_flags": {"emergency_stop": False, "circuit_breaker": False},
        "recent_alerts": [],
        "metrics": {
            "sent_count": 0,
            "failed_count": 0,
            "deduped_count": 0,
            "rate_limited_count": 0,
            "commands_handled": 0,
            "unauthorized_rejected": 0,
            "last_sent_at": None,
            "last_poll_at": None,
            "last_error": None,
        },
    }


class TelegramStateStore:
    """Atomic, 0600-protected JSON state store with cross-process file locking."""

    def __init__(self, state_path: Path) -> None:
        self.state_path = Path(state_path)
        self.lock_path = self.state_path.with_suffix(".lock")
        self._thread_lock = threading.RLock()

    @contextmanager
    def _locked_state(self, write: bool = True) -> Iterator[dict[str, Any]]:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock:
            with open(self.lock_path, "a+", encoding="utf-8") as lock_fh:
                os.chmod(self.lock_path, 0o600)
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
                try:
                    data = self._read_unlocked()
                    yield data
                    if write:
                        self._write_unlocked(data)
                finally:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def _read_unlocked(self) -> dict[str, Any]:
        base = _default_state()
        if not self.state_path.exists():
            return base
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for k, v in loaded.items():
                    if k == "metrics" and isinstance(v, dict):
                        base["metrics"].update(v)
                    else:
                        base[k] = v
        except Exception:
            # Corrupt state recovers cleanly rather than crashing trading loops
            return base
        return base

    def _write_unlocked(self, data: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.state_path)

    def get_update_offset(self) -> int:
        with self._locked_state(write=False) as st:
            return int(st.get("update_offset", 0))

    def set_update_offset(self, offset: int) -> None:
        with self._locked_state(write=True) as st:
            st["update_offset"] = max(int(offset), int(st.get("update_offset", 0)))
            st["metrics"]["last_poll_at"] = datetime.now(timezone.utc).isoformat()

    def record_poll_heartbeat(self, error: str | None = None) -> None:
        with self._locked_state(write=True) as st:
            st["metrics"]["last_poll_at"] = datetime.now(timezone.utc).isoformat()
            if error:
                st["metrics"]["last_error"] = error[:200]

    def record_command_metric(self, authorized: bool) -> None:
        with self._locked_state(write=True) as st:
            key = "commands_handled" if authorized else "unauthorized_rejected"
            st["metrics"][key] = int(st["metrics"].get(key, 0)) + 1

    def check_and_reserve_send(
        self,
        dedup_key: str | None,
        dedup_window_sec: float,
        rate_limit_per_min: int,
        now_epoch: float | None = None,
    ) -> tuple[bool, str | None]:
        """Atomically check dedup + sliding-window rate limit and reserve a send slot."""
        now = time.time() if now_epoch is None else now_epoch
        with self._locked_state(write=True) as st:
            dedup: dict[str, float] = st.setdefault("dedup_history", {})
            # Prune expired dedup entries older than max(dedup_window_sec, 3600)
            prune_cutoff = now - max(dedup_window_sec, 3600.0)
            for k in [k for k, ts in dedup.items() if ts < prune_cutoff]:
                del dedup[k]

            if dedup_key:
                last_ts = dedup.get(dedup_key)
                if last_ts is not None and (now - last_ts) < dedup_window_sec:
                    st["metrics"]["deduped_count"] = int(st["metrics"].get("deduped_count", 0)) + 1
                    return False, "deduplicated"

            sent_ts = [float(t) for t in st.get("sent_timestamps", []) if now - float(t) < 60.0]
            if len(sent_ts) >= rate_limit_per_min:
                st["sent_timestamps"] = sent_ts
                st["metrics"]["rate_limited_count"] = int(st["metrics"].get("rate_limited_count", 0)) + 1
                return False, "rate_limited"

            if dedup_key:
                dedup[dedup_key] = now
            sent_ts.append(now)
            st["sent_timestamps"] = sent_ts
            return True, None

    def record_send_result(
        self,
        event: TelegramAlertEvent,
        success: bool,
        message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        with self._locked_state(write=True) as st:
            metrics = st["metrics"]
            if success:
                metrics["sent_count"] = int(metrics.get("sent_count", 0)) + 1
                metrics["last_sent_at"] = datetime.now(timezone.utc).isoformat()
                metrics["last_error"] = None
            else:
                metrics["failed_count"] = int(metrics.get("failed_count", 0)) + 1
                metrics["last_error"] = (error or "send_failed")[:200]

            alerts: list[dict[str, Any]] = st.setdefault("recent_alerts", [])
            alerts.insert(
                0,
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type.value,
                    "severity": event.severity.value,
                    "title": event.title,
                    "exchange": event.exchange,
                    "symbol": event.symbol,
                    "side": event.side,
                    "price": event.price,
                    "quantity": event.quantity,
                    "pnl_usd": event.pnl_usd,
                    "details": event.details[:240],
                    "timestamp": event.timestamp.isoformat(),
                    "delivered": success,
                    "message_id": message_id,
                },
            )
            del alerts[50:]

    def get_readiness_summary(self) -> dict[str, Any] | None:
        with self._locked_state(write=False) as st:
            return st.get("last_readiness_summary")

    def set_readiness_summary(self, summary: dict[str, Any]) -> None:
        with self._locked_state(write=True) as st:
            st["last_readiness_summary"] = summary

    def update_exchange_health(self, exchange: str, healthy: bool) -> bool | None:
        """Update stored health for an exchange and return previous value (or None if unseen)."""
        with self._locked_state(write=True) as st:
            health_map: dict[str, bool] = st.setdefault("last_exchange_health", {})
            prev = health_map.get(exchange)
            health_map[exchange] = healthy
            return prev

    def update_risk_flags(self, emergency_stop: bool, circuit_breaker: bool) -> dict[str, bool]:
        with self._locked_state(write=True) as st:
            prev = dict(st.get("last_risk_flags") or {"emergency_stop": False, "circuit_breaker": False})
            st["last_risk_flags"] = {
                "emergency_stop": emergency_stop,
                "circuit_breaker": circuit_breaker,
            }
            return prev

    def list_recent_alerts(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._locked_state(write=False) as st:
            return list(st.get("recent_alerts", []))[:limit]

    def get_metrics(self) -> dict[str, Any]:
        with self._locked_state(write=False) as st:
            return dict(st.get("metrics", {}))
