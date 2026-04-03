"""
crypto_setup.py - Phase 1: Foundation

Sets up the Freqtrade environment on this machine:
  1. Creates a Python virtualenv at %USERPROFILE%\\freqtrade-env
  2. Installs Freqtrade into the venv
  3. Scaffolds freqtrade-config/ with config files and strategy
  4. Validates everything is in place

Usage:
    python execution/crypto_setup.py
    python execution/crypto_setup.py --skip-install  # if venv already exists

After this script exits cleanly, validate with:
    %USERPROFILE%\\freqtrade-env\\Scripts\\freqtrade.exe trade --dry-run --config freqtrade-config/config_paper.json
"""

import os
import sys
import json
import subprocess
import argparse
from pathlib import Path

CRYPTO_ROOT = Path(__file__).parent.parent
PROJECT_ROOT = CRYPTO_ROOT.parent
VENV_PATH = Path.home() / "freqtrade-env"
FREQTRADE_BIN = VENV_PATH / "Scripts" / "freqtrade.exe"
CONFIG_DIR = CRYPTO_ROOT / "freqtrade-config"
STRATEGIES_DIR = CONFIG_DIR / "strategies"
DATA_DIR = CONFIG_DIR / "data" / "binanceus"
RESULTS_DIR = CONFIG_DIR / "backtest_results"


def step(msg):
    print(f"\n[+] {msg}")


