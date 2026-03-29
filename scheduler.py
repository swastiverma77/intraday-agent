import logging
import time
import json
import asyncio
from datetime import datetime, date, timedelta

import agent_config as config
import data_engine as de
import sector_screener as ss
import candle_engine as ce
import telegram_bot as tg
import breeze_client as bc

logger = logging.getLogger(__name__)


def _now_str() -> str:
    return datetime.now().strftime("%H:%M")


def _is_trading_day() -> bool:
    return date.today().weekday() < 5


def _wait_until(target_hhmm: str, check_interval: int = 10):
    target = datetime.strptime(target_hhmm, "%H:%M").time()
    while True:
        now = datetime.now().time()
        if now >= target:
            return
        remaining = (
            datetime.combine(date.today(), target) - datetime.now()
        ).seconds
        if remaining > 60:
            logger.debug(f"Waiting for {target_hhmm} — {remaining}s remaining")
        time.sleep(check_interval)


def save_state(state: dict):
    try:
        with open(config.STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"save_state failed: {e}")


def load_state() -> dict:
    try:
        with open(config.STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def run_daily_cycle(breeze):
    if not _is_trading_day():
        logger.info("Weekend — skipping cycle")
        tg.send("📅 Today is a weekend. Agent will resume Monday.")
        return

    state = {
        "date":        str(date.today()),
        "direction":   None,
        "picks":       [],
        "baselines":   {},
        "open_trades": [],
        "trade_taken": False,
        "snapshots":   [],
    }

    ce.set_breeze(breeze)

    # Phase 1: 9:10 Pre-market
    _wait_until(config.PRE_MARKET_TIME)
    logger.info("=== Phase 1: Pre-market snapshot ===")
    try:
        snapshot = de.get_premarket_snapshot(breeze)
        tg.alert_premarket(snapshot["breadth"], snapshot["nifty_settled"])
        state["premarket"] = snapshot
        save_state(state)
    except Exception as e:
        logger.error(f"Phase 1 error: {e}")
        tg.alert_error("Pre-market snapshot", str(e))

    # Phase 2: 9:15-9:30 Live updates
    _wait_until(config.MARKET_OPEN_TIME)
    logger.info("=== Phase 2: Live market monitoring ===")

    update_times = ["09:15", "09:20", "09:25", "09:30"]
    update_num   = 0

    for t in update_times:
        _wait_until(t)
        update_num += 1
        try:
            snap = de.get_live_market_snapshot(breeze)
            state["snapshots"].append(snap)
            tg.alert_market_update(
                snap["nifty"],
                snap["sectors"],
                snap["breadth"],
                update_num
            )
            save_state(state)
        except Exception as e:
            logger.error(f"Market update {t} error: {e}")
            tg.alert_error(f"Market update {t}", str(e))

    # Phase 3: Direction + stock selection
    logger.info("=== Phase 3: Direction + stock selection ===")
    try:
        direction_result = de.determine_direction(state["snapshots"])
        direction        = direction_result["direction"]
        state["direction"] = direction

        tg.alert_direction(direction, direction_result["reasons"])

        if direction == "NO_TRADE":
            logger.info("Direction = NO_TRADE. Skipping to cutoff.")
            tg.send("⚠️ Market direction unclear. No trade setup.")
            _wait_until(config.CUTOFF_TIME)
            tg.alert_no_trade()
            return

        oi_spurts = bc.get_oi_spurts(breeze)
        state["oi_spurts"] = [s["stock"] for s in oi_spurts]

        sectors = ss.screen_sectors(breeze, direction)
        if not sectors:
            tg.send("⚠️ No aligned sectors found. No trade today.")
            save_state(state)
            return

        picks = ss.pick_stocks(breeze, sectors, direction, oi_spurts)
        if not picks:
            tg.send("⚠️ No valid stocks after gap filter. No trade today.")
            save_state(state)
            return

        state["picks"] = picks
        tg.alert_stock_picks(picks, oi_spurts)
        save_state(state)

    except Exception as e:
        logger.error(f"Phase 3 error: {e}")
        tg.alert_error("Direction/selection", str(e))
        return

    # Phase 4: Volume baseline
    logger.info("=== Phase 4: Volume baseline ===")
    baselines = {}
    for pick in picks:
        stock = pick["stock"]
        try:
            baseline = ce.build_volume_baseline(stock)
            baselines[stock] = baseline
            tg.alert_volume_baseline(stock, baseline["candles"])
        except Exception as e:
            logger.error(f"Baseline error {stock}: {e}")

    state["baselines"] = {k: v.get("min_volume") for k, v in baselines.items()}
    save_state(state)

    # Phase 5: Signal scanning 9:30 to 10:30
    logger.info("=== Phase 5: Signal scanning loop ===")
    scanner = ce.SignalScanner(picks, direction, baselines)
    cutoff  = datetime.strptime(config.CUTOFF_TIME, "%H:%M").time()

    while datetime.now().time() < cutoff:
        try:
            signals = scanner.scan_all()
            for signal in signals:
                tg.alert_signal(signal)

            if state["open_trades"]:
                state["open_trades"] = ce.monitor_open_trades(state["open_trades"])

        except Exception as e:
            logger.error(f"Scan loop error: {e}")
            tg.alert_error("Signal scan loop", str(e))

        time.sleep(30)

    # Phase 6: 10:30 cutoff
    logger.info("=== Phase 6: Cutoff ===")
    if not state["trade_taken"]:
        tg.alert_no_trade()

    save_state(state)
    logger.info("Daily cycle complete.")
