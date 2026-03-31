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


def build_volume_baseline(stock: str) -> dict:
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


class SignalScanner:
    def __init__(self, picks: list, direction: str, baselines: dict):
        self.direction = direction
        self.states    = {}

        for pick in picks:
            stock = pick["stock"]
            base  = baselines.get(stock, {})
            self.states[stock] = {
                "pick":              pick,
                "all_volumes":       [c["volume"] for c in base.get("candles", [])],
                "pending_signal":    None,
                "candles_watched":   0,       # candles watched after signal
                "trade_done":        False,
                "last_scanned_dt":   None,
            }

    def scan_all(self) -> list:
        signals  = []
        today    = date.today()
        now      = datetime.now()
        from_dt  = datetime.combine(today, datetime.strptime("09:30", "%H:%M").time())

        for stock, state in self.states.items():
            if state["trade_done"]:
                continue

            candles = bc.get_5min_candles(_breeze, stock, from_dt, now)
            if not candles:
                continue

            # Only process completed candles (exclude currently forming one)
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
        """
        Two modes:
        1. No pending signal → look for new signal candle
        2. Pending signal → watch for entry trigger or cancel conditions
        """
        vol    = candle["volume"]
        high   = candle["high"]
        low    = candle["low"]
        close  = candle["close"]
        open_  = candle["open"]
        is_red = close < open_
        is_grn = close > open_

        # ── Mode 2: Pending signal — check cancel conditions ──────────────────
        if state["pending_signal"]:
            sig           = state["pending_signal"]
            sig_high      = sig["candle_high"]
            sig_low       = sig["candle_low"]
            sig_vol       = sig["candle_volume"]
            candles_watched = state["candles_watched"] + 1
            state["candles_watched"] = candles_watched

            cancel_reason = None

            if self.direction == "BUY":
                # Cancel: red candle closes BELOW signal candle low
                if is_red and close < sig_low:
                    cancel_reason = f"Red candle closed below signal low {sig_low:.2f}"
                # Cancel: new candle has lower volume than signal candle
                elif vol < sig_vol:
                    cancel_reason = f"New candle vol {vol:,} < signal vol {sig_vol:,}"

            else:  # SELL
                # Cancel: green candle closes ABOVE signal candle high
                if is_grn and close > sig_high:
                    cancel_reason = f"Green candle closed above signal high {sig_high:.2f}"
                # Cancel: new candle has lower volume than signal candle
                elif vol < sig_vol:
                    cancel_reason = f"New candle vol {vol:,} < signal vol {sig_vol:,}"

            # Cancel: exceeded max watch window (3 candles)
            if candles_watched >= 3 and not cancel_reason:
                cancel_reason = "Entry not triggered within 3 candles"

            if cancel_reason:
                tg.alert_signal_cancelled(stock, cancel_reason)
                state["pending_signal"]  = None
                state["candles_watched"] = 0
                state["all_volumes"].append(vol)
                # Fall through to check if THIS candle is a new signal
            else:
                # Still within window — check if limit was triggered
                entry = sig["entry"]
                if self.direction == "BUY" and high >= entry:
                    logger.info(f"{stock} BUY entry triggered @ {entry}")
                    state["trade_done"] = True
                    tg.send(
                        f"🟢 <b>ENTRY TRIGGERED — {stock}</b>\n"
                        f"BUY limit hit @ ₹{entry:,.2f}\n"
                        f"SL: ₹{sig['sl']:,.2f} | Target: ₹{sig['target']:,.2f}"
                    )
                    return None

                elif self.direction == "SELL" and low <= entry:
                    logger.info(f"{stock} SELL entry triggered @ {entry}")
                    state["trade_done"] = True
                    tg.send(
                        f"🔴 <b>ENTRY TRIGGERED — {stock}</b>\n"
                        f"SELL limit hit @ ₹{entry:,.2f}\n"
                        f"SL: ₹{sig['sl']:,.2f} | Target: ₹{sig['target']:,.2f}"
                    )
                    return None

                state["all_volumes"].append(vol)
                return None

        # ── Mode 1: No pending signal — scan for new signal candle ───────────
        is_signal_color = (self.direction == "BUY" and is_red) or \
                          (self.direction == "SELL" and is_grn)

        if not is_signal_color:
            state["all_volumes"].append(vol)
            return None

        # Volume must be lower than ALL previous candles
        if state["all_volumes"] and vol >= min(state["all_volumes"]):
            state["all_volumes"].append(vol)
            return None

        # ── Signal candle found! ──────────────────────────────────────────────
        state["all_volumes"].append(vol)

        if self.direction == "BUY":
            entry  = round(high + 0.05, 2)
            sl     = round(low  - 0.05, 2)
            risk   = entry - sl
            target = round(entry + risk * config.RISK_REWARD_RATIO, 2)
        else:
            entry  = round(low  - 0.05, 2)
            sl     = round(high + 0.05, 2)
            risk   = sl - entry
            target = round(entry - risk * config.RISK_REWARD_RATIO, 2)

        quantity = max(1, int(config.CAPITAL_PER_TRADE / entry))

        try:
            dt    = datetime.fromisoformat(candle["datetime"])
            ctime = dt.strftime("%H:%M")
        except Exception:
            ctime = str(candle["datetime"])

        signal = {
            "stock":         stock,
            "direction":     self.direction,
            "entry":         entry,
            "sl":            sl,
            "target":        target,
            "quantity":      quantity,
            "candle_time":   ctime,
            "candle_volume": vol,
            "candle_high":   high,
            "candle_low":    low,
        }

        state["pending_signal"]  = signal
        state["candles_watched"] = 0
        logger.info(f"Signal: {self.direction} {stock} entry={entry} sl={sl} target={target} vol={vol}")
        return signal

    def mark_trade_done(self, stock: str):
        if stock in self.states:
            self.states[stock]["trade_done"] = True


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
