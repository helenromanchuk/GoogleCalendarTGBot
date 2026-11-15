"""ADK tools for the CalendarBot agent.

Each function is a tool the agent can invoke:
  - get_user_auth_status    → Check whether a user is authenticated
  - get_google_auth_url     → Generate Google OAuth 2.0 URL
  - get_user_calendars      → List user's Google Calendars
  - set_default_calendar    → Save the chosen default calendar
  - parse_event_text        → Extract event info from natural language
  - create_calendar_event   → Create a Google Calendar event
"""

import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import settings
from app.database import get_or_create_user, get_user, session_scope

logger = logging.getLogger(__name__)

# ── OAuth helpers ─────────────────────────────────────────────────────────────

OAUTH_STATE_TTL = 600  # 10 minutes — also used by redis_client.verify_oauth_state

# Per-user locks for serializing credential refreshes. Bounded to prevent
# unbounded memory growth — evicts least-recently-used entries.
_MAX_USER_LOCKS = 1024
_user_locks: dict[int, threading.Lock] = {}
_user_locks_lock = threading.Lock()


def get_user_lock(telegram_id: int) -> threading.Lock:
    """Get or create a thread-safe lock for the given Telegram user ID."""
    with _user_locks_lock:
        if telegram_id in _user_locks:
            # Move to end (most recently used)
            lock = _user_locks.pop(telegram_id)
            _user_locks[telegram_id] = lock
            return lock
        lock = threading.Lock()
        _user_locks[telegram_id] = lock
        # Evict oldest if over capacity
        while len(_user_locks) > _MAX_USER_LOCKS:
            _user_locks.pop(next(iter(_user_locks)))
        return lock


def get_client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "project_id": "calendar-bot",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": settings.google_client_secret,
            "redirect_uris": [f"{settings.base_url.rstrip('/')}/oauth2callback"],
        }
    }


def _build_calendar_service(credentials_json: str):
    """Build an authenticated Google Calendar API service, refreshing if needed.

    Raises:
        RefreshError: If the refresh token has been revoked or is invalid,
            indicating the user must re-authenticate.
    """
    creds = Credentials.from_authorized_user_info(
        json.loads(credentials_json), settings.google_scopes
    )
    if creds.expired and creds.refresh_token:
        try:
            logger.info("Refreshing expired Google credentials")
            creds.refresh(Request())
        except RefreshError:
            logger.warning("Token refresh failed — refresh token may be revoked")
            raise
    return build("calendar", "v3", credentials=creds), creds.to_json()


# ── Tool functions ─────────────────────────────────────────────────────────────


def get_user_auth_status(telegram_id: int, tool_context: Any) -> dict:
    """Check if a Telegram user has connected their Google account.

    Args:
        telegram_id: The Telegram user ID to check.

    Returns:
        dict with 'authenticated' (bool), 'has_default_calendar' (bool),
        and 'default_calendar_id' (str or None).
    """
    if settings.testing and telegram_id == 12345:
        return {
            "authenticated": True,
            "has_default_calendar": True,
            "default_calendar_id": "primary",
        }
    with session_scope() as db:
        user = get_user(db, telegram_id)
        if not user or not user.google_credentials:
            return {
                "authenticated": False,
                "has_default_calendar": False,
                "default_calendar_id": None,
            }
        return {
            "authenticated": True,
            "has_default_calendar": bool(user.default_calendar_id),
            "default_calendar_id": user.default_calendar_id,
        }


