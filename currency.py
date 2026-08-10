import os
import json
import base64
import streamlit as st
import yfinance as yf
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.dates as mdates
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="SGD FX Tracker", layout="wide")
st.title("Currency Value Tracker (SGD as base)")

# -----------------------------
# Load pairs from JSON
# -----------------------------
PAIRS_FILE = os.environ.get("PAIRS_FILE", "pairs.json")
with open(PAIRS_FILE) as _f:
    PAIRS = json.load(_f)

# -----------------------------
# Google Sheets
# -----------------------------
def get_gsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    b64 = os.environ.get("GOOGLE_SERVICE_ACCOUNT", "")
    if not b64:
        return None
    info = json.loads(base64.b64decode(b64))
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "")
    if not sheet_id:
        return None
    return gc.open_by_key(sheet_id)

@st.cache_data(ttl=120)
def load_trades():
    try:
        sp = get_gsheet()
        if sp is None:
            return []
        ws = sp.worksheet("Trades")
        return ws.get_all_records()
    except Exception:
        return []

# -----------------------------
# Helpers
# -----------------------------
def _close_series(df):
    if df.empty:
        return None
    s = df.get("Close")
    if s is None:
        return None
    if hasattr(s, "columns"):
        s = s.iloc[:, 0]
    s = s.dropna()
    return s if not s.empty else None

@st.cache_data(ttl=300)
def fetch_last_close(ticker: str):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)
    s = _close_series(df)
    if s is None:
        return None, None
    last = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return last, prev

@st.cache_data(ttl=300)
def fetch_30d_history(ticker: str):
    return yf.download(ticker, period="1mo", interval="1d", progress=False)

@st.cache_data(ttl=300)
def fetch_60d_history(ticker: str):
    return yf.download(ticker, period="2mo", interval="5d", progress=False)

@st.cache_data(ttl=300)
def fetch_2mo_high(ticker: str):
    df = yf.download(ticker, period="2mo", interval="1d", progress=False)
    s = _close_series(df)
    if s is None:
        return None
    return float(s.max())

@st.cache_data(ttl=300)
def get_market_rate(from_ccy, to_ccy):
    ticker = f"{from_ccy}{to_ccy}=X"
    try:
        df = yf.download(ticker, period="5d", interval="1d", progress=False)
        s = _close_series(df)
        if s is None:
            return None
        return float(s.iloc[-1])
    except Exception:
        return None

# -----------------------------
# Quick metrics for SGD -> majors
# -----------------------------
st.subheader("Today's rates — 1 SGD buys…")
cols = st.columns(len(PAIRS))
for i, (ccy, ticker) in enumerate(PAIRS.items()):
    last, prev = fetch_last_close(ticker)
    with cols[i]:
        if last is None:
            st.metric(label=f"{ccy}", value="—", delta="n/a")
        else:
            delta = None if prev is None else (last - prev)
            st.metric(label=f"{ccy}", value=f"{last:.4f}", delta=f"{delta:+.4f}" if delta is not None else "n/a")

st.caption("Higher numbers are better for SGD (you get more foreign currency per 1 SGD).")

# -----------------------------
# Portfolio Summary
# -----------------------------
st.header("📊 Portfolio")
trades = load_trades()

if trades:
    col_portfolio, col_stats = st.columns(2)

    with col_portfolio:
        st.subheader("Holdings")
        holdings = {}
        total_sgd_spent = 0.0
        for r in trades:
            to_ccy = r.get("To", "")
            converted = float(r.get("Converted", 0) or 0)
            amount = float(r.get("Amount", 0) or 0)
            from_ccy = r.get("From", "")
            if from_ccy == "SGD":
                total_sgd_spent += amount
            holdings[to_ccy] = holdings.get(to_ccy, 0.0) + converted

        total_current_value = 0.0
        for ccy, amt in sorted(holdings.items()):
            current_rate = get_market_rate("SGD", ccy)
            if current_rate:
                sgd_val = amt / current_rate
                total_current_value += sgd_val
                st.metric(label=f"{ccy}", value=f"{amt:,.2f}", delta=f"≈ {sgd_val:,.2f} SGD")
            else:
                st.metric(label=f"{ccy}", value=f"{amt:,.2f}", delta="rate unavailable")

    with col_stats:
        st.subheader("Summary")
        total_pnl = total_current_value - total_sgd_spent
        pnl_pct = (total_pnl / total_sgd_spent * 100) if total_sgd_spent else 0
        st.metric("Total SGD Exchanged", f"{total_sgd_spent:,.2f}")
        st.metric("Current Value (SGD)", f"{total_current_value:,.2f}", delta=f"{total_pnl:+,.2f} ({pnl_pct:+.2f}%)")
