import os
import time
import requests
import streamlit as st
import yfinance as yf
import matplotlib.pyplot as plt
import numpy as np

st.set_page_config(page_title=" SGD FX Tracker", layout="wide")
st.title(" Currency Value Tracker (SGD as base)")

# -----------------------------
# Helpers
# -----------------------------
PAIRS = {
    "JPY": "SGDJPY=X",
    "EUR": "SGDEUR=X",
    "AUD": "SGDAUD=X",
    "USD": "SGDUSD=X",
    "GBP": "SGDGBP=X",
}

@st.cache_data(ttl=300)
def fetch_last_close(ticker: str):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)
    if df.empty or "Close" not in df:
        return None, None
    closes = df["Close"].dropna()
    if len(closes) == 0:
        return None, None
    last = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) > 1 else None
    return last, prev

@st.cache_data(ttl=300)
def fetch_30d_history(ticker: str):
    df = yf.download(ticker, period="1mo", interval="1d", progress=False)
    return df

@st.cache_data(ttl=300)
def fetch_2m_history(ticker: str):
    df = yf.download(ticker, period="2mo", interval="1d", progress=False)
    if df.empty or "Close" not in df:
        return None
    return df

# NEW (2-month best)
@st.cache_data(ttl=300)
def fetch_2mo_best(ticker: str):
    """Return the 95th percentile close over the last ~2 months."""
    df = yf.download(ticker, period="2mo", interval="1d", progress=False)
    if df.empty or "Close" not in df:
        return None
    s = df["Close"].dropna()
    if s.empty:
        return None
    return float(np.percentile(s.values, 95))
def send_telegram(token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": text})
        r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)

# -----------------------------
# Quick metrics for SGD -> majors
# -----------------------------
st.subheader("Today’s rates — 1 SGD buys…")
cols = st.columns(len(PAIRS))
latest_rates = {}
for i, (ccy, ticker) in enumerate(PAIRS.items()):
    last, prev = fetch_last_close(ticker)
    latest_rates[ccy] = last
    with cols[i]:
        if last is None:
            st.metric(label=f"{ccy}", value="—", delta="n/a")
        else:
            delta = None if prev is None else (last - prev)
            st.metric(label=f"{ccy}", value=f"{last:.4f}", delta=f"{delta:+.4f}" if delta is not None else "n/a")

st.caption("Note: Higher numbers are better for SGD (you get more foreign currency per 1 SGD).")

# -----------------------------
# Per-pair explorer (optional)
# -----------------------------
with st.expander("Explore any 60-day trend for the five pairs"):
    pick = st.selectbox("Pick a pair", list(PAIRS.keys()))
    tkr = PAIRS[pick]
    hist = fetch_2m_history(tkr)
    if hist.empty:
        st.error("No data available.")
    else:
        # make graph smaller
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(hist.index, hist["Close"], marker="o", linestyle="-")
        ax.tick_params(axis='x', labelsize=5)
        ax.set_title(f"SGD → {pick} (Last 60 Days)")
        ax.set_xlabel("Date")
        ax.set_ylabel("Exchange Rate (per 1 SGD)", size=10)
        ax.grid(True)
        st.pyplot(fig)

# -----------------------------
# Show 30-day trends in tabs
# -----------------------------
st.subheader("Last 30 days (all pairs)")
tabs = st.tabs(list(PAIRS.keys()))
for tab, (ccy, ticker) in zip(tabs, PAIRS.items()):
    with tab:
        hist = fetch_30d_history(ticker)
        if hist.empty:
            st.error(f"No data for SGD{ccy}.")
        else:
            
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(hist.index, hist["Close"], marker="o", linestyle="-")
            # edit x axis index size to smaller font
            ax.tick_params(axis='x', labelsize=5)
            ax.set_title(f"SGD → {ccy} (Last 30 Days)")
            ax.set_xlabel("Date")
            ax.set_ylabel("Exchange Rate (per 1 SGD)")
            ax.grid(True)
            st.pyplot(fig)


# -----------------------------
# Telegram Alerts
# -----------------------------
st.header("📲 Telegram Alerts when SGD is strong")
st.write("Set your thresholds and hit **Save & Send Update**. You'll get a Telegram message: either ✅ targets met, or ℹ️ a status with current values if no targets are hit.")

# Precompute 2-month best for defaults only (users can override)
best_2mo = {ccy: fetch_2mo_best(tkr) for ccy, tkr in PAIRS.items()}

