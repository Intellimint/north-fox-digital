from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import randint


def compute_reply_delay_minutes(min_minutes: int, max_minutes: int) -> int:
    return randint(min_minutes, max_minutes)


def schedule_reply_send(now_utc: datetime, min_minutes: int, max_minutes: int) -> datetime:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc + timedelta(minutes=compute_reply_delay_minutes(min_minutes, max_minutes))
