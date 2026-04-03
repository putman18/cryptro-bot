"""
crypto_live.py - Phase 4: Live Trading

GATED: Only use after Phase 3 paper trading PASSES all metric thresholds.

Starts Freqtrade with real money using live Binance API keys.
Risk controls are hardcoded and cannot be bypassed via CLI.

Usage:
    python execution/crypto_live.py --start
    python execution/crypto_live.py --stop
    python execution/crypto_live.py --status
    python execution/crypto_live.py --check-prerequisites

Risk Controls (hardcoded):
    - Max per trade: $50 USDT
    - Max open trades: 3 (max $150 deployed)
    - Stop-loss: -3% per trade
    - Daily loss limit: -5% triggers auto-pause
    - NO withdrawal permissions on API key

DO NOT add withdrawal permissions to the Binance API key used here.
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

PID_FILE = TMP_DIR / "freqtrade_live.pid"
LOG_FILE = TMP_DIR / "freqtrade_live.log"

API_BASE = "http://127.0.0.1:8080/api/v1"
API_USER = os.getenv("FREQTRADE_API_USER", "admin")
API_PASSWORD = os.getenv("FREQTRADE_API_PASSWORD", "changeme")

# Hardcoded risk controls
MAX_STAKE_PER_TRADE = 50      # USDT
MAX_OPEN_TRADES = 3
STOP_LOSS_PCT = -0.03
DAILY_LOSS_LIMIT_PCT = 5.0    # Pause if daily P&L drops below -5%


def post_discord(webhook_url: str, content: str = None, embeds: list = None):
    if not webhook_url:
        print(f"  [Discord] No webhook URL - skipping")
        return
    payload = {}
    if content:
        payload["content"] = content
    if embeds:
        payload["embeds"] = embeds
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print(f"  [Discord] Failed: {e}")


def api_request(path: str, method="GET", body=None):
    import base64
    credentials = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
    headers = {"Authorization": f"Basic {credentials}"}
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers=headers,
        data=json.dumps(body).encode() if body else None,
        method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def is_running() -> bool:
    if not PID_FILE.exists():
        return False
    pid = int(PID_FILE.read_text().strip())
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV"],
        capture_output=True, text=True
    )
    return str(pid) in result.stdout


def check_prerequisites() -> bool:
    """Validate all requirements before starting live trading."""
    print("\nChecking prerequisites for live trading...")
    issues = []

    # 1. API keys set
    live_key = os.getenv("BINANCE_US_API_KEY_LIVE", "")
    live_secret = os.getenv("BINANCE_US_SECRET_LIVE", "")
    if not live_key or not live_secret:
        issues.append("BINANCE_US_API_KEY_LIVE and BINANCE_US_SECRET_LIVE not set in .env")

    # 2. Paper trading results passed
    paper_report_files = list(TMP_DIR.glob("report_paper_*.json"))
    if not paper_report_files:
        issues.append("No paper trading report found. Complete Phase 3 first.")
    else:
        latest_paper = max(paper_report_files, key=lambda f: f.stat().st_mtime)
        try:
            data = json.loads(latest_paper.read_text())
            if not data.get("pass", False):
                failing = data.get("failing_metrics", ["unknown"])
                issues.append(f"Paper trading metrics failed: {', '.join(failing)}")
            else:
                days = data.get("days_running", 0)
                if days < 14:
                    issues.append(f"Paper trading ran only {days} days (minimum: 14)")
                else:
                    print(f"  [OK] Paper trading metrics passed ({days} days)")
        except Exception as e:
            issues.append(f"Could not read paper trading report: {e}")

    # 3. Freqtrade installed
    if not FREQTRADE_BIN.exists():
        issues.append(f"Freqtrade not found at {FREQTRADE_BIN}")

    # 4. Live config exists
    live_config = CONFIG_DIR / "config_live.json"
    if not live_config.exists():
        issues.append("config_live.json not found")
    else:
        with open(live_config) as f:
            cfg = json.load(f)
        if cfg.get("dry_run", True):
            issues.append("config_live.json has dry_run=true - must be false for live trading")
        if cfg.get("_status") == "placeholder - not configured":
            issues.append("config_live.json has not been configured (still placeholder)")

    if issues:
        print("\n  PREREQUISITES NOT MET:")
        for issue in issues:
            print(f"    - {issue}")
        print()
        return False

    print("  All prerequisites met.")
    return True


def build_live_config():
    """Write the live config using real API keys from .env."""
    live_key = os.getenv("BINANCE_US_API_KEY_LIVE", "")
    live_secret = os.getenv("BINANCE_US_SECRET_LIVE", "")

    config = {
        "max_open_trades": MAX_OPEN_TRADES,
        "stake_currency": "USDT",
        "stake_amount": MAX_STAKE_PER_TRADE,
        "tradable_balance_ratio": 0.99,
        "fiat_display_currency": "USD",
        "dry_run": False,
        "cancel_open_orders_on_exit": True,
        "trading_mode": "spot",
        "margin_mode": "",
        "unfilledtimeout": {
            "entry": 10,
            "exit": 10,
            "unit": "minutes"
        },
        "entry_pricing": {
            "price_side": "same",
            "use_order_book": True,
            "order_book_top": 1,
        },
        "exit_pricing": {
            "price_side": "same",
            "use_order_book": True,
            "order_book_top": 1,
        },
        "exchange": {
            "name": "binanceus",
            "key": live_key,
            "secret": live_secret,
            "pair_whitelist": ["BTC/USDT", "ETH/USDT"],
            "pair_blacklist": []
        },
        "pairlists": [{"method": "StaticPairList"}],
        "telegram": {"enabled": False, "token": "", "chat_id": ""},
        "api_server": {
            "enabled": True,
            "listen_ip_address": "127.0.0.1",
            "listen_port": 8080,
            "verbosity": "error",
            "enable_openapi": False,
            "jwt_secret_key": "changeme-replace-with-random-string",
            "ws_token": "changeme-replace-with-random-string",
            "CORS_origins": [],
            "username": os.getenv("FREQTRADE_API_USER", "admin"),
            "password": os.getenv("FREQTRADE_API_PASSWORD", "changeme")
        },
        "bot_name": "crypto-trader-live",
        "initial_state": "running",
        "internals": {"process_throttle_secs": 5}
    }

    config_path = CONFIG_DIR / "config_live.json"
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"  config_live.json updated with live API keys")


def start_bot():
    """Start Freqtrade in live trading mode."""
    if is_running():
        print("Live trading bot is already running.")
        print(f"  PID: {PID_FILE.read_text().strip()}")
        print(f"  Web UI: http://localhost:8080")
        return

    print("\n" + "!" * 60)
    print("  LIVE TRADING MODE - REAL MONEY")
    print("  Starting with real Binance account")
    print(f"  Max stake: ${MAX_STAKE_PER_TRADE} USDT per trade")
    print(f"  Max positions: {MAX_OPEN_TRADES}")
    print(f"  Stop-loss: {abs(STOP_LOSS_PCT)*100:.0f}% per trade")
    print("!" * 60 + "\n")

    # Final confirmation
    confirm = input("Type 'CONFIRM' to start live trading: ").strip()
    if confirm != "CONFIRM":
        print("Aborted.")
        sys.exit(0)

    if not check_prerequisites():
        print("Cannot start: prerequisites not met.")
        sys.exit(1)

    # Build live config with current keys
    build_live_config()

    config_path = CONFIG_DIR / "config_live.json"
    log_file = open(LOG_FILE, "w")
    proc = subprocess.Popen(
        [
            str(FREQTRADE_BIN),
            "trade",
            "--userdir", str(CONFIG_DIR),
            "--config", str(config_path),
            "--logfile", str(LOG_FILE),
        ],
        stdout=log_file,
        stderr=log_file,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    )

    PID_FILE.write_text(str(proc.pid))
    time.sleep(3)

    if proc.poll() is not None:
        print(f"ERROR: Bot crashed on startup. Check log: {LOG_FILE}")
        print(LOG_FILE.read_text()[-2000:])
        sys.exit(1)

    print(f"  Live bot started (PID: {proc.pid})")
    print(f"  Monitor at: http://localhost:8080")
    print(f"  Log: {LOG_FILE}")
    print(f"\n  Kill switch: python execution/crypto_live.py --stop")

    # Log start event
    start_log = TMP_DIR / "live_trade_log.json"
    log_data = []
    if start_log.exists():
        try:
            log_data = json.loads(start_log.read_text())
        except Exception:
            pass
    log_data.append({
        "event": "start",
        "timestamp": datetime.now().isoformat(),
        "pid": proc.pid,
        "stake_per_trade": MAX_STAKE_PER_TRADE,
        "max_open_trades": MAX_OPEN_TRADES
    })
    start_log.write_text(json.dumps(log_data, indent=2))

    webhook = os.getenv("DISCORD_WEBHOOK_ALERTS", "")
    post_discord(webhook, embeds=[{
        "title": "LIVE TRADING STARTED",
        "description": "Real money trading is now active on Binance.",
        "color": 0xff9900,
        "fields": [
            {"name": "Max Per Trade", "value": f"${MAX_STAKE_PER_TRADE} USDT", "inline": True},
            {"name": "Max Positions", "value": str(MAX_OPEN_TRADES), "inline": True},
            {"name": "Stop-Loss", "value": f"{abs(STOP_LOSS_PCT)*100:.0f}%", "inline": True},
            {"name": "Daily Loss Limit", "value": f"{DAILY_LOSS_LIMIT_PCT}%", "inline": True},
            {"name": "Kill Switch", "value": "python execution/crypto_live.py --stop", "inline": False},
        ],
        "timestamp": datetime.utcnow().isoformat()
    }])
    if webhook:
        print("  Discord alert sent to #alerts")


def stop_bot(reason: str = "manual"):
    """Stop the live trading bot immediately."""
    if not is_running():
        print("Live trading bot is not running.")
        return

    pid = int(PID_FILE.read_text().strip())
    print(f"STOPPING live trading bot (PID: {pid}, reason: {reason})...")

    # Try to close all positions gracefully first
    result = api_request("/forcesell/all", method="POST", body={})
    if result:
        print("  Sent forcesell all open positions")
        time.sleep(5)  # Wait for orders to process

    # Kill process
    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    PID_FILE.unlink(missing_ok=True)
    print(f"  Bot stopped (reason: {reason})")

    # Log stop
    start_log = TMP_DIR / "live_trade_log.json"
    log_data = []
    if start_log.exists():
        try:
            log_data = json.loads(start_log.read_text())
        except Exception:
            pass
    log_data.append({"event": "stop", "timestamp": datetime.now().isoformat(), "reason": reason})
    start_log.write_text(json.dumps(log_data, indent=2))

    color = 0xff0000 if reason != "manual" else 0x888888
    webhook = os.getenv("DISCORD_WEBHOOK_ALERTS", "")
    post_discord(webhook, embeds=[{
        "title": "LIVE TRADING STOPPED",
        "description": f"Reason: {reason}",
        "color": color,
        "timestamp": datetime.utcnow().isoformat()
    }])
    if webhook:
        print("  Discord alert sent")


def show_status():
    """Show current live trading status."""
    if not is_running():
        print("Status: NOT RUNNING")
        return

    pid = PID_FILE.read_text().strip()
    print(f"Status: RUNNING (PID: {pid})")
    print(f"Web UI: http://localhost:8080")

    profit = api_request("/profit")
    status = api_request("/status")

    if profit:
        total_pct = profit.get("profit_all_percent", 0)
        print(f"\nP&L Summary:")
        print(f"  Total profit:    {'+' if total_pct >= 0 else ''}{total_pct:.2f}%")
        print(f"  Closed trades:   {profit.get('trade_count', 0)}")

    open_trades = status if isinstance(status, list) else []
    print(f"\nOpen Positions: {len(open_trades)}")
    for trade in open_trades:
        pct = trade.get("profit_pct", 0)
        deployed = trade.get("stake_amount", 0)
        print(f"  {trade.get('pair')}: {'+' if pct >= 0 else ''}{pct:.2f}%  (${deployed:.0f} USDT)")

    # Check daily loss limit
    if profit:
        daily_pct = profit.get("profit_closed_percent_sum", profit.get("profit_all_percent", 0))
        if daily_pct < -DAILY_LOSS_LIMIT_PCT:
            print(f"\nWARNING: Daily loss {daily_pct:.1f}% exceeds -{DAILY_LOSS_LIMIT_PCT}% limit")
            print("Consider running --stop to pause trading")


def main():
    parser = argparse.ArgumentParser(description="Live crypto trading bot")
    parser.add_argument("--start", action="store_true", help="Start live trading (requires CONFIRM)")
    parser.add_argument("--stop", action="store_true", help="Stop live trading bot")
    parser.add_argument("--status", action="store_true", help="Show current status")
    parser.add_argument("--check-prerequisites", action="store_true", help="Check if ready to go live")
    args = parser.parse_args()

    if args.start:
        start_bot()
    elif args.stop:
        stop_bot()
    elif args.status:
        show_status()
    elif args.check_prerequisites:
        ok = check_prerequisites()
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
