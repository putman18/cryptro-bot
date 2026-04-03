"""
crypto_paper_trade.py - Phase 3: Paper Trading

Starts and stops Freqtrade in dry-run mode (fake money, live market data).
Monitors daily health and posts summaries to Discord.

Usage:
    python execution/crypto_paper_trade.py --start
    python execution/crypto_paper_trade.py --stop
    python execution/crypto_paper_trade.py --status
    python execution/crypto_paper_trade.py --daily-summary
"""

import os
import sys
import json
import time
import argparse
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime

# Load .env
CRYPTO_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = CRYPTO_ROOT.parent
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

VENV_PATH = Path.home() / "freqtrade-env"
FREQTRADE_BIN = VENV_PATH / "Scripts" / "freqtrade.exe"
CONFIG_DIR = CRYPTO_ROOT / "freqtrade-config"
TMP_DIR = PROJECT_ROOT / ".tmp"
TMP_DIR.mkdir(exist_ok=True)

PID_FILE = TMP_DIR / "freqtrade_paper.pid"
LOG_FILE = TMP_DIR / "freqtrade_paper.log"

# Freqtrade REST API (runs on localhost while bot is active)
API_BASE = "http://127.0.0.1:8080/api/v1"
API_USER = os.getenv("FREQTRADE_API_USER", "admin")
API_PASSWORD = os.getenv("FREQTRADE_API_PASSWORD", "changeme")

# Kill conditions
DAILY_DRAWDOWN_LIMIT_PCT = 10.0   # Stop if single-day drawdown > 10% of paper wallet
STUCK_TRADE_HOURS = 24            # Alert if trade open > 24h with no signal


def post_discord(webhook_url: str, content: str = None, embeds: list = None):
    """Post a message to Discord via webhook."""
    if not webhook_url:
        return
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"  [Discord] Failed: {e}")


def api_request(path: str):
    """Make a request to the Freqtrade REST API."""
    import base64
    credentials = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Basic {credentials}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError:
        return None


def is_running() -> bool:
    """Check if the Freqtrade paper trading process is running."""
    if not PID_FILE.exists():
        return False
    pid = int(PID_FILE.read_text().strip())
    # Check if process is alive
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
        capture_output=True, text=True
    )
    return str(pid) in result.stdout


def start_bot():
    """Start Freqtrade in dry-run (paper trading) mode."""
    if is_running():
        print("Paper trading bot is already running.")
        print(f"  PID: {PID_FILE.read_text().strip()}")
        print(f"  Web UI: http://localhost:8080")
        return

    if not FREQTRADE_BIN.exists():
        print(f"ERROR: Freqtrade not found at {FREQTRADE_BIN}")
        print("Run python execution/crypto_setup.py first")
        sys.exit(1)

    config_path = CONFIG_DIR / "config_paper.json"
    if not config_path.exists():
        print(f"ERROR: config_paper.json not found at {config_path}")
        print("Run python execution/crypto_setup.py first")
        sys.exit(1)

    print("Starting paper trading bot...")
    print(f"  Config: {config_path}")
    print(f"  Log: {LOG_FILE}")
    print(f"  Web UI: http://localhost:8080")

    log_file = open(LOG_FILE, "w")
    proc = subprocess.Popen(
        [
            str(FREQTRADE_BIN),
            "trade",
            "--userdir", str(CONFIG_DIR),
            "--config", str(config_path),
            "--strategy", "RSI_MA_Strategy",
        ],
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP  # Windows: detach from console
    )

    PID_FILE.write_text(str(proc.pid))

    # Brief wait to check it started without immediate crash
    time.sleep(3)
    if proc.poll() is not None:
        print(f"ERROR: Bot crashed on startup. Check log: {LOG_FILE}")
        print(LOG_FILE.read_text()[-2000:])
        sys.exit(1)

    print(f"  Bot started (PID: {proc.pid})")
    print(f"  Monitor at: http://localhost:8080")

    # Log start time
    start_log = TMP_DIR / "paper_trade_log.json"
    log_data = []
    if start_log.exists():
        try:
            log_data = json.loads(start_log.read_text())
        except Exception:
            pass
    log_data.append({"event": "start", "timestamp": datetime.now().isoformat(), "pid": proc.pid})
    start_log.write_text(json.dumps(log_data, indent=2))

    # Alert Discord
    webhook = os.getenv("DISCORD_WEBHOOK_TRADING", "")
    post_discord(webhook, embeds=[{
        "title": "Paper Trading Started",
        "description": "Dry-run bot is live. Executing fake trades on real market data.",
        "color": 0x00aaff,
        "fields": [
            {"name": "Starting Balance", "value": "$1,000 USDT (virtual)", "inline": True},
            {"name": "Strategy", "value": "RSI_MA_Strategy", "inline": True},
            {"name": "Pairs", "value": "BTC/USDT, ETH/USDT, XRP/USDT", "inline": True},
            {"name": "Web UI", "value": "http://localhost:8080", "inline": False},
        ],
        "footer": {"text": "Phase 3: Paper Trading"},
        "timestamp": datetime.utcnow().isoformat()
    }])
    if webhook:
        print("  Discord alert sent to #paper-trading")


