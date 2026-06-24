from sqlalchemy import text

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
    from app.database import CalendarDuration, get_calendar_duration, set_calendar_duration

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
