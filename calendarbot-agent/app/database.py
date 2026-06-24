"""SQLAlchemy database setup with Fernet-encrypted credential storage."""

import os
from contextlib import contextmanager

from cryptography.fernet import Fernet
from sqlalchemy import BigInteger, Column, Integer, String, Text, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, declarative_base, sessionmaker
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
if settings.database_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=connect_args,
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


class CalendarDuration(Base):
    """Stores default event duration per calendar for a user."""

    __tablename__ = "calendar_durations"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(BigInteger, index=True)
    calendar_id = Column(String, index=True)
    duration_minutes = Column(Integer, default=60)


# ── Helpers ───────────────────────────────────────────────────────────────────


def init_db() -> None:
    """Create all tables (idempotent)."""
    Base.metadata.create_all(bind=engine)


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
