"""Non-trading operational readiness monitor; never reads credentials."""
import datetime as dt
import json
import os
import pathlib
import subprocess
import urllib.error
import urllib.request

ROOT = pathlib.Path("/root/multi-exchange-paper")
OUT = ROOT / "data" / "readiness_watch.json"
def request(url):
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None

def active(name):
    return subprocess.run(["systemctl", "is-active", "--quiet", name], check=False).returncode == 0

checks = {
    "engine_service_active": active("multi-exchange-paper"),
    "proxy_service_active": active("caddy"),
    "local_paper_health": request("http://127.0.0.1:8788/api/health") == 200,
    "public_https_health": request("https://trade.187-52-118-25.sslip.io/api/health") == 200,
    "private_state_requires_auth": request("https://trade.187-52-118-25.sslip.io/api/state") == 401,
}
result = {
    "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    "checks": checks,
    "operational_checks_passed": all(checks.values()),
    "live_readiness": "NOT_APPROVED",
    "live_execution": "DISABLED",
    "pending": ["multi-day paper soak", "exchange-side OKX key permission reduction", "operator review"],
}
tmp = OUT.with_suffix(".tmp")
tmp.write_text(json.dumps(result, indent=2) + "\n")
os.chmod(tmp, 0o600)
os.replace(tmp, OUT)
print(json.dumps(result))
