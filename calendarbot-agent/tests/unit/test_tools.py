from unittest.mock import MagicMock, patch

import pytest

from app.database import SessionLocal, get_or_create_user, init_db
from app.tools import (
    create_calendar_event,
    get_google_auth_url,
    get_upcoming_events,
    get_user_auth_status,
    get_user_calendars,
    get_user_lock,
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

    # Real user in database, authenticated but no default calendar chosen yet
    db = SessionLocal()
    user = get_or_create_user(db, 88889)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()
    status = get_user_auth_status(88889, None)
    assert status["authenticated"] is True
    assert status["has_default_calendar"] is False
    assert status["default_calendar_id"] is None

    # Real user in database, authenticated with a default calendar
    db = SessionLocal()
    user = get_or_create_user(db, 88890)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "primary"
    db.commit()
    db.close()
    status = get_user_auth_status(88890, None)
    assert status["authenticated"] is True
    assert status["has_default_calendar"] is True
    assert status["default_calendar_id"] == "primary"


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
def test_create_calendar_event_all_day_uses_date_fields(mock_build_service: MagicMock) -> None:
    """All-day events must be created with Google Calendar's 'date' fields, not
    'dateTime', so they render as a genuine all-day event rather than a timed
    event with a fabricated start time."""
    mock_service = MagicMock()
    mock_build_service.return_value = (mock_service, '{"token": "test-token"}')
    mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}
    mock_service.events.return_value.insert.return_value.execute.return_value = {
        "htmlLink": "https://event-link"
    }

    db = SessionLocal()
    user = get_or_create_user(db, 77790)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = create_calendar_event(
        telegram_id=77790,
        title="Company Retreat",
        date="2026-06-25",
        time=None,
        tool_context=None,
        all_day=True,
    )

    assert res["status"] == "success"
    kwargs = mock_service.events.return_value.insert.call_args[1]
    body = kwargs["body"]
    assert body["start"] == {"date": "2026-06-25"}
    assert body["end"] == {"date": "2026-06-26"}
    assert "dateTime" not in body["start"]
    assert "timeZone" not in body["start"]

    # Calendar metadata (timezone) should never be fetched for all-day events.
    mock_service.calendars.assert_not_called()


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_all_day_conflict(mock_build_service: MagicMock) -> None:
    from app.database import get_pending_event, set_pending_event

    mock_service = MagicMock()
    mock_build_service.return_value = (mock_service, '{"token": "test-token"}')
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Existing Whole-Day Event"}]
    }

    db = SessionLocal()
    user = get_or_create_user(db, 77791)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    db = SessionLocal()
    set_pending_event(db, 77791, None, commit=True)
    db.close()

    res = create_calendar_event(
        telegram_id=77791,
        title="Company Retreat",
        date="2026-06-25",
        time=None,
        tool_context=None,
        all_day=True,
    )

    assert res["status"] == "conflict"
    assert "Existing Whole-Day Event" in res["message"]

    db = SessionLocal()
    pending = get_pending_event(db, 77791)
    assert pending is not None
    assert pending["all_day"] is True
    db.close()


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_conflict(mock_build_service: MagicMock) -> None:
    from app.database import set_pending_event, get_pending_event

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
    db = SessionLocal()
    set_pending_event(db, 77778, None, commit=True)
    db.close()

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
    db = SessionLocal()
    pending = get_pending_event(db, 77778)
    assert pending is not None
    assert pending["title"] == "Test Overlapping Event"
    db.close()


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_conflict_bypass(mock_build_service: MagicMock) -> None:
    from app.database import set_pending_event, get_pending_event

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
    db = SessionLocal()
    set_pending_event(db, 77779, None, commit=True)
    db.close()

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
    db = SessionLocal()
    assert get_pending_event(db, 77779) is None
    db.close()


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


def test_get_user_lock_lru_eviction() -> None:
    from app.tools import _MAX_USER_LOCKS, _user_locks

    _user_locks.clear()
    try:
        for i in range(_MAX_USER_LOCKS):
            get_user_lock(i)
        assert len(_user_locks) == _MAX_USER_LOCKS

        # Touch user 0 again so it becomes most-recently-used.
        get_user_lock(0)
        assert len(_user_locks) == _MAX_USER_LOCKS

        # Adding one more user should evict the least-recently-used entry (user 1),
        # not user 0 (which was just touched).
        get_user_lock(_MAX_USER_LOCKS)
        assert len(_user_locks) == _MAX_USER_LOCKS
        assert 0 in _user_locks
        assert 1 not in _user_locks
        assert _MAX_USER_LOCKS in _user_locks
    finally:
        _user_locks.clear()