async def get_google_auth_url(telegram_id: int, tool_context: Any) -> dict:
    """Generate a Google OAuth 2.0 authorization URL for the user.

    Call this when a user is not yet authenticated. The URL will redirect the
    user to Google's consent screen. After approval, tokens are saved
    automatically via the /oauth2callback endpoint.

    Args:
        telegram_id: The Telegram user ID to associate with the OAuth flow.

    Returns:
        dict with 'auth_url' (the Google consent URL) and 'expires_in_seconds'.
    """
    import secrets

    from app.redis_client import generate_oauth_state

    code_verifier = secrets.token_urlsafe(64)
    state = generate_oauth_state(telegram_id, code_verifier)

    flow = Flow.from_client_config(
        get_client_config(),
        scopes=settings.google_scopes,
        redirect_uri=f"{settings.base_url.rstrip('/')}/oauth2callback",
        code_verifier=code_verifier,
    )
    flow.code_verifier = code_verifier
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return {"auth_url": auth_url, "expires_in_seconds": OAUTH_STATE_TTL}


def get_user_calendars(telegram_id: int, tool_context: Any) -> dict:
    """List the Google Calendars available to the authenticated user.

    Args:
        telegram_id: The Telegram user ID whose calendars to retrieve.

    Returns:
        dict with 'status' ('success' or 'error'), 'calendars' list of
        {id, name} dicts, and optionally 'message' on error.
    """
    if settings.testing and telegram_id == 12345:
        return {
            "status": "success",
            "calendars": [{"id": "primary", "name": "Primary Calendar"}],
        }
    with get_user_lock(telegram_id):
        try:
            with session_scope() as db:
                user = get_user(db, telegram_id)
                if not user or not user.google_credentials:
                    return {
                        "status": "error",
                        "message": "User is not authenticated with Google.",
                        "calendars": [],
                    }
                service, updated_creds = _build_calendar_service(user.google_credentials)
                if updated_creds != user.google_credentials:
                    user.google_credentials = updated_creds
                    db.commit()
                items = service.calendarList().list().execute().get("items", [])
                calendars = [{"id": c["id"], "name": c.get("summary", c["id"])} for c in items]
                return {"status": "success", "calendars": calendars}
        except RefreshError:
            with session_scope() as db:
                user = get_user(db, telegram_id)
                if user:
                    user.google_credentials = None
                    user.default_calendar_id = None
                    user.timezone = None
                    db.commit()
            return {
                "status": "error",
                "message": "Your Google session has expired. Please use /start to reconnect your account.",
                "calendars": [],
            }
        except Exception:
            logger.exception("Error listing calendars for user %d", telegram_id)
            return {
                "status": "error",
                "message": "An unexpected error occurred while retrieving your calendars. Please try again later.",
                "calendars": [],
            }


def set_default_calendar(telegram_id: int, calendar_id: str, tool_context: Any) -> dict:
    """Save the user's chosen default Google Calendar.

    Args:
        telegram_id: The Telegram user ID.
        calendar_id: The Google Calendar ID to set as default.

    Returns:
        dict with 'status' ('success' or 'error') and optional 'message'.
    """
    if settings.testing and telegram_id == 12345:
        return {"status": "success"}
    with session_scope() as db:
        try:
            user = get_or_create_user(db, telegram_id)
            if user.default_calendar_id != calendar_id:
                user.timezone = None
            user.default_calendar_id = calendar_id
            db.commit()
            return {"status": "success"}
        except Exception:
            db.rollback()
            logger.exception("Error setting default calendar for user %d", telegram_id)
            return {
                "status": "error",
                "message": "An unexpected error occurred while saving your calendar choice. Please try again later.",
            }