def stop_bot():
    """Stop the running Freqtrade paper trading process."""
    if not is_running():
        print("Paper trading bot is not running.")
        return

    pid = int(PID_FILE.read_text().strip())
    print(f"Stopping paper trading bot (PID: {pid})...")

    # First try graceful stop via API
    api_request("/forcesell/all")
    time.sleep(2)

    # Then kill the process
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    PID_FILE.unlink(missing_ok=True)
    print("  Bot stopped.")

    # Log stop
    start_log = TMP_DIR / "paper_trade_log.json"
    log_data = []
    if start_log.exists():
        try:
            log_data = json.loads(start_log.read_text())
        except Exception:
            pass
    log_data.append({"event": "stop", "timestamp": datetime.now().isoformat(), "pid": pid})
    start_log.write_text(json.dumps(log_data, indent=2))

    webhook = os.getenv("DISCORD_WEBHOOK_TRADING", "")
    post_discord(webhook, content="Paper trading bot stopped.")
    if webhook:
        print("  Discord alert sent")


def show_status():
    """Show current status of the paper trading bot."""
    if not is_running():
        print("Status: NOT RUNNING")
        return

    pid = PID_FILE.read_text().strip()
    print(f"Status: RUNNING (PID: {pid})")
    print(f"Web UI: http://localhost:8080")

    # Try to get status from API
    status = api_request("/status")
    balance = api_request("/balance")
    profit = api_request("/profit")

    if balance:
        print(f"\nBalance:")
        for currency in balance.get("currencies", []):
            if currency.get("balance", 0) > 0:
                print(f"  {currency['currency']}: {currency['balance']:.4f}")

    if profit:
        print(f"\nProfit Summary:")
        print(f"  Total profit:  {profit.get('profit_all_percent', 0):.2f}%")
        print(f"  Closed trades: {profit.get('trade_count', 0)}")
        print(f"  Win rate:      {profit.get('winning_trades', 0)}/{profit.get('trade_count', 1)}")

    if status:
        open_trades = status if isinstance(status, list) else []
        print(f"\nOpen Trades: {len(open_trades)}")
        for trade in open_trades:
            profit_pct = trade.get("profit_pct", 0)
            icon = "+" if profit_pct >= 0 else "-"
            print(f"  {trade.get('pair', '?')}: {icon}{abs(profit_pct):.2f}%  (opened: {trade.get('open_date', '?')})")


