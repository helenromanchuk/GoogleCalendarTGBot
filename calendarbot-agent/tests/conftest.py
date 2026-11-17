import os

import pytest

# ---------------------------------------------------------------------------
# Test environment configuration
# ---------------------------------------------------------------------------
# Static Fernet key for encryption in tests
TEST_ENCRYPTION_KEY = "u1n5HvzQrYsjaAf455NQdZSKzQR2NeSCsx-2I6snPbg="

def configure_test_environment():
    """Configure environment before test collection imports application modules."""
    os.environ["BOT_TOKEN"] = "123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
    os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
    os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
    os.environ["ENCRYPTION_KEY"] = TEST_ENCRYPTION_KEY
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["BASE_URL"] = "http://testserver"
    os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"
    os.environ["TESTING"] = "true"


configure_test_environment()

# Reset shared rate-limiter state before each test so multiple tests hitting
# the same endpoints/users don't cumulatively trip the limits.
@pytest.fixture(autouse=True)
def reset_rate_limiters():
    from app.rate_limit import reset_all

    reset_all()


# ---------------------------------------------------------------------------
# Mocking utilities
# ---------------------------------------------------------------------------
# Prevent accidental real Google Calendar API calls during tests.
@pytest.fixture(autouse=True)
def prevent_real_google_calendar_calls(monkeypatch):
    """Patch ``googleapiclient.discovery.build`` to raise if called for Calendar.

    Tests should use the ADK ``Runner`` API or mock the service directly. This
    guard makes accidental unmocked calls fail fast, providing a clear error.
    """
    import googleapiclient.discovery

    original_build = googleapiclient.discovery.build

    def mock_build(serviceName, *args, **kwargs):
        if serviceName == "calendar":
            raise RuntimeError(
                "Attempted to build a real Google Calendar service in tests. "
                "Please mock 'app.tools._build_calendar_service' or use the mock "
                "telegram_id (12345)."
            )
        return original_build(serviceName, *args, **kwargs)

    monkeypatch.setattr(googleapiclient.discovery, "build", mock_build)
