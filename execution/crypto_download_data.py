"""
crypto_download_data.py - Download historical OHLCV data via Coinbase Exchange public REST API

Uses the Coinbase Exchange (formerly Coinbase Pro) public candles endpoint.
No authentication required for historical data.
Returns up to 300 candles per request (300h = 12.5 days at 1h timeframe).

Freqtrade data format: list of [timestamp_ms, open, high, low, close, volume]
File location: freqtrade-config/data/coinbase/<PAIR>-<TIMEFRAME>.json

Usage:
    python execution/crypto_download_data.py
    python execution/crypto_download_data.py --days 365
"""

import sys
import json
import time
import argparse
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone

CRYPTO_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = CRYPTO_ROOT.parent
CONFIG_DIR = CRYPTO_ROOT / "freqtrade-config"
DATA_DIR = CONFIG_DIR / "data" / "coinbaseadvanced"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Freqtrade pair name -> Coinbase product ID
PAIRS = {
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD",
    "XRP/USD": "XRP-USD",
}
TIMEFRAME = "1h"
GRANULARITY = 3600       # seconds (1h)
LIMIT = 300              # max candles per Coinbase request
BASE_URL = "https://api.exchange.coinbase.com"


def pair_to_filename(pair: str, timeframe: str) -> str:
    return pair.replace("/", "_") + f"-{timeframe}.json"


def fetch_candles(product_id: str, start: datetime, end: datetime) -> list:
    """
    Fetch up to 300 candles from Coinbase Exchange public API.
    Coinbase returns: [[time_unix, low, high, open, close, volume], ...] in descending order.
    Converts to Freqtrade format: [timestamp_ms, open, high, low, close, volume].
    """
    params = urllib.parse.urlencode({
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "granularity": GRANULARITY,
    })
    url = f"{BASE_URL}/products/{product_id}/candles?{params}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    if isinstance(data, dict) and "message" in data:
        raise ValueError(f"Coinbase API error: {data['message']}")

    # Coinbase returns descending order, reverse to ascending
    # Format: [time, low, high, open, close, volume]
    # Freqtrade: [timestamp_ms, open, high, low, close, volume]
    candles = [
        [int(row[0]) * 1000, float(row[3]), float(row[2]), float(row[1]), float(row[4]), float(row[5])]
        for row in reversed(data)
    ]
    return candles


def download_pair(pair: str, product_id: str, days: int) -> list:
    """Download all candles for a pair over the specified number of days."""
    print(f"\n  {pair} ({product_id}):")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now - timedelta(days=days)

    # 300 candles at 1h = 12.5 days per batch
    batch_hours = LIMIT
    all_candles = []
    batch_start = start

    while batch_start < now:
        batch_end = min(batch_start + timedelta(hours=batch_hours), now)
        try:
            batch = fetch_candles(product_id, batch_start, batch_end)
        except Exception as e:
            print(f"    Error fetching batch: {e}. Retrying in 5s...")
            time.sleep(5)
            continue

        if batch:
            all_candles.extend(batch)
            last_dt = datetime.fromtimestamp(batch[-1][0] / 1000).strftime("%Y-%m-%d")
            print(f"    {len(all_candles)} candles fetched... (through {last_dt})", flush=True)

        batch_start = batch_end
        time.sleep(0.4)  # Coinbase public rate limit: ~3 req/sec

    # Deduplicate and sort
    seen = set()
    result = []
    for c in all_candles:
        if c[0] not in seen:
            seen.add(c[0])
            result.append(c)
    result.sort(key=lambda x: x[0])
    return result


def merge_with_existing(data_path: Path, new_candles: list) -> list:
    existing = []
    if data_path.exists():
        try:
            existing = json.loads(data_path.read_text())
            print(f"    Merging with {len(existing)} existing candles")
        except Exception:
            pass
    all_candles = existing + new_candles
    seen = set()
    merged = []
    for c in all_candles:
        if c[0] not in seen:
            seen.add(c[0])
            merged.append(c)
    merged.sort(key=lambda x: x[0])
    return merged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    print(f"\nDownloading {args.days} days of {TIMEFRAME} OHLCV data from Coinbase")
    print(f"  Pairs: {', '.join(PAIRS.keys())}")
    print(f"  Saving to: {DATA_DIR}")

    # Connectivity check
    try:
        with urllib.request.urlopen(
            urllib.request.Request(f"{BASE_URL}/time", headers={"User-Agent": "Mozilla/5.0"}),
            timeout=10
        ) as r:
            ts = json.loads(r.read())["epoch"]
            print(f"  Coinbase reachable. Server time: {datetime.fromtimestamp(float(ts)).strftime('%Y-%m-%d %H:%M:%S')}")
    except Exception as e:
        print(f"  ERROR: Cannot reach Coinbase API: {e}")
        sys.exit(1)

    total = 0
    for pair, product_id in PAIRS.items():
        candles = download_pair(pair, product_id, args.days)
        data_path = DATA_DIR / pair_to_filename(pair, TIMEFRAME)
        merged = merge_with_existing(data_path, candles)
        data_path.write_text(json.dumps(merged))
        total += len(merged)
        first = datetime.fromtimestamp(merged[0][0] / 1000).strftime("%Y-%m-%d")
        last = datetime.fromtimestamp(merged[-1][0] / 1000).strftime("%Y-%m-%d")
        print(f"  Saved {len(merged)} candles ({first} to {last}) -> {data_path.name}")

    print(f"\nDone. {total} total candles saved.")


if __name__ == "__main__":
    main()
