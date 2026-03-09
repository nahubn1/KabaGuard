import os
import holidays
from datetime import datetime, time, date
from zoneinfo import ZoneInfo
from typing import Optional

# Timezone configuration for Ethiopia (EAT/UTC+3)
EAT = ZoneInfo("Africa/Addis_Ababa")

# Ethiopian holidays
ethiopian_holidays = holidays.Ethiopia()

def get_current_time_eat() -> datetime:
    """Get current time in Ethiopian timezone (EAT/UTC+3)."""
    return datetime.now(EAT)

def parse_time(time_str: str) -> Optional[time]:
    """Parse time string in HH:MM format."""
    try:
        return datetime.strptime(time_str, "%H:%M").time()
    except ValueError:
        return None

def is_ethiopian_holiday(check_date: date) -> bool:
    """Check if a date is an Ethiopian holiday."""
    return check_date in ethiopian_holidays

def escape_md(text: str) -> str:
    """Escape markdown special characters to prevent Telegram API errors."""
    if not text:
        return ""
    for char in ['_', '*', '[', ']', '`']:
        text = text.replace(char, f"\\{char}")
    return text
