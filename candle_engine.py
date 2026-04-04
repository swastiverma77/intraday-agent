import logging
import asyncio
from datetime import datetime, date
from breeze_connect import BreezeConnect

import breeze_client as bc
import telegram_bot as tg
import agent_config as config

logger = logging.getLogger(__name__)

_breeze: BreezeConnect = None


def set_breeze(b: BreezeConnect):
    global _breeze
    _breeze = b


def get_candle_at(stock: str, candle_time: str) -> dict:
    """Fetch a specific 5-min candle at given time (HH:MM)."""
    today   = date.today()
    from_dt = datetime.combine(today, datetime.strptime(candle_time, "%H:%M").time())
    to_dt   = datetime.combine(today, datetime.strptime(
        f"{int(candle_time[:2]):02d}:{int(candle_time[3:])+5:02d}", "%H:%M"
    ).time())
    candles = bc.get_5min_candles(_breeze, stock, from_dt, to_dt)
    return candles[0] if candles else None


def get_prev_day_high_low(stock: str) -> tuple:
    """Returns (prev_day_high, prev_day_low) from quote data."""
    quote = bc.get_ltp(_breeze, stock)
    if not quote:
        return 0, 0
    # prev_close is available; for prev high/low use historical
    from datetime import timedelta
    yesterday = date.today() - timedelta(days=1)
    # Skip weekends
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)
    from_dt = datetime.combine(yesterday, datetime.strptime("09:15", "%H:%M").time())
    to_dt   = datetime.combine(yesterday, datetime.strptime("15:30", "%H:%M").time())
    candles = bc.get_5min_candles(_breeze, stock, from_dt, to_dt)
    if not candles:
        return 0, 0
    prev_high = max(c["high"] for c in candles)
    prev_low  = min(c["low"]  for c in candles)
    return prev_high, prev_low


def get_current_day_high_low(stock: str) -> tuple:
    """Returns (current_day_high, current_day_low) from 9:15 to now."""
    today   = date.today()
    from_dt = datetime.combine(today, datetime.strptime("09:15", "%H:%M").time())
    to_dt   = datetime.now()
    candles = bc.get_5min_candles(_breeze, stock, from_dt, to_dt)
    if not candles:
        return 0, 0
    day_high = max(c["high"] for c in candles)
    day_low  = min(c["low"]  for c in candles)
    return day_high, day_low


def build_volume_baseline(stock: str) -> dict:
    """Track volumes from 9:15 onwards."""
    today   = date.today()
    from_dt = datetime.combine(today, datetime.strptime("09:15", "%H:%M").time())
    to_dt   = datetime.combine(today, datetime.strptime("09:30", "%H:%M").time())

    candles = bc.get_5min_candles(_breeze, stock, from_dt, to_dt)
    if not candles:
        logger.warning(f"No baseline candles for {stock}")
        return {"candles": [], "min_volume": 0, "min_candle_index": -1}

    min_vol = min(c["volume"] for c in candles)
    min_idx = next(i for i, c in enumerate(candles) if c["volume"] == min_vol)

    for i, c in enumerate(candles):
        c["is_lowest"] = (i == min_idx)
        try:
            dt = datetime.fromisoformat(c["datetime"])
            c["time"] = dt.strftime("%H:%M")
        except Exception:
            c["time"] = str(c["datetime"])

    logger.info(f"{stock} baseline: {[c['volume'] for c in candles]} min={min_vol}")
    return {
        "candles":          candles,
        "min_volume":       min_vol,
        "min_candle_index": min_idx,
    }


# ── Main Trade Engine (9:20) ──────────────────────────────────────────────────

