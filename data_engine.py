import logging
import time
import requests
from datetime import datetime
from breeze_connect import BreezeConnect
import breeze_client as bc
import agent_config as config

logger = logging.getLogger(__name__)

NSE_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'application/json',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.nseindia.com/',
}

NSE_SECTOR_MAP = {
    "IT":        "NIFTY IT",
    "Bank":      "NIFTY BANK",
    "Auto":      "NIFTY AUTO",
    "FMCG":      "NIFTY FMCG",
    "Pharma":    "NIFTY PHARMA",
    "Metal":     "NIFTY METAL",
    "Energy":    "NIFTY ENERGY",
    "Realty":    "NIFTY REALTY",
    "Financial": "NIFTY FINANCIAL SERVICES",
    "Media":     "NIFTY MEDIA",
    "Infra":     "NIFTY INFRASTRUCTURE",
    "PSU Bank":  "NIFTY PSU BANK",
}


def _get_nse_session():
    """Always creates a fresh NSE session with valid cookies."""
    try:
        session = requests.Session()
        session.get('https://www.nseindia.com', headers=NSE_HEADERS, timeout=10)
        time.sleep(1)
        return session
    except Exception as e:
        logger.error(f"NSE session creation failed: {e}")
        return None


def get_nse_sector_data() -> dict:
    """
    Fetches live sector index data from NSE India API.
    Always creates fresh session to avoid stale cookies.
    Returns dict: {sector_name: {ltp, change_pct}}
    """
    for attempt in range(1, 3):
        try:
            session = _get_nse_session()
            if not session:
                continue

            resp = session.get(
                'https://www.nseindia.com/api/allIndices',
                headers=NSE_HEADERS,
                timeout=10
            )

            if resp.status_code != 200:
                logger.error(f"NSE API status: {resp.status_code}")
                continue

            data    = resp.json()
            indices = {idx['index']: idx for idx in data.get('data', [])}

            result = {}
            for sector, nse_name in NSE_SECTOR_MAP.items():
                idx = indices.get(nse_name)
                if idx:
                    result[sector] = {
                        "ltp":        float(idx.get('last', 0)),
                        "change_pct": float(idx.get('percentChange', 0)),
                        "name":       sector,
                    }

            if len(result) >= 8:
                logger.info(f"NSE sector data fetched: {len(result)} sectors")
                return result

            logger.warning(f"Attempt {attempt}: only {len(result)} sectors — retrying...")
            time.sleep(5)

        except Exception as e:
            logger.error(f"get_nse_sector_data attempt {attempt} failed: {e}")
            time.sleep(5)

    logger.error("All NSE sector data attempts failed")
    return {}


def get_nifty_from_nse() -> float:
    """Fetch Nifty 50 price from NSE API as fallback."""
    try:
        session = _get_nse_session()
        if not session:
            return 0
        resp = session.get(
            'https://www.nseindia.com/api/allIndices',
            headers=NSE_HEADERS,
            timeout=10
        )
        indices = {d['index']: d for d in resp.json().get('data', [])}
        nifty   = indices.get('NIFTY 50', {})
        return float(nifty.get('last', 0))
    except Exception as e:
        logger.error(f"get_nifty_from_nse failed: {e}")
        return 0


def get_premarket_snapshot(breeze: BreezeConnect) -> dict:
    logger.info("Fetching pre-market snapshot...")
    breadth = bc.get_market_breadth(breeze)

    # Try Breeze first, fall back to NSE API
    nifty_settled = 0
    q = bc.get_index_quote(breeze, "NIFTY")
    if q and q.get("ltp", 0) > 0:
        nifty_settled = q["ltp"]
    else:
        nifty_settled = get_nifty_from_nse()
        logger.info(f"Nifty from NSE API: {nifty_settled}")

    logger.info(f"Pre-market: Adv={breadth['advances']} Dec={breadth['declines']} Nifty={nifty_settled}")
    return {
        "breadth":       breadth,
        "nifty_settled": nifty_settled,
    }


def get_live_market_snapshot(breeze: BreezeConnect) -> dict:
    logger.info("Fetching live market snapshot...")

    # Nifty from Breeze, fallback to NSE
    nifty_quote = bc.get_index_quote(breeze, "NIFTY")
    if not nifty_quote or nifty_quote.get("ltp", 0) == 0:
        nifty_price = get_nifty_from_nse()
        nifty_quote = {"ltp": nifty_price, "change_pct": 0, "index": "NIFTY"}

    # Sector data from NSE API
    sector_raw  = get_nse_sector_data()
    sector_data = []
    for name, data in sector_raw.items():
        sector_data.append({
            "name":       name,
            "ltp":        data["ltp"],
            "change_pct": data["change_pct"],
        })

    # Sort by absolute change — strongest movers first
    sector_data.sort(key=lambda x: abs(x["change_pct"]), reverse=True)

    breadth = bc.get_market_breadth(breeze)

    return {
        "nifty":   nifty_quote,
        "sectors": sector_data,
        "breadth": breadth,
    }


def determine_direction(snapshots: list) -> dict:
    if not snapshots:
        return {"direction": "NO_TRADE", "reasons": ["No data"], "score": 0}

    latest   = snapshots[-1]
    breadth  = latest["breadth"]
    nifty    = latest["nifty"]
    adv      = breadth["advances"]
    dec      = breadth["declines"]
    ratio    = breadth["adv_dec_ratio"]
    nifty_ch = nifty.get("change_pct", 0)

    reasons = []
    score   = 0

    if ratio > 2.0:
        score += 2; reasons.append(f"Strong breadth: {adv} adv vs {dec} dec (ratio {ratio})")
    elif ratio > 1.5:
        score += 1; reasons.append(f"Mild bullish breadth: ratio {ratio}")
    elif ratio < 0.5:
        score -= 2; reasons.append(f"Strong bearish breadth: {dec} dec vs {adv} adv (ratio {ratio})")
    elif ratio < 0.67:
        score -= 1; reasons.append(f"Mild bearish breadth: ratio {ratio}")

    if nifty_ch > 0.3:
        score += 1; reasons.append(f"Nifty trending up {nifty_ch:+.2f}%")
    elif nifty_ch > 0:
        score += 0.5; reasons.append(f"Nifty slightly up {nifty_ch:+.2f}%")
    elif nifty_ch < -0.3:
        score -= 1; reasons.append(f"Nifty trending down {nifty_ch:+.2f}%")
    elif nifty_ch < 0:
        score -= 0.5; reasons.append(f"Nifty slightly down {nifty_ch:+.2f}%")

    if len(snapshots) >= 2:
        prev_nifty = snapshots[-2]["nifty"].get("change_pct", 0)
        if nifty_ch > prev_nifty and nifty_ch > 0:
            score += 0.5; reasons.append("Nifty momentum accelerating")
        elif nifty_ch < prev_nifty and nifty_ch < 0:
            score -= 0.5; reasons.append("Nifty momentum weakening")

    if score >= 1.5:
        direction = "BUY"
    elif score <= -1.5:
        direction = "SELL"
    else:
        direction = "NO_TRADE"
        reasons.append(f"Score {score:.1f} — not decisive enough for a trade")

    logger.info(f"Direction: {direction} (score={score:.1f})")
    return {"direction": direction, "reasons": reasons, "score": score}
