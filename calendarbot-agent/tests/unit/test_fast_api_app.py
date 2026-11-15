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

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import init_db
from app.fast_api_app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db() -> None:
    init_db()


def test_health_endpoint() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_feedback_endpoint() -> None:
    payload = {"score": 5, "user_id": "u1", "session_id": "s1", "text": "Great!"}
    response = client.post("/feedback", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@patch("app.telegram_app.bot.set_webhook", new_callable=AsyncMock)
def test_lifespan_registers_webhook_successfully(mock_set_webhook: AsyncMock) -> None:
    with TestClient(app):
        pass

    mock_set_webhook.assert_called_once()
    _, kwargs = mock_set_webhook.call_args
    assert kwargs["secret_token"] == settings.webhook_secret
    assert kwargs["drop_pending_updates"] is False


@patch("app.telegram_app.bot.set_webhook", new_callable=AsyncMock)
def test_lifespan_swallows_webhook_registration_errors(mock_set_webhook: AsyncMock) -> None:
    """If Telegram/webhook registration fails at startup (e.g. Redis or network down),
    the application must still start rather than crashing."""
    mock_set_webhook.side_effect = RuntimeError("Telegram API unreachable")

    with TestClient(app):
        pass  # Must not raise.
