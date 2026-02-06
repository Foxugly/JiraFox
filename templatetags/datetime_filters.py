from django import template
from datetime import datetime
import dateutil.parser  # très utile pour ISO-8601 Jira

register = template.Library()

@register.filter
def format_jira_date(value, date_format="%d/%m/%Y"):
    """
    Transforme une date ISO-8601 Jira (2026-01-26T15:16:00.000+01:00)
    en format dd/mm/YYYY.
    """
    if not value:
        return ""

    try:
        # Parse avec dateutil (gère parfaitement les tz)
        dt = dateutil.parser.isoparse(value)
        return dt.strftime(date_format)
    except Exception:
        return value  # fallback : renvoi brut