with st.form("tg_form", clear_on_submit=False):
    st.markdown("**Telegram setup**")
    tg_token = st.text_input(
        "Bot Token",
        value=st.session_state.get("tg_token", "Your Token here"),
        type="password",
        help="Create a bot via @BotFather and paste its token here."
    )
    tg_chat_id = st.text_input(
        "Chat ID",
        value=st.session_state.get("tg_chat_id", "Your chat id here"),
        help="DM your bot first, then use @userinfobot or getUpdates to find it."
    )

    st.markdown("**Alert thresholds (per 1 SGD):**")
    st.caption("Defaults are the **best (max) rate over the last 2 months** — you can override them below.")

    thr_cols = st.columns(5)
    thresholds = {}
    for i, ccy in enumerate(PAIRS.keys()):
        with thr_cols[i]:
            default_val = best_2mo.get(ccy)
            if default_val is None or default_val <= 0:
                default_val = 0.0001
            # use previously saved value if present
            prior = st.session_state.get("thresholds", {}).get(ccy, round(default_val, 4))
            thresholds[ccy] = st.number_input(
                f"{ccy} ≥",
                min_value=0.0001,
                value=float(prior),
                step=0.0001,
                format="%.4f",
                help="You’ll be alerted when 1 SGD is at least this strong."
            )

    submitted = st.form_submit_button("Save & Send Update")

if submitted:
    bad_token = (not tg_token) or (tg_token.strip().lower() == "your token here")
    bad_chat  = (not tg_chat_id) or (tg_chat_id.strip().lower() == "your chat id here")
    if bad_token or bad_chat:
        st.error("Please provide both Telegram Bot Token and Chat ID.")
    else:
        # persist in session for convenience
        st.session_state["tg_token"] = tg_token
        st.session_state["tg_chat_id"] = tg_chat_id
        st.session_state["thresholds"] = thresholds

        # Always send a message: alerts if any target met, otherwise a status snapshot
        hits = []
        status_lines = []

        for ccy, ticker in PAIRS.items():
            last, _ = fetch_last_close(ticker)
            tgt = thresholds.get(ccy)
            if last is None:
                status_lines.append(f"• SGD→{ccy}: — (no data)")
                continue

            met = (tgt is not None) and (last >= float(tgt))
            if met:
                hits.append((ccy, last, tgt))

            status_lines.append(f"• SGD→{ccy}: {last:.4f} (target {float(tgt):.4f})")

        if hits:
            lines = ["✅ SGD alert — target(s) met"]
            for ccy, last, tgt in hits:
                lines.append(f"• SGD→{ccy}: {last:.4f} ≥ {float(tgt):.4f}")
            lines.append("")
            lines.append("Current snapshot:")
            lines.extend(status_lines)
            ok, err = send_telegram(tg_token, tg_chat_id, "\n".join(lines))
            if ok:
                st.success(f"Sent Telegram alert for {len(hits)} pair(s).")
            else:
                st.error(f"Telegram send failed: {err}")
        else:
            msg = ["ℹ️ Status — no targets hit", ""]
            msg.extend(status_lines)
            ok, err = send_telegram(tg_token, tg_chat_id, "\n".join(msg))
            if ok:
                st.info("No alerts fired. Sent a status update to Telegram.")
            else:
                st.error(f"Telegram send failed: {err}")
# -----------------------------
# Ad-hoc pair lookup
# -----------------------------
st.divider()
st.subheader("Ad-hoc pair lookup (any to any)")
currencies = ["USD", "EUR", "INR", "GBP", "JPY", "AUD", "CAD", "CNY", "SGD"]
base_currency = st.selectbox("Base:", currencies, index=currencies.index("SGD"))
target_currency = st.selectbox("Target:", currencies, index=currencies.index("USD"))
ticker = f"{base_currency}{target_currency}=X"

col1, col2 = st.columns(2)
with col1:
    if st.button("Get Exchange Rate"):
        currency_data = yf.Ticker(ticker)
        try:
            exchange_rate = currency_data.history(period="1d")["Close"].iloc[-1]
            st.success(f"1 {base_currency} = {exchange_rate:.4f} {target_currency}")
        except Exception as e:
            st.error(f"Error fetching data: {e}")

with col2:
    if st.button("Show Last 30 Days Trend"):
        try:
            history = yf.download(ticker, period="1mo", interval="1d", progress=False)
            if history.empty:
                st.error("No data available for the selected currency pair.")
            else:
                fig, ax = plt.subplots()
                ax.plot(history.index, history["Close"], marker="o", linestyle="-")
                ax.set_title(f"{base_currency} to {target_currency} (Last 30 Days)")
                ax.set_xlabel("Date")
                ax.set_ylabel("Exchange Rate")
                ax.grid(True)
                st.pyplot(fig)
        except Exception as e:
            st.error(f"Error fetching historical data: {e}")
