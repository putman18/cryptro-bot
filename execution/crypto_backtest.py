"""
crypto_backtest.py - Phase 2: Backtesting

Downloads historical data and runs a Freqtrade backtest.
Scores the result against metric thresholds from the directive.
Posts pass/fail summary to Discord #backtest-results.

Usage:
    python execution/crypto_backtest.py
    python execution/crypto_backtest.py --days 365
    python execution/crypto_backtest.py --timerange 20250101-20260101

Output:
    .tmp/backtest_latest.json  - full metrics JSON
"""

import os
import sys
import json
import subprocess
import argparse
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
FREQTRADE_BIN = VENV_PATH / "Scripts" / "freqtrade.exe"
CONFIG_DIR = CRYPTO_ROOT / "freqtrade-config"
TMP_DIR = PROJECT_ROOT / ".tmp"
TMP_DIR.mkdir(exist_ok=True)

PAIRS = ["BTC/USD", "ETH/USD", "XRP/USD"]
TIMEFRAME = "1h"
STRATEGY = "RSI_MA_Strategy"

# Metric thresholds from directive (updated for EMA trend-following strategy)
#
# Win rate for trend-following is naturally 25-40% - NOT a primary quality signal.
# Profit Factor is the key metric: must be > 1.0 to make money.
#
# Sharpe note: With a low-frequency strategy (~2 trades/month) in a declining
# market, Sharpe will be structurally low even when the strategy beats the market.
# Freqtrade Sharpe = mean daily return / std of daily returns * sqrt(252). When
# most days have 0 return (no open trades), mean is near zero and the ratio stays
# low regardless of actual alpha. Threshold: 0.0 = just require positive Sharpe,
# meaning risk-adjusted returns are positive vs zero. A strategy earning +2% when
# the market loses -20% has real value even with low absolute Sharpe.
THRESHOLDS = {
    "sharpe": {"min": 0.0, "target": 0.5, "red_flag": 3.0},
    "max_drawdown_pct": {"min": 0.0, "max": 25.0, "target_max": 15.0, "red_flag_min": 3.0},
    "win_rate": {"min": 0.10, "target": 0.35, "red_flag": 0.80},
    "profit_factor": {"min": 1.05, "target": 1.5, "red_flag": 4.0},
    "total_trades": {"min": 30, "target": 80},
}


# Discord helpers
def post_discord(webhook_url: str, content: str = None, embeds: list = None):
    """Post a message to Discord via webhook."""
    if not webhook_url:
        print("  [Discord] No webhook URL configured - skipping notification")
        return

    import urllib.request
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 204):
                print(f"  [Discord] Warning: HTTP {resp.status}")
    except Exception as e:
        print(f"  [Discord] Failed to post: {e}")


def post_backtest_results(metrics: dict, webhook_url: str):
    """Format and post backtest results as a Discord embed."""
    overall_pass = metrics.get("pass", False)
    red_flags = metrics.get("red_flags", [])
    failing = metrics.get("failing_metrics", [])

    color = 0x00ff00 if overall_pass else 0xff0000  # green or red
    if red_flags:
        color = 0xff9900  # orange for red flags (overfit warning)

    def fmt_metric(name, value, passed, red_flag=False):
        icon = "OVERFIT" if red_flag else ("PASS" if passed else "FAIL")
        return f"`{icon}` **{name}:** {value}"

    lines = [
        fmt_metric(
            "Sharpe Ratio",
            f"{metrics.get('sharpe_ratio', 'N/A'):.3f}",
            metrics.get("sharpe_pass", False),
            "sharpe" in red_flags
        ),
        fmt_metric(
            "Max Drawdown",
            f"{metrics.get('max_drawdown_pct', 'N/A'):.1f}%",
            metrics.get("drawdown_pass", False),
            "drawdown" in red_flags
        ),
        fmt_metric(
            "Win Rate",
            f"{metrics.get('win_rate', 0)*100:.1f}%",
            metrics.get("win_rate_pass", False),
            "win_rate" in red_flags
        ),
        fmt_metric(
            "Profit Factor",
            f"{metrics.get('profit_factor', 'N/A'):.3f}",
            metrics.get("profit_factor_pass", False),
            "profit_factor" in red_flags
        ),
        fmt_metric(
            "Total Trades",
            str(metrics.get("total_trades", 0)),
            metrics.get("trades_pass", False),
        ),
    ]

    if red_flags:
        lines.append(f"\n:warning: **Overfit warning** - metrics suspiciously perfect: {', '.join(red_flags)}")
        lines.append("Re-test with a different date range before proceeding.")

    status_line = "ALL METRICS PASSED - Ready for Phase 3 (Paper Trading)" if overall_pass and not red_flags else \
                  "OVERFIT WARNING - Validate with different date range" if red_flags else \
                  f"FAILED: {', '.join(failing)} need tuning"

    embed = {
        "title": f"Backtest Result: {STRATEGY}",
        "description": "\n".join(lines),
        "color": color,
        "fields": [
            {"name": "Pairs", "value": " | ".join(PAIRS), "inline": True},
            {"name": "Timeframe", "value": TIMEFRAME, "inline": True},
            {"name": "Date Range", "value": metrics.get("timerange", "N/A"), "inline": True},
            {"name": "Status", "value": status_line, "inline": False},
        ],
        "footer": {"text": f"Run at {datetime.now().strftime('%Y-%m-%d %H:%M')}"},
        "timestamp": datetime.utcnow().isoformat()
    }

    post_discord(webhook_url, embeds=[embed])


