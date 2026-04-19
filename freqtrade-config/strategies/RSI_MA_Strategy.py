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
    timeframe = "30m"

    # Stoploss
    stoploss = -0.05

    # Trailing stop - locks in profit once trade is up 3%, trails 2% below peak
    # Means minimum 1% profit captured on any trade that hits +3%
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    # ROI table - safety net for trades that never get an exit signal
    # Primary exits are still EMA crossover and SMA200 break
    # Trailing stop locks in profit once up 3%
    minimal_roi = {
        "0":   0.08,   # take 8% profit immediately if hit
        "60":  0.05,   # take 5% after 60 minutes
        "180": 0.03,   # take 3% after 3 hours
        "360": 0.01    # take 1% after 6 hours (cut deadweight)
    }

    # Short selling disabled - Coinbase Advanced spot only
    # Enable on Binance/Bybit futures when moving to live trading
    can_short = False

    # Enable partial position exits
    position_adjustment_enable = True

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

    def custom_entry_price(self, pair, current_time, proposed_rate, entry_tag, side, **kwargs):
        """
        Pullback entry: place limit order at EMA50 instead of buying at market.
        For longs:  wait for price to dip back to EMA50 after bullish crossover.
        For shorts: wait for price to bounce up to EMA50 after bearish crossover.
        Capped at 2% from proposed rate so orders don't sit stale.
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return proposed_rate

        ema50 = dataframe.iloc[-1]["ema50"]

        if side == "long":
            # Limit order at EMA50, but no more than 2% below current price
            return max(ema50, proposed_rate * 0.98)
        else:
            # Limit order at EMA50, but no more than 2% above current price
            return min(ema50, proposed_rate * 1.02)

    def adjust_trade_position(self, trade, current_time, current_rate, current_profit,
                               min_stake, max_stake, current_entry_rate, current_exit_rate,
                               current_entry_profit, current_exit_profit, **kwargs):
        """
        Partial exit: close 50% of position once profit hits +3%.
        Works for both long and short trades.
        The trailing stop then manages the remaining 50%.
        Only triggers once per trade (checked via nr_of_successful_exits == 0).
        """
        if current_profit >= 0.03 and trade.nr_of_successful_exits == 0:
            return -(trade.stake_amount / 2)
        return None

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
