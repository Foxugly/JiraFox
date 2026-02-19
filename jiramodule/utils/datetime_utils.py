from datetime import datetime
from typing import Optional

from django.utils import timezone
from django.utils.formats import date_format


def serialize_dt(dt):
    if not dt:
        return {
            "iso": None,
            "display": ""
        }

    dt_local = timezone.localtime(dt)

    return {
        "iso": dt_local.isoformat(),  # tri
        "display": date_format(dt_local, "DATETIME_FORMAT", use_l10n=True)
    }


def _to_hours(seconds: Optional[int]) -> Optional[float]:
    if seconds is None:
        return None
    return round(seconds / 3600.0, 2)


def _parse_jira_dt(s: Optional[str]) -> Optional[datetime]:
    """
    Jira timestamps look like: '2026-01-22T13:31:12.345+0100'
    """
    if not s:
        return None
    # try a few common formats
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def hours_to_business_days(hours: Optional[float], *, day_hours: float = 8.0) -> Optional[float]:
    if hours is None:
        return None
    return round(hours / day_hours, 2)
