# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import html
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import SessionLocal, User, init_db
from app.fast_api_app import app
from app.redis_client import generate_oauth_state

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db() -> None:
    init_db()


def test_webhook_security_header_authorized() -> None:
    """Verify that /webhook succeeds with the correct secret token."""
    headers = {"X-Telegram-Bot-Api-Secret-Token": settings.webhook_secret}
    # Patch dp.feed_update to avoid running actual Telegram dispatcher logic
    with patch("app.telegram_app.dp.feed_update") as mock_feed:
        mock_feed.return_value = None
        response = client.post("/webhook", headers=headers, json={"update_id": 123})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_webhook_security_header_unauthorized() -> None:
    """Verify that /webhook rejects requests with missing or invalid secret tokens with 401."""
    # Missing token header
    response = client.post("/webhook", json={"update_id": 123})
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized webhook call"

    # Invalid token header
    headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"}
    response = client.post("/webhook", headers=headers, json={"update_id": 123})
    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized webhook call"


def test_oauth_callback_csrf_valid() -> None:
    """Verify /oauth2callback with a valid encrypted state token."""
    telegram_id = 999000111
    code_verifier = "some_code_verifier_123"
    state_token = generate_oauth_state(telegram_id, code_verifier)

    with (
        patch("google_auth_oauthlib.flow.Flow.from_client_config") as mock_flow_class,
        patch("app.telegram_app.bot.send_message") as mock_send_message,
    ):
        mock_flow = MagicMock()
        mock_flow_class.return_value = mock_flow
        mock_flow.credentials.to_json.return_value = '{"token": "test-token"}'

        response = client.get(
            "/oauth2callback",
            params={"code": "auth-code-123", "state": state_token},
        )
        assert response.status_code == 200
        assert "Authentication Successful!" in response.text

        # Verify user was saved in DB
        db = SessionLocal()
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        assert user is not None
        assert user.google_credentials == '{"token": "test-token"}'
        db.close()


def test_oauth_callback_csrf_invalid() -> None:
    """Verify /oauth2callback rejects invalid or expired state tokens with 400."""
    # Bad token
    response = client.get(
        "/oauth2callback",
        params={"code": "auth-code-123", "state": "invalid-state-token"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid or expired state parameter"

    # Missing params
    response = client.get("/oauth2callback", params={"code": "auth-code-123"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Missing code or state"


def test_oauth_callback_xss_protection() -> None:
    """Verify that error inputs are properly HTML escaped in oauth2callback response."""
    malicious_input = "<script>alert('XSS')</script>"
    response = client.get(
        "/oauth2callback",
        params={"error": malicious_input},
    )
    assert response.status_code == 200
    # The output HTML should contain escaped entities, not raw script tag
    assert html.escape(malicious_input) in response.text
    assert malicious_input not in response.text


def test_oauth_callback_exception_xss_protection() -> None:
    """Verify that exception messages are HTML-escaped on OAuth error page."""
    telegram_id = 999000222
    code_verifier = "some_code_verifier_123"
    state_token = generate_oauth_state(telegram_id, code_verifier)

    # Make Flow fetch_token raise a custom exception containing malicious HTML payload
    malicious_err_msg = "Error: <img src=x onerror=alert('XSS')>"
    with patch("google_auth_oauthlib.flow.Flow.from_client_config") as mock_flow_class:
        mock_flow = MagicMock()
        mock_flow_class.return_value = mock_flow
        mock_flow.fetch_token.side_effect = RuntimeError(malicious_err_msg)

        response = client.get(
            "/oauth2callback",
            params={"code": "auth-code-123", "state": state_token},
        )
        assert response.status_code == 500
        # The output HTML should contain escaped error message
        assert html.escape(malicious_err_msg) in response.text
        assert malicious_err_msg not in response.text
