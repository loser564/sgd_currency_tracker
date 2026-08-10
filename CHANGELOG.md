# Changelog

## v2.1.0 — Buy/Sell Recommendations (2026-08-10)

### Added
- `/checkrates` — shows rates in both directions (SGD → FX with 2-month high %, and FX → SGD)

### Changed
- `/recommend` is now the core scheduled job (every 4h), with clear **BUY** and **SELL** labels
  - **SELL**: convert foreign currency back to SGD when profitable, with per-trade P&L breakdown
  - **BUY**: buy foreign currency when SGD rate is near its 2-month high
- Streamlit dashboard cleaned up — removed legacy Telegram alert form, auto-check polling loop, and unused helpers

### Removed
- `/rates` — replaced by `/checkrates`
- `/alert` and `fx_alert_job` — replaced by `/recommend` scheduled job
- 8-hour FX alert scheduled job — single 4-hour recommend job handles everything
- `send_telegram()`, `fetch_2mo_best()`, auto-check polling loop from Streamlit
- `time` and `requests` imports from `currency.py`

---

## v2.0.0 — Mini Forex Engine (2026-08-10)

### Added
- **Telegram bot** (`bot.py`) — long-running bot hosted on Railway, replacing GitHub Actions cron
  - `/exchange` — log trades with flexible input parsing (supports `120 SGD to USD at 0.7815`, `120SGD USD 0.7815`, `->`, `@`, etc.)
  - `/rate` — check current market rate for any pair
  - `/checkrates` — SGD → FX and FX → SGD rates
  - `/portfolio` — holdings summary with live SGD valuations
  - `/history` — last 10 trades from Google Sheets
  - `/recommend` — buy/sell recommendations (every 4h automatic + on demand)
  - `/addpair`, `/removepair`, `/pairs` — manage tracked currency pairs
- **Google Sheets integration** — all trades logged with date, amount, rate, converted amount, market rate, and spread %
- **Scheduled job** — trade recommendations every 4h (proactive buy/sell Telegram notifications)
- **Portfolio & P&L tracking** on the Streamlit dashboard
- **Trade history table** on the Streamlit dashboard
- **Trade recommendations UI** with two tabs: sell (take profit) and buy (near 2-month high)
- **Dockerfile** and `.dockerignore` for Railway deployment
- `pairs.json` — single source of truth for tracked pairs (editable via bot or file)
- `backfill.py` — script to import historical trades with market rate lookup
- `CHANGELOG.md`

### Changed
- Currency pairs moved from hardcoded dicts to `pairs.json`
- Streamlit app reads trades and pairs from Google Sheets / JSON instead of being standalone
- All yfinance `Close` column reads now handle MultiIndex (fixes `Series` error in newer yfinance)
- `requirements.txt` updated with `python-telegram-bot`, `gspread`, `google-auth`, `python-dotenv`
- `.gitignore` updated to exclude `credentials.json` and `__pycache__`
- `README.md` fully rewritten for the new architecture
- Google service account credentials stored as base64-encoded env var (no file mount needed)

### Removed
- `notifier.py` — replaced by `bot.py`
- `.github/workflows/fx-alerts.yml` — replaced by bot's scheduled recommend job
- `.github/workflows/keep-alive.yml` — no longer needed without GitHub Actions
- Telegram alert form in Streamlit (bot token/chat ID input, threshold form, auto-check polling loop)
- `send_telegram()` from `currency.py` — bot handles all Telegram messaging
