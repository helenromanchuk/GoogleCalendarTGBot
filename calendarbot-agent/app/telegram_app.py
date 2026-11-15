"""Telegram bot integration for CalendarBot ADK agent.

This module sets up aiogram v3 with Redis FSM storage and defines handlers
that bridge Telegram messages to the ADK agent via the Runner API.

Architecture:
  Telegram → POST /webhook → FastAPI → aiogram dispatcher → ADK Runner
"""

import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types as genai_types

from app.agent import app as adk_app
from app.config import settings
from app.database import get_or_create_user, get_user, session_scope

logger = logging.getLogger(__name__)

# ── aiogram setup ─────────────────────────────────────────────────────────────

bot = Bot(token=settings.bot_token)

# Fallback to MemoryStorage if Redis is not running
try:
    import redis

    # Simple ping check to see if Redis is reachable
    _temp_client = redis.Redis.from_url(settings.redis_url, socket_timeout=1)
    _temp_client.ping()
    storage = RedisStorage.from_url(settings.redis_url)
    logger.info("Using RedisStorage for FSM storage")
except Exception as _exc:
    logger.warning("Redis is unavailable, falling back to MemoryStorage: %s", _exc)
    storage = MemoryStorage()

dp = Dispatcher(storage=storage)

# ── ADK Runner setup ──────────────────────────────────────────────────────────

def _to_async_db_url(url: str) -> str:
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    base = scheme.split("+")[0]
    driver_map = {
        "sqlite": "sqlite+aiosqlite",
        "postgresql": "postgresql+asyncpg",
        "postgres": "postgresql+asyncpg",
    }
    new_scheme = driver_map.get(base, scheme)
    return f"{new_scheme}://{rest}"

session_service = DatabaseSessionService(_to_async_db_url(settings.database_url))
runner = Runner(app=adk_app, session_service=session_service)

APP_NAME = adk_app.name


# ── FSM States ────────────────────────────────────────────────────────────────


class CalendarSelectionState(StatesGroup):
    waiting_for_calendar_choice = State()


async def _get_user_timezone(telegram_id: int) -> str:
    """Get the timezone for the user's default calendar, defaulting to UTC."""
    if settings.testing and telegram_id == 12345:
        return "UTC"

    def _fetch():
        with session_scope() as db:
            user = get_user(db, telegram_id)
            if not user or not user.google_credentials:
                return "UTC"
            if user.timezone:
                return user.timezone
            try:
                from app.tools import _build_calendar_service
                service, updated_creds = _build_calendar_service(user.google_credentials)
                if updated_creds != user.google_credentials:
                    user.google_credentials = updated_creds
                cal_id = user.default_calendar_id or "primary"
                calendar_metadata = service.calendars().get(calendarId=cal_id).execute()
                tz = calendar_metadata.get("timeZone", "UTC")
                user.timezone = tz
                db.commit()
                return tz
            except Exception:
                return "UTC"

    return await asyncio.to_thread(_fetch)


async def _get_user_today_str(telegram_id: int) -> str:
    """Get today's date in the user's default calendar timezone (defaulting to UTC)."""
    import pytz

    tz_name = await _get_user_timezone(telegram_id)
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.UTC

    return datetime.now(tz).strftime("%Y-%m-%d")


async def _clear_agent_session(telegram_id: int) -> None:
    """Delete the user's ADK session to avoid accumulating unbounded token history."""
    session_id = f"tg_{telegram_id}"
    try:
        await session_service.delete_session(
            app_name=APP_NAME, user_id=str(telegram_id), session_id=session_id
        )
    except Exception:
        pass


