# notifier.py
import os
import requests
import yfinance as yf
from datetime import datetime, timezone, timedelta

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

PAIRS = {
    "JPY": "SGDJPY=X",
    "EUR": "SGDEUR=X",
    "AUD": "SGDAUD=X",
    "USD": "SGDUSD=X",
    "GBP": "SGDGBP=X",
    "THB": "SGDTHB=X",
    "MYR": "SGDMYR=X",
    "CNY": "SGDCNY=X",
    "TWD": "SGDTWD=X"
}

def send_telegram(text: str):
    assert TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, "Missing TELEGRAM_* env vars"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    r.raise_for_status()

def last_close(ticker: str):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)
    if df.empty or 'Close' not in df.columns:
        return None, None
    
    # Handle both regular and MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        s = df['Close'].iloc[:, 0]  # Get first column under 'Close'
    else:
        s = df['Close']
    
    s = s.dropna()
    if s.empty:
        return None, None
        
    last = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return last, prev

def two_month_stats(ticker: str):
    """Returns (prior_max_excl_today, all_time_max_2mo)."""
    df = yf.download(ticker, period="2mo", interval="1d", progress=False)
    s = df.get("Close")
    if s is None or s.dropna().empty:
        return None, None
    s = s.dropna()
    all_max = float(s.max())
    prior_max = float(s.iloc[:-1].max()) if len(s) > 1 else all_max
    return prior_max, all_max

def main():
    # Timestamp (SGT)
    sg_tz = timezone(timedelta(hours=8))
    now_sgt = datetime.now(timezone.utc).astimezone(sg_tz)
    date_str = now_sgt.strftime("%Y-%m-%d %H:%M SGT")

    hits = []
    status_lines = []

    for ccy, tkr in PAIRS.items():
        last, prev = last_close(tkr)
        prior_max, all_max = two_month_stats(tkr)

        if last is None:
            status_lines.append(f"• SGD→{ccy}: — (no data)")
            continue

        # New 2-month high if today's close (or latest) > prior 2-mo max (excluding today)
        is_new_high = (prior_max is not None) and (last > prior_max)
        if is_new_high:
            hits.append((ccy, last))

        best_str = f"{all_max:.4f}" if all_max is not None else "—"
        status_lines.append(f"• SGD→{ccy}: {last:.4f}  (2-mo high: {best_str})")

    if hits:
        lines = [f"✅ SGD strength alert — new 2-month high(s) [{date_str}]"]
        for ccy, last in hits:
            lines.append(f"• SGD→{ccy}: {last:.4f} (new 2-mo high)")
        # Include current snapshot for all pairs too (nice context)
        lines.append("")
        lines.append("Current snapshot:")
        lines.extend(status_lines)
        send_telegram("\n".join(lines))
    else:
        # Always send daily status even with no highs
        msg = ["ℹ️ Daily SGD FX status — no new highs", f"{date_str}", ""]
        msg.extend(status_lines)
        send_telegram("\n".join(msg))

if __name__ == "__main__":
    main()
