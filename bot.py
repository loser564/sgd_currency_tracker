import os
import json
import base64
import logging
from datetime import datetime, timezone, timedelta

import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
import yfinance as yf

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]

PAIRS_FILE = os.environ.get("PAIRS_FILE", "pairs.json")


def load_pairs():
    with open(PAIRS_FILE) as f:
        return json.load(f)


def save_pairs(pairs):
    with open(PAIRS_FILE, "w") as f:
        json.dump(pairs, f, indent=2)


PAIRS = load_pairs()

SG_TZ = timezone(timedelta(hours=8))


# --------------- Google Sheets ---------------

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


def add_pair(ccy, base="SGD"):
    ticker = f"{base}{ccy}=X"
    rate = get_market_rate(base, ccy)
    if rate is None:
        return None, None
    PAIRS[ccy] = ticker
    save_pairs(PAIRS)
    return ticker, rate


def remove_pair(ccy):
    ccy = ccy.upper()
    if ccy not in PAIRS:
        return False
    PAIRS.pop(ccy)
    save_pairs(PAIRS)
    return True


def ensure_trades_sheet(spreadsheet):
    try:
        ws = spreadsheet.worksheet("Trades")
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Trades", rows=1000, cols=10)
        ws.append_row([
            "Date", "From", "To", "Amount", "Rate",
            "Converted", "Notes", "Market Rate", "Spread %",
        ])
    return ws


def log_trade(from_ccy, to_ccy, amount, rate, notes=""):
    sp = get_gsheet()
    ws = ensure_trades_sheet(sp)
    now = datetime.now(SG_TZ).strftime("%Y-%m-%d %H:%M:%S")
    converted = round(amount * rate, 4)

    market_rate = get_market_rate(from_ccy, to_ccy)
    if market_rate:
        spread_pct = round((rate - market_rate) / market_rate * 100, 4)
    else:
        spread_pct = ""
        market_rate = ""

    ws.append_row([
        now, from_ccy, to_ccy, amount, rate,
        converted, notes, market_rate, spread_pct,
    ])
    return converted, market_rate, spread_pct


def get_market_rate(from_ccy, to_ccy):
    ticker = f"{from_ccy}{to_ccy}=X"
    try:
        df = yf.download(ticker, period="5d", interval="1d", progress=False)
        s = df.get("Close")
        if s is None or s.dropna().empty:
            return None
        return float(s.dropna().iloc[-1])
    except Exception:
        return None


# --------------- Portfolio / P&L ---------------

def get_portfolio_summary():
    sp = get_gsheet()
    ws = ensure_trades_sheet(sp)
    rows = ws.get_all_records()
    if not rows:
        return None

    holdings = {}
    total_sgd_spent = 0.0
    for r in rows:
        to_ccy = r.get("To", "")
        converted = float(r.get("Converted", 0) or 0)
        amount = float(r.get("Amount", 0) or 0)
        from_ccy = r.get("From", "")
        if from_ccy == "SGD":
            total_sgd_spent += amount
        holdings[to_ccy] = holdings.get(to_ccy, 0.0) + converted

    lines = ["📊 *Portfolio Summary*", ""]
    for ccy, amt in sorted(holdings.items()):
        current_rate = get_market_rate("SGD", ccy)
        if current_rate:
            current_sgd_value = amt / current_rate
            lines.append(f"• {ccy}: {amt:,.2f} (≈ {current_sgd_value:,.2f} SGD)")
        else:
            lines.append(f"• {ccy}: {amt:,.2f}")

    lines.append(f"\nTotal SGD exchanged: {total_sgd_spent:,.2f}")
    return "\n".join(lines)


def get_trade_history(limit=10):
    sp = get_gsheet()
    ws = ensure_trades_sheet(sp)
    rows = ws.get_all_records()
    if not rows:
        return None
    recent = rows[-limit:]
    lines = [f"📜 *Last {len(recent)} trades*", ""]
    for r in recent:
        spread_str = f" (spread: {r['Spread %']}%)" if r.get("Spread %") else ""
        lines.append(
            f"• {r['Date']}: {r['Amount']} {r['From']} → "
            f"{r['Converted']} {r['To']} @ {r['Rate']}{spread_str}"
        )
    return "\n".join(lines)


# --------------- Recommendations ---------------

