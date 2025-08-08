# notifier.py
import os, sys, time
import requests
import yfinance as yf
import numpy as np

PAIRS = {
    "JPY": "SGDJPY=X",
    "EUR": "SGDEUR=X",
    "AUD": "SGDAUD=X",
    "USD": "SGDUSD=X",
    "GBP": "SGDGBP=X",
}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(text: str):
    assert TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, "Missing TELEGRAM_* env vars"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": text})
    r.raise_for_status()

def last_close(ticker: str):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)
    s = df.get("Close")
    if s is None or s.dropna().empty:
        return None, None
    s = s.dropna()
    last = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return last, prev

def best_2mo(ticker: str):
    df = yf.download(ticker, period="2mo", interval="1d", progress=False)
    s = df.get("Close")
    if s is None or s.dropna().empty:
        return None
    return float(s.dropna().max())

def main():
    hits = []
    lines = []
    for ccy, tkr in PAIRS.items():
        last, prev = last_close(tkr)
        best = best_2mo(tkr)
        if last is None or best is None:
            continue
        # Alert when we hit a NEW 2-month high (strictly greater than prior 2-mo max excluding "now")
        # Using prior_max to reduce spam:
        df = yf.download(tkr, period="2mo", interval="1d", progress=False).get("Close").dropna()
        prior_max = float(df.iloc[:-1].max()) if len(df) > 1 else best
        if last > prior_max:   # strictly new high
            hits.append((ccy, last, best))
            lines.append(f"• SGD→{ccy}: {last:.4f} (new 2-mo high)")

    if hits:
        msg = "✅ SGD strength alert\n" + "\n".join(lines)
        send_telegram(msg)
        print("Sent:", msg)
    else:
        print("No new highs.")

if __name__ == "__main__":
    main()
