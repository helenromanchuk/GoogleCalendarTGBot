from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from app.database import SessionLocal, User, get_or_create_user, get_user, init_db


def test_database_operations() -> None:
    # Initialize DB (in-memory SQLite from conftest.py)
    init_db()
    db = SessionLocal()
    try:
        db.query(User).delete()
        db.commit()

        telegram_id = 555444333

        # User shouldn't exist initially
        user = get_user(db, telegram_id)
        assert user is None

        # Create user
        user = get_or_create_user(db, telegram_id)
        assert user is not None
        assert user.telegram_id == telegram_id
        assert user.google_credentials is None

        # Update credentials and default calendar
        creds_json = '{"token": "test-oauth-token", "refresh_token": "test-refresh"}'
        user.google_credentials = creds_json
        user.default_calendar_id = "test-cal-123"
        db.commit()

        # Fetch again to verify values are decrypted correctly
        db.expire_all()
        fetched_user = get_user(db, telegram_id)
        assert fetched_user is not None
        assert fetched_user.google_credentials == creds_json
        assert fetched_user.default_calendar_id == "test-cal-123"

        # Query raw database to verify encryption at rest
        raw_result = db.execute(
            text("SELECT google_credentials FROM users WHERE telegram_id = :id"),
            {"id": telegram_id},
        ).fetchone()

        assert raw_result is not None
        raw_creds = raw_result[0]
        # Raw credentials in database must be encrypted (should not be plain JSON string)
        assert raw_creds is not None
        assert creds_json not in raw_creds

    finally:
        db.close()


def test_calendar_duration_operations() -> None:
    from app.database import (
        CalendarDuration,
        get_calendar_duration,
        set_calendar_duration,
    )

    db = SessionLocal()
    try:
        db.query(CalendarDuration).delete()
        db.commit()

        # Default duration should be 60 if not set
        duration = get_calendar_duration(db, 999111, "calendar-abc")
        assert duration == 60

        # Set duration
        set_calendar_duration(db, 999111, "calendar-abc", 45)
        duration = get_calendar_duration(db, 999111, "calendar-abc")
        assert duration == 45

        # Check another calendar for the same user is still default (60)
        duration = get_calendar_duration(db, 999111, "calendar-xyz")
        assert duration == 60

        # Update duration
        set_calendar_duration(db, 999111, "calendar-abc", 90)
        duration = get_calendar_duration(db, 999111, "calendar-abc")
        assert duration == 90
    finally:
        db.close()


def test_get_set_calendar_duration_empty_calendar_id_is_noop() -> None:
    from app.database import (
        CalendarDuration,
        get_calendar_duration,
        set_calendar_duration,
    )

    db = SessionLocal()
    try:
        # An empty/falsy calendar_id should just return the default without querying.
        assert get_calendar_duration(db, 76101, "") == 60
        assert get_calendar_duration(db, 76101, None) == 60

        # Setting a duration with an empty/falsy calendar_id should be a no-op.
        db.query(CalendarDuration).filter(CalendarDuration.telegram_id == 76102).delete()
        db.commit()
        set_calendar_duration(db, 76102, "", 90)
        set_calendar_duration(db, 76102, None, 90)
        remaining = (
            db.query(CalendarDuration)
            .filter(CalendarDuration.telegram_id == 76102)
            .all()
        )
        assert remaining == []
    finally:
        db.close()


def test_get_pending_event_malformed_json_returns_none() -> None:
    from app.database import get_pending_event

    db = SessionLocal()
    try:
        user = get_or_create_user(db, 76103)
        user.pending_event_data = "not valid json{{{"
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        assert get_pending_event(db, 76103) is None
    finally:
        db.close()


def test_get_or_create_user_integrity_error_race() -> None:
    """Simulates two concurrent requests creating the same user row.

    get_or_create_user must recover from the IntegrityError raised when a
    concurrent insert wins the race, by rolling back and re-fetching.
    """
    import app.database as database_module

    telegram_id = 76100

    # Pre-insert the row directly, simulating another request that already
    # committed the same user between our SELECT and INSERT.
    setup_db = SessionLocal()
    setup_db.add(User(telegram_id=telegram_id))
    setup_db.commit()
    setup_db.close()

    original_get_user = database_module.get_user
    call_count = {"n": 0}

    def fake_get_user(db_arg, tid):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None
        return original_get_user(db_arg, tid)

    db = SessionLocal()
    try:
        with patch("app.database.get_user", side_effect=fake_get_user):
            user = get_or_create_user(db, telegram_id)

        assert user is not None
        assert user.telegram_id == telegram_id
        assert call_count["n"] == 2
    finally:
        db.close()