def test_get_user_calendars_mock_user() -> None:
    res = get_user_calendars(telegram_id=12345, tool_context=None)
    assert res == {
        "status": "success",
        "calendars": [{"id": "primary", "name": "Primary Calendar"}],
    }


@patch("app.tools.build")
@patch("app.tools.Credentials")
def test_build_calendar_service_refreshes_expired_token(
    mock_credentials_cls: MagicMock, mock_build: MagicMock
) -> None:
    from app.tools import _build_calendar_service

    mock_creds = MagicMock()
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh-tok"
    mock_creds.to_json.return_value = '{"token": "refreshed"}'
    mock_credentials_cls.from_authorized_user_info.return_value = mock_creds

    mock_service = MagicMock()
    mock_build.return_value = mock_service

    service, creds_json = _build_calendar_service('{"token": "old"}')

    mock_creds.refresh.assert_called_once()
    assert creds_json == '{"token": "refreshed"}'
    assert service is mock_service


@patch("app.tools.build")
@patch("app.tools.Credentials")
def test_build_calendar_service_refresh_error_propagates(
    mock_credentials_cls: MagicMock, mock_build: MagicMock
) -> None:
    from google.auth.exceptions import RefreshError

    from app.tools import _build_calendar_service

    mock_creds = MagicMock()
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh-tok"
    mock_creds.refresh.side_effect = RefreshError("revoked")
    mock_credentials_cls.from_authorized_user_info.return_value = mock_creds

    with pytest.raises(RefreshError):
        _build_calendar_service('{"token": "old"}')


def test_get_user_calendars_not_authenticated() -> None:
    res = get_user_calendars(telegram_id=76001, tool_context=None)
    assert res["status"] == "error"
    assert "not authenticated" in res["message"].lower()
    assert res["calendars"] == []


@patch("app.tools._build_calendar_service")
def test_get_user_calendars_success_with_credential_refresh(mock_build: MagicMock) -> None:
    mock_service = MagicMock()
    mock_service.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "cal1", "summary": "Cal One"}, {"id": "cal2"}]
    }
    mock_build.return_value = (mock_service, '{"token": "refreshed-token"}')

    db = SessionLocal()
    user = get_or_create_user(db, 76002)
    user.google_credentials = '{"token": "old-token"}'
    db.commit()
    db.close()

    res = get_user_calendars(telegram_id=76002, tool_context=None)
    assert res["status"] == "success"
    assert res["calendars"] == [
        {"id": "cal1", "name": "Cal One"},
        {"id": "cal2", "name": "cal2"},
    ]

    db = SessionLocal()
    user = get_or_create_user(db, 76002)
    assert user.google_credentials == '{"token": "refreshed-token"}'
    db.close()


@patch("app.tools._build_calendar_service")
def test_get_user_calendars_generic_exception(mock_build: MagicMock) -> None:
    mock_build.side_effect = RuntimeError("boom")

    db = SessionLocal()
    user = get_or_create_user(db, 76003)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    res = get_user_calendars(telegram_id=76003, tool_context=None)
    assert res["status"] == "error"
    assert res["message"] == "boom"
    assert res["calendars"] == []


def test_set_default_calendar_mock_user() -> None:
    res = set_default_calendar(12345, "some-cal", None)
    assert res == {"status": "success"}


@patch("app.tools.get_or_create_user")
def test_set_default_calendar_exception(mock_get_or_create: MagicMock) -> None:
    mock_get_or_create.side_effect = RuntimeError("db exploded")

    res = set_default_calendar(76004, "some-cal", None)
    assert res["status"] == "error"
    assert res["message"] == "db exploded"


def test_create_calendar_event_mock_user() -> None:
    res = create_calendar_event(
        telegram_id=12345,
        title="t",
        date="2026-01-01",
        time="10:00",
        tool_context=None,
    )
    assert res["status"] == "success"
    assert "mock_event_12345" in res["event_url"]


def test_create_calendar_event_not_authenticated() -> None:
    res = create_calendar_event(
        telegram_id=76005,
        title="t",
        date="2026-01-01",
        time="10:00",
        tool_context=None,
    )
    assert res["status"] == "error"
    assert "not authenticated" in res["message"].lower()


