from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.rate_limit import (
    SlidingWindowRateLimiter,
    agent_run_limiter,
    feedback_limiter,
    oauth_callback_limiter,
)


# ── SlidingWindowRateLimiter unit tests ───────────────────────────────────────


def test_limiter_allows_up_to_max_then_blocks() -> None:
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)

    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False


def test_limiter_keys_are_independent() -> None:
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)

    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.allow("b") is True


def test_limiter_window_expiry() -> None:
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)

    with patch("app.rate_limit.time.monotonic") as mock_time:
        mock_time.return_value = 1000.0
        assert limiter.allow("k") is True
        assert limiter.allow("k") is False

        # Just inside the window — still blocked.
        mock_time.return_value = 1059.0
        assert limiter.allow("k") is False

        # Past the window — allowed again.
        mock_time.return_value = 1061.0
        assert limiter.allow("k") is True


def test_limiter_evicts_lru_keys_beyond_max() -> None:
    limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60, max_keys=3)

    limiter.allow("a")
    limiter.allow("b")
    limiter.allow("c")
    limiter.allow("a")  # refresh "a" so "b" is now least recently used
    limiter.allow("d")  # evicts "b"

    assert set(limiter._hits) == {"a", "c", "d"}


def test_limiter_reset() -> None:
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False

    limiter.reset()
    assert limiter.allow("k") is True


# ── Endpoint integration ──────────────────────────────────────────────────────


def _make_client() -> TestClient:
    from app.fast_api_app import app

    return TestClient(app)


def test_feedback_endpoint_rate_limited() -> None:
    client = _make_client()
    for _ in range(feedback_limiter.max_requests):
        response = client.post("/feedback", json={"score": 5})
        assert response.status_code == 200

    response = client.post("/feedback", json={"score": 5})
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests"


def test_oauth_callback_rate_limited() -> None:
    client = _make_client()
    # Requests with an invalid state come back 400 but still count.
    for _ in range(oauth_callback_limiter.max_requests):
        response = client.get(
            "/oauth2callback", params={"code": "c", "state": "invalid"}
        )
        assert response.status_code == 400

    response = client.get("/oauth2callback", params={"code": "c", "state": "invalid"})
    assert response.status_code == 429


def test_client_ip_uses_last_xff_entry() -> None:
    """Client-supplied X-Forwarded-For entries are spoofable; only the last
    entry (appended by the Cloud Run front end) may be trusted."""
    from app.fast_api_app import _client_ip

    request = AsyncMock()
    request.headers = {"X-Forwarded-For": "6.6.6.6, 1.2.3.4"}
    assert _client_ip(request) == "1.2.3.4"

    request.headers = {}
    request.client.host = "127.0.0.1"
    assert _client_ip(request) == "127.0.0.1"

    request.client = None
    assert _client_ip(request) == "unknown"


# ── Per-user agent-run limiting ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_agent_rate_limited_per_user() -> None:
    from app.telegram_app import _run_agent

    async def _one_event():
        from unittest.mock import MagicMock

        part = MagicMock()
        part.text = "ok"
        event = MagicMock()
        event.is_final_response.return_value = True
        event.content.parts = [part]
        yield event

    with (
        patch(
            "app.telegram_app.session_service.get_session", new_callable=AsyncMock
        ) as mock_get,
        patch("app.telegram_app.runner.run_async", side_effect=lambda **kw: _one_event()),
    ):
        mock_get.return_value = object()

        for _ in range(agent_run_limiter.max_requests):
            assert await _run_agent(661000, "hi") == "ok"

        # Next call for the same user is limited without reaching the runner.
        result = await _run_agent(661000, "hi")
        assert "Too many requests" in result

        # A different user is unaffected.
        assert await _run_agent(661001, "hi") == "ok"