class MainTradeScanner:
    """
    Runs at exactly 9:20 AM.
    Checks Candle1 (9:15) and Candle2 (9:20) for breakout setup.
    LONG:  Both candles bullish + Candle2 High > Prev Day High
    SHORT: Both candles bearish + Candle2 Low  < Prev Day Low
    """

    def __init__(self, picks: list, direction: str):
        self.picks     = picks
        self.direction = direction
        self.signals   = []

    def scan(self) -> list:
        signals = []
        for pick in self.picks:
            stock = pick["stock"]
            signal = self._check_main_trade(stock)
            if signal:
                signals.append(signal)
        return signals

    def _check_main_trade(self, stock: str) -> dict:
        today = date.today()

        # Fetch candle 1 (9:15) and candle 2 (9:20)
        from_dt  = datetime.combine(today, datetime.strptime("09:15", "%H:%M").time())
        to_dt    = datetime.combine(today, datetime.strptime("09:25", "%H:%M").time())
        candles  = bc.get_5min_candles(_breeze, stock, from_dt, to_dt)

        if len(candles) < 2:
            logger.info(f"{stock} main trade: insufficient candles ({len(candles)})")
            return None

        c1 = candles[0]  # 9:15 candle
        c2 = candles[1]  # 9:20 candle

        c1_bull = c1["close"] > c1["open"]
        c1_bear = c1["close"] < c1["open"]
        c2_bull = c2["close"] > c2["open"]
        c2_bear = c2["close"] < c2["open"]

        prev_high, prev_low = get_prev_day_high_low(stock)
        if prev_high == 0:
            logger.warning(f"{stock} no prev day data")
            return None

        risk_reward = config.RISK_REWARD_RATIO

        # LONG setup
        if self.direction == "BUY":
            if c1_bull and c2_bull and c2["high"] > prev_high:
                entry  = round(c2["high"] + 0.05, 2)
                sl     = round(c2["low"]  - 0.05, 2)
                risk   = entry - sl
                if risk <= 0:
                    return None
                qty    = max(1, int(config.RISK_PER_TRADE / risk))
                target = round(entry + risk * risk_reward, 2)
                logger.info(f"MAIN LONG {stock}: entry={entry} sl={sl} target={target} prev_high={prev_high}")
                return {
                    "stock":       stock,
                    "direction":   "BUY",
                    "setup":       "MAIN_TRADE",
                    "entry":       entry,
                    "sl":          sl,
                    "target":      target,
                    "quantity":    qty,
                    "candle_time": "09:20",
                    "candle_high": c2["high"],
                    "candle_low":  c2["low"],
                    "prev_high":   prev_high,
                    "prev_low":    prev_low,
                }

        # SHORT setup
        elif self.direction == "SELL":
            if c1_bear and c2_bear and c2["low"] < prev_low:
                entry  = round(c2["low"]  - 0.05, 2)
                sl     = round(c2["high"] + 0.05, 2)
                risk   = sl - entry
                if risk <= 0:
                    return None
                qty    = max(1, int(config.RISK_PER_TRADE / risk))
                target = round(entry - risk * risk_reward, 2)
                logger.info(f"MAIN SHORT {stock}: entry={entry} sl={sl} target={target} prev_low={prev_low}")
                return {
                    "stock":       stock,
                    "direction":   "SELL",
                    "setup":       "MAIN_TRADE",
                    "entry":       entry,
                    "sl":          sl,
                    "target":      target,
                    "quantity":    qty,
                    "candle_time": "09:20",
                    "candle_high": c2["high"],
                    "candle_low":  c2["low"],
                    "prev_high":   prev_high,
                    "prev_low":    prev_low,
                }

        return None


# ── Low Volume Engine (9:30–10:30) ────────────────────────────────────────────