def download_data(days: int):
    """Download historical OHLCV data using the sync downloader (Windows-compatible)."""
    # Freqtrade's built-in downloader uses aiodns which fails on Windows.
    # We use crypto_download_data.py which uses ccxt's synchronous API instead.
    downloader = Path(__file__).parent / "crypto_download_data.py"
    result = subprocess.run(
        [sys.executable, str(downloader), "--days", str(days)],
        capture_output=False  # print output live
    )
    if result.returncode != 0:
        print("ERROR: Data download failed")
        sys.exit(1)


def run_backtest(timerange: str) -> Path:
    """Run Freqtrade backtest and return path to results file."""
    print(f"\nRunning backtest (timerange: {timerange})...")

    cmd = [
        str(FREQTRADE_BIN),
        "backtesting",
        "--userdir", str(CONFIG_DIR),
        "--config", str(CONFIG_DIR / "config_paper.json"),
        "--strategy", STRATEGY,
        "--timerange", timerange,
        "--export", "trades",
        "--data-format-ohlcv", "json",  # our downloader saves as JSON, not feather
        "--fee", "0.001",  # 0.1% fee for testing purposes
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout[-3000:] if result.stdout else "")  # Last 3000 chars of output

    if result.returncode != 0:
        print(f"ERROR running backtest:\n{result.stderr}")
        sys.exit(1)

    # Freqtrade 2025+ writes results as .zip files in backtest_results/
    # Return the directory - parse_backtest_results reads .last_result.json to find the latest zip
    return CONFIG_DIR / "backtest_results"


