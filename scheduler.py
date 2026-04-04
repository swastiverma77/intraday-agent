import logging
import time
import json
from datetime import datetime, date, timedelta

import agent_config as config
import data_engine as de
import sector_screener as ss
import candle_engine as ce
import telegram_bot as tg
import breeze_client as bc

logger = logging.getLogger(__name__)

# Trading mode
MODE_BULLISH   = "BULLISH"
MODE_BEARISH   = "BEARISH"
MODE_LOW_VOL   = "LOW_VOLUME"
MODE_NONE      = "NONE"


def _now_str() -> str:
    return datetime.now().strftime("%H:%M")


def _is_trading_day() -> bool:
    from holidays import is_trading_day
    trading, reason = is_trading_day()
    if not trading:
        logger.info(f"Not a trading day: {reason}")
    return trading


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


def _ensure_session(breeze, reinit_func):
    if not bc.is_session_valid(breeze):
        logger.warning("Session expired — re-logging in...")
        tg.send("⚠️ Session expired. Re-logging in...")
        try:
            new_breeze = reinit_func()
            ce.set_breeze(new_breeze)
            return new_breeze
        except Exception as e:
            logger.error(f"Re-login failed: {e}")
            tg.alert_error("Re-login failed", str(e))
    return breeze


def _get_picks(breeze, direction, oi_spurts):
    """Get sector + stock picks aligned with direction."""
    sectors = ss.screen_sectors(breeze, direction)
    if not sectors:
        return []
    picks = ss.pick_stocks(breeze, sectors, direction, oi_spurts)
    return picks


