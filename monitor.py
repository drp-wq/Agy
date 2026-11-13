import concurrent.futures
import datetime
import json
import os
import time
from pathlib import Path

import ccxt

BASE = Path("/root/multi-exchange-paper")
EXCHANGES = ("okx", "bybit")


def check(name: str) -> dict:
    exchange = getattr(ccxt, name)({"enableRateLimit": True, "timeout": 15000})
    try:
        ticker = exchange.fetch_ticker("BTC/USDT")
        return {"exchange": name, "status": "ok", "last": ticker.get("last")}
    except Exception as exc:
        return {"exchange": name, "status": "error", "error_type": type(exc).__name__}
    finally:
        exchange.close()


def once() -> dict:
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(EXCHANGES)) as pool:
        markets = list(pool.map(check, EXCHANGES))
    data = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "paper",
        "live_orders_enabled": False,
        "order_routes": 0,
        "markets": markets,
    }
    tmp = BASE / "status.tmp"
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, BASE / "status.json")
    print(json.dumps(data), flush=True)
    return data


if __name__ == "__main__":
    import sys

    if "--loop" in sys.argv:
        while True:
            once()
            time.sleep(60)
    else:
        once()
