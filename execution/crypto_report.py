"""
crypto_report.py - Performance Reporting and Go-Live Gate

Reads trading results and evaluates whether metrics pass thresholds.
Used at end of Phase 2 (backtesting) and throughout Phase 3 (paper trading).

Usage:
    python execution/crypto_report.py --mode paper
    python execution/crypto_report.py --mode paper --check-go-live
    python execution/crypto_report.py --mode live --period 30d
    python execution/crypto_report.py --mode backtest   (reads .tmp/backtest_latest.json)

Exit codes:
    0 = all metrics passed
    1 = metrics failed or go-live not ready
"""

import os
import sys
import json
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta

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
CONFIG_DIR = CRYPTO_ROOT / "freqtrade-config"
TMP_DIR = PROJECT_ROOT / ".tmp"

API_BASE = "http://127.0.0.1:8080/api/v1"
API_USER = os.getenv("FREQTRADE_API_USER", "admin")
API_PASSWORD = os.getenv("FREQTRADE_API_PASSWORD", "changeme")

# Go-live thresholds (same as backtest thresholds)
THRESHOLDS = {
    "sharpe_ratio": {"min": 0.75, "target": 1.5, "red_flag": 3.0, "direction": "higher"},
    "max_drawdown_pct": {"min": 0.0, "max": 25.0, "target_max": 15.0, "red_flag_min": 3.0, "direction": "lower"},
    "win_rate": {"min": 0.50, "target": 0.55, "red_flag": 0.80, "direction": "higher"},
    "profit_factor": {"min": 1.3, "target": 1.8, "red_flag": 4.0, "direction": "higher"},
    "total_trades": {"min": 30, "target": 60, "direction": "higher"},  # lower for paper (2-4 weeks)
}

# Minimum paper trading duration before go-live
MIN_PAPER_DAYS = 14


def post_discord(webhook_url: str, content: str = None, embeds: list = None):
    if not webhook_url:
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