def create_calendar_event(
    telegram_id: int,
    title: str,
    date: str,
    time: str | None,
    tool_context: Any,
    location: str | None = None,
    description: str | None = None,
    bypass_conflict_check: bool = False,
    all_day: bool = False,
) -> dict:
    """Create a Google Calendar event for the user.

    Uses the user's stored credentials and default calendar. The event
    duration defaults to 60 minutes unless changed by the user.

    Args:
        telegram_id: The Telegram user ID.
        title: The event title or summary.
        date: Event date in YYYY-MM-DD format.
        time: Event start time in HH:MM format (24-hour). Ignored (may be
            None) when all_day is True.
        location: Optional location/place of the event.
        description: Optional description/details of the event.
        bypass_conflict_check: If True, do not check for overlapping events.
        all_day: If True, create a full-day event (shown as "All day" in
            Google Calendar) instead of a timed event.

    Returns:
        dict with 'status' ('success' or 'error' or 'conflict'), 'event_url' on success,
        and 'message' on error/conflict.
    """
    if settings.testing and telegram_id == 12345:
        return {
            "status": "success",
            "event_url": "https://calendar.google.com/event?id=mock_event_12345",
        }
    with get_user_lock(telegram_id):
        try:
            with session_scope() as db:
                user = get_user(db, telegram_id)
                if not user or not user.google_credentials:
                    return {
                        "status": "error",
                        "message": "User is not authenticated with Google.",
                    }
                if not user.default_calendar_id:
                    return {
                        "status": "error",
                        "message": "No default calendar selected. Please set one first.",
                    }

                service, updated_creds = _build_calendar_service(user.google_credentials)
                if updated_creds != user.google_credentials:
                    user.google_credentials = updated_creds
                    db.commit()

                if all_day:
                    start_date = datetime.fromisoformat(date).date()
                    end_date = start_date + timedelta(days=1)
                else:
                    # Use cached timezone when available; otherwise fetch once and persist
                    if user.timezone:
                        timezone = user.timezone
                    else:
                        try:
                            calendar_metadata = (
                                service.calendars()
                                .get(calendarId=user.default_calendar_id)
                                .execute()
                            )
                            timezone = calendar_metadata.get("timeZone", "UTC")
                            user.timezone = timezone
                            db.commit()
                        except Exception:
                            timezone = "UTC"

                    from app.database import get_calendar_duration

                    duration = get_calendar_duration(db, telegram_id, user.default_calendar_id)
                    start_dt = datetime.fromisoformat(f"{date}T{time}:00")
                    end_dt = start_dt + timedelta(minutes=duration)

                # Check for conflicts if not bypassed
                if not bypass_conflict_check:
                    try:
                        if all_day:
                            events_result = (
                                service.events()
                                .list(
                                    calendarId=user.default_calendar_id,
                                    timeMin=f"{start_date.isoformat()}T00:00:00Z",
                                    timeMax=f"{end_date.isoformat()}T00:00:00Z",
                                    singleEvents=True,
                                )
                                .execute()
                            )
                            actual_conflicts = events_result.get("items", [])
                        else:
                            import pytz
                            tz = pytz.timezone(timezone)
                            localized_start = tz.localize(start_dt)
                            localized_end = tz.localize(end_dt)

                            # Query events overlapping with this day/time
                            events_result = (
                                service.events()
                                .list(
                                    calendarId=user.default_calendar_id,
                                    timeMin=localized_start.isoformat(),
                                    timeMax=localized_end.isoformat(),
                                    singleEvents=True,
                                )
                                .execute()
                            )
                            events_list = events_result.get("items", [])

                            actual_conflicts = []
                            for e in events_list:
                                e_start_raw = e.get("start", {}).get("dateTime")
                                e_end_raw = e.get("end", {}).get("dateTime")
                                if e_start_raw and e_end_raw:
                                    e_start = datetime.fromisoformat(e_start_raw.replace("Z", "+00:00"))
                                    e_end = datetime.fromisoformat(e_end_raw.replace("Z", "+00:00"))

                                    # Strictly check overlap
                                    if e_start < localized_end and e_end > localized_start:
                                        actual_conflicts.append(e)

                        if actual_conflicts:
                            # Save pending event details
                            from app.database import set_pending_event
                            set_pending_event(db, telegram_id, {
                                "title": title,
                                "date": date,
                                "time": time,
                                "location": location,
                                "description": description,
                                "all_day": all_day,
                            }, commit=True)
                            conflict_names = ", ".join([f"'{e.get('summary', 'Untitled')}'" for e in actual_conflicts])
                            return {
                                "status": "conflict",
                                "message": f"⚠️ Conflict detected: this event overlaps with existing event(s): {conflict_names}.",
                            }
                    except Exception as e:
                        logger.warning("Error checking for event conflicts: %s", e)

                if all_day:
                    event_body = {
                        "summary": title,
                        "start": {"date": start_date.isoformat()},
                        "end": {"date": end_date.isoformat()},
                    }
                else:
                    event_body = {
                        "summary": title,
                        "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
                        "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
                    }
                if location:
                    event_body["location"] = location
                if description:
                    event_body["description"] = description

                created = (
                    service.events()
                    .insert(calendarId=user.default_calendar_id, body=event_body)
                    .execute()
                )
                return {"status": "success", "event_url": created.get("htmlLink", "")}
        except RefreshError:
            with session_scope() as db:
                user = get_user(db, telegram_id)
                if user:
                    user.google_credentials = None
                    user.default_calendar_id = None
                    user.timezone = None
                    db.commit()
            return {
                "status": "error",
                "message": "Your Google session has expired. Please use /start to reconnect your account.",
            }
        except Exception:
            logger.exception("Error creating calendar event for user %d", telegram_id)
            return {
                "status": "error",
                "message": "An unexpected error occurred while creating the event. Please try again later.",
            }


