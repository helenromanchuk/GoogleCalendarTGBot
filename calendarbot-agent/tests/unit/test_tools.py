from unittest.mock import MagicMock, patch

import pytest

from app.database import SessionLocal, get_or_create_user, init_db
from app.tools import (
    create_calendar_event,
    get_google_auth_url,
    get_user_auth_status,
    set_default_calendar,
)


@pytest.fixture(autouse=True)
def setup_db() -> None:
    init_db()


def test_get_user_auth_status() -> None:
    # Mock user 12345 (used in integration tests)
    status = get_user_auth_status(12345, None)
    assert status["authenticated"] is True
    assert status["has_default_calendar"] is True

    # Real user not in database
    status = get_user_auth_status(99999, None)
    assert status["authenticated"] is False
    assert status["has_default_calendar"] is False

    # Real user in database, not authenticated
    db = SessionLocal()
    get_or_create_user(db, 88888)
    db.close()
    status = get_user_auth_status(88888, None)
    assert status["authenticated"] is False


@pytest.mark.asyncio
async def test_get_google_auth_url() -> None:
    res = await get_google_auth_url(88888, None)
    assert "auth_url" in res
    assert "accounts.google.com" in res["auth_url"]
    assert "state=" in res["auth_url"]


def test_set_default_calendar() -> None:
    res = set_default_calendar(88888, "my-calendar-id", None)
    assert res["status"] == "success"

    # Verify db was updated
    db = SessionLocal()
    user = get_or_create_user(db, 88888)
    assert user.default_calendar_id == "my-calendar-id"
    db.close()


