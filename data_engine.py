import logging
import time
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

# Map our sector names to NSE index names
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

# Persistent NSE session — reused across calls
_nse_session = None

def _get_nse_session():
    """Returns a valid NSE session with cookies."""
    import requests
    global _nse_session
    try:
        if _nse_session is None:
            _nse_session = requests.Session()
        _nse_session.get('https://www.nseindia.com', headers=NSE_HEADERS, timeout=10)
        time.sleep(1)
        return _nse_session
    except Exception:
        _nse_session = None
        return None


def get_nse_sector_data() -> dict:
    """
    Fetches live sector index data from NSE India API.
    Returns dict: {sector_name: {ltp, change_pct}}
    """
    try:
        session = _get_nse_session()
        if not session:
            return {}
        resp = session.get(
            'https://www.nseindia.com/api/allIndices',
            headers=NSE_HEADERS,
            timeout=10
        )
        if resp.status_code != 200:
            logger.error(f"NSE API status: {resp.status_code}")
            return {}

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
        if len(result) < 8:
            logger.warning(f"Only {len(result)} sectors returned — retrying...")
            raise Exception("Insufficient sector data")

        logger.info(f"NSE sector data fetched: {len(result)} sectors")
        return result

    except Exception as e:
        logger.error(f"get_nse_sector_data failed: {e} — retrying in 5s")
        try:
            time.sleep(5)
            session2 = requests.Session()
            session2.get('https://www.nseindia.com', headers=NSE_HEADERS, timeout=10)
            time.sleep(2)
            resp2 = session2.get('https://www.nseindia.com/api/allIndices', headers=NSE_HEADERS, timeout=10)
            data2 = resp2.json()
            indices2 = {idx['index']: idx for idx in data2.get('data', [])}
            result2 = {}
            for sector, nse_name in NSE_SECTOR_MAP.items():
                idx = indices2.get(nse_name)
                if idx:
                    result2[sector] = {
                        "ltp":        float(idx.get('last', 0)),
                        "change_pct": float(idx.get('percentChange', 0)),
                        "name":       sector,
                    }
            logger.info(f"NSE retry successful: {len(result2)} sectors")
            return result2
        except Exception as e2:
            logger.error(f"NSE retry also failed: {e2}")
            return {}


def get_premarket_snapshot(breeze: BreezeConnect) -> dict:
    logger.info("Fetching pre-market snapshot...")
    breadth = bc.get_market_breadth(breeze)

    # Try multiple Nifty codes for pre-market
    nifty_settled = 0
    for code in ["NIFTY", "NIFTY 50", "Nifty 50"]:
        q = bc.get_index_quote(breeze, code)
        if q and q.get("ltp", 0) > 0:
            nifty_settled = q["ltp"]
            break

    # Fallback: use NSE API for Nifty pre-market price
    if nifty_settled == 0:
        try:
            import requests
            session = requests.Session()
            session.get('https://www.nseindia.com', headers=NSE_HEADERS, timeout=10)
            import time as _time
            _time.sleep(1)
            resp = session.get(
                'https://www.nseindia.com/api/allIndices',
                headers=NSE_HEADERS, timeout=10
            )
            indices = {d['index']: d for d in resp.json().get('data', [])}
            nifty_data = indices.get('NIFTY 50', {})
            nifty_settled = float(nifty_data.get('last', 0))
            logger.info(f"Nifty pre-market from NSE API: {nifty_settled}")
        except Exception as e:
            logger.error(f"NSE API Nifty fallback failed: {e}")

    logger.info(f"Pre-market: Adv={breadth['advances']} Dec={breadth['declines']} Nifty={nifty_settled}")
    return {
        "breadth":       breadth,
        "nifty_settled": nifty_settled,
    }


def get_live_market_snapshot(breeze: BreezeConnect) -> dict:
    logger.info("Fetching live market snapshot...")
    nifty_quote  = bc.get_index_quote(breeze, "NIFTY")
    sector_raw   = get_nse_sector_data()

    sector_data = []
    for name, data in sector_raw.items():
        sector_data.append({
            "name":       name,
            "ltp":        data["ltp"],
            "change_pct": data["change_pct"],
        })

    # For SELL: most negative first. For BUY: most positive first.
    # Since we don't know direction here, sort by absolute change (strongest movers first)
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