async def _run_agent(telegram_id: int, user_message: str) -> str:
    """Send a message to the ADK agent and return its text response."""
    import asyncio

    from app.rate_limit import agent_run_limiter

    AGENT_TIMEOUT_SECONDS = 30

    # Every Gemini call funnels through here, so this caps per-user LLM cost.
    if not agent_run_limiter.allow(str(telegram_id)):
        logger.warning("Agent rate limit exceeded for user %d", telegram_id)
        return "⏳ Too many requests. Please wait a minute and try again."

    session_id = f"tg_{telegram_id}"
    try:
        session = await session_service.get_session(
            app_name=APP_NAME, user_id=str(telegram_id), session_id=session_id
        )
        if not session:
            session = await session_service.create_session(
                app_name=APP_NAME,
                user_id=str(telegram_id),
                session_id=session_id,
                state={"telegram_id": telegram_id},
            )
    except Exception:
        session = await session_service.create_session(
            app_name=APP_NAME,
            user_id=str(telegram_id),
            session_id=session_id,
            state={"telegram_id": telegram_id},
        )

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_message)],
    )

    async def _collect_response() -> str:
        response_parts = []
        async for event in runner.run_async(
            user_id=str(telegram_id),
            session_id=session_id,
            new_message=content,
        ):
            if event.is_final_response() and event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        response_parts.append(part.text)
        return (
            "\n".join(response_parts)
            if response_parts
            else "Sorry, I couldn't process that. Please try again."
        )

    try:
        result = await asyncio.wait_for(_collect_response(), timeout=AGENT_TIMEOUT_SECONDS)
    except TimeoutError:
        logger.warning("Agent timed out after %ds for user %d", AGENT_TIMEOUT_SECONDS, telegram_id)
        return "⏱️ Sorry, the request timed out. Please try again."

    if "✅ Event created!" in result or "✅ Событие создано!" in result:
        await _clear_agent_session(telegram_id)

    return result


# ── Handlers ──────────────────────────────────────────────────────────────────


@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext) -> None:
    """Handle /start: check auth status and show OAuth link or calendar picker."""
    await state.clear()
    telegram_id = message.from_user.id
    await _clear_agent_session(telegram_id)

    with session_scope() as db:
        user = get_user(db, telegram_id)
        if not user or not user.google_credentials:
            from app.tools import get_google_auth_url

            res = await get_google_auth_url(telegram_id, None)
            auth_url = res["auth_url"]

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🔗 Connect Google Account",
                            url=auth_url,
                        )
                    ]
                ]
            )

            await message.answer(
                "👋 <b>Welcome to CalendarBot!</b>\n\n"
                "I'm your assistant that helps you add events to Google Calendar using natural language.\n\n"
                "🔑 <b>Getting Started:</b>\n"
                "Please connect your Google account using the button below to allow me to manage your calendar.\n\n"
                "After connecting, send me a message like:\n"
                "• <i>'Meeting with Bob tomorrow at 5pm'</i>\n"
                "• <i>'Встреча в пятницу в 14:00'</i>",
                reply_markup=keyboard,
                parse_mode="HTML",
            )
            return

        # Authenticated — check if default calendar is set
        if not user.default_calendar_id:
            # Not set — delegate to /setcalendar to pick one
            await cmd_setcalendar(message, state)
            return

    # Authenticated and calendar is set — show welcome back message
    await message.answer(
        "🟢 <b>Welcome back to CalendarBot!</b>\n\n"
        "You are fully connected and ready to go. Simply send me a message describing your event, and I will handle the rest!\n\n"
        "💡 <b>Example Messages:</b>\n"
        '• <i>"Doctor appointment next Monday at 10am"</i>\n'
        '• <i>"Встреча с клиентом в пятницу в 11:00"</i>\n'
        '• <i>"Dinner with family on Saturday at 7pm"</i>\n\n'
        "⏱️ <b>Helpful Commands:</b>\n"
        "• /upcoming — View your next 5 events\n"
        "• /setcalendar — Change your default calendar\n"
        "• /setduration — Change default event duration\n"
        "• /status — Check connection and timezone\n"
        "• /help — View the full help menu",
        parse_mode="HTML",
    )


