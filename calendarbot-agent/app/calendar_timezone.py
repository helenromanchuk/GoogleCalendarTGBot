"""Bound the lifetime of calendar timezone metadata stored in the database."""

from time import time

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.database import User

TIMEZONE_CACHE_TTL_SECONDS = 300


def get_cached_timezone(user: User) -> str | None:
    """Return a fresh timezone, treating legacy rows without timestamps as stale."""
    if user.timezone and user.timezone_fetched_at is not None:
        age = time() - user.timezone_fetched_at
        if 0 <= age < TIMEZONE_CACHE_TTL_SECONDS:
            return user.timezone
    return None


def cache_timezone(user: User, timezone: str) -> None:
    """Record a successful metadata lookup; the caller commits the transaction."""
    user.timezone = timezone
    user.timezone_fetched_at = int(time())


def cache_timezone_if_calendar_matches(
    db: Session, telegram_id: int, calendar_id: str, timezone: str
) -> None:
    """Atomically cache metadata only for the queried calendar; caller commits."""
    db.execute(
        update(User)
        .where(
            User.telegram_id == telegram_id,
            func.coalesce(func.nullif(User.default_calendar_id, ""), "primary")
            == calendar_id,
        )
        .values(timezone=timezone, timezone_fetched_at=int(time()))
        .execution_options(synchronize_session=False)
    )
