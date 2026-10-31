"""Small shared helpers for facts actually present on public event pages."""
from datetime import datetime, timedelta, timezone
import re

KST = timezone(timedelta(hours=9))


def checked_now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def event_metadata(promotion: str, checked: str, url: str, *,
                   period_text: str = "", start: str = "", end: str = "") -> dict:
    # A yearless '10.1~10.31' is preserved verbatim, not assigned the receipt year.
    dates = re.findall(r"\b(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b", period_text)
    if len(dates) == 2 and not (start or end):
        try:
            parsed = [datetime(int(y), int(m), int(d)).date().isoformat() for y, m, d in dates]
            start, end = parsed
        except ValueError:
            pass
    bundle = re.fullmatch(r"([123])\s*[+＋]\s*1", promotion.strip())
    return {
        "received_at": checked,
        "promotion_checked_at": checked,
        "promotion_source_url": url,
        "promotion_start_date": start,
        "promotion_end_date": end,
        "promotion_buy_quantity": int(bundle.group(1)) if bundle else None,
        "promotion_gift_quantity": 1 if bundle else None,
        "promotion_member_condition": "미확인",
        "promotion_card_condition": "미확인",
        "promotion_cross_gift": None,
        "promotion_conditions_verified": False,
        "raw_payload": {"promotion_label": promotion, "period_text": period_text},
    }