@dp.message(Command("status"))
async def cmd_status(message: types.Message) -> None:
    """Handle /status: show authentication status, default calendar, and time zone."""
    import html

    telegram_id = message.from_user.id
    with session_scope() as db:
        user = get_user(db, telegram_id)
        if not user or not user.google_credentials:
            await message.answer(
                "🔒 <b>Google Account</b>: Not connected\n\n"
                "Please connect your Google account using /start.",
                parse_mode="HTML",
            )
            return

        from app.tools import _build_calendar_service

        try:
            if settings.testing and telegram_id == 12345:
                summary = "Primary Calendar"
                timezone = "UTC"
                cal_id = "primary"
            else:
                service, updated_creds = _build_calendar_service(user.google_credentials)
                if updated_creds != user.google_credentials:
                    user.google_credentials = updated_creds
                    db.commit()

                cal_id = user.default_calendar_id or "primary"
                calendar_metadata = service.calendars().get(calendarId=cal_id).execute()
                summary = calendar_metadata.get("summary", "Unknown")
                timezone = calendar_metadata.get("timeZone", "Unknown")
                if timezone != "Unknown" and user.timezone != timezone:
                    user.timezone = timezone
                    db.commit()

            escaped_summary = html.escape(summary)
            escaped_cal_id = html.escape(cal_id)
            escaped_timezone = html.escape(timezone)

            calendar_status = (
                f"📅 <b>Default Calendar</b>: {escaped_summary} (<code>{escaped_cal_id}</code>)"
                if user.default_calendar_id
                else f"📅 <b>Default Calendar</b>: Not explicitly chosen (using Primary: {escaped_summary})"
            )

            from app.database import get_calendar_duration
            duration = get_calendar_duration(db, telegram_id, cal_id)

            await message.answer(
                "🟢 <b>Google Account</b>: Connected\n"
                f"{calendar_status}\n"
                f"🌐 <b>Time Zone</b>: {escaped_timezone}\n"
                f"⏱️ <b>Default Duration</b>: {duration} minutes",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.exception(
                "Error fetching status details for user %d: %s", telegram_id, exc
            )
            from google.auth.exceptions import RefreshError
            from googleapiclient.errors import HttpError

            is_auth_error = isinstance(exc, RefreshError)
            if isinstance(exc, HttpError) and exc.resp.status in [400, 401, 403]:
                is_auth_error = True

            if is_auth_error:
                user.google_credentials = None
                user.default_calendar_id = None
                user.timezone = None
                db.commit()
                await message.answer(
                    "⚠️ <b>Status Error</b>: CalendarBot access has been revoked or expired.\n\n"
                    "Please run /start to connect your Google account again.",
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    "🟢 <b>Google Account</b>: Connected\n"
                    "⚠️ <b>Status Error</b>: Unable to fetch calendar information from Google. Your connection might be expired or invalid. Please run /start to reconnect.",
                    parse_mode="HTML",
                )


@dp.message(Command("setcalendar"))
async def cmd_setcalendar(message: types.Message, state: FSMContext) -> None:
    """Handle /setcalendar: allow user to choose default calendar."""
    await state.clear()
    telegram_id = message.from_user.id
    with session_scope() as db:
        user = get_user(db, telegram_id)
        if not user or not user.google_credentials:
            await message.answer(
                "🔒 <b>Google Account</b>: Not connected\n\n"
                "Please connect your Google account using /start before setting a calendar.",
                parse_mode="HTML",
            )
            return

        await _show_calendar_picker(message, telegram_id, state)


async def _show_calendar_picker(
    message: types.Message, telegram_id: int, state: FSMContext
) -> None:
    """Fetch and display the user's Google Calendars as inline buttons."""
    with session_scope() as db:
        user = get_user(db, telegram_id)
        if not user or not user.google_credentials:
            return

        from app.tools import _build_calendar_service

        try:
            service, updated_creds = _build_calendar_service(user.google_credentials)
            if updated_creds != user.google_credentials:
                user.google_credentials = updated_creds
                db.commit()

            items = service.calendarList().list().execute().get("items", [])
            calendars = [{"id": c["id"], "name": c.get("summary", c["id"])} for c in items]
        except Exception as exc:
            from google.auth.exceptions import RefreshError
            from googleapiclient.errors import HttpError

            is_auth_error = isinstance(exc, RefreshError)
            if isinstance(exc, HttpError) and exc.resp.status in [400, 401, 403]:
                is_auth_error = True

            if is_auth_error:
                logger.warning(
                    "Auth error for user %d: %s. Clearing credentials.",
                    telegram_id,
                    exc,
                )
                user.google_credentials = None
                user.default_calendar_id = None
                user.timezone = None
                db.commit()

                from app.tools import get_google_auth_url

                res = await get_google_auth_url(telegram_id, None)
                auth_url = res["auth_url"]

                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔗 Connect Google Account",
                                url=auth_url,
                            )
                        ]
                    ]
                )
                await message.answer(
                    "⚠️ <b>Access Expired or Revoked</b>\n\n"
                    "It looks like CalendarBot no longer has access to your Google account (or access was revoked).\n\n"
                    "Please click the button below to connect your account again:",
                    reply_markup=keyboard,
                    parse_mode="HTML",
                )
            else:
                logger.exception("Error listing calendars for user %d: %s", telegram_id, exc)
                await message.answer(
                    "⚠️ An error occurred while retrieving your calendars. Please try again later."
                )
            return

        if not calendars:
            await message.answer("⚠️ No calendars found in your Google account.")
            return

        # Store calendars in FSM for later reference
        await state.set_data({"calendars": calendars})
        await state.set_state(CalendarSelectionState.waiting_for_calendar_choice)

        buttons = [
            [InlineKeyboardButton(text=cal["name"], callback_data=f"cal_{i}")]
            for i, cal in enumerate(calendars)
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer(
            "📅 Which calendar should I add events to?", reply_markup=keyboard
        )


@dp.callback_query(F.data.startswith("cal_"))
async def callback_calendar_selected(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle calendar selection from inline keyboard."""
    data = await state.get_data()
    calendars = data.get("calendars", [])
    try:
        idx = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Invalid selection.")
        return

    if idx >= len(calendars):
        await callback.answer("Invalid selection.")
        return

    chosen = calendars[idx]
    telegram_id = callback.from_user.id

    try:
        with session_scope() as db:
            user = get_or_create_user(db, telegram_id)
            user.default_calendar_id = chosen["id"]
            db.commit()

            if settings.testing and telegram_id == 12345:
                timezone = "UTC"
                duration = 60
            else:
                from app.tools import _build_calendar_service

                service, updated_creds = _build_calendar_service(user.google_credentials)
                if updated_creds != user.google_credentials:
                    user.google_credentials = updated_creds
                    db.commit()
                calendar_metadata = (
                    service.calendars().get(calendarId=chosen["id"]).execute()
                )
                timezone = calendar_metadata.get("timeZone", "UTC")
                user.timezone = timezone
                db.commit()

                from app.database import get_calendar_duration
                duration = get_calendar_duration(db, telegram_id, chosen["id"])
    except Exception as exc:
        logger.exception("Error getting calendar timezone or duration for user %d: %s", telegram_id, exc)
        timezone = "UTC"
        duration = 60

    await state.clear()
    await callback.message.edit_text(
        f"✅ Default calendar set to: <b>{chosen['name']}</b> (Time Zone: <code>{timezone}</code>, Default Duration: <code>{duration} min</code>)\n\n"
        "Now send me any event to add it. For example:\n"
        "• 'Team meeting tomorrow at 3pm'\n"
        "• 'Звонок с клиентом в пятницу в 11:00'",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(Command("setduration"))
async def cmd_setduration(message: types.Message, state: FSMContext) -> None:
    """Handle /setduration: set default event duration for the chosen calendar."""
    await state.clear()
    telegram_id = message.from_user.id
    with session_scope() as db:
        user = get_user(db, telegram_id)
        if not user or not user.google_credentials:
            await message.answer(
                "🔒 <b>Google Account</b>: Not connected\n\n"
                "Please connect your Google account using /start before setting event duration.",
                parse_mode="HTML",
            )
            return

        if not user.default_calendar_id:
            await message.answer(
                "📅 <b>Default Calendar</b>: Not set\n\n"
                "Please choose a default calendar using /setcalendar before setting duration.",
                parse_mode="HTML",
            )
            return

        # Check if the user passed arguments directly, e.g. /setduration 90
        args = message.text.split(maxsplit=1)
        if len(args) == 2:
            try:
                minutes = int(args[1].strip())
                if minutes <= 0:
                    raise ValueError()

                from app.database import set_calendar_duration

                set_calendar_duration(
                    db, telegram_id, user.default_calendar_id, minutes
                )

                await message.answer(
                    f"✅ Default duration for the current calendar has been set to <b>{minutes} minutes</b>.",
                    parse_mode="HTML",
                )
                return
            except ValueError:
                await message.answer(
                    "⚠️ Please specify a valid positive number of minutes. For example: <code>/setduration 90</code>",
                    parse_mode="HTML",
                )
                return

        # Show duration picker with inline buttons
        from app.database import get_calendar_duration

        current_dur = get_calendar_duration(db, telegram_id, user.default_calendar_id)

        buttons = [
            [
                InlineKeyboardButton(text="30 min", callback_data="dur_30"),
                InlineKeyboardButton(text="1 hour", callback_data="dur_60"),
            ],
            [
                InlineKeyboardButton(text="1.5 hours", callback_data="dur_90"),
                InlineKeyboardButton(text="2 hours", callback_data="dur_120"),
            ],
            [InlineKeyboardButton(text="Cancel", callback_data="dur_cancel")],
        ]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer(
            f"⏱️ <b>Default Event Duration</b>\n\n"
            f"Current default for the chosen calendar: <b>{current_dur} minutes</b>\n"
            "Choose a new default duration below:",
            reply_markup=keyboard,
            parse_mode="HTML",
        )


@dp.callback_query(F.data.startswith("dur_"))
async def callback_duration_selected(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle duration selection from inline keyboard."""
    data = callback.data.split("_", 1)[1]
    if data == "cancel":
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("❌ Cancelled duration change.")
        await callback.answer()
        return

    try:
        minutes = int(data)
        telegram_id = callback.from_user.id
        with session_scope() as db:
            user = get_user(db, telegram_id)
            if not user or not user.default_calendar_id:
                await callback.answer("Error: no default calendar set.")
                return

            from app.database import set_calendar_duration

            set_calendar_duration(db, telegram_id, user.default_calendar_id, minutes)

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"✅ Default duration for your default calendar set to <b>{minutes} minutes</b>.",
            parse_mode="HTML",
        )
    except Exception:
        await callback.answer("An error occurred.")
    await callback.answer()


@dp.message(Command("upcoming"))
async def cmd_upcoming(message: types.Message) -> None:
    """Handle /upcoming: show next 5 events from the default calendar."""
    import html

    from app.tools import get_upcoming_events

    telegram_id = message.from_user.id
    await bot.send_chat_action(message.chat.id, "typing")

    res = await asyncio.to_thread(get_upcoming_events, telegram_id, 5)
    if res["status"] == "error":
        if "not authenticated" in res["message"].lower():
            await message.answer(
                "🔒 <b>Google Account</b>: Not connected\n\n"
                "Please connect your Google account using /start.",
                parse_mode="HTML",
            )
        else:
            await message.answer(
                f"⚠️ Error fetching events: {html.escape(res['message'])}"
            )
        return

    events = res.get("events", [])
    if not events:
        await message.answer("📅 No upcoming events found.")
        return

    response_lines = ["📅 <b>Upcoming Events (Next 5):</b>\n"]
    for idx, event in enumerate(events, 1):
        summary = html.escape(event["summary"])
        location = event.get("location")
        html_link = event.get("htmlLink", "")

        # Format time
        start = event["start"]
        end = event["end"]

        time_str = ""
        if "dateTime" in start:
            try:
                start_dt = datetime.fromisoformat(
                    start["dateTime"].replace("Z", "+00:00")
                )
                end_dt = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00"))
                time_str = f"{start_dt.strftime('%Y-%m-%d %H:%M')} - {end_dt.strftime('%H:%M')}"
            except Exception:
                time_str = start.get("dateTime", "Unknown time")
        elif "date" in start:
            time_str = f"{start.get('date')} (Whole day)"

        event_text = f"{idx}. <b>{summary}</b>\n   🕒 {time_str}"
        if location:
            event_text += f"\n   📍 {html.escape(location)}"
        if html_link:
            event_text += f'\n   🔗 <a href="{html_link}">View in Google Calendar</a>'

        response_lines.append(event_text)

    await message.answer(
        "\n\n".join(response_lines),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    """Handle /help: list available commands and usage instructions."""
    help_text = (
        "🤖 <b>CalendarBot Help</b>\n\n"
        "Here are the commands you can use:\n"
        "• /start - Start the bot, connect Google account, and set default calendar\n"
        "• /status - Show connected account status, default calendar, and time zone\n"
        "• /setcalendar - Choose which calendar to add events to\n"
        "• /setduration <code>[minutes]</code> - Change default event duration for the chosen calendar (default is 1 hour)\n"
        "• /upcoming - Show the next 5 upcoming events on your calendar\n"
        "• /help - Display this help message\n\n"
        "📅 <b>How to Add Events:</b>\n"
        "Simply send me a text message describing the event you want to create. For example:\n"
        '• <i>"Meeting with Bob tomorrow at 5pm"</i>\n'
        '• <i>"Встреча с клиентом в пятницу в 11:00"</i>\n'
        '• <i>"Team lunch next Monday"</i>\n\n'
        "If some details (like date or time) are missing, I'll prompt you using inline buttons!"
    )
    await message.answer(help_text, parse_mode="HTML")


async def _send_formatted_response(message: types.Message, response: str) -> None:
    """Format the response (convert markdown to HTML) and send it with keyboard markup if needed."""
    import html
    import re

    escaped_response = html.escape(response)
    formatted_response = re.sub(
        r"\[([^\]]+)\]\((https?://[^\)]+)\)",
        r'<a href="\2">\1</a>',
        escaped_response,
    )
    formatted_response = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", formatted_response)

    if (
        "Please specify the date for the event" in response
        or "Укажите дату для события" in response
    ):
        is_russian = "Укажите дату для события" in response
        today_text = "Сегодня" if is_russian else "Today"
        another_text = "Другая дата" if is_russian else "Another Date"
        cancel_text = "Отмена" if is_russian else "Cancel"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text=today_text, callback_data="date_today"),
                    InlineKeyboardButton(
                        text=another_text, callback_data="date_another"
                    ),
                ],
                [InlineKeyboardButton(text=cancel_text, callback_data="date_cancel")],
            ]
        )
        await message.answer(
            formatted_response, reply_markup=keyboard, parse_mode="HTML"
        )
    elif (
        "Please specify the start time for the event" in response
        or "Укажите время начала события" in response
    ):
        is_russian = "Укажите время начала события" in response
        whole_day_text = "Весь день" if is_russian else "Whole day"
        another_time_text = "Другое время" if is_russian else "Another time"
        cancel_text = "Отмена" if is_russian else "Cancel"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=whole_day_text, callback_data="time_whole_day"
                    ),
                    InlineKeyboardButton(
                        text=another_time_text, callback_data="time_another"
                    ),
                ],
                [InlineKeyboardButton(text=cancel_text, callback_data="time_cancel")],
            ]
        )
        await message.answer(
            formatted_response, reply_markup=keyboard, parse_mode="HTML"
        )
    elif (
        "Conflict detected: this event overlaps with existing" in response
        or "Обнаружен конфликт: это событие пересекается" in response
    ):
        is_russian = "Обнаружен конфликт: это событие пересекается" in response
        add_anyway_text = "Все равно добавить" if is_russian else "Add anyway"
        choose_another_text = "Выбрать другое время" if is_russian else "Choose another time"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=add_anyway_text, callback_data="conflict_add_anyway"
                    ),
                    InlineKeyboardButton(
                        text=choose_another_text, callback_data="conflict_another"
                    ),
                ]
            ]
        )
        await message.answer(
            formatted_response, reply_markup=keyboard, parse_mode="HTML"
        )
    else:
        await message.answer(formatted_response, parse_mode="HTML")


