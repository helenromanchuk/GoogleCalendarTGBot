"""Configuration settings for CalendarBot ADK agent."""

import os
import secrets

from dotenv import load_dotenv

# Load .env from the project root (calendarbot-agent directory)
load_dotenv(
    dotenv_path=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
)


class Settings:
    """Application settings loaded from environment variables."""

    # Telegram
    bot_token: str = os.environ["BOT_TOKEN"]

    # Google Gemini / AI Studio
    gemini_api_key: str = os.environ.get(
        "GOOGLE_API_KEY", os.environ.get("GEMINI_API_KEY", "")
    )

    # Google OAuth 2.0 for Calendar
    google_client_id: str = os.environ["GOOGLE_CLIENT_ID"]
    google_client_secret: str = os.environ["GOOGLE_CLIENT_SECRET"]

    # Fernet key for encrypting OAuth tokens at rest
    encryption_key: str = os.environ["ENCRYPTION_KEY"]

    # Base URL for OAuth callback (e.g. https://my-service.run.app)
    base_url: str = os.environ.get("BASE_URL", "http://localhost:8080")

    # SQLite database path
    database_url: str = os.environ.get(
        "DATABASE_URL", "sqlite:///./data/calendar_bot.db"
    )

    # Redis URL for OAuth CSRF state storage
    redis_url: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Webhook security token (generated at startup)
    webhook_secret: str = os.environ.get("WEBHOOK_SECRET", secrets.token_urlsafe(32))

    # Gemini model name
    gemini_model: str = os.environ.get(
        "GEMINI_MODEL", "gemini-3.1-flash-lite"
    )

    # Enable mock behavior for user ID 12345 (used in integration/unit testing)
    @property
    def testing(self) -> bool:
        if os.environ.get("K_SERVICE"):
            return False
        return os.environ.get("TESTING", "false").lower() in ("true", "1", "yes")

    # Google Calendar OAuth scopes
    google_scopes: tuple[str, ...] = (
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.readonly",
    )


settings = Settings()