class LowVolumeScanner:
    """
    Scans for lowest volume candle after 9:30.
    BUY:  red candle + lowest volume + candle high > current day high
    SELL: green candle + lowest volume + candle low < current day low
    Cancel if: boundary breach OR new lower volume candle OR 3 candles elapsed
    """

    def __init__(self, picks: list, direction: str, baselines: dict):
        self.direction = direction
        self.states    = {}

        for pick in picks:
            stock = pick["stock"]
            base  = baselines.get(stock, {})
            self.states[stock] = {
                "pick":            pick,
                "all_volumes":     [c["volume"] for c in base.get("candles", [])],
                "pending_signal":  None,
                "candles_watched": 0,
                "trade_done":      False,
                "last_scanned_dt": None,
            }

    def scan_all(self) -> list:
        signals = []
        today   = date.today()
        now     = datetime.now()
        from_dt = datetime.combine(today, datetime.strptime("09:30", "%H:%M").time())

        for stock, state in self.states.items():
            if state["trade_done"]:
                continue

            candles   = bc.get_5min_candles(_breeze, stock, from_dt, now)
            if not candles:
                continue

            completed = candles[:-1] if len(candles) > 1 else candles

            for candle in completed:
                dt_str = candle["datetime"]
                if dt_str == state["last_scanned_dt"]:
                    continue
                state["last_scanned_dt"] = dt_str

                result = self._process_candle(stock, state, candle)
                if result:
                    signals.append(result)

        return signals

    def _process_candle(self, stock: str, state: dict, candle: dict):
        vol    = candle["volume"]
        high   = candle["high"]
        low    = candle["low"]
        close  = candle["close"]
        open_  = candle["open"]
        is_red = close < open_
        is_grn = close > open_

        # ── Pending signal: check cancel or entry trigger ─────────────────────
        if state["pending_signal"]:
            sig             = state["pending_signal"]
            sig_high        = sig["candle_high"]
            sig_low         = sig["candle_low"]
            sig_vol         = sig["candle_volume"]
            candles_watched = state["candles_watched"] + 1
            state["candles_watched"] = candles_watched

            cancel_reason = None

            if self.direction == "BUY":
                if is_red and close < sig_low:
                    cancel_reason = f"Red candle closed below signal low {sig_low:.2f}"
                elif vol < sig_vol:
                    cancel_reason = f"New lower volume candle {vol:,} < {sig_vol:,}"
            else:
                if is_grn and close > sig_high:
                    cancel_reason = f"Green candle closed above signal high {sig_high:.2f}"
                elif vol < sig_vol:
                    cancel_reason = f"New lower volume candle {vol:,} < {sig_vol:,}"

            if candles_watched >= 3 and not cancel_reason:
                cancel_reason = "Entry not triggered within 3 candles"

            if cancel_reason:
                tg.alert_signal_cancelled(stock, cancel_reason)
                state["pending_signal"]  = None
                state["candles_watched"] = 0
                state["all_volumes"].append(vol)
            else:
                entry = sig["entry"]
                if self.direction == "BUY" and high >= entry:
                    logger.info(f"{stock} LV BUY entry triggered @ {entry}")
                    state["trade_done"] = True
                    tg.send(
                        f"🟢 <b>ENTRY TRIGGERED — {stock}</b>\n"
                        f"Low Volume BUY @ ₹{entry:,.2f}\n"
                        f"SL: ₹{sig['sl']:,.2f} | Target: ₹{sig['target']:,.2f}"
                    )
                    return None
                elif self.direction == "SELL" and low <= entry:
                    logger.info(f"{stock} LV SELL entry triggered @ {entry}")
                    state["trade_done"] = True
                    tg.send(
                        f"🔴 <b>ENTRY TRIGGERED — {stock}</b>\n"
                        f"Low Volume SELL @ ₹{entry:,.2f}\n"
                        f"SL: ₹{sig['sl']:,.2f} | Target: ₹{sig['target']:,.2f}"
                    )
                    return None
                state["all_volumes"].append(vol)
                return None

        # ── No pending signal: look for low volume signal candle ─────────────
        is_signal_color = (self.direction == "BUY" and is_red) or \
                          (self.direction == "SELL" and is_grn)

        if not is_signal_color:
            state["all_volumes"].append(vol)
            return None

        # Volume must be lower than ALL previous candles
        if state["all_volumes"] and vol >= min(state["all_volumes"]):
            state["all_volumes"].append(vol)
            return None

        # Get current day high/low for additional condition
        day_high, day_low = get_current_day_high_low(stock)

        # BUY: candle high must be > current day high
        if self.direction == "BUY" and high <= day_high:
            logger.info(f"{stock} LV BUY skipped — candle high {high} not > day high {day_high}")
            state["all_volumes"].append(vol)
            return None

        # SELL: candle low must be < current day low
        if self.direction == "SELL" and low >= day_low:
            logger.info(f"{stock} LV SELL skipped — candle low {low} not < day low {day_low}")
            state["all_volumes"].append(vol)
            return None

        # Signal candle found!
        state["all_volumes"].append(vol)

        if self.direction == "BUY":
            entry  = round(high + 0.05, 2)
            sl     = round(low  - 0.05, 2)
            risk   = entry - sl
            if risk <= 0:
                return None
            qty    = max(1, int(config.RISK_PER_TRADE / risk))
            target = round(entry + risk * config.RISK_REWARD_RATIO, 2)
        else:
            entry  = round(low  - 0.05, 2)
            sl     = round(high + 0.05, 2)
            risk   = sl - entry
            if risk <= 0:
                return None
            qty    = max(1, int(config.RISK_PER_TRADE / risk))
            target = round(entry - risk * config.RISK_REWARD_RATIO, 2)

        try:
            dt    = datetime.fromisoformat(candle["datetime"])
            ctime = dt.strftime("%H:%M")
        except Exception:
            ctime = str(candle["datetime"])

        signal = {
            "stock":         stock,
            "direction":     self.direction,
            "setup":         "LOW_VOLUME",
            "entry":         entry,
            "sl":            sl,
            "target":        target,
            "quantity":      qty,
            "candle_time":   ctime,
            "candle_volume": vol,
            "candle_high":   high,
            "candle_low":    low,
            "day_high":      day_high,
            "day_low":       day_low,
        }

        state["pending_signal"]  = signal
        state["candles_watched"] = 0
        logger.info(f"LV Signal: {self.direction} {stock} entry={entry} sl={sl} target={target}")
        return signal

    def mark_trade_done(self, stock: str):
        if stock in self.states:
            self.states[stock]["trade_done"] = True