@dp.message(F.text)
async def handle_message(message: types.Message, state: FSMContext) -> None:
    """Route any text message to the ADK agent."""
    telegram_id = message.from_user.id
    user_text = message.text.strip()

    if not user_text:
        return

    with session_scope() as db:
        user = get_user(db, telegram_id)
        is_authed = bool(
            (settings.testing and telegram_id == 12345)
            or (user and user.google_credentials)
        )
        has_default_cal = bool(
            (settings.testing and telegram_id == 12345)
            or (user and user.default_calendar_id)
        )

    if not is_authed:
        from app.tools import get_google_auth_url

        res = await get_google_auth_url(telegram_id, None)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔗 Connect Google Account",
                        url=res["auth_url"],
                    )
                ]
            ]
        )
        await message.answer(
            "🔒 Please connect your Google account first using the button below, then send your message again.",
            reply_markup=keyboard,
        )
        return

    if not has_default_cal:
        await _show_calendar_picker(message, telegram_id, state)
        return

    # Include today's date context and pre-verified auth status in the message
    today = await _get_user_today_str(telegram_id)
    augmented = f"[telegram_id={telegram_id}, today={today}, auth=ok]\n{user_text}"

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        response = await _run_agent(telegram_id, augmented)
        await _send_formatted_response(message, response)
    except Exception as exc:
        logger.exception("Agent error for user %d: %s", telegram_id, exc)
        await message.answer("⚠️ Something went wrong. Please try again in a moment.")


