import time
import logging
import re
import asyncio
from datetime import datetime
from urllib.parse import urlparse, parse_qs

from breeze_connect import BreezeConnect
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from telegram import Bot


import agent_config as config

logger = logging.getLogger(__name__)

# Global OTP holder — filled when user replies to Telegram message


async def _send_telegram(text: str):
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
    await bot.send_message(
        chat_id=config.TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="HTML"
    )




def _wait_for_otp_via_telegram(timeout: int = 300) -> str:
    import requests
    import time as _time

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    elapsed = 0
    last_update_id = None

    # Flush Telegram update queue — clears messages consumed by main polling loop
    try:
        requests.get(f"{url}/getUpdates?offset=-1", timeout=5)
        time.sleep(2)
    except Exception:
        pass

    try:
        r = requests.get(f"{url}/getUpdates", timeout=10).json()
        updates = r.get("result", [])
        if updates:
            last_update_id = updates[-1]["update_id"]
    except Exception:
        pass

    while elapsed < timeout:  # 5 min window
        try:
            params = {"timeout": 5, "allowed_updates": ["message"]}
            if last_update_id:
                params["offset"] = last_update_id + 1
            r = requests.get(f"{url}/getUpdates", params=params, timeout=15).json()
            updates = r.get("result", [])
            for update in updates:
                last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id == str(config.TELEGRAM_CHAT_ID) and text.isdigit() and len(text) == 6:
                    logger.info(f"OTP received: {text}")
                    return text
        except Exception as e:
            logger.warning(f"OTP poll error: {e}")
        _time.sleep(2)
        elapsed += 7

    raise TimeoutError("OTP not received within timeout period")


def get_session_token_via_selenium() -> str:
    logger.info("Starting Selenium login to get Breeze session token...")

    chrome_options = Options()
    if config.HEADLESS_BROWSER:
        chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-background-networking")
    chrome_options.add_argument("--disable-default-apps")
    chrome_options.add_argument("--disable-sync")
    chrome_options.add_argument("--no-first-run")
    chrome_options.add_argument("--shm-size=256m")
    chrome_options.add_argument("--window-size=1280,900")
    chrome_options.binary_location = "/usr/bin/google-chrome"
    from webdriver_manager.chrome import ChromeDriverManager
    service = Service(ChromeDriverManager().install())
    driver  = webdriver.Chrome(service=service, options=chrome_options)
    wait    = WebDriverWait(driver, 30)

    try:
        login_url = (
            f"https://api.icicidirect.com/apiuser/login"
            f"?api_key={config.BREEZE_API_KEY}"
        )
        driver.get(login_url)
        logger.info("Opened ICICI Direct login page")

        # Step 1 — Enter User ID
        uid_field = wait.until(EC.presence_of_element_located((By.ID, "txtuid")))
        uid_field.clear()
        uid_field.send_keys(config.ICICI_USER_ID)

        # Step 2 — Enter Password
        pwd_field = driver.find_element(By.ID, "txtPass")
        pwd_field.clear()
        pwd_field.send_keys(config.ICICI_PASSWORD)

        # Step 3 — Click Login
        driver.find_element(By.ID, "chkssTnc").click()
        driver.find_element(By.ID, "btnSubmit").click()
        logger.info("Submitted login credentials")
        time.sleep(3)

        # Step 4 — Ask user for OTP via Telegram
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_send_telegram(
            "🔐 <b>ICICI Direct OTP Required</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "An OTP has been sent to your registered mobile.\n\n"
            "Please reply to this message with your <b>6-digit OTP</b> now.\n"
            "⏳ You have <b>2 minutes</b> to respond."
        ))
        logger.info("OTP request sent via Telegram — waiting for user response...")

        # Step 5 — Wait for OTP from Telegram
        otp = _wait_for_otp_via_telegram(timeout=300)
        logger.info("OTP received, submitting...")


        # Step 6 — Enter OTP digit by digit into 6 separate fields
        try:
            time.sleep(2)
            otp_inputs = driver.find_elements(By.XPATH, "//input[@type='text' and not(@id)]")
            if len(otp_inputs) >= 6:
                for i, digit in enumerate(otp[:6]):
                    otp_inputs[i].clear()
                    otp_inputs[i].send_keys(digit)
                logger.info("OTP entered digit by digit")
            else:
                # fallback — try injecting directly into hidden field
                driver.execute_script(f"document.getElementById('hiotp').value='{otp}'")
                logger.info("OTP injected via hidden field")
            time.sleep(1)
            driver.find_element(By.ID, "Button1").click()
            logger.info("OTP submitted")
        except Exception as e:
            logger.warning(f"OTP submission error: {e}")


        # Step 7 — Extract session token
        time.sleep(5)
        current_url = driver.current_url
        logger.info(f"Redirect URL: {current_url}")
        parsed = urlparse(current_url)
        params = parse_qs(parsed.query)
        token  = params.get("apisession", [None])[0]

        if not token:
            source = driver.page_source
            match  = re.search(r'apisession=([A-Za-z0-9]+)', source)
            if match:
                token = match.group(1)

        if not token:
            raise ValueError("Could not extract session token from redirect URL")

        logger.info("Session token obtained successfully")

        loop2 = asyncio.new_event_loop()
        asyncio.set_event_loop(loop2)
        loop2.run_until_complete(_send_telegram(
            "✅ <b>Login successful!</b>\n"
            "Breeze session is active. Agent is now live."
        ))

        return token

    finally:
        driver.quit()