@patch("app.tools._build_calendar_service")
def test_create_calendar_event(mock_build_service: MagicMock) -> None:
    # Prepare mocked calendar service
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_insert = MagicMock()
    mock_calendars = MagicMock()
    mock_get = MagicMock()

    mock_build_service.return_value = (mock_service, '{"token": "test-token"}')
    mock_service.events.return_value = mock_events
    mock_events.insert.return_value = mock_insert
    mock_insert.execute.return_value = {"htmlLink": "https://event-link"}
    mock_service.calendars.return_value = mock_calendars
    mock_calendars.get.return_value = mock_get
    mock_get.execute.return_value = {"timeZone": "Europe/Moscow"}

    # Set up user in database with credentials and default calendar
    db = SessionLocal()
    user = get_or_create_user(db, 77777)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    # Call tool function
    res = create_calendar_event(
        telegram_id=77777,
        title="Test Event",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
        location="Meeting Room 1",
        description="Discuss project status",
    )

    assert res["status"] == "success"
    assert res["event_url"] == "https://event-link"

    # Verify parameters sent to google client
    mock_service.events.return_value.insert.assert_called_once()
    kwargs = mock_service.events.return_value.insert.call_args[1]
    assert kwargs["calendarId"] == "test-calendar"
    assert kwargs["body"]["summary"] == "Test Event"
    assert kwargs["body"]["location"] == "Meeting Room 1"
    assert kwargs["body"]["description"] == "Discuss project status"
    assert "2026-06-25T15:30:00" in kwargs["body"]["start"]["dateTime"]
    assert kwargs["body"]["start"]["timeZone"] == "Europe/Moscow"
    assert kwargs["body"]["end"]["timeZone"] == "Europe/Moscow"


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_conflict(mock_build_service: MagicMock) -> None:
    from app.tools import _pending_events

    # Prepare mocked calendar service
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_list = MagicMock()
    mock_calendars = MagicMock()
    mock_get = MagicMock()

    mock_build_service.return_value = (mock_service, '{"token": "test-token"}')
    mock_service.events.return_value = mock_events
    mock_events.list.return_value = mock_list
    mock_list.execute.return_value = {
        "items": [
            {
                "summary": "Existing Busy Event",
                "start": {"dateTime": "2026-06-25T16:00:00+03:00"},
                "end": {"dateTime": "2026-06-25T17:00:00+03:00"},
            }
        ]
    }
    mock_service.calendars.return_value = mock_calendars
    mock_calendars.get.return_value = mock_get
    mock_get.execute.return_value = {"timeZone": "Europe/Moscow"}

    # Set up user in database with credentials and default calendar
    db = SessionLocal()
    user = get_or_create_user(db, 77778)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    # Clear pending events before calling
    _pending_events.pop(77778, None)

    # Call tool function (should fail check due to conflict)
    res = create_calendar_event(
        telegram_id=77778,
        title="Test Overlapping Event",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
        location="Meeting Room 1",
        description="Discuss project status",
    )

    assert res["status"] == "conflict"
    assert "Conflict detected" in res["message"]
    assert "'Existing Busy Event'" in res["message"]

    # Verify event was cached in pending
    assert 77778 in _pending_events
    assert _pending_events[77778]["title"] == "Test Overlapping Event"


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_conflict_bypass(mock_build_service: MagicMock) -> None:
    from app.tools import _pending_events

    # Prepare mocked calendar service
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_insert = MagicMock()
    mock_list = MagicMock()
    mock_calendars = MagicMock()
    mock_get = MagicMock()

    mock_build_service.return_value = (mock_service, '{"token": "test-token"}')
    mock_service.events.return_value = mock_events
    mock_events.insert.return_value = mock_insert
    mock_insert.execute.return_value = {"htmlLink": "https://event-link"}
    mock_events.list.return_value = mock_list
    # List still returns conflicting events, but we bypass check
    mock_list.execute.return_value = {
        "items": [
            {
                "summary": "Existing Busy Event",
                "start": {"dateTime": "2026-06-25T16:00:00+03:00"},
                "end": {"dateTime": "2026-06-25T17:00:00+03:00"},
            }
        ]
    }
    mock_service.calendars.return_value = mock_calendars
    mock_calendars.get.return_value = mock_get
    mock_get.execute.return_value = {"timeZone": "Europe/Moscow"}

    # Set up user in database with credentials and default calendar
    db = SessionLocal()
    user = get_or_create_user(db, 77779)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    # Clear pending events before calling
    _pending_events.pop(77779, None)

    # Call tool function with bypass_conflict_check=True
    res = create_calendar_event(
        telegram_id=77779,
        title="Test Overlapping Event",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
        location="Meeting Room 1",
        description="Discuss project status",
        bypass_conflict_check=True,
    )

    assert res["status"] == "success"
    assert res["event_url"] == "https://event-link"

    # Verify event was NOT cached
    assert 77779 not in _pending_events


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_refresh_error(mock_build: MagicMock) -> None:
    from google.auth.exceptions import RefreshError
    mock_build.side_effect = RefreshError("revoked")

    db = SessionLocal()
    user = get_or_create_user(db, 77780)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = create_calendar_event(
        telegram_id=77780,
        title="Test Event",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
    )

    assert res["status"] == "error"
    assert "session has expired" in res["message"]

    db = SessionLocal()
    user = get_or_create_user(db, 77780)
    assert user.google_credentials is None
    assert user.default_calendar_id is None
    db.close()


@patch("app.tools._build_calendar_service")
def test_get_user_calendars_refresh_error(mock_build: MagicMock) -> None:
    from app.tools import get_user_calendars
    from google.auth.exceptions import RefreshError
    mock_build.side_effect = RefreshError("revoked")

    db = SessionLocal()
    user = get_or_create_user(db, 77781)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = get_user_calendars(telegram_id=77781, tool_context=None)

    assert res["status"] == "error"
    assert "session has expired" in res["message"]

    db = SessionLocal()
    user = get_or_create_user(db, 77781)
    assert user.google_credentials is None
    assert user.default_calendar_id is None
    db.close()


@patch("app.tools._build_calendar_service")
def test_get_upcoming_events_refresh_error(mock_build: MagicMock) -> None:
    from app.tools import get_upcoming_events
    from google.auth.exceptions import RefreshError
    mock_build.side_effect = RefreshError("revoked")

    db = SessionLocal()
    user = get_or_create_user(db, 77782)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = get_upcoming_events(telegram_id=77782, max_results=5)

    assert res["status"] == "error"
    assert "session has expired" in res["message"]

    db = SessionLocal()
    user = get_or_create_user(db, 77782)
    assert user.google_credentials is None
    assert user.default_calendar_id is None
    db.close()


