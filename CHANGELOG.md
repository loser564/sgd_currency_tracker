# Changelog

## v2.0.0 — Mini Forex Engine (2026-08-10)

### Added
- **Telegram bot** (`bot.py`) — long-running bot hosted on Railway, replacing GitHub Actions cron
  - `/exchange` — log trades with flexible input parsing (supports `120 SGD to USD at 0.7815`, `120SGD USD 0.7815`, `->`, `@`, etc.)
  - `/rate`, `/rates` — check current market rates
  - `/portfolio` — holdings summary with live SGD valuations
  - `/history` — last 10 trades from Google Sheets
  - `/recommend` — trade recommendations (reverse profit-taking + forward buy signals near 2-month highs)
  - `/addpair`, `/removepair`, `/pairs` — manage tracked currency pairs
  - `/alert` — on-demand FX alert check
- **Google Sheets integration** — all trades logged with date, amount, rate, converted amount, market rate, and spread %
- **Scheduled jobs** — FX alerts every 8h, trade recommendations every 4h (proactive Telegram notifications)
- **Portfolio & P&L tracking** on the Streamlit dashboard
- **Trade history table** on the Streamlit dashboard
- **Trade recommendations UI** with two tabs: convert back (take profit) and buy more (near 2-month high)
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
- `notifier.py` — replaced by `bot.py` scheduled jobs
- `.github/workflows/fx-alerts.yml` — replaced by bot's built-in 8h alert job
- `.github/workflows/keep-alive.yml` — no longer needed without GitHub Actions
- Telegram alert form in Streamlit (bot token/chat ID input, threshold form, auto-check polling loop)
- `send_telegram()` from `currency.py` — bot handles all Telegram messaging
- `fetch_2mo_best()` — only used by the removed alert form