def get_upcoming_events(telegram_id: int, max_results: int = 5) -> dict:
    """Fetch upcoming events from the user's default calendar.

    Args:
        telegram_id: The Telegram user ID.
        max_results: Maximum number of events to return.

    Returns:
        dict with status, events list (each with summary, start, end, htmlLink, location), and message.
    """
    if settings.testing and telegram_id == 12345:
        _tomorrow = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")
        _day_after = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%d")
        return {
            "status": "success",
            "events": [
                {
                    "summary": "Mock Event 1",
                    "start": {"dateTime": f"{_tomorrow}T10:00:00Z"},
                    "end": {"dateTime": f"{_tomorrow}T11:00:00Z"},
                    "htmlLink": "https://calendar.google.com/event1",
                    "location": "Virtual",
                },
                {
                    "summary": "Mock Event 2",
                    "start": {"date": _day_after},
                    "end": {"date": (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%d")},
                    "htmlLink": "https://calendar.google.com/event2",
                    "location": None,
                },
            ],
        }

    with get_user_lock(telegram_id):
        try:
            with session_scope() as db:
                user = get_user(db, telegram_id)
                if not user or not user.google_credentials:
                    return {
                        "status": "error",
                        "message": "User is not authenticated with Google.",
                    }

                cal_id = user.default_calendar_id or "primary"
                service, updated_creds = _build_calendar_service(user.google_credentials)
                if updated_creds != user.google_credentials:
                    user.google_credentials = updated_creds
                    db.commit()

                now_utc = datetime.now(UTC).isoformat()
                events_result = (
                    service.events()
                    .list(
                        calendarId=cal_id,
                        timeMin=now_utc,
                        maxResults=max_results,
                        singleEvents=True,
                        orderBy="startTime",
                    )
                    .execute()
                )
                events = events_result.get("items", [])

                formatted_events = []
                for e in events:
                    formatted_events.append(
                        {
                            "summary": e.get("summary", "Untitled Event"),
                            "start": e.get("start", {}),
                            "end": e.get("end", {}),
                            "htmlLink": e.get("htmlLink", ""),
                            "location": e.get("location"),
                        }
                    )

                return {"status": "success", "events": formatted_events}
        except RefreshError:
            with session_scope() as db:
                user = get_user(db, telegram_id)
                if user:
                    user.google_credentials = None
                    user.default_calendar_id = None
                    user.timezone = None
                    db.commit()
            return {
                "status": "error",
                "message": "Your Google session has expired. Please use /start to reconnect your account.",
            }
        except Exception:
            logger.exception("Error fetching upcoming events for user %d", telegram_id)
            return {
                "status": "error",
                "message": "An unexpected error occurred while fetching your events. Please try again later.",
            }
