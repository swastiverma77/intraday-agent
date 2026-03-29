import logging
import asyncio
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

import agent_config as config

logger = logging.getLogger(__name__)

_bot: Bot = None
_app: Application = None
_pending_signals: dict = {}


def init_bot():
    global _bot, _app
    _bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    _app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    _app.add_handler(CallbackQueryHandler(handle_confirm_callback))
    logger.info("Telegram bot initialised")
    return _app


async def _send(text: str, reply_markup=None, parse_mode="HTML"):
    global _bot
    if _bot is None:
        _bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    await _bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )


def send(text: str, reply_markup=None):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_send(text, reply_markup))
        else:
            loop.run_until_complete(_send(text, reply_markup))
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")


def alert_premarket(breadth: dict, nifty_settled: float):
    adv   = breadth["advances"]
    dec   = breadth["declines"]
    unc   = breadth["unchanged"]
    ratio = breadth["adv_dec_ratio"]
    bias  = "🟢 Bullish lean" if adv > dec else "🔴 Bearish lean" if dec > adv else "⚪ Neutral"
    text  = (
        f"📊 <b>PRE-MARKET SNAPSHOT — {_now()}</b>\n"
        f"{'─' * 32}\n"
        f"🔼 Advances : <b>{adv}</b>\n"
        f"🔽 Declines : <b>{dec}</b>\n"
        f"➡️  Unchanged: <b>{unc}</b>\n"
        f"📐 Adv/Dec  : <b>{ratio}</b>\n\n"
        f"📌 Nifty 50 Settled: <b>₹{nifty_settled:,.2f}</b>\n"
        f"🧭 Bias: {bias}\n"
    )
    send(text)


def alert_market_update(nifty_data: dict, sector_data: list, breadth: dict, update_num: int):
    adv   = breadth["advances"]
    dec   = breadth["declines"]
    sector_lines = ""
    for s in sector_data[:6]:
        arrow = "🟢" if s["change_pct"] >= 0 else "🔴"
        sector_lines += f"  {arrow} {s['name']:<12} {s['change_pct']:+.2f}%\n"
    text = (
        f"📡 <b>MARKET UPDATE #{update_num} — {_now()}</b>\n"
        f"{'─' * 32}\n"
        f"📈 Nifty 50 : <b>{nifty_data.get('ltp', 0):,.2f}</b>  "
        f"({nifty_data.get('change_pct', 0):+.2f}%)\n\n"
        f"<b>Sector Snapshot:</b>\n"
        f"{sector_lines}\n"
        f"📊 Breadth  : 🔼{adv} / 🔽{dec}\n"
    )
    send(text)


def alert_direction(direction: str, reasons: list):
    icon = "📈" if direction == "BUY" else "📉"
    reasons_text = "\n".join(f"  • {r}" for r in reasons)
    text = (
        f"{icon} <b>DIRECTION CALL — {_now()}</b>\n"
        f"{'─' * 32}\n"
        f"Direction: <b>{direction}</b>\n\n"
        f"<b>Reasoning:</b>\n{reasons_text}\n"
    )
    send(text)


def alert_stock_picks(picks: list, oi_spurts: list):
    oi_stocks = {s["stock"] for s in oi_spurts}
    lines = ""
    for p in picks:
        oi_tag = "✅ OI Spurt" if p["stock"] in oi_stocks else "⬜ No OI data"
        lines += (
            f"\n🏷 <b>{p['stock']}</b> ({p['sector']})\n"
            f"   Gap: {p['gap_pct']:+.2f}% | LTP: ₹{p['ltp']:,.2f}\n"
            f"   {oi_tag}\n"
        )
    text = (
        f"🎯 <b>STOCK PICKS — {_now()}</b>\n"
        f"{'─' * 32}"
        f"{lines}"
    )
    send(text)


def alert_volume_baseline(stock: str, candles: list):
    lines = ""
    for i, c in enumerate(candles, 1):
        marker = " ← lowest" if c.get("is_lowest") else ""
        lines += f"  Candle {i} ({c['time']}): Vol {c['volume']:,}{marker}\n"
    text = (
        f"🕯 <b>VOLUME BASELINE — {stock}</b>\n"
        f"{'─' * 32}\n"
        f"{lines}"
        f"\n📌 Watching for lowest-vol signal candle from 9:30..."
    )
    send(text)