# ── Trade execution (called from Telegram confirm callback) ───────────────────

async def execute_confirmed_trade(signal: dict):
    stock     = signal["stock"]
    direction = signal["direction"].lower()
    quantity  = signal["quantity"]
    entry     = signal["entry"]

    logger.info(f"Executing: {direction} {quantity} {stock} @ {entry}")
    resp = bc.place_limit_order(_breeze, stock, direction, quantity, entry)

    if resp and not resp.get("error"):
        success  = resp.get("Success", {})
        order_id = success.get("order_id", "N/A") if isinstance(success, dict) else "N/A"
        tg.alert_order_placed(stock, direction.upper(), quantity, entry, str(order_id))
    else:
        err = resp.get("error", "Unknown error") if resp else "No response"
        tg.alert_order_failed(stock, err)


def monitor_open_trades(open_trades: list) -> list:
    remaining = []
    for trade in open_trades:
        stock = trade["stock"]
        quote = bc.get_ltp(_breeze, stock)
        if not quote:
            remaining.append(trade)
            continue

        ltp  = quote["ltp"]
        qty  = trade["quantity"]
        dir_ = trade["direction"]

        if dir_ == "BUY":
            if ltp >= trade["target"]:
                pnl = (trade["target"] - trade["entry"]) * qty
                tg.alert_target_hit(stock, dir_, trade["target"], pnl)
                continue
            elif ltp <= trade["sl"]:
                pnl = (trade["sl"] - trade["entry"]) * qty
                tg.alert_sl_hit(stock, dir_, trade["sl"], pnl)
                continue
        else:
            if ltp <= trade["target"]:
                pnl = (trade["entry"] - trade["target"]) * qty
                tg.alert_target_hit(stock, dir_, trade["target"], pnl)
                continue
            elif ltp >= trade["sl"]:
                pnl = (trade["entry"] - trade["sl"]) * qty
                tg.alert_sl_hit(stock, dir_, trade["sl"], pnl)
                continue

        remaining.append(trade)
    return remaining