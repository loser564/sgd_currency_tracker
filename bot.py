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


def ensure_holdings_sheet(spreadsheet):
    try:
        ws = spreadsheet.worksheet("Holdings")
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title="Holdings", rows=100, cols=3)
        ws.append_row(["Currency", "Amount", "Avg SGD Cost (optional)"])
    return ws


def get_holdings():
    """Reads the user-maintained Holdings sheet: Currency | Amount | Avg SGD Cost (optional)."""
    sp = get_gsheet()
    ws = ensure_holdings_sheet(sp)
    rows = ws.get_all_records()
    holdings = {}
    for r in rows:
        ccy = str(r.get("Currency", "")).upper().strip()
        amount = r.get("Amount", 0)
        cost = r.get("Avg SGD Cost (optional)", "")
        try:
            amount = float(amount or 0)
        except (TypeError, ValueError):
            amount = 0
        try:
            cost = float(cost) if cost not in ("", None) else None
        except (TypeError, ValueError):
            cost = None
        if ccy and amount > 0:
            holdings[ccy] = {"amount": amount, "avg_cost": cost}
    return holdings


def set_holding(ccy, amount, avg_cost=None):
    """Create or update a row in the Holdings sheet for the given currency."""
    ccy = ccy.upper()
    sp = get_gsheet()
    ws = ensure_holdings_sheet(sp)
    cell = ws.find(ccy, in_column=1)
    cost_val = avg_cost if avg_cost is not None else ""
    if cell:
        ws.update_cell(cell.row, 2, amount)
        ws.update_cell(cell.row, 3, cost_val)
    else:
        ws.append_row([ccy, amount, cost_val])


def remove_holding(ccy):
    ccy = ccy.upper()
    sp = get_gsheet()
    ws = ensure_holdings_sheet(sp)
    cell = ws.find(ccy, in_column=1)
    if cell:
        ws.delete_rows(cell.row)
        return True
    return False


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


def _close_series(df):
    s = df.get("Close")
    if s is None:
        return None
    if hasattr(s, "columns"):
        s = s.iloc[:, 0]
    s = s.dropna()
    return s if not s.empty else None


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


# --------------- Portfolio / P&L ---------------

def _avg_buy_rate(rows, to_ccy):
    """Average SGD->to_ccy rate from the Trades sheet, used as a cost-basis
    fallback when the Holdings sheet doesn't specify one."""
    total_converted = 0.0
    total_spent = 0.0
    for r in rows:
        if r.get("From", "") != "SGD" or r.get("To", "") != to_ccy:
            continue
        amount = float(r.get("Amount", 0) or 0)
        converted = float(r.get("Converted", 0) or 0)
        if converted > 0:
            total_converted += converted
            total_spent += amount
    if total_converted <= 0:
        return None
    return total_spent / total_converted  # SGD cost per unit of to_ccy


def get_holdings_with_pnl():
    """Combines the Holdings sheet with live rates and cost basis to compute
    unrealized P&L per currency (used by /portfolio and /holdings)."""
    holdings = get_holdings()
    if not holdings:
        return []

    sp = get_gsheet()
    trades_ws = ensure_trades_sheet(sp)
    rows = trades_ws.get_all_records()

    results = []
    for ccy, h in sorted(holdings.items()):
        amount = h["amount"]
        avg_cost_rate = h["avg_cost"]
        cost_is_estimated = avg_cost_rate is None
        if avg_cost_rate is None:
            avg_cost_rate = _avg_buy_rate(rows, ccy)

        current_rate = get_market_rate("SGD", ccy)  # SGD -> ccy, for display
        current_value_sgd = None
        cost_sgd = None
        pnl = None
        pnl_pct = None

        reverse_rate = get_market_rate(ccy, "SGD")
        if reverse_rate is not None:
            current_value_sgd = amount * reverse_rate
            if avg_cost_rate:
                cost_sgd = amount * avg_cost_rate
                pnl = current_value_sgd - cost_sgd
                pnl_pct = pnl / cost_sgd * 100 if cost_sgd else None

        results.append({
            "ccy": ccy,
            "amount": amount,
            "avg_cost_rate": avg_cost_rate,
            "cost_is_estimated": cost_is_estimated,
            "current_value_sgd": current_value_sgd,
            "cost_sgd": cost_sgd,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        })
    return results