def daily_summary():
    """Post a daily summary to Discord."""
    print("Generating daily summary...")

    if not is_running():
        webhook = os.getenv("DISCORD_WEBHOOK_ALERTS", "")
        post_discord(webhook, content="WARNING: Paper trading bot is not running. Check the system.")
        print("  Bot not running - alert sent")
        return

    profit = api_request("/profit")
    status = api_request("/status")
    balance = api_request("/balance")

    if not profit:
        print("  Could not reach bot API. Check http://localhost:8080")
        return

    open_trades = status if isinstance(status, list) else []
    total_profit_pct = profit.get("profit_all_percent", 0)
    closed_trades = profit.get("trade_count", 0)
    wins = profit.get("winning_trades", 0)
    win_rate = (wins / closed_trades * 100) if closed_trades > 0 else 0

    color = 0x00ff00 if total_profit_pct >= 0 else 0xff0000

    embed = {
        "title": "Daily Paper Trading Summary",
        "color": color,
        "fields": [
            {"name": "Total P&L", "value": f"{'+' if total_profit_pct >= 0 else ''}{total_profit_pct:.2f}%", "inline": True},
            {"name": "Closed Trades", "value": str(closed_trades), "inline": True},
            {"name": "Win Rate", "value": f"{win_rate:.1f}%", "inline": True},
            {"name": "Open Trades", "value": str(len(open_trades)), "inline": True},
        ],
        "footer": {"text": "Phase 3: Paper Trading"},
        "timestamp": datetime.utcnow().isoformat()
    }

    if open_trades:
        trade_lines = []
        for t in open_trades[:5]:  # max 5
            pct = t.get("profit_pct", 0)
            trade_lines.append(f"{t.get('pair')}: {'+' if pct >= 0 else ''}{pct:.2f}%")
        embed["fields"].append({
            "name": "Open Positions",
            "value": "\n".join(trade_lines),
            "inline": False
        })

    webhook = os.getenv("DISCORD_WEBHOOK_TRADING", "")
    post_discord(webhook, embeds=[embed])
    print("  Daily summary posted to Discord")

    # Check kill conditions
    _check_kill_conditions(total_profit_pct, open_trades)


def _check_kill_conditions(daily_pct: float, open_trades: list):
    """Check if any kill conditions are met and stop bot if so."""
    triggered = []

    # Kill condition 1: daily drawdown > 10%
    if daily_pct < -DAILY_DRAWDOWN_LIMIT_PCT:
        triggered.append(f"Daily drawdown {daily_pct:.1f}% exceeds -{DAILY_DRAWDOWN_LIMIT_PCT}% limit")

    # Kill condition 2: stuck trades > 24h
    now = datetime.now()
    for trade in open_trades:
        try:
            open_dt = datetime.fromisoformat(trade.get("open_date", "").replace("Z", ""))
            hours_open = (now - open_dt).total_seconds() / 3600
            if hours_open > STUCK_TRADE_HOURS:
                triggered.append(f"{trade.get('pair')} stuck for {hours_open:.0f}h")
        except Exception:
            pass

    if triggered:
        print(f"\nKILL CONDITIONS TRIGGERED:")
        for reason in triggered:
            print(f"  - {reason}")

        webhook = os.getenv("DISCORD_WEBHOOK_ALERTS", "")
        post_discord(webhook, embeds=[{
            "title": "KILL CONDITION TRIGGERED - Bot Stopping",
            "description": "\n".join(f"- {r}" for r in triggered),
            "color": 0xff0000,
            "footer": {"text": "Manual restart required after investigation"},
            "timestamp": datetime.utcnow().isoformat()
        }])

        stop_bot()


def main():
    parser = argparse.ArgumentParser(description="Manage paper trading bot")
    parser.add_argument("--start", action="store_true", help="Start the paper trading bot")
    parser.add_argument("--stop", action="store_true", help="Stop the paper trading bot")
    parser.add_argument("--status", action="store_true", help="Show bot status")
    parser.add_argument("--daily-summary", action="store_true", help="Post daily summary to Discord")
    args = parser.parse_args()

    if args.start:
        start_bot()
    elif args.stop:
        stop_bot()
    elif args.status:
        show_status()
    elif args.daily_summary:
        daily_summary()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
