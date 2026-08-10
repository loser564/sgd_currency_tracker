
# SGD Currency Tracker & Mini Forex Engine

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://sgdcurrencytracker.streamlit.app)

Track how strong the Singapore Dollar (SGD) is against major currencies, log your exchanges, and get **smart trade recommendations** via a Telegram bot backed by Google Sheets.

**Live dashboard:** https://sgdcurrencytracker.streamlit.app

---

## Features

### Telegram Bot
| Command | Description |
|---------|-------------|
| `/exchange` | Log a currency exchange (flexible input formats) |
| `/rate SGD USD` | Get current market rate |
| `/rates` | All tracked SGD pair rates |
| `/portfolio` | Holdings summary with current SGD valuations |
| `/history` | Last 10 trades from Google Sheets |
| `/recommend` | Trade recommendations (reverse + forward) |
| `/alert` | Trigger FX alert check (2-month highs) |
| `/addpair KRW` | Add a new currency pair |
| `/removepair KRW` | Remove a tracked pair |
| `/pairs` | List all tracked pairs |

### Exchange Logging (`/exchange`)

The bot accepts flexible input formats — type it however feels natural:

```
/exchange 120 SGD USD 0.7815
/exchange 120 SGD to USD 0.7815
/exchange 120 SGD to USD at 0.7815
/exchange 120 SGD -> USD @ 0.7815
/exchange 120SGD USD 0.7815
/exchange 120 SGD USD 0.7815 wise transfer
```

Each trade is logged to Google Sheets with: date, currencies, amount, rate, converted amount, market rate at the time, and spread %.

### Recommendations (`/recommend`)

- **Convert back (take profit):** Alerts when converting your foreign currency back to SGD would be profitable, referencing each original trade
- **Buy more (near 2-month high):** Alerts when SGD→foreign currency rate is within 2% of its 2-month high

Runs automatically every 4 hours + on demand.

### Streamlit Dashboard
- **Today's rates** with change vs previous day
- **Portfolio** with current SGD valuations and P&L
- **Trade history** table from Google Sheets
- **Recommendations** with per-trade profit breakdown
- **30-day & 60-day trend charts**
- **Telegram alerts** with thresholds auto-set to 2-month bests

### Scheduled Alerts
- **FX alerts** every 8 hours — notifies on new 2-month highs
- **Trade recommendations** every 4 hours — proactive profit-taking and buy signals

---

## Tech Stack

- **Bot**: [python-telegram-bot](https://python-telegram-bot.org/)
- **Dashboard**: [Streamlit](https://streamlit.io/)
- **Data**: [yfinance](https://github.com/ranaroussi/yfinance) (Yahoo Finance FX tickers)
- **Storage**: Google Sheets (via [gspread](https://gspread.readthedocs.io/))
- **Charts**: Matplotlib
- **Hosting**: [Railway](https://railway.app/) (Docker)
- **Language**: Python 3.11+

---

## Repo Structure

```
.
├── bot.py                # Telegram bot (main entrypoint)
├── currency.py           # Streamlit dashboard
├── backfill.py           # One-time script to backfill historical trades
├── pairs.json            # Tracked currency pairs (editable)
├── Dockerfile
├── .dockerignore
├── requirements.txt
├── .env                  # Local secrets (not committed)
├── .gitignore
└── .github/
    └── workflows/
        ├── fx-alerts.yml
        └── keep-alive.yml
```

---

## Setup

### 1. Telegram Bot

1. Create a bot with **@BotFather** → copy the **bot token**
2. DM your bot once (say "hi")
3. Get your **chat ID** via: `https://api.telegram.org/bot<TOKEN>/getUpdates`

### 2. Google Sheets

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** → download the JSON key
4. Base64-encode the JSON: `base64 -w 0 credentials.json`
5. Create a Google Sheet → share it with the service account email (Editor access)
6. Copy the Sheet ID from the URL: `docs.google.com/spreadsheets/d/<SHEET_ID>/edit`

### 3. Environment Variables

Create a `.env` file (or set in Railway dashboard):

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
GOOGLE_SHEET_ID=your_sheet_id
GOOGLE_SERVICE_ACCOUNT=base64_encoded_service_account_json
```

### 4. Run Locally

```bash
python -m venv .venv
. .venv/Scripts/activate        # Windows
# source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt

# Run the bot
python bot.py

# Run the dashboard (separate terminal)
streamlit run currency.py
```

### 5. Deploy to Railway

1. Push to GitHub
2. Connect repo to [Railway](https://railway.app/)
3. Set environment variables in Railway dashboard
4. Mount `pairs.json` to a persistent volume so `/addpair` changes survive redeployments
5. Railway builds from the Dockerfile automatically

---

## Backfilling Historical Trades

To import past trades into your Google Sheet:

1. Edit `backfill.py` with your trade history
2. Run: `python backfill.py`

The script fetches the historical market rate for each trade date and calculates the spread %.

---

## Security Notes

- Never commit `.env` or `credentials.json` — both are in `.gitignore`
- If a token was ever pushed, **revoke and rotate** it via @BotFather
- The service account JSON is stored as a base64-encoded env var, not as a file

---

## License

MIT (feel free to reuse with attribution)
