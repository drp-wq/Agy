#!/usr/bin/env python3
"""Read-only Telegram readiness alert bridge sharing dedup/rate-limit state with the main engine."""

from __future__ import annotations

import fcntl
import html
import json
import os
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from uuid import uuid4

ROOT = pathlib.Path("/root/multi-exchange-paper")
CONFIG = pathlib.Path("/root/trading-secrets/telegram.env")
LEGACY_STATE = ROOT / "data/telegram_alert_state.json"
SHARED_STATE = ROOT / "data/telegram_state.json"
SHARED_LOCK = ROOT / "data/telegram_state.lock"
REPORT = ROOT / "data/readiness_watch.json"
DASHBOARD_URL = "https://trade.187-52-118-25.sslip.io"


def _load_env(path: pathlib.Path) -> tuple[str, str]:
    cfg: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if "=" in s and not s.startswith("#"):
            k, v = s.split("=", 1)
            cfg[k.strip()] = v.strip().strip("'\"")
    token = cfg.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = cfg.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        raise RuntimeError("Telegram config missing")
    return token, chat


def _send_with_retry(token: str, chat: str, html_text: str, retries: int = 3) -> int:
    body = urllib.parse.urlencode(
        {
            "chat_id": chat,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    last_err = "unknown"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body)
            with urllib.request.urlopen(req, timeout=12) as resp:
                result = json.load(resp)
            if not result.get("ok"):
                raise RuntimeError("Telegram send rejected")
            return int(result["result"]["message_id"])
        except urllib.error.HTTPError as exc:
            last_err = f"http_{exc.code}"
            if exc.code == 429 or exc.code >= 500:
                time.sleep(0.8 * (2**attempt))
                continue
            raise RuntimeError(last_err) from None
        except Exception as exc:
            last_err = type(exc).__name__
            if attempt < retries - 1:
                time.sleep(0.8 * (2**attempt))
                continue
            raise RuntimeError(last_err) from None
    raise RuntimeError(last_err)


def main() -> None:
    token, chat = _load_env(CONFIG)
    if not REPORT.exists():
        print("NO_REPORT")
        return

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    checks = report.get("checks", {})
    failed = sorted(k for k, v in checks.items() if v is not True)
    summary = {
        "failed": failed,
        "live_execution": report.get("live_execution"),
        "live_readiness": report.get("live_readiness"),
    }

    dedup_key = f"readiness:{','.join(failed) or 'ok'}:{summary['live_execution']}:{summary['live_readiness']}"
    SHARED_STATE.parent.mkdir(parents=True, exist_ok=True)

    with open(SHARED_LOCK, "a+", encoding="utf-8") as lock_fh:
        os.chmod(SHARED_LOCK, 0o600)
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            state = {}
            if SHARED_STATE.exists():
                try:
                    state = json.loads(SHARED_STATE.read_text(encoding="utf-8"))
                except Exception:
                    state = {}
            if not isinstance(state, dict):
                state = {}

            prev_summary = state.get("last_readiness_summary")
            if prev_summary is None and LEGACY_STATE.exists():
                try:
                    prev_summary = json.loads(LEGACY_STATE.read_text(encoding="utf-8"))
                except Exception:
                    prev_summary = None

            if prev_summary == summary:
                print("UNCHANGED")
                return

            now = time.time()
            dedup = state.setdefault("dedup_history", {})
            if dedup_key in dedup and (now - float(dedup[dedup_key])) < 60.0:
                print("DEDUPLICATED")
                return

            sent_ts = [float(t) for t in state.get("sent_timestamps", []) if now - float(t) < 60.0]
            if len(sent_ts) >= 20:
                print("RATE_LIMITED")
                return

            sev = "HIGH" if failed else "INFO"
            sev_icon = "🟠" if failed else "🟢"
            detail = (
                f"Failed checks: {', '.join(failed)}"
                if failed
                else "All operational checks passing."
            )
            event_id = uuid4().hex[:10]
            checked = str(report.get("checked_at_utc", "unknown"))[:19].replace("T", " ")
            html_msg = "\n".join(
                [
                    f"{sev_icon} 🧭 <b>Operational Readiness Transition</b>",
                    f"<b>Mode:</b> <code>PAPER_ONLY</code>  <b>Severity:</b> <code>{sev}</code>",
                    html.escape(detail),
                    f"<b>Live Execution:</b> <code>{html.escape(str(summary['live_execution']))}</code>  "
                    f"<b>Readiness:</b> <code>{html.escape(str(summary['live_readiness']))}</code>",
                    f"<b>Time:</b> <code>{html.escape(checked)} UTC</code>  <b>ID:</b> <code>{event_id}</code>",
                    f'🔗 <a href="{html.escape(DASHBOARD_URL)}">Open Dashboard</a>',
                ]
            )

            msg_id = _send_with_retry(token, chat, html_msg)

            dedup[dedup_key] = now
            sent_ts.append(now)
            state["sent_timestamps"] = sent_ts
            state["last_readiness_summary"] = summary
            metrics = state.setdefault("metrics", {})
            metrics["sent_count"] = int(metrics.get("sent_count", 0)) + 1
            metrics["last_sent_at"] = datetime.now(timezone.utc).isoformat()
            metrics["last_error"] = None
            alerts = state.setdefault("recent_alerts", [])
            alerts.insert(
                0,
                {
                    "event_id": event_id,
                    "event_type": "readiness_transition",
                    "severity": sev.lower(),
                    "title": "Operational Readiness Transition",
                    "details": detail[:240],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "delivered": True,
                    "message_id": msg_id,
                },
            )
            del alerts[50:]

            tmp_shared = SHARED_STATE.with_suffix(".tmp")
            tmp_shared.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.chmod(tmp_shared, 0o600)
            os.replace(tmp_shared, SHARED_STATE)

            tmp_legacy = LEGACY_STATE.with_suffix(".tmp")
            tmp_legacy.write_text(json.dumps(summary), encoding="utf-8")
            os.chmod(tmp_legacy, 0o600)
            os.replace(tmp_legacy, LEGACY_STATE)

            print(f"SENT message_id={msg_id}")
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