def init_breeze() -> BreezeConnect:
    breeze = BreezeConnect(api_key=config.BREEZE_API_KEY)
    session_token = get_session_token_via_selenium()
    breeze.generate_session(
        api_secret=config.BREEZE_API_SECRET,
        session_token=session_token
    )
    logger.info("Breeze session initialised successfully")
    return breeze


def get_ltp(breeze: BreezeConnect, stock_code: str) -> dict:
    try:
        resp = breeze.get_quotes(
            stock_code=stock_code,
            exchange_code="NSE",
            product_type="cash",
            right="",
            strike_price=""
        )
        if resp and resp.get("Success"):
            nse = [d for d in resp["Success"] if d.get("exchange_code") == "NSE"]
            data = nse[0] if nse else resp["Success"][0]
            ltp        = float(data.get("ltp", 0))
            prev_close = float(data.get("previous_close", 0))
            change_pct = ((float(data.get("ltp", 0)) - float(data.get("previous_close", 0))) / float(data.get("previous_close", 1))) * 100
            return {
                "stock":      stock_code,
                "ltp":        ltp,
                "open":       float(data.get("open", 0)),
                "high":       float(data.get("high", 0)),
                "low":        float(data.get("low", 0)),
                "prev_close": prev_close,
                "change_pct": round(change_pct, 2),
                "volume":     int(data.get("total_quantity_traded", 0)),
            }
    except Exception as e:
        logger.error(f"get_ltp failed for {stock_code}: {e}")
    return {}


def get_5min_candles(breeze, stock_code, from_time, to_time) -> list:
    try:
        resp = breeze.get_historical_data_v2(
            interval="5minute",
            from_date=from_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            to_date=to_time.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            stock_code=stock_code,
            exchange_code="NSE",
            product_type="cash"
        )
        if resp and resp.get("Success"):
            candles = []
            for c in resp["Success"]:
                candles.append({
                    "datetime": c.get("datetime"),
                    "open":     float(c.get("open", 0)),
                    "high":     float(c.get("high", 0)),
                    "low":      float(c.get("low", 0)),
                    "close":    float(c.get("close", 0)),
                    "volume":   int(c.get("volume", 0)),
                })
            return candles
    except Exception as e:
        logger.error(f"get_5min_candles failed for {stock_code}: {e}")
    return []


def get_index_quote(breeze, index_code) -> dict:
    try:
        resp = breeze.get_quotes(
            stock_code=index_code,
            exchange_code="NSE",
            product_type="cash",
            right="",
            strike_price=""
        )
        if resp and resp.get("Success"):
            nse = [d for d in resp["Success"] if d.get("exchange_code") == "NSE"]
            data = nse[0] if nse else resp["Success"][0]
            ltp        = float(data.get("ltp", 0))
            prev_close = float(data.get("previous_close", 0))
            change_pct = ((float(data.get("ltp", 0)) - float(data.get("previous_close", 0))) / float(data.get("previous_close", 1))) * 100
            return {
                "index":      index_code,
                "ltp":        ltp,
                "prev_close": prev_close,
                "change_pct": round(change_pct, 2),
            }
    except Exception as e:
        logger.error(f"get_index_quote failed for {index_code}: {e}")
    return {}


def get_market_breadth(breeze) -> dict:
    advances  = 0
    declines  = 0
    unchanged = 0

    for stock in config.NIFTY50_FNO_STOCKS:
        quote = get_ltp(breeze, stock)
        if not quote:
            continue
        chg = quote.get("change_pct", 0)
        if chg > 0.05:
            advances += 1
        elif chg < -0.05:
            declines += 1
        else:
            unchanged += 1
        time.sleep(0.7)

    ratio = round(advances / declines, 2) if declines > 0 else float("inf")
    return {
        "advances":      advances,
        "declines":      declines,
        "unchanged":     unchanged,
        "adv_dec_ratio": ratio,
    }


def get_oi_spurts(breeze) -> list:
    oi_spurts = []
    try:
        for stock in config.NIFTY50_FNO_STOCKS:
            resp = breeze.get_quotes(
                stock_code=stock,
                exchange_code="NFO",
                product_type="futures",
                right="others",
                strike_price="0",
                expiry_date=""
            )
            if resp and resp.get("Success"):
                data    = resp["Success"][0]
                oi      = int(data.get("open_interest", 0))
                prev_oi = int(data.get("prev_open_interest", 0))
                if prev_oi > 0:
                    oi_change_pct = (oi - prev_oi) / prev_oi * 100
                    if oi_change_pct >= 10:
                        oi_spurts.append({
                            "stock":         stock,
                            "oi":            oi,
                            "prev_oi":       prev_oi,
                            "oi_change_pct": round(oi_change_pct, 2),
                        })
            time.sleep(0.7)
    except Exception as e:
        logger.error(f"get_oi_spurts error: {e}")

    oi_spurts.sort(key=lambda x: x["oi_change_pct"], reverse=True)
    return oi_spurts


def place_limit_order(breeze, stock_code, action, quantity, price) -> dict:
    try:
        resp = breeze.place_order(
            stock_code=stock_code,
            exchange_code="NSE",
            product="cash",
            action=action.lower(),
            order_type="limit",
            stoploss="0",
            quantity=str(quantity),
            price=str(round(price, 2)),
            validity="day",
            validity_date=datetime.now().strftime("%Y-%m-%dT00:00:00.000Z"),
            disclosed_quantity="0",
            expiry_date="",
            right="",
            strike_price="0",
            user_remark="IntraAgent"
        )
        logger.info(f"Order placed: {action.upper()} {quantity} {stock_code} @ {price}")
        return resp
    except Exception as e:
        logger.error(f"place_limit_order failed: {e}")
        return {"error": str(e)}
