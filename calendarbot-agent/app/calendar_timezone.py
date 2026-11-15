"""Bound the lifetime of calendar timezone metadata stored in the database."""

from time import time

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