else:
    st.info("No trades recorded yet. Use the Telegram bot /exchange command to log trades.")

# -----------------------------
# Trade History
# -----------------------------
st.header("📜 Trade History")
if trades:
    trade_df = pd.DataFrame(trades)
    display_cols = ["Date", "From", "To", "Amount", "Rate", "Converted", "Market Rate", "Spread %", "Notes"]
    available_cols = [c for c in display_cols if c in trade_df.columns]
    st.dataframe(trade_df[available_cols], use_container_width=True, hide_index=True)
else:
    st.info("No trades to display.")

# -----------------------------
# Recommendations
# -----------------------------
st.header("💡 Trade Recommendations")

if trades:
    positions = {}
    for r in trades:
        from_ccy = r.get("From", "")
        to_ccy = r.get("To", "")
        amount = float(r.get("Amount", 0) or 0)
        converted = float(r.get("Converted", 0) or 0)
        rate = float(r.get("Rate", 0) or 0)
        if not from_ccy or not to_ccy or rate == 0:
            continue
        key = (from_ccy, to_ccy)
        if key not in positions:
            positions[key] = []
        positions[key].append({
            "date": r.get("Date", ""),
            "amount": amount,
            "converted": converted,
            "rate": rate,
        })

    reverse_tab, forward_tab = st.tabs(["🔄 Convert Back (Take Profit)", "📈 Buy More (Near 2-Mo High)"])

    with reverse_tab:
        has_reverse = False
        for (from_ccy, to_ccy), pos_trades in positions.items():
            total_converted = sum(t["converted"] for t in pos_trades)
            total_original = sum(t["amount"] for t in pos_trades)
            current_reverse_rate = get_market_rate(to_ccy, from_ccy)
            current_forward_rate = get_market_rate(from_ccy, to_ccy)
            if current_reverse_rate is None or total_original == 0:
                continue
            convert_back = total_converted * current_reverse_rate
            profit = convert_back - total_original
            profit_pct = profit / total_original * 100
            if profit <= 0:
                continue

            has_reverse = True
            with st.expander(f"🟢 {to_ccy} → {from_ccy} | Profit: {profit:+,.2f} {from_ccy} ({profit_pct:+.2f}%)", expanded=True):
                c1, c2, c3 = st.columns(3)
                c1.metric("Holding", f"{total_converted:,.2f} {to_ccy}")
                c2.metric("Convert Back Now", f"{convert_back:,.2f} {from_ccy}")
                c3.metric("Profit", f"{profit:+,.2f} {from_ccy}", delta=f"{profit_pct:+.2f}%")

                st.markdown("**Original trades:**")
                for t in pos_trades:
                    t_back = t["converted"] * current_reverse_rate
                    t_profit = t_back - t["amount"]
                    date_short = t["date"][:10] if t["date"] else "?"
                    st.markdown(
                        f"- `{date_short}`: {t['amount']:,.2f} {from_ccy} → "
                        f"{t['converted']:,.2f} {to_ccy} @ {t['rate']:.4f} "
                        f"→ now worth **{t_back:,.2f} {from_ccy}** ({t_profit:+,.2f})"
                    )

        if not has_reverse:
            st.info("No profitable reverse trades at current rates.")

    with forward_tab:
        has_forward = False
        for (from_ccy, to_ccy), pos_trades in positions.items():
            if from_ccy != "SGD":
                continue
            total_converted = sum(t["converted"] for t in pos_trades)
            total_original = sum(t["amount"] for t in pos_trades)
            avg_rate = total_converted / total_original if total_original else 0
            current_rate = get_market_rate(from_ccy, to_ccy)
            two_mo_high = fetch_2mo_high(PAIRS.get(to_ccy, f"SGD{to_ccy}=X"))
            if current_rate is None or two_mo_high is None:
                continue
            pct_of_high = current_rate / two_mo_high * 100
            if pct_of_high < 98:
                continue

            has_forward = True
            with st.expander(f"🟢 SGD → {to_ccy} | {pct_of_high:.1f}% of 2-mo high", expanded=True):
                c1, c2, c3 = st.columns(3)
                c1.metric("Current Rate", f"{current_rate:.4f}")
                c2.metric("2-Month High", f"{two_mo_high:.4f}")
                c3.metric("Your Avg Rate", f"{avg_rate:.4f}", delta=f"{(current_rate - avg_rate) / avg_rate * 100:+.2f}% vs avg")
                st.progress(min(pct_of_high / 100, 1.0))
                st.caption(f"Rate is at {pct_of_high:.1f}% of the 2-month high — good time to buy more {to_ccy}")

        if not has_forward:
            st.info("No currencies near their 2-month high right now.")

