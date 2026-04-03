"""
crypto_fetch_signals.py - Download Fear & Greed and Funding Rate history

Fetches two external signals used as entry filters in RSI_MA_Strategy:

  Fear & Greed Index (alternative.me - free, no API key)
    - Daily sentiment score 0-100. 0 = extreme fear, 100 = extreme greed
    - Source: https://api.alternative.me/fng/
    - Saves to: .tmp/fear_greed_history.json

  BTC Funding Rates (OKX public API - free, no API key, works in US)
    - Perpetual futures funding rate, published every 8 hours
    - Positive = longs paying shorts (market overleveraged long = risky)
    - Negative = shorts paying longs (market overleveraged short = potential bounce)
    - Source: https://www.okx.com/api/v5/public/funding-rate-history
    - Saves to: .tmp/funding_rates_btc.json

Usage:
    python execution/crypto_fetch_signals.py
    python execution/crypto_fetch_signals.py --days 730
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
TMP_DIR = PROJECT_ROOT / ".tmp"
TMP_DIR.mkdir(exist_ok=True)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def fetch_url(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def fetch_fear_greed(days: int) -> list:
    """Download Fear & Greed history from alternative.me. Returns list of {timestamp, value}."""
    print(f"\n  Fear & Greed Index ({days} days)...")
    url = f"https://api.alternative.me/fng/?limit={days}&format=json"
    data = fetch_url(url)
    records = data.get("data", [])
    # Normalize: [{"timestamp": unix_sec, "value": int}]
    result = [{"timestamp": int(r["timestamp"]), "value": int(r["value"])} for r in records]
    result.sort(key=lambda x: x["timestamp"])
    print(f"  Got {len(result)} daily records ({datetime.fromtimestamp(result[0]['timestamp'], tz=timezone.utc).date()} to {datetime.fromtimestamp(result[-1]['timestamp'], tz=timezone.utc).date()})")
    return result


def fetch_funding_rates(days: int) -> list:
    """Download BTC funding rate history from OKX (8h intervals). Returns list of {timestamp_ms, rate}."""
    print(f"\n  BTC Funding Rates ({days} days, OKX)...")
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    all_records = []
    # OKX paginates via 'after' param (returns records older than this timestamp)
    # Start from now and page backwards
    after_ms = None

    while True:
        url = "https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=100"
        if after_ms:
            url += f"&after={after_ms}"

        try:
            data = fetch_url(url)
        except Exception as e:
            print(f"    Error fetching page: {e}. Retrying in 2s...")
            time.sleep(2)
            continue

        records = data.get("data", [])
        if not records:
            break

        for r in records:
            ts_ms = int(r["fundingTime"])
            if ts_ms < cutoff_ms:
                break
            all_records.append({"timestamp_ms": ts_ms, "rate": float(r["fundingRate"])})

        # Check if oldest record in this page is before cutoff
        oldest_ts = min(int(r["fundingTime"]) for r in records)
        if oldest_ts < cutoff_ms:
            break

        # Page backwards: set after to oldest timestamp in this batch
        after_ms = oldest_ts
        time.sleep(0.3)  # rate limit

    all_records.sort(key=lambda x: x["timestamp_ms"])
    # Deduplicate
    seen = set()
    result = []
    for r in all_records:
        if r["timestamp_ms"] not in seen:
            seen.add(r["timestamp_ms"])
            result.append(r)

    if result:
        first = datetime.fromtimestamp(result[0]["timestamp_ms"] / 1000, tz=timezone.utc).date()
        last = datetime.fromtimestamp(result[-1]["timestamp_ms"] / 1000, tz=timezone.utc).date()
        print(f"  Got {len(result)} 8h records ({first} to {last})")
        avg = sum(r["rate"] for r in result) / len(result)
        print(f"  Avg funding rate: {avg*100:.4f}% per 8h")
    else:
        print("  No records returned")

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=730)
    args = parser.parse_args()

    print(f"Fetching external signals ({args.days} days)...")

    # Fear & Greed
    try:
        fg = fetch_fear_greed(args.days)
        out = TMP_DIR / "fear_greed_history.json"
        out.write_text(json.dumps(fg))
        print(f"  Saved -> {out.name}")
    except Exception as e:
        print(f"  ERROR fetching Fear & Greed: {e}")
        sys.exit(1)

    # Funding Rates
    try:
        fr = fetch_funding_rates(args.days)
        out = TMP_DIR / "funding_rates_btc.json"
        out.write_text(json.dumps(fr))
        print(f"  Saved -> {out.name}")
    except Exception as e:
        print(f"  ERROR fetching Funding Rates: {e}")
        sys.exit(1)

    print(f"\nDone. Signal files in {TMP_DIR}")


if __name__ == "__main__":
    main()
