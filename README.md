
#  SGD Currency Tracker

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://sgdcurrencytracker.streamlit.app)

 **Inspiration:** This project was inspired by the approach in [SatyamSharma007/currency-tracker-challenge] and of course his amazing frontend ui was a godsend.(https://github.com/SatyamSharma007/currency-tracker-challenge/blob/main/currency_tracker.py). 


Track how strong the Singapore Dollar (SGD) is against major currencies and get **Telegram alerts** when 1 SGD buys as much (or more) than the **best rate in the last 2 months**.

**Live app:** https://sgdcurrencytracker.streamlit.app

---

## Features

- **Pairs tracked**: SGD → **JPY, EUR, AUD, USD, GBP**
- **Today’s rates** with change vs previous day
- **30-day trends** (tabs + an explorer)
- **Alert thresholds** auto-set to the **2-month best** (configurable)
- **Telegram alerts**
  - From the UI (manual or every 5 minutes while the page is open)
  - **Always-on background alerts** via GitHub Actions (new 2-month highs)

---

## Tech Stack

- **Frontend / App**: [Streamlit](https://streamlit.io/)
- **Data**: [yfinance](https://github.com/ranaroussi/yfinance) (Yahoo Finance FX tickers)
- **Charts**: Matplotlib
- **Alerts**: Telegram Bot API
- **Scheduler**: GitHub Actions (cron)
- **Language**: Python 3.11+

---

## Repo Structure

``` 

.
├─ currency.py                # Streamlit UI
├─ notifier.py                # Headless Telegram notifier (for GitHub Actions)
├─ requirements.txt
├─ .env                       # (local only; not committed)
├─ .gitignore
└─ .github/
└─ workflows/
└─ fx-alerts.yml        # GitHub Actions scheduler (optional)
└─ keep-alive.yml        # Commit some dummy commit once a month to keep action alive

```

---

## Local Setup

1. **Clone & install**
   ```bash
   python -m venv .venv
   . .venv/Scripts/activate        # Windows
   # source .venv/bin/activate     # macOS/Linux
   pip install -r requirements.txt
    ```

2. **Telegram credentials**

   * Create a bot with **@BotFather** → copy the **bot token**.
   * DM your bot **once** (say “hi”).
   * Get your **chat\_id** via:

     ```
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
     ```

     Look for `"chat":{"id": ... }`.

3. **Provide secrets**

   * **Option A (env vars)**:

     ```bash
     setx TELEGRAM_BOT_TOKEN "123:ABC..."
     setx TELEGRAM_CHAT_ID   "123456789"
     # reopen terminal after setx
     ```
   * **Option B (Streamlit secrets)**: create `.streamlit/secrets.toml`

     ```toml
     TELEGRAM_BOT_TOKEN = "123:ABC..."
     TELEGRAM_CHAT_ID   = "123456789"
     ```

4. **Run the app**

   ```bash
   streamlit run currency.py
   ```

---

## Always-On Alerts (GitHub Actions)

1. **Add repo secrets** (GitHub → Settings → Secrets and variables → Actions):

   * `TELEGRAM_BOT_TOKEN`
   * `TELEGRAM_CHAT_ID`

2. **Workflow file**: `.github/workflows/fx-alerts.yml`

   ```yaml
   name: FX Alerts

   on:
     schedule:
       - cron: "*/15 * * * *"   # every 15 minutes
     workflow_dispatch:

   jobs:
     run:
       runs-on: ubuntu-latest
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: "3.11"
         - name: Install deps
           run: |
             python -m pip install --upgrade pip
             pip install yfinance requests numpy
         - name: Run notifier
           env:
             TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
             TELEGRAM_CHAT_ID:   ${{ secrets.TELEGRAM_CHAT_ID }}
           run: python notifier.py
   ```

3. **Test it**: GitHub → **Actions** → **FX Alerts** → **Run workflow**.

`notifier.py` sends a message when **SGD makes a new 2-month high** versus any tracked currency (prevents spam).

---

## Environment Variables

* `TELEGRAM_BOT_TOKEN` — your bot token from @BotFather
* `TELEGRAM_CHAT_ID` — your user or group chat ID

---

## Security Notes

* Don’t commit real secrets. If a token was pushed, **revoke and rotate** it in @BotFather.
* Keep `.env` in `.gitignore`.

---

## Roadmap / Ideas

* Add more pairs (CNY, HKD, INR)
* Set variable threshold percentile
* Email / Discord / Slack alerts
* CI to run basic data smoke tests

---

## License

MIT (feel free to reuse with attribution)