else:
    st.info("Log trades via the Telegram bot to see recommendations.")

# -----------------------------
# Per-pair explorer
# -----------------------------
with st.expander("Explore any 60-day trend"):
    pick = st.selectbox("Pick a pair", list(PAIRS.keys()))
    tkr = PAIRS[pick]
    hist = fetch_60d_history(tkr)
    if hist.empty:
        st.error("No data available.")
    else:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(hist.index, hist["Close"], marker="o", linestyle="-")
        ax.set_title(f"SGD → {pick} (Last 60 Days)", fontsize=12)
        ax.set_xlabel("Date", fontsize=10)
        ax.set_ylabel("Exchange Rate (per 1 SGD)", fontsize=10)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
        plt.setp(ax.get_xticklabels(), fontsize=8)
        ax.grid(True)
        st.pyplot(fig)

# -----------------------------
# 30-day trends
# -----------------------------
st.subheader("Last 30 days (all pairs)")
tabs = st.tabs(list(PAIRS.keys()))
for tab, (ccy, ticker) in zip(tabs, PAIRS.items()):
    with tab:
        hist = fetch_30d_history(ticker)
        if hist.empty:
            st.error(f"No data for SGD{ccy}.")
        else:
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(hist.index, hist["Close"], marker="o", linestyle="-")
            ax.set_title(f"SGD → {ccy} (Last 30 Days)", fontsize=12)
            ax.set_xlabel("Date", fontsize=10)
            ax.set_ylabel("Exchange Rate (per 1 SGD)", fontsize=10)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
            plt.setp(ax.get_xticklabels(), fontsize=8)
            ax.grid(True)
            st.pyplot(fig)

# -----------------------------
# Ad-hoc pair lookup
# -----------------------------
st.divider()
st.subheader("Ad-hoc pair lookup (any to any)")
currencies_list = ["SGD"] + list(PAIRS.keys())
base_currency = st.selectbox("Base:", currencies_list, index=currencies_list.index("SGD"))
target_currency = st.selectbox("Target:", currencies_list, index=currencies_list.index("USD"))
ad_hoc_ticker = f"{base_currency}{target_currency}=X"

col1, col2 = st.columns(2)
with col1:
    if st.button("Get Exchange Rate"):
        rate = get_market_rate(base_currency, target_currency)
        if rate:
            st.success(f"1 {base_currency} = {rate:.4f} {target_currency}")
        else:
            st.error("Could not fetch rate for this pair.")

with col2:
    if st.button("Show Last 30 Days Trend"):
        hist = fetch_30d_history(ad_hoc_ticker)
        if hist.empty:
            st.error("No data available for the selected currency pair.")
        else:
            fig, ax = plt.subplots(figsize=(10, 5))
            s = _close_series(hist)
            if s is not None:
                ax.plot(s.index, s.values, marker="o", linestyle="-")
            ax.set_title(f"{base_currency} to {target_currency} (Last 30 Days)")
            ax.set_xlabel("Date", fontsize=10)
            ax.set_ylabel("Exchange Rate", fontsize=10)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
            plt.setp(ax.get_xticklabels(), fontsize=8)
            ax.grid(True)
            st.pyplot(fig)
