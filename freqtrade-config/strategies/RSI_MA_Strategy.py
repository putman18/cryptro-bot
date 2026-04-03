# RSI_MA_Strategy.py - v6
#
# Strategy: EMA20/50 crossover + SMA200 macro filter + ADX(14) + RSI momentum confirmation
#
# Entry:  EMA20 crosses above EMA50 (bullish momentum)
#         AND price > SMA200 (macro uptrend)
#         AND ADX(14) > 20 (real trend present, not chop)
#         AND RSI(14) > 50 (momentum already bullish at entry)
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
#   v6:    ADX back to 20, added RSI(14) > 50 confirmation - win rate 16.2% failing gate
#          ADX 25 was tried but over-filtered (0 wins, 29 trades, -3.1%)

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

        # RSI for momentum confirmation (entry only when momentum is already bullish)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

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

                # Momentum confirmation: RSI > 50 means bullish momentum at entry
                (dataframe["rsi"] > 50) &

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
