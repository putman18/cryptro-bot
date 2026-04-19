"""
Crypto Trading Dashboard - Streamlit App
Connects to Freqtrade REST API and displays live trading data.
"""

import json
import os
import base64
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd
import altair as alt

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Crypto Bot Dashboard",
    page_icon="",
    layout="wide"
)

API_BASE = st.secrets.get("FREQTRADE_API_URL", os.getenv("FREQTRADE_API_URL", ""))
API_USER = st.secrets.get("FREQTRADE_API_USER", os.getenv("FREQTRADE_API_USER", "admin"))
API_PASS = st.secrets.get("FREQTRADE_API_PASSWORD", os.getenv("FREQTRADE_API_PASSWORD", ""))

START_BALANCE = 1000.0
PAIRS = ["BTC/USD", "ETH/USD", "XRP/USD"]
SESSION_START = datetime(2026, 4, 4, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def _api(path: str):
    token = base64.b64encode(f"{API_USER}:{API_PASS}".encode()).decode()
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": f"Basic {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read())
    except Exception as e:
        return None


@st.cache_data(ttl=30)
def get_all_data():
    return {
        "profit":  _api("/profit"),
        "balance": _api("/balance"),
        "status":  _api("/status"),
        "trades":  _api("/trades?limit=200"),
    }


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.title(" Crypto Bot Dashboard")
st.caption("BTC/ETH/XRP  |  EMA20/50 + SMA200 + ADX + RSI  |  Paper Trading")

# Sidebar
with st.sidebar:
    st.header("Settings")
    api_url  = st.text_input("API URL",  value=API_BASE)
    api_user = st.text_input("Username", value=API_USER)
    api_pass = st.text_input("Password", value=API_PASS, type="password")
    if st.button("Reconnect"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    if st.button("Refresh Data", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.caption("Data refreshes every 30 seconds automatically")

# Override from sidebar
API_BASE = api_url
API_USER = api_user
API_PASS = api_pass

data = get_all_data()

if not data["profit"] and not data["balance"]:
    st.error("Cannot connect to Freqtrade API. Make sure the bot is running and the URL is correct.")
    st.stop()

profit  = data["profit"]  or {}
balance = data["balance"] or {}
status  = data["status"]  or []
trades  = (data["trades"] or {}).get("trades", [])

# Key metrics
now     = datetime.now(timezone.utc)
day_num = (now - SESSION_START).days + 1
uptime  = now - SESSION_START
hours   = int(uptime.total_seconds() // 3600)
mins    = int((uptime.total_seconds() % 3600) // 60)

total_bal     = balance.get("total", START_BALANCE)
bal_change    = total_bal - START_BALANCE
bal_pct       = bal_change / START_BALANCE * 100
profit_usd    = profit.get("profit_closed_coin", 0) or 0
total_trades  = profit.get("trade_count", 0) or 0
win_rate      = (profit.get("winrate", 0) or 0) * 100
profit_factor = profit.get("profit_factor", 0) or 0
open_count    = len(status) if isinstance(status, list) else 0

# Top metrics row
st.subheader(f"Day {day_num} of 14  |  Uptime: {hours}h {mins}m")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Balance",      f"${total_bal:.2f}",   f"{bal_pct:+.2f}%")
c2.metric("Total P&L",    f"${profit_usd:+.2f}")
c3.metric("Trades",       total_trades)
c4.metric("Win Rate",     f"{win_rate:.1f}%")
c5.metric("Open Now",     open_count)

st.divider()

tab1, tab2, tab3 = st.tabs(["Live Positions", "Trade History", "Performance"])

# ---------------------------------------------------------------------------
# Tab 1: Live Positions
# ---------------------------------------------------------------------------
with tab1:
    if not isinstance(status, list) or not status:
        st.info("No open positions right now. Waiting for a buy signal.")
    else:
        for pos in status:
            pair     = pos.get("pair", "?")
            pl_pct   = pos.get("profit_ratio", 0) * 100
            pl_abs   = pos.get("profit_abs", 0)
            entry    = pos.get("open_rate", 0)
            current  = pos.get("current_rate", 0)
            stake    = pos.get("stake_amount", 0)
            dur_min  = pos.get("open_trade_duration_min", 0)
            h, m     = int(dur_min // 60), int(dur_min % 60)

            color = "green" if pl_pct >= 0 else "red"
            arrow = "UP" if pl_pct >= 0 else "DOWN"

            with st.container(border=True):
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Pair",    pair)
                col2.metric("Entry",   f"${entry:.4f}")
                col3.metric("Current", f"${current:.4f}", f"{pl_pct:+.2f}%")
                col4.metric("P&L",     f"${pl_abs:+.2f}")
                st.caption(f"{arrow}  Open for {h}h {m}m  |  Stake: ${stake:.2f}")

# ---------------------------------------------------------------------------
# Tab 2: Trade History
# ---------------------------------------------------------------------------
with tab2:
    closed = [t for t in trades if not t.get("is_open")]
    if not closed:
        st.info("No closed trades yet.")
    else:
        rows = []
        for t in reversed(closed):
            pp  = t.get("profit_ratio", 0) * 100
            pa  = t.get("profit_abs", 0)
            rows.append({
                "Pair":       t.get("pair", "?"),
                "Entry":      f"${t.get('open_rate', 0):.4f}",
                "Exit":       f"${t.get('close_rate', 0):.4f}",
                "P&L %":      f"{pp:+.2f}%",
                "P&L $":      f"${pa:+.2f}",
                "Result":     "WIN" if pa >= 0 else "LOSS",
                "Exit Reason": t.get("exit_reason", t.get("sell_reason", "?")),
            })

        df = pd.DataFrame(rows)

        def color_result(row):
            if row["Result"] == "WIN":
                return ["background-color: #1a3a1a"] * len(row)
            return ["background-color: #3a1a1a"] * len(row)

        st.dataframe(
            df.style.apply(color_result, axis=1),
            use_container_width=True,
            hide_index=True
        )

# ---------------------------------------------------------------------------
# Tab 3: Performance
# ---------------------------------------------------------------------------
with tab3:
    closed = [t for t in trades if not t.get("is_open")]

    # Per-pair breakdown
    pair_stats = {}
    for t in closed:
        p = t.get("pair", "?")
        pair_stats.setdefault(p, {"profit": 0, "wins": 0, "count": 0})
        pair_stats[p]["profit"] += t.get("profit_abs", 0)
        pair_stats[p]["count"]  += 1
        if t.get("profit_abs", 0) >= 0:
            pair_stats[p]["wins"] += 1

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Per-Pair Breakdown")
        if pair_stats:
            pair_rows = []
            for pair in PAIRS:
                s = pair_stats.get(pair, {"profit": 0, "wins": 0, "count": 0})
                wr = s["wins"] / max(s["count"], 1) * 100
                pair_rows.append({
                    "Pair":     pair,
                    "Trades":   s["count"],
                    "Win Rate": f"{wr:.1f}%",
                    "P&L":      f"${s['profit']:+.2f}",
                })
            st.dataframe(pd.DataFrame(pair_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No closed trades yet.")

    with col2:
        st.subheader("Stats")
        st.metric("Profit Factor", f"{profit_factor:.2f}")
        st.metric("Total Trades",  total_trades)
        st.metric("Win Rate",      f"{win_rate:.1f}%")
        st.metric("Balance Change", f"${bal_change:+.2f}")

    # Cumulative P&L chart
    if closed:
        st.subheader("Cumulative P&L")
        cumulative = 0
        chart_rows = []
        for i, t in enumerate(reversed(closed)):
            cumulative += t.get("profit_abs", 0)
            chart_rows.append({"Trade": i + 1, "Cumulative P&L": cumulative})

        chart_df = pd.DataFrame(chart_rows)
        line = alt.Chart(chart_df).mark_line(point=True).encode(
            x=alt.X("Trade:Q", title="Trade #"),
            y=alt.Y("Cumulative P&L:Q", title="P&L ($)"),
            color=alt.condition(
                alt.datum["Cumulative P&L"] >= 0,
                alt.value("#4CAF50"),
                alt.value("#f44336")
            )
        ).properties(height=300)
        st.altair_chart(line, use_container_width=True)

# Auto refresh
st.markdown(
    """
    <script>
    setTimeout(function() { window.location.reload(); }, 30000);
    </script>
    """,
    unsafe_allow_html=True
)