def alert_signal(signal: dict) -> str:
    direction = signal["direction"]
    stock     = signal["stock"]
    entry     = signal["entry"]
    sl        = signal["sl"]
    target    = signal["target"]
    candle_t  = signal["candle_time"]
    vol       = signal["candle_volume"]
    qty       = signal["quantity"]
    risk      = abs(entry - sl) * qty
    reward    = abs(target - entry) * qty
    icon      = "📈" if direction == "BUY" else "📉"
    callback_key = f"trade_{stock}_{int(datetime.now().timestamp())}"
    _pending_signals[callback_key] = signal

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ CONFIRM TRADE", callback_data=f"confirm_{callback_key}"),
            InlineKeyboardButton("❌ SKIP",          callback_data=f"skip_{callback_key}"),
        ]
    ])
    text = (
        f"{icon} <b>TRADE SIGNAL — {_now()}</b>\n"
        f"{'─' * 32}\n"
        f"Stock     : <b>{stock}</b>\n"
        f"Direction : <b>{direction}</b>\n\n"
        f"🕯 Signal Candle: {candle_t}  Vol: {vol:,}\n"
        f"{'─' * 32}\n"
        f"📥 Entry  : <b>₹{entry:,.2f}</b>  "
        f"{'(above candle high)' if direction == 'BUY' else '(below candle low)'}\n"
        f"🛑 SL     : <b>₹{sl:,.2f}</b>\n"
        f"🎯 Target : <b>₹{target:,.2f}</b>  (1:2 R:R)\n"
        f"{'─' * 32}\n"
        f"📦 Qty    : {qty} shares\n"
        f"💸 Risk   : ₹{risk:,.0f}   Reward: ₹{reward:,.0f}\n"
    )
    send(text, reply_markup=keyboard)
    return callback_key


def alert_signal_cancelled(stock: str, reason: str):
    text = (
        f"🚫 <b>SIGNAL CANCELLED — {stock}</b>\n"
        f"{'─' * 32}\n"
        f"Reason: {reason}\n"
        f"🔍 Scanning for next valid candle...\n"
    )
    send(text)


def alert_order_placed(stock, direction, qty, price, order_id):
    text = (
        f"✅ <b>ORDER PLACED — {_now()}</b>\n"
        f"{'─' * 32}\n"
        f"Stock     : <b>{stock}</b>\n"
        f"Direction : <b>{direction}</b>\n"
        f"Qty       : {qty} @ ₹{price:,.2f}\n"
        f"Order ID  : <code>{order_id}</code>\n"
    )
    send(text)


def alert_order_failed(stock, error):
    text = (
        f"❗ <b>ORDER FAILED — {stock}</b>\n"
        f"Error: {error}\n"
        f"Please place manually.\n"
    )
    send(text)


def alert_no_trade():
    text = (
        f"⏰ <b>NO TRADE TODAY — {_now()}</b>\n"
        f"{'─' * 32}\n"
        f"No qualifying signal found before 10:30 AM.\n"
        f"Agent will restart tomorrow at 9:10 AM. 🔄\n"
    )
    send(text)


def alert_target_hit(stock, direction, target, pnl):
    text = (
        f"🎉 <b>TARGET HIT — {stock}</b>\n"
        f"Direction: {direction} | Target: ₹{target:,.2f}\n"
        f"Approx P&L: <b>+₹{pnl:,.0f}</b> 🟢\n"
    )
    send(text)


def alert_sl_hit(stock, direction, sl, pnl):
    text = (
        f"🛑 <b>STOP LOSS HIT — {stock}</b>\n"
        f"Direction: {direction} | SL: ₹{sl:,.2f}\n"
        f"Approx P&L: <b>-₹{abs(pnl):,.0f}</b> 🔴\n"
    )
    send(text)


def alert_error(context, error):
    text = f"⚠️ <b>AGENT ERROR</b>\n{context}\n<code>{error}</code>"
    send(text)


async def handle_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from candle_engine import execute_confirmed_trade
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("confirm_"):
        key    = data[len("confirm_"):]
        signal = _pending_signals.pop(key, None)
        if signal:
            await query.edit_message_text(
                text=f"✅ Trade confirmed for <b>{signal['stock']}</b>. Placing order...",
                parse_mode="HTML"
            )
            asyncio.ensure_future(execute_confirmed_trade(signal))
        else:
            await query.edit_message_text("⚠️ Signal expired or already actioned.")

    elif data.startswith("skip_"):
        key    = data[len("skip_"):]
        signal = _pending_signals.pop(key, None)
        stock  = signal["stock"] if signal else "Unknown"
        await query.edit_message_text(
            text=f"❌ Trade skipped for <b>{stock}</b>.",
            parse_mode="HTML"
        )


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")