def get_recommendations():
    sp = get_gsheet()
    ws = ensure_trades_sheet(sp)
    rows = ws.get_all_records()
    if not rows:
        return None

    positions = {}
    for r in rows:
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

    reverse_recs = []
    forward_recs = []

    for (from_ccy, to_ccy), trades in positions.items():
        total_converted = sum(t["converted"] for t in trades)
        total_original = sum(t["amount"] for t in trades)
        avg_rate = total_converted / total_original if total_original else 0

        current_forward_rate = get_market_rate(from_ccy, to_ccy)
        current_reverse_rate = get_market_rate(to_ccy, from_ccy)

        # --- Reverse: convert holdings back to original currency ---
        if current_reverse_rate is not None and total_original > 0:
            convert_back = total_converted * current_reverse_rate
            profit = convert_back - total_original
            profit_pct = profit / total_original * 100
            if profit > 0:
                trade_details = []
                for t in trades:
                    t_back = t["converted"] * current_reverse_rate
                    t_profit = t_back - t["amount"]
                    date_short = t["date"][:10] if t["date"] else "?"
                    trade_details.append({
                        "date": date_short,
                        "amount": t["amount"],
                        "converted": t["converted"],
                        "rate": t["rate"],
                        "convert_back": t_back,
                        "profit": t_profit,
                    })
                reverse_recs.append({
                    "from": from_ccy,
                    "to": to_ccy,
                    "holding": total_converted,
                    "original_spent": total_original,
                    "avg_rate": avg_rate,
                    "current_rate": current_forward_rate or 0,
                    "reverse_rate": current_reverse_rate,
                    "convert_back": convert_back,
                    "profit": profit,
                    "profit_pct": profit_pct,
                    "trades": trade_details,
                })

        # --- Forward: recommend buying when rate is near 2-month high ---
        if from_ccy == "SGD" and current_forward_rate is not None:
            _, two_mo_high = two_month_stats(PAIRS.get(to_ccy, f"SGD{to_ccy}=X"))
            if two_mo_high is not None:
                pct_of_high = current_forward_rate / two_mo_high * 100
                if pct_of_high >= 98:
                    forward_recs.append({
                        "direction": "forward",
                        "from": from_ccy,
                        "to": to_ccy,
                        "avg_rate": avg_rate,
                        "current_rate": current_forward_rate,
                        "two_mo_high": two_mo_high,
                        "pct_of_high": pct_of_high,
                        "num_trades": len(trades),
                    })

    if not reverse_recs and not forward_recs:
        return None

    reverse_recs.sort(key=lambda r: r["profit_pct"], reverse=True)
    forward_recs.sort(key=lambda r: r["pct_of_high"], reverse=True)

    lines = ["💡 *Trade Recommendations*"]

    if reverse_recs:
        lines.append("")
        lines.append("*🔄 Convert back (take profit):*")
        for rec in reverse_recs:
            num = len(rec["trades"])
            lines.append(
                f"\n🟢 *{rec['to']} → {rec['from']}*\n"
                f"  Holding: {rec['holding']:,.2f} {rec['to']} | "
                f"Reverse rate now: {rec['reverse_rate']:.4f}\n"
                f"  Convert back: {rec['convert_back']:,.2f} {rec['from']}\n"
                f"  *Total profit: {rec['profit']:,.2f} {rec['from']} ({rec['profit_pct']:+.2f}%)*\n"
                f"\n  Original trade{'s' if num > 1 else ''}:"
            )
            for t in rec["trades"]:
                lines.append(
                    f"  ▸ {t['date']}: {t['amount']:,.2f} {rec['from']} → "
                    f"{t['converted']:,.2f} {rec['to']} @ {t['rate']:.4f} "
                    f"→ now worth {t['convert_back']:,.2f} {rec['from']} "
                    f"({t['profit']:+,.2f})"
                )

    if forward_recs:
        lines.append("")
        lines.append("*📈 Buy more (rate improved):*")
        for rec in forward_recs:
            lines.append(
                f"\n🟢 *{rec['from']} → {rec['to']}*\n"
                f"  Current: {rec['current_rate']:.4f} | 2-mo high: {rec['two_mo_high']:.4f}\n"
                f"  Your avg rate: {rec['avg_rate']:.4f}\n"
                f"  *Rate is at {rec['pct_of_high']:.1f}% of 2-month high* — good time to buy"
            )

    return "\n".join(lines)


async def recommend_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = get_recommendations()
        if msg:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID, text=msg, parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Recommend job failed: {e}")


# --------------- FX alerts (migrated from notifier.py) ---------------

