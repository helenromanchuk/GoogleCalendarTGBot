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
"""FastAPI application for CalendarBot.

Mounts:
  - ADK's built-in /run, /sessions, /events endpoints (dev playground)
  - POST /webhook          → Telegram bot webhook receiver
  - GET  /oauth2callback   → Google OAuth 2.0 redirect handler
  - GET  /health           → Health check
"""

import html
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from google.adk.cli.fast_api import get_fast_api_app

from app.config import settings
from app.database import get_or_create_user, init_db, session_scope
from app.rate_limit import feedback_limiter, oauth_callback_limiter

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting.

    On Cloud Run the Google front end appends the real client IP as the last
    X-Forwarded-For entry (earlier entries are client-supplied and spoofable),
    so take the last one. Locally there is no XFF and we use the socket peer.
    """
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"

# ── ADK FastAPI sub-application ───────────────────────────────────────────────

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

adk_fastapi: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=None,
    allow_origins=None,
    session_service_uri=None,
)
adk_fastapi.title = "calendarbot-agent"
adk_fastapi.description = "CalendarBot — Telegram → Google Calendar via ADK"

# ── Telegram webhook constants ────────────────────────────────────────────────

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{settings.base_url.rstrip('/')}{WEBHOOK_PATH}"


# ── Lifespan (startup / shutdown) ─────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — lazily import Telegram to avoid connection errors during ADK run/playground
    logging.basicConfig(level=logging.INFO)
    init_db()

    try:
        from app.telegram_app import bot, dp  # noqa: F401

        await bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
        )
        logger.info("Telegram webhook registered at %s", WEBHOOK_URL)
    except Exception as exc:
        logger.warning("Telegram webhook setup skipped (likely missing Redis): %s", exc)

    yield

    # Shutdown
    # Do NOT delete the webhook on shutdown in serverless environments (Cloud Run)
    # so that Telegram continues to route messages and wake up instances.
    pass


# ── Main application ──────────────────────────────────────────────────────────

app = FastAPI(
    title="CalendarBot",
    description="Telegram bot that adds events to Google Calendar via ADK",
    lifespan=lifespan,
)

# Mount the ADK sub-app (playground, run, sessions endpoints)
app.mount("/adk", adk_fastapi)


# ── Custom endpoints ──────────────────────────────────────────────────────────


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    """Receive and process Telegram updates via webhook."""
    from aiogram import types as aiogram_types

    from app.telegram_app import bot, dp

    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if not secrets.compare_digest(secret_token or "", settings.webhook_secret):
        raise HTTPException(status_code=401, detail="Unauthorized webhook call")

    update_data = await request.json()
    update = aiogram_types.Update(**update_data)
    await dp.feed_update(bot=bot, update=update)
    return {"status": "ok"}


@app.get("/oauth2callback")
async def oauth2callback(request: Request):
    """Handle Google OAuth 2.0 redirect and store tokens for the user."""
    if not oauth_callback_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(
            "<h1>Authentication failed</h1>"
            f"<p>Error: {html.escape(str(error))}</p>"
            "<p>You can close this window and try again from Telegram.</p>"
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    # Verify CSRF state token
    from app.redis_client import verify_oauth_state

    state_payload = verify_oauth_state(state)
    if (
        not state_payload
        or "telegram_id" not in state_payload
        or "code_verifier" not in state_payload
    ):
        raise HTTPException(
            status_code=400, detail="Invalid or expired state parameter"
        )

    telegram_id = state_payload["telegram_id"]
    code_verifier = state_payload["code_verifier"]

    try:
        from google_auth_oauthlib.flow import Flow

        from app.tools import get_client_config

        flow = Flow.from_client_config(
            get_client_config(),
            scopes=settings.google_scopes,
            redirect_uri=f"{settings.base_url.rstrip('/')}/oauth2callback",
            code_verifier=code_verifier,
        )
        flow.code_verifier = code_verifier
        flow.fetch_token(code=code)
        credentials_json = flow.credentials.to_json()

        with session_scope() as db:
            user = get_or_create_user(db, telegram_id)
            user.google_credentials = credentials_json
            user.default_calendar_id = "primary"
            user.timezone = None
            db.commit()

        # Notify via Telegram bot
        try:
            from app.telegram_app import bot

            await bot.send_message(
                chat_id=telegram_id,
                text=(
                    "✅ Google account connected successfully!\n\n"
                    "Now send me an event description to get started.\n"
                    "Example: 'Team standup tomorrow at 9am'"
                ),
            )
        except Exception as notify_exc:
            logger.warning("Failed to send Telegram notification after OAuth: %s", notify_exc)

        return HTMLResponse(
            "<html><body style='font-family: sans-serif; text-align: center; padding: 40px;'>"
            "<h1>✅ Authentication Successful!</h1>"
            "<p>Your Google account is now connected to CalendarBot.</p>"
            "<p>You can close this window and return to Telegram.</p>"
            "</body></html>"
        )

    except Exception as exc:
        logger.exception("OAuth callback error for user %d: %s", telegram_id, exc)
        # Deliberately generic: exception text can contain internal details
        # (library errors, endpoints, token fragments) and must not reach the browser.
        return HTMLResponse(
            "<h1>An error occurred</h1>"
            "<p>Something went wrong while connecting your Google account.</p>"
            "<p>Please try again from Telegram.</p>",
            status_code=500,
        )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/feedback")
async def feedback(request: Request):
    """Collect and log user feedback."""
    if not feedback_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests")

    data = await request.json()
    logger.info("Received user feedback: %s", data)
    return {"status": "ok"}


# Main execution (local dev)
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app.fast_api_app:app", host="0.0.0.0", port=port, reload=False)
