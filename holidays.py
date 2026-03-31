# =============================================================================
# holidays.py — NSE Trading Holidays 2026
# Source: NSE Circular NSE/CMTR/71775 dated December 12, 2025
# =============================================================================

NSE_HOLIDAYS_2026 = {
    "2026-01-26": "Republic Day",
    "2026-03-03": "Holi",
    "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti",
    "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id",
    "2026-06-26": "Muharram",
    "2026-08-15": "Independence Day",
    "2026-08-27": "Ganesh Chaturthi",
    "2026-10-02": "Gandhi Jayanti / Dussehra",
    "2026-10-22": "Dussehra (Maha Navami)",
    "2026-11-08": "Diwali Laxmi Pujan (Muhurat Trading)",
    "2026-11-10": "Diwali Balipratipada",
    "2026-11-23": "Gurunanak Jayanti",
    "2026-12-25": "Christmas",
}


def is_market_holiday(date_str: str = None) -> tuple:
    """
    Check if given date (YYYY-MM-DD) is an NSE trading holiday.
    Returns (is_holiday: bool, reason: str)
    """
    from datetime import date
    if date_str is None:
        date_str = str(date.today())
    reason = NSE_HOLIDAYS_2026.get(date_str, "")
    return bool(reason), reason


def is_trading_day(date_str: str = None) -> tuple:
    """
    Returns (is_trading: bool, reason: str)
    Checks both weekends and NSE holidays.
    """
    from datetime import date, datetime
    if date_str is None:
        date_str = str(date.today())

    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    if d.weekday() >= 5:
        day_name = "Saturday" if d.weekday() == 5 else "Sunday"
        return False, day_name

    is_holiday, reason = is_market_holiday(date_str)
    if is_holiday:
        return False, reason

    return True, "Trading day"
