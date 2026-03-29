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
                "pick":            pick,
                "min_volume":      base.get("min_volume", float("inf")),
                "all_volumes":     [c["volume"] for c in base.get("candles", [])],
                "pending_signal":  None,
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

            candles = bc.get_5min_candles(_breeze, stock, from_dt, now)
            if not candles:
                continue

            completed = candles[:-1] if len(candles) > 1 else candles

            for candle in completed:
                dt_str = candle["datetime"]
                if dt_str == state["last_scanned_dt"]:
                    continue
                state["last_scanned_dt"] = dt_str
                signal = self._evaluate_candle(stock, state, candle)
                if signal:
                    signals.append(signal)

        return signals

    def _evaluate_candle(self, stock: str, state: dict, candle: dict):
        vol    = candle["volume"]
        is_red = candle["close"] < candle["open"]
        is_grn = candle["close"] > candle["open"]

        if state["pending_signal"]:
            if self.direction == "BUY" and is_red:
                tg.alert_signal_cancelled(
                    stock,
                    "New red candle before entry — resetting"
                )
                state["pending_signal"] = None
            elif self.direction == "SELL" and is_grn:
                tg.alert_signal_cancelled(
                    stock,
                    "New green candle before entry — resetting"
                )
                state["pending_signal"] = None

        is_signal_color = (self.direction == "BUY" and is_red) or \
                          (self.direction == "SELL" and is_grn)

        if not is_signal_color:
            state["all_volumes"].append(vol)
            return None

        if state["all_volumes"] and vol >= min(state["all_volumes"]):
            state["all_volumes"].append(vol)
            return None

        state["all_volumes"].append(vol)

        high = candle["high"]
        low  = candle["low"]

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

        state["pending_signal"] = signal
        logger.info(f"Signal: {self.direction} {stock} entry={entry} sl={sl} target={target}")
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