def get_portfolio_summary():
    holdings = get_holdings_with_pnl()
    if not holdings:
        return None

    lines = ["📊 *Portfolio Summary*", ""]
    total_value = 0.0
    total_cost = 0.0
    for h in holdings:
        if h["current_value_sgd"] is not None:
            total_value += h["current_value_sgd"]
            value_str = f"≈ {h['current_value_sgd']:,.2f} SGD"
        else:
            value_str = "rate unavailable"

        pnl_str = ""
        if h["pnl"] is not None:
            total_cost += h["cost_sgd"]
            pnl_str = f" | P&L: {h['pnl']:+,.2f} SGD ({h['pnl_pct']:+.2f}%)"

        lines.append(f"• {h['ccy']}: {h['amount']:,.2f} ({value_str}){pnl_str}")

    lines.append(f"\nTotal current value: {total_value:,.2f} SGD")
    if total_cost > 0:
        total_pnl = total_value - total_cost
        total_pnl_pct = total_pnl / total_cost * 100
        lines.append(f"Total unrealized P&L: {total_pnl:+,.2f} SGD ({total_pnl_pct:+.2f}%)")
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
    trades_ws = ensure_trades_sheet(sp)
    rows = trades_ws.get_all_records()

    holdings = get_holdings()
    if not holdings and not rows:
        return None

    # --- SELL side: based on what you say you currently hold ---
    reverse_recs = []
    for ccy, h in holdings.items():
        total_holding = h["amount"]
        avg_cost_rate = h["avg_cost"]
        if avg_cost_rate is None:
            avg_cost_rate = _avg_buy_rate(rows, ccy)
        if avg_cost_rate is None or avg_cost_rate <= 0:
            continue
        total_cost = total_holding * avg_cost_rate

        current_reverse_rate = get_market_rate(ccy, "SGD")
        if current_reverse_rate is None:
            continue

        convert_back = total_holding * current_reverse_rate
        profit = convert_back - total_cost
        profit_pct = profit / total_cost * 100
        if profit <= 0:
            continue

        reverse_recs.append({
            "to": ccy,
            "from": "SGD",
            "holding": total_holding,
            "original_spent": total_cost,
            "avg_cost_rate": avg_cost_rate,
            "reverse_rate": current_reverse_rate,
            "convert_back": convert_back,
            "profit": profit,
            "profit_pct": profit_pct,
        })

    # --- BUY side: unrelated to current holdings — average historical SGD->X buy rate ---
    buy_positions = {}
    for r in rows:
        from_ccy = r.get("From", "")
        to_ccy = r.get("To", "")
        if from_ccy != "SGD":
            continue
        amount = float(r.get("Amount", 0) or 0)
        converted = float(r.get("Converted", 0) or 0)
        rate = float(r.get("Rate", 0) or 0)
        if rate == 0:
            continue
        buy_positions.setdefault(to_ccy, []).append({"amount": amount, "converted": converted})

    forward_recs = []
    for to_ccy, trades in buy_positions.items():
        total_converted = sum(t["converted"] for t in trades)
        total_original = sum(t["amount"] for t in trades)
        avg_rate = total_converted / total_original if total_original else 0

        current_forward_rate = get_market_rate("SGD", to_ccy)
        if current_forward_rate is None:
            continue

        _, two_mo_high = two_month_stats(PAIRS.get(to_ccy, f"SGD{to_ccy}=X"))
        if two_mo_high is None:
            continue

        pct_of_high = current_forward_rate / two_mo_high * 100
        if pct_of_high >= 98:
            forward_recs.append({
                "to": to_ccy,
                "avg_rate": avg_rate,
                "current_rate": current_forward_rate,
                "two_mo_high": two_mo_high,
                "pct_of_high": pct_of_high,
            })

    if not reverse_recs and not forward_recs:
        return None

    reverse_recs.sort(key=lambda r: r["profit_pct"], reverse=True)
    forward_recs.sort(key=lambda r: r["pct_of_high"], reverse=True)

    lines = ["💡 *Trade Recommendations*"]

    if reverse_recs:
        lines.append("")
        lines.append("*💰 SELL — convert back to SGD (take profit):*")
        for rec in reverse_recs:
            lines.append(
                f"\n🟢 *Sell {rec['to']} → {rec['from']}*\n"
                f"  Holding: {rec['holding']:,.2f} {rec['to']} "
                f"(avg cost: {rec['avg_cost_rate']:.4f})\n"
                f"  Reverse rate now: {rec['reverse_rate']:.4f}\n"
                f"  Convert back: {rec['convert_back']:,.2f} {rec['from']}\n"
                f"  *Profit: {rec['profit']:,.2f} {rec['from']} ({rec['profit_pct']:+.2f}%)*"
            )

    if forward_recs:
        lines.append("")
        lines.append("*🛒 BUY — SGD is near 2-month high:*")
        for rec in forward_recs:
            lines.append(
                f"\n🟢 *Buy {rec['to']}* (SGD → {rec['to']})\n"
                f"  Current: {rec['current_rate']:.4f} | 2-mo high: {rec['two_mo_high']:.4f}\n"
                f"  Your avg rate: {rec['avg_rate']:.4f}\n"
                f"  *Rate at {rec['pct_of_high']:.1f}% of 2-month high* — good time to buy"
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


# --------------- Rate checking ---------------

def last_close(ticker):
    df = yf.download(ticker, period="5d", interval="1d", progress=False)
    s = _close_series(df)
    if s is None:
        return None, None
    last = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return last, prev


def two_month_stats(ticker):
    df = yf.download(ticker, period="2mo", interval="1d", progress=False)
    s = _close_series(df)
    if s is None:
        return None, None
    all_max = float(s.max())
    prior_max = float(s.iloc[:-1].max()) if len(s) > 1 else all_max
    return prior_max, all_max


def get_checkrates_sgd_to_fx():
    now_sgt = datetime.now(timezone.utc).astimezone(SG_TZ)
    date_str = now_sgt.strftime("%Y-%m-%d %H:%M SGT")
    lines = [f"📈 *SGD → Foreign Currency* [{date_str}]", ""]
    for ccy, tkr in PAIRS.items():
        last, prev = last_close(tkr)
        _, all_max = two_month_stats(tkr)
        if last is None:
            lines.append(f"• SGD→{ccy}: — (no data)")
            continue
        delta = f" ({last - prev:+.4f})" if prev else ""
        high_str = f" | 2-mo high: {all_max:.4f}" if all_max else ""
        pct = f" ({last / all_max * 100:.1f}%)" if all_max else ""
        lines.append(f"• SGD→{ccy}: {last:.4f}{delta}{high_str}{pct}")
    return "\n".join(lines)


def get_checkrates_fx_to_sgd():
    now_sgt = datetime.now(timezone.utc).astimezone(SG_TZ)
    date_str = now_sgt.strftime("%Y-%m-%d %H:%M SGT")
    lines = [f"📉 *Foreign Currency → SGD* [{date_str}]", ""]
    for ccy in PAIRS:
        rate = get_market_rate(ccy, "SGD")
        if rate is None:
            lines.append(f"• {ccy}→SGD: — (no data)")
            continue
        lines.append(f"• 1 {ccy} = {rate:.4f} SGD")
    return "\n".join(lines)


# --------------- Bot commands ---------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 SGD Currency Tracker Bot\n\n"
        "Commands:\n"
        "/exchange <amount> <from> <to> <rate> [notes]\n"
        "  e.g. /exchange 120 SGD USD 0.7815\n"
        "/rate <from> <to> — get current market rate\n"
        "/checkrates — SGD → FX and FX → SGD rates\n"
        "/portfolio — your holdings summary with P&L\n"
        "/holdings — view holdings with P&L\n"
        "/sethold <CCY> <AMOUNT> [avg_cost] — set/update a holding\n"
        "/removehold <CCY> — remove a holding\n"
        "/history — last 10 trades\n"
        "/recommend — buy/sell recommendations\n"
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


async def cmd_checkrates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Fetching rates...")
    msg_sgd = get_checkrates_sgd_to_fx()
    msg_fx = get_checkrates_fx_to_sgd()
    await update.message.reply_text(msg_sgd, parse_mode="Markdown")
    await update.message.reply_text(msg_fx, parse_mode="Markdown")


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


async def cmd_recommend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analysing your trades...")
    msg = get_recommendations()
    if msg:
        await update.message.reply_text(msg, parse_mode="Markdown")
    else:
        await update.message.reply_text("No buy/sell recommendations right now.")


async def cmd_holdings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    holdings = get_holdings_with_pnl()
    if not holdings:
        await update.message.reply_text(
            "No holdings set. Use /sethold <CCY> <AMOUNT> [avg_cost] to add one, "
            "or fill in the *Holdings* sheet directly (Currency | Amount | Avg SGD Cost).",
            parse_mode="Markdown",
        )
        return
    lines = ["💼 *Your Holdings*", ""]
    for h in holdings:
        cost_note = " (est. from Trades)" if h["cost_is_estimated"] and h["avg_cost_rate"] else ""
        cost_str = f" @ avg {h['avg_cost_rate']:.4f}{cost_note}" if h["avg_cost_rate"] else " (no cost basis)"
        pnl_str = f" | P&L: {h['pnl']:+,.2f} SGD ({h['pnl_pct']:+.2f}%)" if h["pnl"] is not None else ""
        lines.append(f"• {h['ccy']}: {h['amount']:,.2f}{cost_str}{pnl_str}")
    lines.append("")
    lines.append("Use /sethold <CCY> <AMOUNT> [avg_cost] to update, /removehold <CCY> to remove.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_sethold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: /sethold <CCY> <AMOUNT> [avg_cost]\n"
            "Example: /sethold USD 93.78\n"
            "Example: /sethold USD 93.78 1.2792"
        )
        return
    ccy = args[0].upper()
    try:
        amount = float(args[1])
        avg_cost = float(args[2]) if len(args) > 2 else None
    except ValueError:
        await update.message.reply_text("Amount and avg_cost must be numbers.")
        return

    try:
        set_holding(ccy, amount, avg_cost)
    except Exception as e:
        logger.error(f"Failed to set holding: {e}")
        await update.message.reply_text(f"❌ Failed to update holding: {e}")
        return

    cost_str = f" @ avg cost {avg_cost:.4f}" if avg_cost else ""
    await update.message.reply_text(f"✅ Holdings updated: {ccy} = {amount:,.2f}{cost_str}")


async def cmd_removehold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removehold <CCY>\nExample: /removehold JPY")
        return
    ccy = args[0].upper()
    if remove_holding(ccy):
        await update.message.reply_text(f"✅ Removed {ccy} from holdings.")
    else:
        await update.message.reply_text(f"{ccy} is not in your holdings.")


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
    app.add_handler(CommandHandler("checkrates", cmd_checkrates))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("holdings", cmd_holdings))
    app.add_handler(CommandHandler("sethold", cmd_sethold))
    app.add_handler(CommandHandler("removehold", cmd_removehold))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("recommend", cmd_recommend))
    app.add_handler(CommandHandler("addpair", cmd_addpair))
    app.add_handler(CommandHandler("removepair", cmd_removepair))
    app.add_handler(CommandHandler("pairs", cmd_pairs))

    job_queue = app.job_queue
    job_queue.run_repeating(recommend_job, interval=4 * 3600, first=60)

    logger.info("Bot started — polling for messages")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