def run(cmd, check=True, **kwargs):
    print(f"    > {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=check, **kwargs)


def create_venv():
    step(f"Creating virtualenv at {VENV_PATH}")
    if VENV_PATH.exists():
        print("    Already exists - skipping")
        return
    run([sys.executable, "-m", "venv", str(VENV_PATH)])
    print("    Done")


def install_freqtrade():
    step("Installing Freqtrade into venv")
    pip = VENV_PATH / "Scripts" / "pip.exe"
    run([str(pip), "install", "--upgrade", "pip"], check=False)
    run([str(pip), "install", "freqtrade"])
    print("    Done")


def scaffold_config():
    step("Scaffolding freqtrade-config/ directories")
    for d in [CONFIG_DIR, STRATEGIES_DIR, DATA_DIR, RESULTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)
        print(f"    {d}")


def write_paper_config():
    step("Writing freqtrade-config/config_paper.json")
    config_path = CONFIG_DIR / "config_paper.json"
    if config_path.exists():
        print("    Already exists - skipping")
        return

    # Load env for API credentials
    env_path = PROJECT_ROOT / ".env"
    env = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()

    config = {
        "max_open_trades": 3,
        "stake_currency": "USDT",
        "stake_amount": 50,
        "tradable_balance_ratio": 0.99,
        "fiat_display_currency": "USD",
        "dry_run": True,
        "dry_run_wallet": 1000,
        "cancel_open_orders_on_exit": False,
        "trading_mode": "spot",
        "margin_mode": "",
        "unfilledtimeout": {
            "entry": 10,
            "exit": 10,
            "exit_timeout_count": 0,
            "unit": "minutes"
        },
        "entry_pricing": {
            "price_side": "same",
            "use_order_book": True,
            "order_book_top": 1,
            "price_last_balance": 0.0,
            "check_depth_of_market": {
                "enabled": False,
                "bids_to_ask_delta": 1
            }
        },
        "exit_pricing": {
            "price_side": "same",
            "use_order_book": True,
            "order_book_top": 1
        },
        "exchange": {
            "name": "binanceus",
            "key": env.get("BINANCE_API_KEY", ""),
            "secret": env.get("BINANCE_SECRET", ""),
            "ccxt_config": {},
            "ccxt_async_config": {},
            "pair_whitelist": [
                "BTC/USDT",
                "ETH/USDT",
                "XRP/USDT"
            ],
            "pair_blacklist": [
                "BNB/.*"
            ]
        },
        "pairlists": [
            {"method": "StaticPairList"}
        ],
        "telegram": {
            "enabled": False,
            "token": "",
            "chat_id": ""
        },
        "api_server": {
            "enabled": True,
            "listen_ip_address": "127.0.0.1",
            "listen_port": 8080,
            "verbosity": "error",
            "enable_openapi": False,
            "jwt_secret_key": "change_this_to_a_random_string",
            "ws_token": "change_this_too",
            "CORS_origins": [],
            "username": env.get("FREQTRADE_API_USER", "admin"),
            "password": env.get("FREQTRADE_API_PASSWORD", "changeme")
        },
        "bot_name": "freqtrade-paper",
        "initial_state": "running",
        "force_entry_enable": False,
        "internals": {
            "process_throttle_secs": 5
        },
        "strategy": "RSI_MA_Strategy",
        "strategy_path": str(STRATEGIES_DIR),
        "datadir": str(DATA_DIR),
        "user_data_dir": str(CONFIG_DIR)
    }

    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    print(f"    Written to {config_path}")


def write_live_config():
    step("Writing freqtrade-config/config_live.json (placeholder)")
    config_path = CONFIG_DIR / "config_live.json"
    if config_path.exists():
        print("    Already exists - skipping")
        return

    # Minimal placeholder - filled in properly during Phase 4
    placeholder = {
        "_note": "DO NOT USE until Phase 3 paper trading passes all metric gates.",
        "_fill_in": "Copy config_paper.json here, set dry_run=false, and update exchange keys to LIVE keys."
    }
    with open(config_path, "w") as f:
        json.dump(placeholder, f, indent=2)
    print(f"    Written to {config_path}")


def write_strategy():
    step("Writing RSI_MA_Strategy.py (v5 - EMA crossover + SMA200 + ADX filter)")
    strategy_path = STRATEGIES_DIR / "RSI_MA_Strategy.py"
    if strategy_path.exists():
        print("    Already exists - skipping")
        return

    strategy = '''# RSI_MA_Strategy.py - v5
#
# Strategy: EMA20/50 crossover + SMA200 macro filter + ADX(14) trend-strength filter
#
# Entry:  EMA20 crosses above EMA50 (bullish momentum)
#         AND price > SMA200 (macro uptrend)
#         AND ADX(14) > 20 (actual trend present, not chop)
#
# Exit:   EMA20 crosses below EMA50 (momentum turns bearish)
#         OR price breaks below SMA200 (macro trend broken)
#
# Stop:   -5% hard floor (rarely hit - most exits via signal)
#
# Version history:
#   v1-v3: RSI < 30 mean-reversion - failed (-22% total, losses > wins in $ terms)
#   v4:    EMA20/50 crossover + SMA200 - whipsaw in choppy market (PF 0.98)
#   v5:    v4 + ADX > 20 filter - reduces false entries in choppy conditions

import pandas as pd
from freqtrade.strategy import IStrategy, merge_informative_pair
from pandas import DataFrame
import talib.abstract as ta


class RSI_MA_Strategy(IStrategy):
    """
    EMA Crossover trend-following strategy with macro and trend-strength filters.
    """

    INTERFACE_VERSION = 3

    # Timeframe
    timeframe = "1h"

    # Stoploss
    stoploss = -0.05

    # Trailing stop (disabled by default - enable if winners are cut too short)
    trailing_stop = False
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    # ROI - let signals handle exits (very long to avoid premature exit)
    minimal_roi = {
        "0": 100.0
    }

    # Run once per candle close
    process_only_new_candles = True

    # Startup candles needed for SMA200
    startup_candle_count = 210

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # EMAs for crossover signal
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["ema50"] = ta.EMA(dataframe, timeperiod=50)

        # SMA200 for macro trend filter
        dataframe["sma200"] = ta.SMA(dataframe, timeperiod=200)

        # ADX for trend strength (avoids entries in choppy/ranging markets)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)

        # Previous values for crossover detection
        dataframe["ema20_prev"] = dataframe["ema20"].shift(1)
        dataframe["ema50_prev"] = dataframe["ema50"].shift(1)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                # EMA crossover: EMA20 crosses above EMA50
                (dataframe["ema20"] > dataframe["ema50"]) &
                (dataframe["ema20_prev"] <= dataframe["ema50_prev"]) &

                # Macro filter: price above SMA200 (uptrend only)
                (dataframe["close"] > dataframe["sma200"]) &

                # Trend strength filter: ADX > 20 (real trend, not chop)
                (dataframe["adx"] > 20) &

                # Volume sanity check
                (dataframe["volume"] > 0)
            ),
            "enter_long"
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (
                # EMA crossover turns bearish
                (dataframe["ema20"] < dataframe["ema50"]) &
                (dataframe["ema20_prev"] >= dataframe["ema50_prev"])
            ) |
            (
                # Macro trend broken - price breaks below SMA200
                (dataframe["close"] < dataframe["sma200"])
            ),
            "exit_long"
        ] = 1

        return dataframe
'''

    with open(strategy_path, "w") as f:
        f.write(strategy)
    print(f"    Written to {strategy_path}")


def validate():
    step("Validating setup")
    errors = []

    if not FREQTRADE_BIN.exists():
        errors.append(f"Freqtrade binary not found: {FREQTRADE_BIN}")

    for f in ["config_paper.json", "config_live.json"]:
        if not (CONFIG_DIR / f).exists():
            errors.append(f"Config missing: freqtrade-config/{f}")

    if not (STRATEGIES_DIR / "RSI_MA_Strategy.py").exists():
        errors.append("Strategy missing: freqtrade-config/strategies/RSI_MA_Strategy.py")

    if errors:
        print("\nERRORS:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print("    All files present")
    print("\n    Next step - validate Freqtrade connects:")
    print(f"    {FREQTRADE_BIN} trade --dry-run --config freqtrade-config/config_paper.json")
    print("\n    Then run Phase 2:")
    print("    python execution/crypto_backtest.py --days 365")
    print()


def main():
    parser = argparse.ArgumentParser(description="Phase 1: Set up Freqtrade environment")
    parser.add_argument("--skip-install", action="store_true", help="Skip venv creation and pip install")
    args = parser.parse_args()

    print("\nCrypto Trading System - Phase 1: Foundation")
    print("=" * 50)

    if not args.skip_install:
        create_venv()
        install_freqtrade()
    else:
        print("\n[skip] Skipping venv/install (--skip-install)")

    scaffold_config()
    write_paper_config()
    write_live_config()
    write_strategy()
    validate()

    print("Phase 1 complete.")


if __name__ == "__main__":
    main()
