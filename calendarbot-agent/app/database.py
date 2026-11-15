"""SQLAlchemy database setup with Fernet-encrypted credential storage."""

import os
from contextlib import contextmanager

from cryptography.fernet import Fernet
from sqlalchemy import (
    BigInteger,
    Column,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator

from app.config import settings

# ── Encryption ────────────────────────────────────────────────────────────────

fernet = Fernet(settings.encryption_key.encode("utf-8"))


class EncryptedText(TypeDecorator):
    """Transparent Fernet encrypt/decrypt for SQLAlchemy Text columns."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            return fernet.encrypt(value.encode("utf-8")).decode("utf-8")
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        return value


# ── Engine & Session ──────────────────────────────────────────────────────────

if settings.database_url.startswith("sqlite:///"):
    db_path = settings.database_url.replace("sqlite:///", "")
    db_dir = os.path.dirname(db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

connect_args = {}
engine_options = {}
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False
    if settings.database_url in ("sqlite://", "sqlite:///:memory:"):
        # Worker threads must see the same in-memory database and schema.
        engine_options["poolclass"] = StaticPool

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
    **engine_options,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Models ────────────────────────────────────────────────────────────────────


class User(Base):
    """Stores per-user Google OAuth credentials and preferences."""

    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    google_credentials = Column(
        EncryptedText, nullable=True
    )  # JSON string, Fernet-encrypted
    default_calendar_id = Column(String, nullable=True)
    default_duration_minutes = Column(Integer, default=60)
    pending_event_data = Column(EncryptedText, nullable=True)  # JSON string, Fernet-encrypted
    timezone = Column(String, nullable=True)
    timezone_fetched_at = Column(BigInteger, nullable=True)


class CalendarDuration(Base):
    """Stores default event duration per calendar for a user."""

    __tablename__ = "calendar_durations"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, index=True)
    calendar_id = Column(String, index=True)
    duration_minutes = Column(Integer, default=60)


# ── Helpers ───────────────────────────────────────────────────────────────────


def init_db() -> None:
    """Create all tables (idempotent) and apply additive schema migrations."""
    with _schema_connection() as conn:
        Base.metadata.create_all(bind=conn)
        _apply_migrations(conn)


@contextmanager
def _schema_connection():
    """Serialize schema inspection and DDL across application instances."""
    with engine.begin() as conn:
        if conn.dialect.name == "sqlite":
            conn.exec_driver_sql("BEGIN IMMEDIATE")
        elif conn.dialect.name == "postgresql":
            # Stable, application-specific transaction lock, including create_all.
            conn.execute(text("SELECT pg_advisory_xact_lock(734162390418)"))
        yield conn


def migrate_db() -> None:
    """Apply additive schema changes that create_all() cannot handle."""
    with _schema_connection() as conn:
        _apply_migrations(conn)


def _apply_migrations(conn) -> None:
    existing = {column["name"] for column in inspect(conn).get_columns("users")}
    if "pending_event_data" not in existing:
        conn.execute(text("ALTER TABLE users ADD COLUMN pending_event_data TEXT"))
    if "timezone" not in existing:
        conn.execute(text("ALTER TABLE users ADD COLUMN timezone TEXT"))
    if "timezone_fetched_at" not in existing:
        conn.execute(text("ALTER TABLE users ADD COLUMN timezone_fetched_at BIGINT"))


# Database tables are initialized explicitly in FastAPI lifespan or in test setup.


def get_db() -> Session:  # type: ignore[return]
    """Context-manager-style DB session factory."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Session:  # type: ignore[return]
    """Provide a transactional scope around a series of operations.

    Usage::

        with session_scope() as db:
            user = get_user(db, telegram_id)
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_user(db: Session, telegram_id: int) -> User | None:
    """Fetch a user by Telegram ID, or None if not found."""
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def get_or_create_user(db: Session, telegram_id: int) -> User:
    """Fetch or create a user row (safe under concurrent access)."""
    user = get_user(db, telegram_id)
    if not user:
        try:
            user = User(telegram_id=telegram_id)
            db.add(user)
            db.commit()
            db.refresh(user)
        except IntegrityError:
            db.rollback()
            user = get_user(db, telegram_id)
    return user


def get_calendar_duration(db: Session, telegram_id: int, calendar_id: str) -> int:
    """Get default event duration (in minutes) for a specific calendar."""
    if not calendar_id:
        return 60
    pref = (
        db.query(CalendarDuration)
        .filter(
            CalendarDuration.telegram_id == telegram_id,
            CalendarDuration.calendar_id == calendar_id,
        )
        .first()
    )
    return pref.duration_minutes if pref else 60


def set_calendar_duration(
    db: Session, telegram_id: int, calendar_id: str, minutes: int
) -> None:
    """Set default event duration (in minutes) for a specific calendar."""
    if not calendar_id:
        return
    pref = (
        db.query(CalendarDuration)
        .filter(
            CalendarDuration.telegram_id == telegram_id,
            CalendarDuration.calendar_id == calendar_id,
        )
        .first()
    )
    if not pref:
        pref = CalendarDuration(
            telegram_id=telegram_id,
            calendar_id=calendar_id,
            duration_minutes=minutes,
        )
        db.add(pref)
    else:
        pref.duration_minutes = minutes
    db.commit()


def get_pending_event(db: Session, telegram_id: int) -> dict | None:
    """Get pending event data for a user."""
    import json
    user = get_user(db, telegram_id)
    if user and user.pending_event_data:
        try:
            return json.loads(user.pending_event_data)
        except json.JSONDecodeError:
            pass
    return None


def set_pending_event(
    db: Session, telegram_id: int, data: dict | None, *, commit: bool = False
) -> None:
    """Set or clear pending event data for a user."""
    import json
    user = get_or_create_user(db, telegram_id)
    if data is None:
        user.pending_event_data = None
    else:
        user.pending_event_data = json.dumps(data)

    if commit:
        db.commit()


def consume_pending_event(db: Session, telegram_id: int) -> dict | None:
    """Claim and clear a confirmation; only one concurrent caller gets its data."""
    import json

    # Compare ciphertext directly: EncryptedText would encrypt a bound plaintext
    # again with a different Fernet nonce, so it cannot be used for equality.
    ciphertext_column = User.pending_event_data.cast(Text)
    ciphertext = db.scalar(
        select(ciphertext_column).where(User.telegram_id == telegram_id)
    )
    if ciphertext is None:
        return None
    claimed = db.execute(
        update(User)
        .where(User.telegram_id == telegram_id, ciphertext_column == ciphertext)
        .values(pending_event_data=None)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    if claimed != 1:
        return None
    try:
        return json.loads(fernet.decrypt(ciphertext.encode("utf-8")))
    except json.JSONDecodeError:
        return None