def run_daily_cycle(breeze):
    if not _is_trading_day():
        from holidays import is_trading_day
        _, reason = is_trading_day()
        tg.send(f"📅 Market closed today — {reason}. Agent resumes next trading day.")
        return

    state = {
        "date":          str(date.today()),
        "mode":          MODE_NONE,
        "trades_taken":  0,
        "open_trades":   [],
        "snapshots":     [],
    }

    ce.set_breeze(breeze)

    # ── Phase 1: 9:00 — Pre-market scan ──────────────────────────────────────
    _wait_until(config.PRE_MARKET_TIME)
    logger.info("=== Phase 1: Pre-market snapshot ===")
    try:
        snapshot = de.get_premarket_snapshot(breeze)
        tg.alert_premarket(snapshot["breadth"], snapshot["nifty_settled"])
        save_state(state)
    except Exception as e:
        logger.error(f"Phase 1 error: {e}")
        tg.alert_error("Pre-market snapshot", str(e))

    # ── Phase 2: 9:15 — Market open + sentiment every 5 min ──────────────────
    _wait_until("09:15")
    logger.info("=== Phase 2: Market sentiment ===")

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

    # ── Phase 3: Determine direction + mode ───────────────────────────────────
    logger.info("=== Phase 3: Direction + mode ===")
    breeze = _ensure_session(breeze, bc.init_breeze)
    ce.set_breeze(breeze)

    try:
        direction_result = de.determine_direction(state["snapshots"])
        direction        = direction_result["direction"]
        nifty_pct        = state["snapshots"][-1]["nifty"].get("change_pct", 0) if state["snapshots"] else 0
        adv              = state["snapshots"][-1]["breadth"]["advances"] if state["snapshots"] else 0
        dec              = state["snapshots"][-1]["breadth"]["declines"] if state["snapshots"] else 0

        # Determine mode per strategy rules
        if adv > dec and nifty_pct > 0:
            mode = MODE_BULLISH
        elif dec > adv and nifty_pct < 0:
            mode = MODE_BEARISH
        else:
            mode = MODE_LOW_VOL  # divergence = low volume mode

        state["mode"] = mode
        tg.alert_direction(direction, direction_result["reasons"])
        tg.send(f"🎯 <b>Mode: {mode}</b>")
        logger.info(f"Mode set to: {mode}")

        if direction == "NO_TRADE":
            tg.send("⚠️ No clear direction. Switching to LOW_VOLUME mode.")
            mode = MODE_LOW_VOL
            state["mode"] = mode

        oi_spurts = bc.get_oi_spurts(breeze)
        picks     = _get_picks(breeze, direction if direction != "NO_TRADE" else "BUY", oi_spurts)

        if not picks:
            tg.send("⚠️ No valid stocks found. Switching to LOW_VOLUME mode.")
            mode = MODE_LOW_VOL
            state["mode"] = mode

        save_state(state)

    except Exception as e:
        logger.error(f"Phase 3 error: {e}")
        tg.alert_error("Direction/selection", str(e))
        return

    # ── Phase 4: 9:20 — Main Trade Engine ────────────────────────────────────
    if mode != MODE_LOW_VOL and state["trades_taken"] < config.MAX_TRADES:
        _wait_until("09:20")
        logger.info("=== Phase 4: Main Trade Engine ===")
        try:
            main_scanner = ce.MainTradeScanner(picks, direction)
            main_signals = main_scanner.scan()

            if main_signals:
                for signal in main_signals:
                    tg.alert_signal(signal)
                    state["trades_taken"] += 1
                logger.info(f"Main trade signals fired: {len(main_signals)}")
            else:
                tg.send("📊 No Main Trade setup at 9:20. Switching to LOW_VOLUME mode.")
                mode = MODE_LOW_VOL
                state["mode"] = mode

        except Exception as e:
            logger.error(f"Phase 4 error: {e}")
            tg.alert_error("Main Trade Engine", str(e))
            mode = MODE_LOW_VOL
            state["mode"] = mode

    # ── Phase 5: 9:25–9:35 — Sector Rotation Check ───────────────────────────
    if mode != MODE_LOW_VOL:
        _wait_until("09:25")
        logger.info("=== Phase 5: Sector Rotation Check ===")
        try:
            # Re-fetch sector data and compare direction
            snap_new    = de.get_live_market_snapshot(breeze)
            sector_data = snap_new.get("sectors", [])

            # Check if top sectors have reversed direction
            top_sectors = sector_data[:3]
            reversals   = 0
            for s in top_sectors:
                chg = s.get("change_pct", 0)
                if direction == "BUY" and chg < 0:
                    reversals += 1
                elif direction == "SELL" and chg > 0:
                    reversals += 1

            if reversals >= 2:
                tg.send(
                    f"🔄 <b>Sector Rotation Detected</b>\n"
                    f"{reversals} of top 3 sectors reversed direction.\n"
                    f"Switching to LOW_VOLUME mode."
                )
                mode = MODE_LOW_VOL
                state["mode"] = mode
                logger.info("Sector rotation detected — switching to LOW_VOLUME")

        except Exception as e:
            logger.error(f"Phase 5 sector rotation error: {e}")

    # ── Phase 6: Volume baseline for Low Volume engine ────────────────────────
    logger.info("=== Phase 6: Volume baseline ===")
    baselines = {}
    lv_picks  = picks if picks else []

    if not lv_picks:
        try:
            lv_dir    = direction if direction != "NO_TRADE" else "BUY"
            oi_spurts = bc.get_oi_spurts(breeze)
            lv_picks  = _get_picks(breeze, lv_dir, oi_spurts)
        except Exception as e:
            logger.error(f"LV picks error: {e}")

    for pick in lv_picks:
        stock = pick["stock"]
        try:
            baseline = ce.build_volume_baseline(stock)
            baselines[stock] = baseline
            tg.alert_volume_baseline(stock, baseline["candles"])
        except Exception as e:
            logger.error(f"Baseline error {stock}: {e}")

    save_state(state)

    # ── Phase 7: 9:30–10:30 — Low Volume Engine ───────────────────────────────
    _wait_until("09:30")
    logger.info("=== Phase 7: Low Volume Engine ===")
    breeze = _ensure_session(breeze, bc.init_breeze)
    ce.set_breeze(breeze)

    lv_direction = direction if direction != "NO_TRADE" else "BUY"
    lv_scanner   = ce.LowVolumeScanner(lv_picks, lv_direction, baselines)
    cutoff       = datetime.strptime(config.CUTOFF_TIME, "%H:%M").time()

    while datetime.now().time() < cutoff:
        if state["trades_taken"] >= config.MAX_TRADES:
            logger.info("Max trades reached — monitoring only")
            if state["open_trades"]:
                state["open_trades"] = ce.monitor_open_trades(state["open_trades"])
            time.sleep(30)
            continue

        try:
            signals = lv_scanner.scan_all()
            for signal in signals:
                tg.alert_signal(signal)
                state["trades_taken"] += 1

            if state["open_trades"]:
                state["open_trades"] = ce.monitor_open_trades(state["open_trades"])

        except Exception as e:
            logger.error(f"LV scan error: {e}")
            tg.alert_error("Low Volume scan", str(e))

        time.sleep(30)

    # ── Phase 8: Cutoff ────────────────────────────────────────────────────────
    logger.info("=== Phase 8: Cutoff ===")
    if state["trades_taken"] == 0:
        tg.alert_no_trade()

    save_state(state)
    logger.info("Daily cycle complete.")