import logging
import asyncio
import time
import threading
from datetime import datetime, date, timedelta

import agent_config as config
import breeze_client as bc
import telegram_bot as tg
import scheduler as sched

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

_polling_active = False
_polling_thread = None

def start_polling():
    global _polling_active, _polling_thread
    if _polling_active:
        return
    _polling_active = True
    app = tg.init_bot()
    def _run():
        async def _inner():
            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            while _polling_active:
                await asyncio.sleep(1)
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_inner())
    _polling_thread = threading.Thread(target=_run, daemon=True)
    _polling_thread.start()
    time.sleep(4)
    logger.info("Telegram polling started")

def stop_polling():
    global _polling_active
    _polling_active = False
    time.sleep(6)
    logger.info("Telegram polling stopped")

def wait_for_login_window():
    target = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
    now = datetime.now()
    if now < target:
        wait_sec = (target - now).seconds
        logger.info(f"Waiting {wait_sec}s until 9:00 AM...")
        time.sleep(wait_sec)

def init_breeze_with_retry(max_retries: int = 3):
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Breeze login attempt {attempt}/{max_retries}...")
            stop_polling()
            breeze = bc.init_breeze()
            start_polling()
            tg.send("Breeze session active. Agent is live for today.")
            return breeze
        except Exception as e:
            logger.error(f"Breeze login attempt {attempt} failed: {e}")
            start_polling()
            tg.alert_error(f"Breeze login attempt {attempt}", str(e))
            if attempt < max_retries:
                time.sleep(60)
    raise RuntimeError("All Breeze login attempts failed.")

def next_weekday_9am() -> float:
    now = datetime.now()
    tomorrow = now + timedelta(days=1)
    while tomorrow.weekday() >= 5:
        tomorrow += timedelta(days=1)
    next_run = tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
    return (next_run - now).total_seconds()

def main():
    logger.info("=" * 60)
    logger.info("Intraday Agent starting up")
    logger.info("=" * 60)
    start_polling()
    tg.send("Intraday Agent started. Initialising for today session...")
    while True:
        try:
            wait_for_login_window()
            breeze = init_breeze_with_retry()
            sched.run_daily_cycle(breeze)
        except Exception as e:
            logger.error(f"Daily cycle crashed: {e}", exc_info=True)
            start_polling()
            tg.alert_error("Daily cycle crashed", str(e))
        wait_sec = next_weekday_9am()
        logger.info(f"Sleeping {wait_sec/3600:.1f}h until next session.")
        tg.send("Agent sleeping until tomorrow 9:00 AM.")
        time.sleep(wait_sec)

if __name__ == "__main__":
    main()