def last_close(ticker):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)
    s = df.get("Close")
    if s is None or s.dropna().empty:
        return None, None
    s = s.dropna()
    last = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return last, prev


def two_month_stats(ticker):
    df = yf.download(ticker, period="2mo", interval="1d", progress=False)
    s = df.get("Close")
    if s is None or s.dropna().empty:
        return None, None
    s = s.dropna()
    all_max = float(s.max())
    prior_max = float(s.iloc[:-1].max()) if len(s) > 1 else all_max
    return prior_max, all_max


async def fx_alert_job(context: ContextTypes.DEFAULT_TYPE):
    now_sgt = datetime.now(timezone.utc).astimezone(SG_TZ)
    date_str = now_sgt.strftime("%Y-%m-%d %H:%M SGT")

    hits = []
    status_lines = []

    for ccy, tkr in PAIRS.items():
        last, prev = last_close(tkr)
        prior_max, all_max = two_month_stats(tkr)

        if last is None:
            status_lines.append(f"• SGD→{ccy}: — (no data)")
            continue

        is_new_high = (prior_max is not None) and (last > prior_max)
        if is_new_high:
            hits.append((ccy, last))

        best_str = f"{all_max:.4f}" if all_max is not None else "—"
        status_lines.append(f"• SGD→{ccy}: {last:.4f}  (2-mo high: {best_str})")

    if hits:
        lines = [f"✅ SGD strength alert — new 2-month high(s) [{date_str}]"]
        for ccy, last in hits:
            lines.append(f"• SGD→{ccy}: {last:.4f} (new 2-mo high)")
        lines.append("")
        lines.append("Current snapshot:")
        lines.extend(status_lines)
    else:
        lines = ["ℹ️ Daily SGD FX status — no new highs", date_str, ""]
        lines.extend(status_lines)

    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="\n".join(lines))


# --------------- Bot commands ---------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 SGD Currency Tracker Bot\n\n"
        "Commands:\n"
        "/exchange <amount> <from> <to> <rate> [notes]\n"
        "  e.g. /exchange 120 SGD USD 0.7815\n"
        "/rate <from> <to> — get current market rate\n"
        "/rates — all SGD rates\n"
        "/portfolio — your holdings summary\n"
        "/history — last 10 trades\n"
        "/alert — trigger FX alert check now\n"
        "/recommend — check profitable reverse trades\n"
        "/addpair <CCY> — add a new currency (e.g. /addpair KRW)\n"
        "/removepair <CCY> — remove a tracked currency\n"
        "/pairs — list all tracked pairs"
    )


def parse_exchange_args(args):
    """Parse flexible exchange input formats.

    Supported:
      120 SGD USD 0.7815 [notes]
      120 SGD to USD 0.7815 [notes]
      120 SGD to USD at 0.7815 [notes]
      120 SGD -> USD 0.7815 [notes]
      120 SGD -> USD @ 0.7815 [notes]
      120SGD USD 0.7815 [notes]
      SGD 120 USD 0.7815 [notes]
    """
    import re

    text = " ".join(args)
    text = text.replace("->", " ").replace("→", " ").replace("=>", " ")
    text = re.sub(r"\bat\b", " ", text, flags=re.IGNORECASE)
    text = text.replace("@", " ").replace(",", "")
    text = re.sub(r"\bto\b", " ", text, flags=re.IGNORECASE)

    # split "120SGD" or "SGD120" into separate tokens
    text = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z])(\d)", r"\1 \2", text)

    tokens = text.split()

    numbers = []
    currencies = []
    note_tokens = []

    for tok in tokens:
        try:
            numbers.append(float(tok))
        except ValueError:
            if len(tok) == 3 and tok.isalpha() and len(currencies) < 2:
                currencies.append(tok.upper())
            else:
                note_tokens.append(tok)

    if len(numbers) < 2 or len(currencies) < 2:
        return None

    amount = numbers[0]
    rate = numbers[1]
    from_ccy = currencies[0]
    to_ccy = currencies[1]
    notes = " ".join(note_tokens)
    return amount, from_ccy, to_ccy, rate, notes


