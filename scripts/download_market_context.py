"""Download context candles and merge only identical overlaps safely.

Importing this module has no network or filesystem side effects. Exchange-session
validity is intentionally not inferred here; unusual timestamps are preserved.
"""
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
from app.candle_integrity import merge_candle_sources


START_DATE = date(2026, 10, 2)
END_DATE = date(2026, 10, 7)
INTERVAL = 5
INSTRUMENTS = {
    "NIFTY50": "NSE_INDEX|Nifty 50",
    "NIFTY_BANK": "NSE_INDEX|Nifty Bank",
    "NIFTY_IT": "NSE_INDEX|Nifty IT",
    "SENSEX": "BSE_INDEX|SENSEX",
    "INDIA_VIX": "NSE_INDEX|India VIX",
}
OUTPUT_DIR = PROJECT_ROOT / "data" / "research" / "market_context"
BASE_URL = "https://api.upstox.com/v3/historical-candle"
CONTEXT_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi", "date"]


def fetch_day(instrument_key, day, headers):
    encoded_key = requests.utils.quote(instrument_key, safe="")
    url = f"{BASE_URL}/{encoded_key}/minutes/{INTERVAL}/{day.isoformat()}/{day.isoformat()}"
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"Upstox historical request failed with HTTP {response.status_code}")
    return response.json().get("data", {}).get("candles", [])


def main():
    load_dotenv()
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is required")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, instrument_key in INSTRUMENTS.items():
        rows = []
        day = START_DATE
        while day <= END_DATE:
            candles = fetch_day(instrument_key, day, headers)
            rows.extend({
                "timestamp": c[0], "open": c[1], "high": c[2], "low": c[3],
                "close": c[4], "volume": c[5], "oi": c[6],
            } for c in candles)
            time.sleep(0.25)
            day += timedelta(days=1)
        if not rows:
            continue

        incoming = pd.DataFrame(rows, columns=CONTEXT_COLUMNS[:-1])
        incoming["timestamp"] = pd.to_datetime(incoming["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
        incoming["date"] = incoming.timestamp.dt.date.astype(str)
        output = OUTPUT_DIR / f"{name.lower()}_5min.csv"
        sources = [("new Upstox download", incoming)]
        if output.exists():
            old = pd.read_csv(output)
            sources.insert(0, (str(output), old))
        merged, audit = merge_candle_sources(sources, key_columns=[])
        if audit["duplicate_coverage"]["count"]:
            print(f"{name}: identical overlaps retained once ({audit['duplicate_coverage']['count']})")
        merged.to_csv(output, index=False)
        print(f"{name}: merged {len(merged)} candles")


if __name__ == "__main__":
    main()