def test_create_calendar_event_no_default_calendar() -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 76006)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    res = create_calendar_event(
        telegram_id=76006,
        title="t",
        date="2026-01-01",
        time="10:00",
        tool_context=None,
    )
    assert res["status"] == "error"
    assert "No default calendar" in res["message"]


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_credential_refresh_persisted(mock_build: MagicMock) -> None:
    mock_service = MagicMock()
    mock_service.calendars.return_value.get.return_value.execute.return_value = {
        "timeZone": "UTC"
    }
    mock_service.events.return_value.insert.return_value.execute.return_value = {
        "htmlLink": "https://event-link"
    }
    mock_build.return_value = (mock_service, '{"token": "refreshed"}')

    db = SessionLocal()
    user = get_or_create_user(db, 76007)
    user.google_credentials = '{"token": "old"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = create_calendar_event(
        telegram_id=76007,
        title="t",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
        bypass_conflict_check=True,
    )
    assert res["status"] == "success"

    db = SessionLocal()
    user = get_or_create_user(db, 76007)
    assert user.google_credentials == '{"token": "refreshed"}'
    db.close()


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_timezone_fetch_failure_falls_back_to_utc(
    mock_build: MagicMock,
) -> None:
    mock_service = MagicMock()
    mock_service.calendars.return_value.get.return_value.execute.side_effect = RuntimeError(
        "no access"
    )
    mock_service.events.return_value.insert.return_value.execute.return_value = {
        "htmlLink": "https://event-link"
    }
    mock_build.return_value = (mock_service, '{"token": "test-token"}')

    db = SessionLocal()
    user = get_or_create_user(db, 76008)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = create_calendar_event(
        telegram_id=76008,
        title="t",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
        bypass_conflict_check=True,
    )
    assert res["status"] == "success"
    kwargs = mock_service.events.return_value.insert.call_args[1]
    assert kwargs["body"]["start"]["timeZone"] == "UTC"


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_conflict_check_exception_still_creates_event(
    mock_build: MagicMock,
) -> None:
    mock_service = MagicMock()
    mock_service.calendars.return_value.get.return_value.execute.return_value = {
        "timeZone": "UTC"
    }
    mock_service.events.return_value.list.return_value.execute.side_effect = RuntimeError(
        "conflict check failed"
    )
    mock_service.events.return_value.insert.return_value.execute.return_value = {
        "htmlLink": "https://event-link"
    }
    mock_build.return_value = (mock_service, '{"token": "test-token"}')

    db = SessionLocal()
    user = get_or_create_user(db, 76009)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = create_calendar_event(
        telegram_id=76009,
        title="t",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
    )
    # Conflict-check failures are swallowed (logged) — the event is still created.
    assert res["status"] == "success"
    assert res["event_url"] == "https://event-link"


@patch("app.tools._build_calendar_service")
def test_create_calendar_event_generic_exception(mock_build: MagicMock) -> None:
    mock_build.side_effect = RuntimeError("boom")

    db = SessionLocal()
    user = get_or_create_user(db, 76010)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = create_calendar_event(
        telegram_id=76010,
        title="t",
        date="2026-06-25",
        time="15:30",
        tool_context=None,
    )
    assert res["status"] == "error"
    assert res["message"] == "boom"


def test_get_upcoming_events_not_authenticated() -> None:
    res = get_upcoming_events(telegram_id=76011)
    assert res["status"] == "error"
    assert "not authenticated" in res["message"].lower()


@patch("app.tools._build_calendar_service")
def test_get_upcoming_events_success(mock_build: MagicMock) -> None:
    mock_service = MagicMock()
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [
            {
                "summary": "Event A",
                "start": {"dateTime": "2026-06-25T10:00:00Z"},
                "end": {"dateTime": "2026-06-25T11:00:00Z"},
                "htmlLink": "link-a",
                "location": "Room 1",
            },
            {
                "start": {"date": "2026-06-26"},
                "end": {"date": "2026-06-27"},
            },
        ]
    }
    mock_build.return_value = (mock_service, '{"token": "refreshed"}')

    db = SessionLocal()
    user = get_or_create_user(db, 76012)
    user.google_credentials = '{"token": "old"}'
    user.default_calendar_id = "test-calendar"
    db.commit()
    db.close()

    res = get_upcoming_events(telegram_id=76012, max_results=5)
    assert res["status"] == "success"
    assert len(res["events"]) == 2
    assert res["events"][0]["summary"] == "Event A"
    assert res["events"][0]["location"] == "Room 1"
    assert res["events"][1]["summary"] == "Untitled Event"

    db = SessionLocal()
    user = get_or_create_user(db, 76012)
    assert user.google_credentials == '{"token": "refreshed"}'
    db.close()


@patch("app.tools._build_calendar_service")
def test_get_upcoming_events_generic_exception(mock_build: MagicMock) -> None:
    mock_build.side_effect = RuntimeError("boom")

    db = SessionLocal()
    user = get_or_create_user(db, 76013)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    res = get_upcoming_events(telegram_id=76013)
    assert res["status"] == "error"
    assert res["message"] == "boom"