async def cmd_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /exchange <amount> <from> <to> <rate> [notes]\n\n"
            "Flexible formats accepted:\n"
            "  /exchange 120 SGD USD 0.7815\n"
            "  /exchange 120 SGD to USD at 0.7815\n"
            "  /exchange 120 SGD -> USD @ 0.7815\n"
            "  /exchange 120SGD USD 0.7815\n"
            "  /exchange 120 SGD USD 0.7815 wise transfer"
        )
        return

    parsed = parse_exchange_args(args)
    if parsed is None:
        await update.message.reply_text(
            "❌ Couldn't parse that. Make sure you include:\n"
            "• an amount (number)\n"
            "• two 3-letter currency codes\n"
            "• a rate (number)\n\n"
            "Example: /exchange 120 SGD to USD at 0.7815"
        )
        return

    amount, from_ccy, to_ccy, rate, notes = parsed

    try:
        converted, market_rate, spread_pct = log_trade(from_ccy, to_ccy, amount, rate, notes)
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")
        await update.message.reply_text(f"❌ Failed to log trade: {e}")
        return

    msg = (
        f"✅ Trade logged!\n"
        f"{amount:,.2f} {from_ccy} → {converted:,.4f} {to_ccy} @ {rate}\n"
    )
    if market_rate:
        msg += f"Market rate: {market_rate:.4f} | Spread: {spread_pct}%"
    await update.message.reply_text(msg)


async def cmd_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /rate <from> <to>\nExample: /rate SGD USD")
        return
    from_ccy = args[0].upper()
    to_ccy = args[1].upper()
    rate = get_market_rate(from_ccy, to_ccy)
    if rate:
        await update.message.reply_text(f"1 {from_ccy} = {rate:.4f} {to_ccy}")
    else:
        await update.message.reply_text(f"Could not fetch rate for {from_ccy}/{to_ccy}")


async def cmd_rates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📈 *Current SGD rates*", ""]
    for ccy, tkr in PAIRS.items():
        last, prev = last_close(tkr)
        if last is None:
            lines.append(f"• SGD→{ccy}: — (no data)")
        else:
            delta = f" ({last - prev:+.4f})" if prev else ""
            lines.append(f"• SGD→{ccy}: {last:.4f}{delta}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = get_portfolio_summary()
    if summary:
        await update.message.reply_text(summary, parse_mode="Markdown")
    else:
        await update.message.reply_text("No trades recorded yet. Use /exchange to log one.")


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_trade_history()
    if history:
        await update.message.reply_text(history, parse_mode="Markdown")
    else:
        await update.message.reply_text("No trades recorded yet.")


async def cmd_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Checking rates...")
    await fx_alert_job(context)


async def cmd_recommend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analysing your trades...")
    msg = get_recommendations()
    if msg:
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("No profitable reverse trades found right now.")


async def cmd_addpair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /addpair <CCY> [base]\nExample: /addpair KRW\nExample: /addpair INR USD")
        return
    ccy = args[0].upper()
    base = args[1].upper() if len(args) > 1 else "SGD"
    if ccy in PAIRS:
        await update.message.reply_text(f"{ccy} is already tracked (ticker: {PAIRS[ccy]})")
        return
    ticker, rate = add_pair(ccy, base)
    if ticker is None:
        await update.message.reply_text(f"❌ Could not find a valid rate for {base}/{ccy}. Check the currency code.")
        return
    await update.message.reply_text(f"✅ Added {base}→{ccy} ({ticker})\nCurrent rate: {rate:.4f}")


async def cmd_removepair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removepair <CCY>\nExample: /removepair KRW")
        return
    ccy = args[0].upper()
    if remove_pair(ccy):
        await update.message.reply_text(f"✅ Removed {ccy} from tracked pairs.")
    else:
        await update.message.reply_text(f"{ccy} is not in the tracked pairs.")


async def cmd_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📋 *Tracked pairs*", ""]
    for ccy, tkr in sorted(PAIRS.items()):
        lines.append(f"• {ccy}: `{tkr}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# --------------- Main ---------------

def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("exchange", cmd_exchange))
    app.add_handler(CommandHandler("rate", cmd_rate))
    app.add_handler(CommandHandler("rates", cmd_rates))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("alert", cmd_alert))
    app.add_handler(CommandHandler("recommend", cmd_recommend))
    app.add_handler(CommandHandler("addpair", cmd_addpair))
    app.add_handler(CommandHandler("removepair", cmd_removepair))
    app.add_handler(CommandHandler("pairs", cmd_pairs))

    job_queue = app.job_queue
    job_queue.run_repeating(fx_alert_job, interval=8 * 3600, first=10)
    job_queue.run_repeating(recommend_job, interval=4 * 3600, first=60)

    logger.info("Bot started — polling for messages")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