def api_request(path: str):
    """Call Freqtrade REST API."""
    import base64
    credentials = base64.b64encode(f"{API_USER}:{API_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Basic {credentials}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def get_paper_metrics() -> dict:
    """Fetch current paper trading metrics from Freqtrade API."""
    profit = api_request("/profit")
    performance = api_request("/performance")

    if not profit:
        print("ERROR: Cannot reach Freqtrade API. Is the bot running?")
        print("  Start with: python execution/crypto_paper_trade.py --start")
        sys.exit(1)

    total_trades = profit.get("trade_count", 0)
    wins = profit.get("winning_trades", 0)
    win_rate = wins / total_trades if total_trades > 0 else 0.0
    profit_factor = profit.get("profit_factor", 0)
    max_drawdown_pct = abs(profit.get("max_drawdown", 0)) * 100
    sharpe = profit.get("sharpe", profit.get("sharpe_ratio", 0))
    total_profit_pct = profit.get("profit_all_percent", 0)

    # Get paper trading start time from log
    start_log = TMP_DIR / "paper_trade_log.json"
    started_at = None
    if start_log.exists():
        try:
            log = json.loads(start_log.read_text())
            starts = [e for e in log if e["event"] == "start"]
            if starts:
                started_at = datetime.fromisoformat(starts[0]["timestamp"])
        except Exception:
            pass

    days_running = 0
    if started_at:
        days_running = (datetime.now() - started_at).days

    return {
        "mode": "paper",
        "total_trades": total_trades,
        "wins": wins,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(float(profit_factor), 4) if profit_factor else 0.0,
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "sharpe_ratio": round(float(sharpe), 4) if sharpe else 0.0,
        "total_profit_pct": round(float(total_profit_pct), 2),
        "days_running": days_running,
        "started_at": started_at.isoformat() if started_at else None,
        "run_at": datetime.now().isoformat(),
    }


def get_backtest_metrics() -> dict:
    """Read metrics from the most recent backtest run."""
    path = TMP_DIR / "backtest_latest.json"
    if not path.exists():
        print("ERROR: No backtest results found. Run python execution/crypto_backtest.py first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def score_metrics(metrics: dict, mode: str) -> dict:
    """Score metrics against thresholds. Returns dict with pass/fail per metric."""
    failing = []
    red_flags = []

    # Sharpe
    sharpe = metrics.get("sharpe_ratio", 0)
    sharpe_pass = sharpe >= THRESHOLDS["sharpe_ratio"]["min"]
    if not sharpe_pass:
        failing.append(f"Sharpe ({sharpe:.3f} < {THRESHOLDS['sharpe_ratio']['min']})")
    if sharpe > THRESHOLDS["sharpe_ratio"]["red_flag"]:
        red_flags.append("sharpe_ratio")

    # Drawdown
    dd = metrics.get("max_drawdown_pct", 100)
    drawdown_pass = dd <= THRESHOLDS["max_drawdown_pct"]["max"]
    if not drawdown_pass:
        failing.append(f"Drawdown ({dd:.1f}% > {THRESHOLDS['max_drawdown_pct']['max']}%)")
    if dd < THRESHOLDS["max_drawdown_pct"]["red_flag_min"]:
        red_flags.append("max_drawdown")

    # Win Rate
    wr = metrics.get("win_rate", 0)
    win_rate_pass = wr >= THRESHOLDS["win_rate"]["min"]
    if not win_rate_pass:
        failing.append(f"WinRate ({wr*100:.1f}% < {THRESHOLDS['win_rate']['min']*100:.0f}%)")
    if wr > THRESHOLDS["win_rate"]["red_flag"]:
        red_flags.append("win_rate")

    # Profit Factor
    pf = metrics.get("profit_factor", 0)
    profit_factor_pass = pf >= THRESHOLDS["profit_factor"]["min"]
    if not profit_factor_pass:
        failing.append(f"ProfitFactor ({pf:.3f} < {THRESHOLDS['profit_factor']['min']})")
    if pf > THRESHOLDS["profit_factor"]["red_flag"]:
        red_flags.append("profit_factor")

    # Total Trades
    min_trades = THRESHOLDS["total_trades"]["min"]
    trades = metrics.get("total_trades", 0)
    trades_pass = trades >= min_trades
    if not trades_pass:
        failing.append(f"TotalTrades ({trades} < {min_trades})")

    # Duration check (paper mode only)
    duration_pass = True
    if mode == "paper":
        days = metrics.get("days_running", 0)
        duration_pass = days >= MIN_PAPER_DAYS
        if not duration_pass:
            failing.append(f"Duration ({days} days < {MIN_PAPER_DAYS} day minimum)")

    overall_pass = len(failing) == 0
    metrics["sharpe_pass"] = sharpe_pass
    metrics["drawdown_pass"] = drawdown_pass
    metrics["win_rate_pass"] = win_rate_pass
    metrics["profit_factor_pass"] = profit_factor_pass
    metrics["trades_pass"] = trades_pass
    metrics["duration_pass"] = duration_pass
    metrics["pass"] = overall_pass
    metrics["failing_metrics"] = failing
    metrics["red_flags"] = red_flags

    return metrics


def print_report(metrics: dict, mode: str, go_live_check: bool):
    """Print a formatted performance report."""
    print("\n" + "=" * 65)
    print(f"  PERFORMANCE REPORT - {mode.upper()} MODE")
    if metrics.get("started_at"):
        print(f"  Running since: {metrics['started_at'][:10]} ({metrics.get('days_running', 0)} days)")
    print("=" * 65)

    def icon(passed, red_flag=False):
        if red_flag:
            return "[OVERFIT?]"
        return "[PASS]" if passed else "[FAIL]"

    rf = metrics.get("red_flags", [])

    print(f"  {icon(metrics.get('sharpe_pass'), 'sharpe_ratio' in rf):<12} Sharpe Ratio:   {metrics.get('sharpe_ratio', 0):.3f}   (min: {THRESHOLDS['sharpe_ratio']['min']})")
    print(f"  {icon(metrics.get('drawdown_pass'), 'max_drawdown' in rf):<12} Max Drawdown:   {metrics.get('max_drawdown_pct', 0):.1f}%   (max: {THRESHOLDS['max_drawdown_pct']['max']}%)")
    print(f"  {icon(metrics.get('win_rate_pass'), 'win_rate' in rf):<12} Win Rate:       {metrics.get('win_rate', 0)*100:.1f}%   (min: {THRESHOLDS['win_rate']['min']*100:.0f}%)")
    print(f"  {icon(metrics.get('profit_factor_pass'), 'profit_factor' in rf):<12} Profit Factor:  {metrics.get('profit_factor', 0):.3f}   (min: {THRESHOLDS['profit_factor']['min']})")
    print(f"  {icon(metrics.get('trades_pass')):<12} Total Trades:   {metrics.get('total_trades', 0)}")
    if mode == "paper":
        print(f"  {icon(metrics.get('duration_pass')):<12} Days Running:   {metrics.get('days_running', 0)}   (min: {MIN_PAPER_DAYS})")
    print(f"\n  Total P&L:  {'+' if metrics.get('total_profit_pct', 0) >= 0 else ''}{metrics.get('total_profit_pct', 0):.2f}%")

    print()
    if go_live_check:
        if metrics["pass"] and not rf:
            print("  GO-LIVE CHECK: PASSED")
            print("  All metrics meet thresholds. Ready for Phase 4.")
            print("  Next: Create Binance account, fund with $100-200 USDT,")
            print("        add BINANCE_API_KEY_LIVE to .env, run crypto_live.py --start")
        elif rf:
            print("  GO-LIVE CHECK: HOLD - Overfit warning")
            print(f"  Suspicious metrics: {', '.join(rf)}")
            print("  Validate by running backtest on a different date range.")
        else:
            print("  GO-LIVE CHECK: NOT READY")
            print("  Failing metrics:")
            for f in metrics["failing_metrics"]:
                print(f"    - {f}")
            print("\n  See directives/crypto_trading.md Tuning Guide")
    else:
        print("  OVERALL:", "ALL PASS" if metrics["pass"] else "FAILING")

    print("=" * 65)


def post_report_discord(metrics: dict, mode: str, go_live_check: bool):
    """Post report to appropriate Discord channel."""
    rf = metrics.get("red_flags", [])
    overall_pass = metrics.get("pass", False)

    if go_live_check:
        if overall_pass and not rf:
            color = 0x00ff00
            status = "GO-LIVE APPROVED - All metrics passed"
            webhook = os.getenv("DISCORD_WEBHOOK_ALERTS", "")
        elif rf:
            color = 0xff9900
            status = f"HOLD: Overfit warning on {', '.join(rf)}"
            webhook = os.getenv("DISCORD_WEBHOOK_ALERTS", "")
        else:
            color = 0xff0000
            status = f"NOT READY: {len(metrics['failing_metrics'])} metrics failing"
            webhook = os.getenv("DISCORD_WEBHOOK_TRADING", "")
    else:
        color = 0x00aaff
        status = "Performance update"
        webhook = os.getenv("DISCORD_WEBHOOK_TRADING", "")

    def pf(passed, red_flag=False):
        return "OVERFIT?" if red_flag else ("PASS" if passed else "FAIL")

    lines = [
        f"`{pf(metrics.get('sharpe_pass'), 'sharpe_ratio' in rf)}` **Sharpe:** {metrics.get('sharpe_ratio', 0):.3f}",
        f"`{pf(metrics.get('drawdown_pass'), 'max_drawdown' in rf)}` **Drawdown:** {metrics.get('max_drawdown_pct', 0):.1f}%",
        f"`{pf(metrics.get('win_rate_pass'), 'win_rate' in rf)}` **Win Rate:** {metrics.get('win_rate', 0)*100:.1f}%",
        f"`{pf(metrics.get('profit_factor_pass'), 'profit_factor' in rf)}` **Profit Factor:** {metrics.get('profit_factor', 0):.3f}",
        f"`{pf(metrics.get('trades_pass'))}` **Trades:** {metrics.get('total_trades', 0)}",
    ]
    if mode == "paper":
        lines.append(f"`{pf(metrics.get('duration_pass'))}` **Days Running:** {metrics.get('days_running', 0)}")

    embed = {
        "title": f"{'Go-Live Check' if go_live_check else 'Performance Report'} - {mode.title()} Mode",
        "description": "\n".join(lines),
        "color": color,
        "fields": [
            {"name": "Total P&L", "value": f"{'+' if metrics.get('total_profit_pct', 0) >= 0 else ''}{metrics.get('total_profit_pct', 0):.2f}%", "inline": True},
            {"name": "Status", "value": status, "inline": False},
        ],
        "timestamp": datetime.utcnow().isoformat()
    }

    post_discord(webhook, embeds=[embed])
    if webhook:
        print(f"  Report posted to Discord")


def main():
    parser = argparse.ArgumentParser(description="Crypto trading performance report")
    parser.add_argument("--mode", choices=["paper", "live", "backtest"], default="paper")
    parser.add_argument("--check-go-live", action="store_true", help="Check if ready to go live")
    parser.add_argument("--period", default="30d", help="Period for live mode (e.g. 7d, 30d)")
    args = parser.parse_args()

    if args.mode == "backtest":
        metrics = get_backtest_metrics()
        metrics = score_metrics(metrics, "backtest")
    elif args.mode == "paper":
        metrics = get_paper_metrics()
        metrics = score_metrics(metrics, "paper")
    elif args.mode == "live":
        # Live mode uses the same API, just different labeling
        metrics = get_paper_metrics()  # same API structure
        metrics["mode"] = "live"
        metrics = score_metrics(metrics, "live")

    print_report(metrics, args.mode, args.check_go_live)
    post_report_discord(metrics, args.mode, args.check_go_live)

    # Save report
    report_path = TMP_DIR / f"report_{args.mode}_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    save_data = {k: v for k, v in metrics.items() if k != "raw"}
    with open(report_path, "w") as f:
        json.dump(save_data, f, indent=2)

    sys.exit(0 if metrics["pass"] else 1)


if __name__ == "__main__":
    main()