def test_migrate_db_noop_when_column_already_exists() -> None:
    """The shared test DB already has pending_event_data; migrate_db must be a safe no-op."""
    from app.database import migrate_db

    migrate_db()
    migrate_db()


def test_postgres_drivers_available() -> None:
    """Both Postgres drivers must be installed: psycopg2 for the sync engine
    (database.py builds it straight from DATABASE_URL) and asyncpg for the
    async engine (telegram_app._to_async_db_url maps postgresql → asyncpg for
    ADK's DatabaseSessionService). Engine construction imports the DBAPI, so
    this fails if either driver is missing from the dependencies — no live
    Postgres server is needed.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.telegram_app import _to_async_db_url

    pg_url = "postgresql://user:pass@localhost:5432/calendarbot"

    sync_engine = create_engine(pg_url)
    assert sync_engine.dialect.driver == "psycopg2"
    sync_engine.dispose()

    async_engine = create_async_engine(_to_async_db_url(pg_url))
    assert async_engine.dialect.driver == "asyncpg"


def test_migrate_db_adds_missing_column(tmp_path, monkeypatch) -> None:
    """On a legacy schema missing pending_event_data, migrate_db must add it."""
    import app.database as database_module

    db_file = tmp_path / "migrate_test.db"
    temp_engine = create_engine(f"sqlite:///{db_file}")
    try:
        with temp_engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE users ("
                    "telegram_id INTEGER PRIMARY KEY, "
                    "google_credentials TEXT, "
                    "default_calendar_id TEXT, "
                    "default_duration_minutes INTEGER)"
                )
            )
            conn.commit()

        monkeypatch.setattr(database_module, "engine", temp_engine)

        database_module.migrate_db()

        with temp_engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        assert "pending_event_data" in cols
        assert "timezone" in cols
    finally:
        temp_engine.dispose()


def test_pending_event_is_consumed_once_under_concurrency(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session, sessionmaker

    from app.database import (
        Base,
        consume_pending_event,
        get_pending_event,
        set_pending_event,
    )

    temp_engine = create_engine(f"sqlite:///{tmp_path / 'claims.db'}")
    Base.metadata.create_all(temp_engine)
    both_read = Barrier(2)

    class ConcurrentSession(Session):
        def scalar(self, *args, **kwargs):
            value = super().scalar(*args, **kwargs)
            both_read.wait(timeout=10)
            return value

    sessions = sessionmaker(bind=temp_engine, class_=ConcurrentSession)
    payload = {"title": "Private meeting", "date": "2026-06-25", "time": "20:00"}
    try:
        with Session(temp_engine) as db:
            set_pending_event(db, 90210, payload, commit=True)
            raw = db.execute(text("SELECT pending_event_data FROM users")).scalar_one()
            assert "Private meeting" not in raw

        def claim(_):
            with sessions() as db:
                return consume_pending_event(db, 90210)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        assert results.count(payload) == 1
        assert results.count(None) == 1
        with Session(temp_engine) as db:
            assert get_pending_event(db, 90210) is None
            assert consume_pending_event(db, 90210) is None
            assert consume_pending_event(db, 99999) is None
    finally:
        temp_engine.dispose()


@pytest.mark.parametrize("legacy_schema", [False, True])
def test_concurrent_initialization_serializes_schema_changes(tmp_path, monkeypatch, legacy_schema):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    import app.database as database_module

    temp_engine = create_engine(f"sqlite:///{tmp_path / 'rollout.db'}")
    try:
        if legacy_schema:
            with temp_engine.begin() as conn:
                conn.execute(text("CREATE TABLE users (telegram_id INTEGER PRIMARY KEY)"))
        monkeypatch.setattr(database_module, "engine", temp_engine)
        start = Barrier(4)

        def initialize(_):
            start.wait(timeout=10)
            database_module.init_db()

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(initialize, range(4)))

        with temp_engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        assert {"pending_event_data", "timezone"} <= cols
    finally:
        temp_engine.dispose()
