"""
crypto_hourly_update.py

Posts rich hourly status updates to #paper-trading and instant trade
alerts to #trade-alerts whenever Freqtrade opens or closes a position.

Usage:
    python execution/crypto_hourly_update.py           # single update
    python execution/crypto_hourly_update.py --loop    # continuous (hourly update + 5min trade poll)
"""

import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CRYPTO_ROOT  = Path(__file__).parent.parent
PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(CRYPTO_ROOT.parent)))
env_file = PROJECT_ROOT / ".env"
if not env_file.exists():
    env_file = Path("/freqtrade/.env")
for line in env_file.read_text().splitlines() if env_file.exists() else []:
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

API_BASE             = "http://127.0.0.1:8080/api/v1"
API_USER             = os.getenv("FREQTRADE_API_USER", "admin")
API_PASS             = os.getenv("FREQTRADE_API_PASSWORD", "")
WEBHOOK_TRADING      = os.getenv("DISCORD_WEBHOOK_TRADING", "")
WEBHOOK_TRADE_ALERTS = os.getenv("DISCORD_WEBHOOK_TRADE_ALERTS", "")

SESSION_START = datetime.now(timezone.utc)
PAIRS = ["BTC/USD", "ETH/USD", "XRP/USD"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _api(path: str):
    url = f"{API_BASE}{path}"
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, API_BASE, API_USER, API_PASS)
    opener = urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr))
    try:
        with opener.open(url, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  API error {path}: {e}")
        return None


def _post(webhook: str, payload: dict):
    if not webhook:
        return
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        webhook, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"  Discord error: {e}")


# ---------------------------------------------------------------------------
# Hourly status update
# ---------------------------------------------------------------------------