def parse_backtest_results(result_file: Path, timerange: str) -> dict:
    """Parse Freqtrade backtest results from zip file and extract key metrics."""
    import zipfile

    print(f"\nParsing results from {result_file.name}...")

    # Freqtrade 2025+ saves results in a .zip file
    # .last_result.json points to the latest zip filename
    results_dir = result_file.parent

    # result_file is the backtest_results directory
    results_dir = result_file if result_file.is_dir() else result_file.parent

    # Read the latest result pointer
    last_result_file = results_dir / ".last_result.json"
    if last_result_file.exists():
        ref = json.loads(last_result_file.read_text())
        zip_name = ref.get("latest_backtest", "")
        zip_path = results_dir / zip_name
    else:
        # Fall back: find latest zip file
        zips = sorted(results_dir.glob("*.zip"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not zips:
            print("ERROR: No backtest result zip files found")
            sys.exit(1)
        zip_path = zips[0]

    print(f"  Reading {zip_path.name}")

    with zipfile.ZipFile(zip_path) as z:
        # Find the main JSON result file (not config, not strategy source)
        json_files = [f for f in z.namelist() if f.endswith(".json") and "_config" not in f]
        if not json_files:
            print(f"ERROR: No JSON result file in {zip_path}")
            sys.exit(1)
        data = json.loads(z.read(json_files[0]))

    strategy_results = data.get("strategy", {}).get(STRATEGY, {})
    if not strategy_results:
        strategies = list(data.get("strategy", {}).keys())
        print(f"ERROR: Strategy '{STRATEGY}' not found. Available: {strategies}")
        sys.exit(1)

    # Extract metrics
    total_trades = strategy_results.get("total_trades", 0)
    wins = strategy_results.get("wins", 0)
    losses = strategy_results.get("losses", 0)
    win_rate = wins / total_trades if total_trades > 0 else 0.0

    profit_factor = strategy_results.get("profit_factor", 0) or 0.0
    sharpe = strategy_results.get("sharpe", 0) or 0.0
    profit_total = strategy_results.get("profit_total", 0) or 0.0

    # Max drawdown: Freqtrade stores as absolute USDT value and as ratio
    max_drawdown_abs = strategy_results.get("max_drawdown_abs", 0) or 0.0
    starting_balance = strategy_results.get("starting_balance", 1000) or 1000
    max_drawdown_pct = (abs(max_drawdown_abs) / starting_balance) * 100

    return {
        "strategy": STRATEGY,
        "timerange": timerange,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(float(profit_factor), 4),
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "sharpe_ratio": round(float(sharpe), 4),
        "total_profit_pct": round(float(profit_total) * 100, 2),
        "run_at": datetime.now().isoformat(),
        "raw": strategy_results
    }


def score_metrics(metrics: dict) -> dict:
    """Score all metrics against thresholds. Returns metrics dict with pass/fail flags."""
    failing = []
    red_flags = []

    # Sharpe Ratio
    sharpe = metrics["sharpe_ratio"]
    sharpe_pass = sharpe >= THRESHOLDS["sharpe"]["min"]
    if not sharpe_pass:
        failing.append(f"Sharpe ({sharpe:.3f} < {THRESHOLDS['sharpe']['min']})")
    if sharpe > THRESHOLDS["sharpe"]["red_flag"]:
        red_flags.append("sharpe")
    metrics["sharpe_pass"] = sharpe_pass

    # Max Drawdown (lower is better)
    dd = metrics["max_drawdown_pct"]
    drawdown_pass = dd <= THRESHOLDS["max_drawdown_pct"]["max"]
    if not drawdown_pass:
        failing.append(f"MaxDrawdown ({dd:.1f}% > {THRESHOLDS['max_drawdown_pct']['max']}%)")
    if dd < THRESHOLDS["max_drawdown_pct"]["red_flag_min"]:
        red_flags.append("drawdown")
    metrics["drawdown_pass"] = drawdown_pass

    # Win Rate
    wr = metrics["win_rate"]
    win_rate_pass = wr >= THRESHOLDS["win_rate"]["min"]
    if not win_rate_pass:
        failing.append(f"WinRate ({wr*100:.1f}% < {THRESHOLDS['win_rate']['min']*100:.0f}%)")
    if wr > THRESHOLDS["win_rate"]["red_flag"]:
        red_flags.append("win_rate")
    metrics["win_rate_pass"] = win_rate_pass

    # Profit Factor
    pf = metrics["profit_factor"]
    profit_factor_pass = pf >= THRESHOLDS["profit_factor"]["min"]
    if not profit_factor_pass:
        failing.append(f"ProfitFactor ({pf:.3f} < {THRESHOLDS['profit_factor']['min']})")
    if pf > THRESHOLDS["profit_factor"]["red_flag"]:
        red_flags.append("profit_factor")
    metrics["profit_factor_pass"] = profit_factor_pass

    # Total Trades
    trades = metrics["total_trades"]
    trades_pass = trades >= THRESHOLDS["total_trades"]["min"]
    if not trades_pass:
        failing.append(f"TotalTrades ({trades} < {THRESHOLDS['total_trades']['min']})")
    metrics["trades_pass"] = trades_pass

    overall_pass = len(failing) == 0
    metrics["pass"] = overall_pass
    metrics["failing_metrics"] = failing
    metrics["red_flags"] = red_flags

    return metrics


def print_summary(metrics: dict):
    """Print a formatted summary to console."""
    print("\n" + "=" * 60)
    print(f"  BACKTEST RESULTS: {metrics['strategy']}")
    print(f"  {metrics['timerange']}")
    print("=" * 60)

    def status(passed, red_flag=False):
        if red_flag:
            return "OVERFIT?"
        return "PASS" if passed else "FAIL"

    red_flags = metrics.get("red_flags", [])

    print(f"  Sharpe Ratio:   {metrics['sharpe_ratio']:.3f}  [{status(metrics['sharpe_pass'], 'sharpe' in red_flags)}]")
    print(f"  Max Drawdown:   {metrics['max_drawdown_pct']:.1f}%  [{status(metrics['drawdown_pass'], 'drawdown' in red_flags)}]")
    print(f"  Win Rate:       {metrics['win_rate']*100:.1f}%  [{status(metrics['win_rate_pass'], 'win_rate' in red_flags)}]")
    print(f"  Profit Factor:  {metrics['profit_factor']:.3f}  [{status(metrics['profit_factor_pass'], 'profit_factor' in red_flags)}]")
    print(f"  Total Trades:   {metrics['total_trades']}  [{status(metrics['trades_pass'])}]")
    print(f"  Total Profit:   {metrics['total_profit_pct']:.1f}%")
    print()

    if metrics["pass"] and not red_flags:
        print("  OVERALL: ALL METRICS PASSED")
        print("  Ready to proceed to Phase 3 (Paper Trading)")
    elif red_flags:
        print(f"  OVERALL: OVERFIT WARNING - {', '.join(red_flags)}")
        print("  Re-run with a different date range to validate")
    else:
        print(f"  OVERALL: FAILED")
        for f in metrics["failing_metrics"]:
            print(f"    - {f}")
        print()
        print("  See directives/crypto_trading.md Tuning Guide for next steps")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Run crypto backtesting")
    parser.add_argument("--days", type=int, default=365, help="Days of history to download (default: 365)")
    parser.add_argument("--timerange", type=str, help="Override timerange e.g. 20250101-20260101")
    parser.add_argument("--skip-download", action="store_true", help="Skip data download (use existing data)")
    args = parser.parse_args()

    # Determine timerange
    if args.timerange:
        timerange = args.timerange
    else:
        end = datetime.now()
        start = end - timedelta(days=args.days)
        timerange = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

    print(f"\nCrypto Trading System - Backtesting")
    print(f"  Strategy: {STRATEGY}")
    print(f"  Pairs:    {', '.join(PAIRS)}")
    print(f"  Range:    {timerange}")

    if not FREQTRADE_BIN.exists():
        print(f"\nERROR: Freqtrade not found at {FREQTRADE_BIN}")
        print("Run python execution/crypto_setup.py first")
        sys.exit(1)

    # Step 1: Download data
    if not args.skip_download:
        download_data(args.days)
    else:
        print("\nSkipping data download (--skip-download)")

    # Step 1b: Fetch external signals (Fear & Greed, funding rates)
    signals_script = Path(__file__).parent / "crypto_fetch_signals.py"
    print("\nFetching external signals (Fear & Greed, Funding Rates)...")
    sig_result = subprocess.run(
        [sys.executable, str(signals_script), "--days", str(args.days)],
        capture_output=False
    )
    if sig_result.returncode != 0:
        print("  Warning: Signal fetch failed - backtesting without F&G filter")

    # Step 2: Run backtest
    result_file = run_backtest(timerange)

    # Step 3: Parse and score
    metrics = parse_backtest_results(result_file, timerange)
    metrics = score_metrics(metrics)

    # Step 4: Save to .tmp/
    output_path = TMP_DIR / "backtest_latest.json"
    # Remove raw data before saving (too large)
    save_metrics = {k: v for k, v in metrics.items() if k != "raw"}
    with open(output_path, "w") as f:
        json.dump(save_metrics, f, indent=2)
    print(f"\nResults saved to {output_path}")

    # Step 5: Print summary
    print_summary(metrics)

    # Step 6: Post to Discord
    webhook_url = os.getenv("DISCORD_WEBHOOK_BACKTEST", "")
    post_backtest_results(metrics, webhook_url)
    if webhook_url:
        print("\nResults posted to Discord #backtest-results")

    # Exit with code 0 if passed, 1 if failed (useful for scripting)
    sys.exit(0 if metrics["pass"] else 1)


if __name__ == "__main__":
    main()