@dp.callback_query(F.data == "date_today")
async def callback_date_today(callback: types.CallbackQuery, state: FSMContext) -> None:
    """Handle clicking 'Today' button when date is missing."""
    telegram_id = callback.from_user.id
    is_russian = "Укажите дату для события" in (callback.message.text or "")
    simulated_input = "сегодня" if is_russian else "today"

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"📅 {simulated_input.capitalize()}")

    today = await _get_user_today_str(telegram_id)
    augmented = f"[telegram_id={telegram_id}, today={today}, auth=ok]\n{simulated_input}"
    await bot.send_chat_action(callback.message.chat.id, "typing")
    try:
        response = await _run_agent(telegram_id, augmented)
        await _send_formatted_response(callback.message, response)
    except Exception as exc:
        logger.exception("Agent error for user %d: %s", telegram_id, exc)
        await callback.message.answer("⚠️ Something went wrong. Please try again.")
    await callback.answer()


@dp.callback_query(F.data == "date_another")
async def callback_date_another(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle clicking 'Another Date' button when date is missing."""
    is_russian = "Укажите дату для события" in (callback.message.text or "")
    await callback.message.edit_reply_markup(reply_markup=None)
    if is_russian:
        await callback.message.answer(
            "Пожалуйста, введите дату текстом (например: 'завтра', 'в пятницу' или конкретную дату '2026-06-25')."
        )
    else:
        await callback.message.answer(
            "Please type the date for the event (e.g. 'tomorrow', 'Friday', or a specific date like '2026-06-25')."
        )
    await callback.answer()


@dp.callback_query(F.data == "date_cancel")
async def callback_date_cancel(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle clicking 'Cancel' button when date is missing."""
    is_russian = "Укажите дату для события" in (callback.message.text or "")
    await callback.message.edit_reply_markup(reply_markup=None)
    telegram_id = callback.from_user.id
    await _clear_agent_session(telegram_id)
    if is_russian:
        await callback.message.answer("❌ Создание события отменено.")
    else:
        await callback.message.answer("❌ Event creation cancelled.")
    await callback.answer()


@dp.callback_query(F.data == "time_whole_day")
async def callback_time_whole_day(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle clicking 'Whole day' button when time is missing."""
    telegram_id = callback.from_user.id
    is_russian = "Укажите время начала события" in (callback.message.text or "")
    simulated_input = "весь день" if is_russian else "whole day"

    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"🕒 {simulated_input.capitalize()}")

    today = await _get_user_today_str(telegram_id)
    augmented = f"[telegram_id={telegram_id}, today={today}, auth=ok]\n{simulated_input}"
    await bot.send_chat_action(callback.message.chat.id, "typing")
    try:
        response = await _run_agent(telegram_id, augmented)
        await _send_formatted_response(callback.message, response)
    except Exception as exc:
        logger.exception("Agent error for user %d: %s", telegram_id, exc)
        await callback.message.answer("⚠️ Something went wrong. Please try again.")
    await callback.answer()


@dp.callback_query(F.data == "time_another")
async def callback_time_another(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle clicking 'Another time' button when time is missing."""
    is_russian = "Укажите время начала события" in (callback.message.text or "")
    await callback.message.edit_reply_markup(reply_markup=None)
    if is_russian:
        await callback.message.answer(
            "Пожалуйста, введите время начала события (например: 'в 15:00', '17:30' или 'весь день')."
        )
    else:
        await callback.message.answer(
            "Please type the start time for the event (e.g. 'at 5pm', '17:30', or 'all day')."
        )
    await callback.answer()


@dp.callback_query(F.data == "conflict_add_anyway")
async def callback_conflict_add_anyway(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle clicking 'Add anyway' button when there is a scheduling conflict."""
    telegram_id = callback.from_user.id

    from app.database import get_pending_event, session_scope, set_pending_event
    from app.tools import create_calendar_event

    with session_scope() as db:
        pending = get_pending_event(db, telegram_id)
        if pending:
            set_pending_event(db, telegram_id, None, commit=True)
    if not pending:
        await callback.answer("Error: no pending event found.")
        return

    await callback.message.edit_reply_markup(reply_markup=None)
    await bot.send_chat_action(callback.message.chat.id, "typing")

    is_all_day = pending.get("all_day", False)

    try:
        res = create_calendar_event(
            telegram_id=telegram_id,
            title=pending["title"],
            date=pending["date"],
            time=pending["time"],
            tool_context=None,
            location=pending["location"],
            description=pending["description"],
            bypass_conflict_check=True,
            all_day=is_all_day,
        )

        if res.get("status") == "success":
            await _clear_agent_session(telegram_id)
            event_url = res.get("event_url", "")
            is_russian = "Все равно добавить" in (callback.message.text or "") or "это событие пересекается" in (callback.message.text or "")

            if is_all_day:
                date_obj = datetime.fromisoformat(pending["date"])
                day_label = "Весь день" if is_russian else "All day"
                formatted_date_time = f"{date_obj.strftime('%d.%m.%Y')} ({day_label})"
            else:
                date_obj = datetime.fromisoformat(f"{pending['date']}T{pending['time']}:00")
                formatted_date_time = date_obj.strftime("%d.%m.%Y, %H:%M")

            if is_russian:
                success_text = (
                    f"✅ Событие создано!\n"
                    f"📅 <b>{formatted_date_time}</b>\n"
                    f"🏷️ <b>{pending['title']}</b>\n"
                    f"📍 <b>{pending['location'] or 'Не указано'}</b>\n"
                    f"📝 <b>{pending['description'] or 'Не указано'}</b>\n\n"
                    f"<a href=\"{event_url}\">Посмотреть в Google Календаре</a>"
                )
            else:
                success_text = (
                    f"✅ Event created!\n"
                    f"📅 <b>{formatted_date_time}</b>\n"
                    f"🏷️ <b>{pending['title']}</b>\n"
                    f"📍 <b>{pending['location'] or 'Not specified'}</b>\n"
                    f"📝 <b>{pending['description'] or 'Not specified'}</b>\n\n"
                    f"<a href=\"{event_url}\">View in Google Calendar</a>"
                )
            await callback.message.answer(success_text, parse_mode="HTML", disable_web_page_preview=True)
        else:
            await callback.message.answer(f"⚠️ Error creating event: {res.get('message')}")
    except Exception as exc:
        logger.exception("Error in callback_conflict_add_anyway for user %d: %s", telegram_id, exc)
        await callback.message.answer("⚠️ Something went wrong. Please try again.")
    await callback.answer()


@dp.callback_query(F.data == "conflict_another")
async def callback_conflict_another(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle clicking 'Choose another time' button when there is a scheduling conflict."""
    telegram_id = callback.from_user.id

    from app.database import session_scope, set_pending_event

    with session_scope() as db:
        set_pending_event(db, telegram_id, None, commit=True)

    is_russian = "Все равно добавить" in (callback.message.text or "") or "это событие пересекается" in (callback.message.text or "")

    await callback.message.edit_reply_markup(reply_markup=None)
    if is_russian:
        await callback.message.answer(
            "Пожалуйста, введите другое время начала события (например: 'в 15:00', '17:30' или 'весь день')."
        )
    else:
        await callback.message.answer(
            "Please type another start time for the event (e.g. 'at 5pm', '17:30', or 'all day')."
        )
    await callback.answer()



@dp.callback_query(F.data == "time_cancel")
async def callback_time_cancel(
    callback: types.CallbackQuery, state: FSMContext
) -> None:
    """Handle clicking 'Cancel' button when time is missing."""
    is_russian = "Укажите время начала события" in (callback.message.text or "")
    await callback.message.edit_reply_markup(reply_markup=None)
    telegram_id = callback.from_user.id
    await _clear_agent_session(telegram_id)
    if is_russian:
        await callback.message.answer("❌ Создание события отменено.")
    else:
        await callback.message.answer("❌ Event creation cancelled.")
    await callback.answer()
