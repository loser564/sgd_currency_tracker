import os
import json
import base64
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
import yfinance as yf

load_dotenv()

GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]

def get_gsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    service_account_b64 = os.environ["GOOGLE_SERVICE_ACCOUNT"]
    service_account_info = json.loads(base64.b64decode(service_account_b64))
    creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(GOOGLE_SHEET_ID)

def ensure_trades_sheet(sp):
    try:
        ws = sp.worksheet("Trades")
    except gspread.exceptions.WorksheetNotFound:
        ws = sp.add_worksheet(title="Trades", rows=1000, cols=10)
        ws.append_row([
            "Date", "From", "To", "Amount", "Rate",
            "Converted", "Notes", "Market Rate", "Spread %",
        ])
    return ws

TRADES = [
    {"date": "2025-07-26 00:00:00", "from": "SGD", "to": "JPY", "amount": 80, "rate": 115.4, "converted": 9232.0, "notes": "backfill"},
    {"date": "2025-08-20 00:00:00", "from": "SGD", "to": "AUD", "amount": 350, "rate": 1.203, "converted": 421.05, "notes": "backfill"},
    {"date": "2025-12-12 00:00:00", "from": "SGD", "to": "JPY", "amount": 100, "rate": 120.7, "converted": 12070.0, "notes": "backfill"},
    {"date": "2026-06-02 00:00:00", "from": "SGD", "to": "USD", "amount": 119, "rate": 0.7820, "converted": 93.06, "notes": "backfill"},
    {"date": "2026-08-10 00:00:00", "from": "SGD", "to": "USD", "amount": 120, "rate": 0.7815, "converted": 93.78, "notes": "backfill"},
]

def get_historical_rate(from_ccy, to_ccy, date_str):
    ticker = f"{from_ccy}{to_ccy}=X"
    trade_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
    try:
        df = yf.download(
            ticker,
            start=(trade_date.strftime("%Y-%m-%d")),
            end=((trade_date + timedelta(days=5)).strftime("%Y-%m-%d")),
            interval="1d",
            progress=False,
        )
        s = df["Close"]
        if hasattr(s, "columns"):
            s = s.iloc[:, 0]
        s = s.dropna()
        if not s.empty:
            return float(s.iloc[0])
    except Exception as e:
        print(f"  Could not fetch historical rate for {ticker} on {date_str[:10]}: {e}")
    return None


def main():
    sp = get_gsheet()
    ws = ensure_trades_sheet(sp)
    for t in TRADES:
        market_rate = get_historical_rate(t["from"], t["to"], t["date"])
        if market_rate:
            spread_pct = round((t["rate"] - market_rate) / market_rate * 100, 4)
        else:
            market_rate = ""
            spread_pct = ""
        ws.append_row([
            t["date"], t["from"], t["to"], t["amount"], t["rate"],
            t["converted"], t["notes"], market_rate, spread_pct,
        ])
        print(
            f"Added: {t['amount']} {t['from']} → {t['converted']} {t['to']} @ {t['rate']} "
            f"({t['date'][:10]}) | market: {market_rate} | spread: {spread_pct}%"
        )
    print("Done!")

if __name__ == "__main__":
    main()
