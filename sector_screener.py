import logging
import time
from breeze_connect import BreezeConnect
import breeze_client as bc
import agent_config as config

logger = logging.getLogger(__name__)


def screen_sectors(breeze: BreezeConnect, direction: str) -> list:
    """
    Uses NSE India API for accurate real-time sector index data.
    Picks top NUM_SECTORS aligned with direction (strongest movers first).
    """
    from data_engine import get_nse_sector_data
    logger.info(f"Screening sectors for direction: {direction}")

    sector_raw = get_nse_sector_data()

    if not sector_raw:
        logger.error("NSE sector data unavailable")
        return []

    results = []
    for name, data in sector_raw.items():
        change_pct = data["change_pct"]
        gap_pct    = abs(change_pct)

        if gap_pct >= config.MAX_GAP_PERCENT:
            logger.info(f"Sector {name} skipped — gap {gap_pct:.1f}%")
            continue

        aligned = (direction == "BUY" and change_pct > 0) or \
                  (direction == "SELL" and change_pct < 0)

        if not aligned:
            continue

        results.append({
            "name":       name,
            "symbol":     name,
            "ltp":        data.get("ltp", 0),
            "change_pct": round(change_pct, 2),
            "gap_pct":    gap_pct,
            "aligned":    True,
        })
        logger.info(f"Sector {name}: {change_pct:.2f}% — aligned")

    # Sort: BUY = most positive first, SELL = most negative first
    if direction == "SELL":
        results.sort(key=lambda x: x["change_pct"])
    else:
        results.sort(key=lambda x: -x["change_pct"])

    selected = results[:config.NUM_SECTORS]
    logger.info(f"Selected sectors: {[(s['name'], s['change_pct']) for s in selected]}")
    return selected


def pick_stocks(breeze: BreezeConnect, sectors: list, direction: str, oi_spurts: list) -> list:
    """
    For each selected sector, picks top MAX_STOCKS_PER_SECTOR stocks.
    Filters: gap < MAX_GAP_PERCENT, aligned with direction, price filter.
    """
    oi_stocks = {s["stock"] for s in oi_spurts}
    picks     = []

    for sector in sectors:
        sector_name  = sector["name"]
        constituents = config.SECTOR_STOCKS.get(sector_name, [])
        candidates   = []

        for stock in constituents:
            quote = bc.get_ltp(breeze, stock)
            if not quote:
                continue

            gap_pct    = abs(quote.get("change_pct", 0))
            change_pct = quote.get("change_pct", 0)
            ltp        = quote.get("ltp", 0)
            prev_close = quote.get("prev_close", 0)
            open_price = quote.get("open", 0)

            # Gap filter
            if gap_pct >= config.MAX_GAP_PERCENT:
                logger.info(f"  {stock} skipped — gap {gap_pct:.1f}%")
                time.sleep(0.7)
                continue

            # Direction alignment
            aligned = (direction == "BUY" and change_pct > 0) or \
                      (direction == "SELL" and change_pct < 0)
            if not aligned:
                time.sleep(0.7)
                continue

            # Price filter: BUY above prev_close, SELL below today open
            if direction == "BUY" and ltp <= prev_close:
                logger.info(f"  {stock} skipped — BUY but LTP {ltp:.2f} <= prev_close {prev_close:.2f}")
                time.sleep(0.7)
                continue
            if direction == "SELL" and ltp >= open_price:
                logger.info(f"  {stock} skipped — SELL but LTP {ltp:.2f} >= open {open_price:.2f}")
                time.sleep(0.7)
                continue

            has_oi_spurt = stock in oi_stocks
            candidates.append({
                "stock":      stock,
                "sector":     sector_name,
                "ltp":        ltp,
                "prev_close": prev_close,
                "open":       open_price,
                "change_pct": change_pct,
                "gap_pct":    gap_pct,
                "oi_spurt":   has_oi_spurt,
                "volume":     quote.get("volume", 0),
            })
            time.sleep(0.7)

        if not candidates:
            logger.warning(f"No valid candidates in sector {sector_name}")
            continue

        # Rank: OI spurt first, then lowest gap
        candidates.sort(key=lambda x: (not x["oi_spurt"], x["gap_pct"]))

        # Pick top MAX_STOCKS_PER_SECTOR stocks
        for best in candidates[:config.MAX_STOCKS_PER_SECTOR]:
            picks.append(best)
            logger.info(f"Picked {best['stock']} from {sector_name} — "
                        f"gap {best['gap_pct']:.1f}% ltp={best['ltp']:.2f}")

    return picks