def post_hourly_update():
    profit  = _api("/profit")
    balance = _api("/balance")
    status  = _api("/status")
    trades  = _api("/trades?limit=50")

    if not profit or not balance:
        print("  Freqtrade API unavailable - skipping update")
        return

    now      = datetime.now(timezone.utc)
    day_num  = (now - SESSION_START).days + 1
    uptime   = now - SESSION_START
    hours    = int(uptime.total_seconds() // 3600)
    minutes  = int((uptime.total_seconds() % 3600) // 60)

    # Balance
    total_bal  = balance.get("total", 0)
    start_bal  = 1000.0
    bal_change = total_bal - start_bal
    bal_pct    = bal_change / start_bal * 100

    # P&L stats
    profit_usd    = profit.get("profit_closed_coin", 0)
    profit_pct    = profit.get("profit_closed_percent_mean", 0) * 100
    total_trades  = profit.get("trade_count", 0)
    win_rate      = profit.get("winrate", 0) * 100
    profit_factor = profit.get("profit_factor", 0)

    color = 0x00cc44 if profit_usd >= 0 else 0xff4444

    # Per-pair breakdown from closed trades
    pair_stats = {}
    if trades and "trades" in trades:
        for t in trades["trades"]:
            if not t.get("is_open", False):
                p = t.get("pair", "?")
                pair_stats.setdefault(p, {"profit": 0, "count": 0})
                pair_stats[p]["profit"] += t.get("profit_abs", 0)
                pair_stats[p]["count"]  += 1

    pair_lines = []
    for pair in PAIRS:
        s = pair_stats.get(pair)
        if s:
            sign = "+" if s["profit"] >= 0 else ""
            pair_lines.append(f"`{pair}` {sign}${s['profit']:.2f} over {s['count']} trades")
        else:
            pair_lines.append(f"`{pair}` No closed trades yet")

    # Open positions
    open_fields = []
    if status and isinstance(status, list):
        for pos in status[:3]:
            pair    = pos.get("pair", "?")
            pl_pct  = pos.get("profit_ratio", 0) * 100
            pl_abs  = pos.get("profit_abs", 0)
            entry   = pos.get("open_rate", 0)
            current = pos.get("current_rate", 0)
            dur_min = pos.get("open_trade_duration_min", 0)
            arrow   = "UP" if pl_pct >= 0 else "DN"
            h, m    = int(dur_min // 60), int(dur_min % 60)
            open_fields.append({
                "name": f"{arrow}  {pair}",
                "value": f"Entry: ${entry:.4f}  Now: ${current:.4f}\nP&L: {pl_pct:+.2f}% (${pl_abs:+.2f})  Open: {h}h {m}m",
                "inline": True,
            })

    fields = [
        {
            "name": "Balance",
            "value": f"**${total_bal:.2f}**\nStart: ${start_bal:.2f}  Change: {bal_pct:+.2f}% (${bal_change:+.2f})",
            "inline": True,
        },
        {
            "name": "All-time P&L",
            "value": f"**${profit_usd:+.2f}**\nAvg/trade: {profit_pct:+.2f}%  Factor: {profit_factor:.2f}",
            "inline": True,
        },
        {
            "name": "Stats",
            "value": f"Trades: {total_trades}  Win: {win_rate:.1f}%\nUptime: {hours}h {minutes}m",
            "inline": True,
        },
        {
            "name": "Pair Breakdown",
            "value": "\n".join(pair_lines) or "No closed trades yet",
            "inline": False,
        },
    ]

    if open_fields:
        fields.append({"name": "Open Positions", "value": "\u200b", "inline": False})
        fields.extend(open_fields)
    else:
        fields.append({"name": "Open Positions", "value": "None currently open", "inline": False})

    embed = {
        "title": f"Paper Trading Update - Day {day_num} of 14",
        "description": "BTC/ETH/XRP  |  EMA20/50 + SMA200 + ADX + RSI Strategy",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Next update in ~1 hour  |  {now.strftime('%Y-%m-%d %H:%M UTC')}"},
        "timestamp": now.isoformat(),
    }

    _post(WEBHOOK_TRADING, {"embeds": [embed]})
    print(f"  Hourly update posted | P&L: ${profit_usd:+.2f} | Trades: {total_trades} | Win: {win_rate:.1f}%")


# ---------------------------------------------------------------------------
# Trade alert poller (every 5 min)
# ---------------------------------------------------------------------------

_seen_open_ids:   set = set()
_seen_closed_ids: set = set()


def _init_seen():
    """Snapshot existing trades on startup so we don't re-alert them."""
    status = _api("/status")
    trades = _api("/trades?limit=50")
    if status and isinstance(status, list):
        for t in status:
            _seen_open_ids.add(t.get("trade_id"))
    if trades and "trades" in trades:
        for t in trades["trades"]:
            _seen_closed_ids.add(t.get("trade_id"))
    print(f"  Watching {len(_seen_open_ids)} open + {len(_seen_closed_ids)} closed trades")


def check_trade_alerts():
    status = _api("/status")
    trades = _api("/trades?limit=20")

    # New open positions
    if status and isinstance(status, list):
        for pos in status:
            tid = pos.get("trade_id")
            if tid and tid not in _seen_open_ids:
                _seen_open_ids.add(tid)
                pair   = pos.get("pair", "?")
                rate   = pos.get("open_rate", 0)
                stake  = pos.get("stake_amount", 0)
                reason = pos.get("open_reason", "signal")
                embed  = {
                    "title": f"BUY  {pair}",
                    "description": f"New position opened via {reason}",
                    "color": 0x00cc44,
                    "fields": [
                        {"name": "Entry",  "value": f"${rate:.4f}",  "inline": True},
                        {"name": "Stake",  "value": f"${stake:.2f}", "inline": True},
                        {"name": "Pair",   "value": pair,            "inline": True},
                    ],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                _post(WEBHOOK_TRADE_ALERTS, {"embeds": [embed]})
                print(f"  ALERT: BUY {pair} @ ${rate:.4f}")

    # Newly closed trades
    if trades and "trades" in trades:
        for t in trades["trades"]:
            tid = t.get("trade_id")
            if tid and not t.get("is_open") and tid not in _seen_closed_ids:
                _seen_closed_ids.add(tid)
                pair       = t.get("pair", "?")
                profit_pct = t.get("profit_ratio", 0) * 100
                profit_abs = t.get("profit_abs", 0)
                entry      = t.get("open_rate", 0)
                exit_rate  = t.get("close_rate", 0)
                reason     = t.get("exit_reason", t.get("sell_reason", "signal"))
                color      = 0x00cc44 if profit_abs >= 0 else 0xff4444
                result     = "WIN" if profit_abs >= 0 else "LOSS"
                embed = {
                    "title": f"SELL  {pair}  -  {result}",
                    "description": f"Exit reason: {reason}",
                    "color": color,
                    "fields": [
                        {"name": "Entry",  "value": f"${entry:.4f}",     "inline": True},
                        {"name": "Exit",   "value": f"${exit_rate:.4f}", "inline": True},
                        {"name": "P&L",    "value": f"{profit_pct:+.2f}% (${profit_abs:+.2f})", "inline": True},
                    ],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                _post(WEBHOOK_TRADE_ALERTS, {"embeds": [embed]})
                print(f"  ALERT: SELL {pair} {profit_pct:+.2f}% (${profit_abs:+.2f})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    args = parser.parse_args()

    if not args.loop:
        print("Posting single update...")
        post_hourly_update()
        return

    print("Starting loop: hourly updates + 5min trade alerts...")
    _init_seen()

    last_hourly = 0

    while True:
        now = time.time()

        print(f"[{datetime.now().strftime('%H:%M')}] Checking trades...")
        check_trade_alerts()

        if now - last_hourly >= 3600:
            print(f"[{datetime.now().strftime('%H:%M')}] Posting hourly update...")
            post_hourly_update()
            last_hourly = now

        time.sleep(300)  # poll every 5 minutes


if __name__ == "__main__":
    main()
