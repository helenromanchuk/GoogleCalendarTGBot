import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.fsm.context import FSMContext

from app.database import CalendarDuration, SessionLocal, User, get_or_create_user
from app.telegram_app import (
    _send_formatted_response,
    callback_duration_selected,
    cmd_help,
    cmd_setduration,
    cmd_start,
    cmd_upcoming,
)


def _make_final_event(text: str) -> MagicMock:
    part = MagicMock()
    part.text = text
    event = MagicMock()
    event.is_final_response.return_value = True
    event.content.parts = [part]
    return event


async def _async_gen(items):
    for item in items:
        yield item


@pytest.fixture(autouse=True)
def setup_db() -> None:
    from app.database import init_db

    init_db()
    db = SessionLocal()
    try:
        db.query(User).delete()
        db.query(CalendarDuration).delete()
        db.commit()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_send_formatted_response_standard() -> None:
    message = AsyncMock()
    response = "Your event has been created. [View in Google Calendar](https://calendar.google.com/event)."

    await _send_formatted_response(message, response)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "View in Google Calendar" in args[0]
    assert "href=" in args[0]
    assert "reply_markup" not in kwargs


@pytest.mark.asyncio
async def test_send_formatted_response_missing_date_english() -> None:
    message = AsyncMock()
    response = "📅 Please specify the date for the event:"

    await _send_formatted_response(message, response)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Please specify the date for the event" in args[0]
    assert "reply_markup" in kwargs
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Today"
    assert keyboard.inline_keyboard[0][0].callback_data == "date_today"
    assert keyboard.inline_keyboard[0][1].text == "Another Date"
    assert keyboard.inline_keyboard[0][1].callback_data == "date_another"
    assert keyboard.inline_keyboard[1][0].text == "Cancel"
    assert keyboard.inline_keyboard[1][0].callback_data == "date_cancel"


@pytest.mark.asyncio
async def test_send_formatted_response_missing_date_russian() -> None:
    message = AsyncMock()
    response = "📅 Укажите дату для события:"

    await _send_formatted_response(message, response)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Укажите дату для события" in args[0]
    assert "reply_markup" in kwargs
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Сегодня"
    assert keyboard.inline_keyboard[0][0].callback_data == "date_today"
    assert keyboard.inline_keyboard[0][1].text == "Другая дата"
    assert keyboard.inline_keyboard[0][1].callback_data == "date_another"
    assert keyboard.inline_keyboard[1][0].text == "Отмена"
    assert keyboard.inline_keyboard[1][0].callback_data == "date_cancel"


@pytest.mark.asyncio
async def test_send_formatted_response_missing_time_english() -> None:
    message = AsyncMock()
    response = "🕒 Please specify the start time for the event:"

    await _send_formatted_response(message, response)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Please specify the start time for the event" in args[0]
    assert "reply_markup" in kwargs
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Whole day"
    assert keyboard.inline_keyboard[0][0].callback_data == "time_whole_day"
    assert keyboard.inline_keyboard[0][1].text == "Another time"
    assert keyboard.inline_keyboard[0][1].callback_data == "time_another"
    assert keyboard.inline_keyboard[1][0].text == "Cancel"
    assert keyboard.inline_keyboard[1][0].callback_data == "time_cancel"


@pytest.mark.asyncio
async def test_send_formatted_response_missing_time_russian() -> None:
    message = AsyncMock()
    response = "🕒 Укажите время начала события:"

    await _send_formatted_response(message, response)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Укажите время начала события" in args[0]
    assert "reply_markup" in kwargs
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Весь день"
    assert keyboard.inline_keyboard[0][0].callback_data == "time_whole_day"
    assert keyboard.inline_keyboard[0][1].text == "Другое время"
    assert keyboard.inline_keyboard[0][1].callback_data == "time_another"
    assert keyboard.inline_keyboard[1][0].text == "Отмена"
    assert keyboard.inline_keyboard[1][0].callback_data == "time_cancel"


@pytest.mark.asyncio
async def test_cmd_setduration_not_connected() -> None:
    message = AsyncMock()
    message.from_user.id = 999001
    message.text = "/setduration"
    state = AsyncMock(spec=FSMContext)

    await cmd_setduration(message, state)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Not connected" in args[0]


@pytest.mark.asyncio
async def test_cmd_setduration_no_calendar() -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 999002)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 999002
    message.text = "/setduration"
    state = AsyncMock(spec=FSMContext)

    await cmd_setduration(message, state)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Not set" in args[0]


@pytest.mark.asyncio
async def test_cmd_setduration_with_arg() -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 999003)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "cal-abc"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 999003
    message.text = "/setduration 45"
    state = AsyncMock(spec=FSMContext)

    await cmd_setduration(message, state)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "45 minutes" in args[0]

    # Verify db was updated
    db = SessionLocal()
    from app.database import get_calendar_duration

    assert get_calendar_duration(db, 999003, "cal-abc") == 45
    db.close()


@pytest.mark.asyncio
async def test_cmd_setduration_show_picker() -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 999004)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "cal-abc"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 999004
    message.text = "/setduration"
    state = AsyncMock(spec=FSMContext)

    await cmd_setduration(message, state)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Choose a new default duration" in args[0]
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 3


@pytest.mark.asyncio
async def test_callback_duration_selected() -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 999005)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "cal-abc"
    db.commit()
    db.close()

    callback = AsyncMock()
    callback.from_user.id = 999005
    callback.data = "dur_90"
    state = AsyncMock(spec=FSMContext)

    await callback_duration_selected(callback, state)

    callback.message.edit_reply_markup.assert_called_once()
    callback.message.answer.assert_called_once()
    args, _ = callback.message.answer.call_args
    assert "90 minutes" in args[0]

    # Verify db was updated
    db = SessionLocal()
    from app.database import get_calendar_duration

    assert get_calendar_duration(db, 999005, "cal-abc") == 90
    db.close()


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action")
async def test_cmd_upcoming_not_connected(mock_chat_action: MagicMock) -> None:
    message = AsyncMock()
    message.from_user.id = 999123

    await cmd_upcoming(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Not connected" in args[0]


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action")
async def test_cmd_upcoming_mock_user(mock_chat_action: MagicMock) -> None:
    message = AsyncMock()
    message.from_user.id = 12345

    await cmd_upcoming(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Mock Event 1" in args[0]
    assert "Mock Event 2" in args[0]


@pytest.mark.asyncio
async def test_cmd_help() -> None:
    message = AsyncMock()
    await cmd_help(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "CalendarBot Help" in args[0]
    assert "/start" in args[0]
    assert "/status" in args[0]
    assert "/setcalendar" in args[0]
    assert "/setduration" in args[0]
    assert "/upcoming" in args[0]
    assert "/help" in args[0]


@pytest.mark.asyncio
async def test_cmd_start_not_connected() -> None:
    message = AsyncMock()
    message.from_user.id = 888001
    state = AsyncMock(spec=FSMContext)

    await cmd_start(message, state)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Welcome to CalendarBot" in args[0]
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
@patch("app.telegram_app.cmd_setcalendar")
async def test_cmd_start_no_calendar(mock_setcalendar: MagicMock) -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 888002)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 888002
    state = AsyncMock(spec=FSMContext)

    await cmd_start(message, state)

    mock_setcalendar.assert_called_once_with(message, state)


@pytest.mark.asyncio
@patch("app.telegram_app.cmd_setcalendar")
async def test_cmd_start_fully_configured(mock_setcalendar: MagicMock) -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 888003)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "primary"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 888003
    state = AsyncMock(spec=FSMContext)

    await cmd_start(message, state)

    mock_setcalendar.assert_not_called()
    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Welcome back to CalendarBot" in args[0]


@pytest.mark.asyncio
async def test_callback_calendar_selected() -> None:
    from app.telegram_app import callback_calendar_selected

    # Setup database with a user
    db = SessionLocal()
    user = get_or_create_user(db, 888004)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    # Prepare callback mock
    callback = AsyncMock()
    callback.from_user.id = 888004
    callback.data = "cal_0"

    # Mock state data with calendars list
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {
        "calendars": [{"id": "primary", "name": "Primary Calendar"}]
    }

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_calendars = MagicMock()
        mock_get = MagicMock()

        mock_build.return_value = (mock_service, '{"token": "test-token"}')
        mock_service.calendars.return_value = mock_calendars
        mock_calendars.get.return_value = mock_get
        mock_get.execute.return_value = {"timeZone": "Europe/Moscow"}

        await callback_calendar_selected(callback, state)

    # Verify state cleared and confirmation sent with timezone and duration
    state.clear.assert_called_once()
    callback.message.edit_text.assert_called_once()
    args, _ = callback.message.edit_text.call_args
    assert (
        "Default calendar set to: <b>Primary Calendar</b> (Time Zone: <code>Europe/Moscow</code>, Default Duration: <code>60 min</code>)"
        in args[0]
    )


@pytest.mark.asyncio
async def test_cmd_status_connected() -> None:
    from app.telegram_app import cmd_status

    # Setup database with a user
    db = SessionLocal()
    user = get_or_create_user(db, 888005)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "primary"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 888005

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_calendars = MagicMock()
        mock_get = MagicMock()

        mock_build.return_value = (mock_service, '{"token": "test-token"}')
        mock_service.calendars.return_value = mock_calendars
        mock_calendars.get.return_value = mock_get
        mock_get.execute.return_value = {
            "summary": "Primary Calendar",
            "timeZone": "Europe/Moscow",
        }

        await cmd_status(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Default Calendar" in args[0]
    assert "Primary Calendar" in args[0]
    assert "Europe/Moscow" in args[0]
    assert "Default Duration" in args[0]
    assert "60 minutes" in args[0]


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action")
async def test_callback_time_whole_day(mock_chat_action: MagicMock) -> None:
    from app.telegram_app import callback_time_whole_day

    callback = AsyncMock()
    callback.from_user.id = 888006
    callback.message.text = "🕒 Please specify the start time for the event:"

    state = AsyncMock(spec=FSMContext)

    with (
        patch("app.telegram_app._run_agent") as mock_run_agent,
        patch("app.telegram_app._send_formatted_response") as mock_send,
    ):
        mock_run_agent.return_value = "✅ Event created!"

        await callback_time_whole_day(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with("🕒 Whole day")
    mock_run_agent.assert_called_once()
    mock_send.assert_called_once_with(callback.message, "✅ Event created!")
    callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_time_another() -> None:
    from app.telegram_app import callback_time_another

    callback = AsyncMock()
    callback.message.text = "🕒 Please specify the start time for the event:"

    state = AsyncMock(spec=FSMContext)

    await callback_time_another(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with(
        "Please type the start time for the event (e.g. 'at 5pm', '17:30', or 'all day')."
    )
    callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_time_cancel() -> None:
    from app.telegram_app import callback_time_cancel

    callback = AsyncMock()
    callback.from_user.id = 888007
    callback.message.text = "🕒 Please specify the start time for the event:"

    state = AsyncMock(spec=FSMContext)

    with patch("app.telegram_app.session_service.delete_session") as mock_delete:
        await callback_time_cancel(callback, state)
        mock_delete.assert_called_once()

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with("❌ Event creation cancelled.")
    callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_send_formatted_response_conflict_english() -> None:
    message = AsyncMock()
    response = "⚠️ Conflict detected: this event overlaps with existing event(s): 'Busy'."

    await _send_formatted_response(message, response)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Conflict detected" in args[0]
    assert "reply_markup" in kwargs
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].text == "Add anyway"
    assert keyboard.inline_keyboard[0][0].callback_data == "conflict_add_anyway"
    assert keyboard.inline_keyboard[0][1].text == "Choose another time"
    assert keyboard.inline_keyboard[0][1].callback_data == "conflict_another"


@pytest.mark.asyncio
async def test_send_formatted_response_conflict_russian() -> None:
    message = AsyncMock()
    response = "⚠️ Обнаружен конфликт: это событие пересекается с существующими событиями: 'Busy'."

    await _send_formatted_response(message, response)

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Обнаружен конфликт" in args[0]
    assert "reply_markup" in kwargs
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 1
    assert keyboard.inline_keyboard[0][0].text == "Все равно добавить"
    assert keyboard.inline_keyboard[0][0].callback_data == "conflict_add_anyway"
    assert keyboard.inline_keyboard[0][1].text == "Выбрать другое время"
    assert keyboard.inline_keyboard[0][1].callback_data == "conflict_another"


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action")
async def test_callback_conflict_add_anyway_success(mock_chat_action: MagicMock) -> None:
    from app.database import get_pending_event, session_scope, set_pending_event
    from app.telegram_app import callback_conflict_add_anyway

    telegram_id = 999123
    with session_scope() as db:
        set_pending_event(db, telegram_id, {
            "title": "Conflicting Party",
            "date": "2026-06-25",
            "time": "18:00",
            "location": "Lobby",
            "description": "Party description",
        }, commit=True)

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.message.text = "⚠️ Conflict detected: this event overlaps with existing event(s): 'Busy'."
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools.create_calendar_event") as mock_create:
        mock_create.return_value = {
            "status": "success",
            "event_url": "https://calendar.google.com/event?id=123",
        }

        await callback_conflict_add_anyway(callback, state)

        mock_create.assert_called_once_with(
            telegram_id=telegram_id,
            title="Conflicting Party",
            date="2026-06-25",
            time="18:00",
            tool_context=None,
            location="Lobby",
            description="Party description",
            bypass_conflict_check=True,
            all_day=False,
        )

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once()
    args, _kwargs = callback.message.answer.call_args
    assert "✅ Event created!" in args[0]
    assert "Conflicting Party" in args[0]
    assert "https://calendar.google.com/event?id=123" in args[0]
    callback.answer.assert_called_once()
    with session_scope() as db:
        assert get_pending_event(db, telegram_id) is None


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action")
async def test_callback_conflict_add_anyway_russian_success(mock_chat_action: MagicMock) -> None:
    from app.database import get_pending_event, session_scope, set_pending_event
    from app.telegram_app import callback_conflict_add_anyway

    telegram_id = 999124
    with session_scope() as db:
        set_pending_event(db, telegram_id, {
            "title": "Встреча",
            "date": "2026-06-25",
            "time": "18:00",
            "location": None,
            "description": None,
        }, commit=True)

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.message.text = "⚠️ Обнаружен конфликт: это событие пересекается с существующими событиями: 'Busy'."
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools.create_calendar_event") as mock_create:
        mock_create.return_value = {
            "status": "success",
            "event_url": "https://calendar.google.com/event?id=124",
        }

        await callback_conflict_add_anyway(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once()
    args, _kwargs = callback.message.answer.call_args
    assert "✅ Событие создано!" in args[0]
    assert "Встреча" in args[0]
    callback.answer.assert_called_once()
    with session_scope() as db:
        assert get_pending_event(db, telegram_id) is None


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action")
async def test_callback_conflict_add_anyway_all_day_success(mock_chat_action: MagicMock) -> None:
    from app.database import get_pending_event, session_scope, set_pending_event
    from app.telegram_app import callback_conflict_add_anyway

    telegram_id = 999128
    with session_scope() as db:
        set_pending_event(db, telegram_id, {
            "title": "Company Retreat",
            "date": "2026-06-25",
            "time": None,
            "location": None,
            "description": None,
            "all_day": True,
        }, commit=True)

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.message.text = "⚠️ Conflict detected: this event overlaps with existing event(s): 'Busy'."
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools.create_calendar_event") as mock_create:
        mock_create.return_value = {
            "status": "success",
            "event_url": "https://calendar.google.com/event?id=125",
        }

        await callback_conflict_add_anyway(callback, state)

        mock_create.assert_called_once_with(
            telegram_id=telegram_id,
            title="Company Retreat",
            date="2026-06-25",
            time=None,
            tool_context=None,
            location=None,
            description=None,
            bypass_conflict_check=True,
            all_day=True,
        )

    callback.message.answer.assert_called_once()
    args, _ = callback.message.answer.call_args
    assert "Company Retreat" in args[0]
    assert "All day" in args[0]
    assert "25.06.2026" in args[0]
    with session_scope() as db:
        assert get_pending_event(db, telegram_id) is None


@pytest.mark.asyncio
async def test_callback_conflict_add_anyway_no_pending() -> None:
    from app.database import session_scope, set_pending_event
    from app.telegram_app import callback_conflict_add_anyway

    telegram_id = 999125
    with session_scope() as db:
        set_pending_event(db, telegram_id, None, commit=True)

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    state = AsyncMock(spec=FSMContext)

    await callback_conflict_add_anyway(callback, state)

    callback.answer.assert_called_once_with("Error: no pending event found.")


@pytest.mark.asyncio
async def test_callback_conflict_another_english() -> None:
    from app.database import get_pending_event, session_scope, set_pending_event
    from app.telegram_app import callback_conflict_another

    telegram_id = 999126
    with session_scope() as db:
        set_pending_event(db, telegram_id, {"title": "Busy Event"}, commit=True)

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.message.text = "⚠️ Conflict detected: this event overlaps with existing event(s): 'Busy'."
    state = AsyncMock(spec=FSMContext)

    await callback_conflict_another(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with(
        "Please type another start time for the event (e.g. 'at 5pm', '17:30', or 'all day')."
    )
    callback.answer.assert_called_once()
    with session_scope() as db:
        assert get_pending_event(db, telegram_id) is None


@pytest.mark.asyncio
async def test_callback_conflict_another_russian() -> None:
    from app.database import get_pending_event, session_scope, set_pending_event
    from app.telegram_app import callback_conflict_another

    telegram_id = 999127
    with session_scope() as db:
        set_pending_event(db, telegram_id, {"title": "Busy Event"}, commit=True)

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.message.text = "⚠️ Обнаружен конфликт: это событие пересекается с существующими событиями: 'Busy'."
    state = AsyncMock(spec=FSMContext)

    await callback_conflict_another(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with(
        "Пожалуйста, введите другое время начала события (например: 'в 15:00', '17:30' или 'весь день')."
    )
    callback.answer.assert_called_once()
    with session_scope() as db:
        assert get_pending_event(db, telegram_id) is None


@pytest.mark.asyncio
async def test_show_calendar_picker_auth_error() -> None:
    from google.auth.exceptions import RefreshError

    from app.telegram_app import _show_calendar_picker

    telegram_id = 999333
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "some-cal"
    db.commit()
    db.close()

    message = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools._build_calendar_service", side_effect=RefreshError("revoked")):
        await _show_calendar_picker(message, telegram_id, state)

    # Verify credentials were cleared in database
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials is None
    assert user.default_calendar_id is None
    db.close()

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Access Expired or Revoked" in args[0]
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_show_calendar_picker_http_error_auth() -> None:
    from googleapiclient.errors import HttpError

    from app.telegram_app import _show_calendar_picker

    telegram_id = 999335
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "some-cal"
    db.commit()
    db.close()

    message = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    mock_resp = MagicMock()
    mock_resp.status = 401
    http_error = HttpError(resp=mock_resp, content=b"unauthorized")

    with patch("app.tools._build_calendar_service", side_effect=http_error):
        await _show_calendar_picker(message, telegram_id, state)

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials is None
    assert user.default_calendar_id is None
    db.close()

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Access Expired or Revoked" in args[0]


@pytest.mark.asyncio
async def test_cmd_status_auth_error() -> None:
    from google.auth.exceptions import RefreshError

    from app.telegram_app import cmd_status

    telegram_id = 999334
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "some-cal"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id

    with patch("app.tools._build_calendar_service", side_effect=RefreshError("revoked")):
        await cmd_status(message)

    # Verify credentials were cleared in database
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials is None
    assert user.default_calendar_id is None
    db.close()

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "CalendarBot access has been revoked or expired" in args[0]


# ── _to_async_db_url ──────────────────────────────────────────────────────────


def test_to_async_db_url_mappings() -> None:
    from app.telegram_app import _to_async_db_url

    assert _to_async_db_url("sqlite:///./data.db") == "sqlite+aiosqlite:///./data.db"
    assert (
        _to_async_db_url("postgresql://user:pass@host/db")
        == "postgresql+asyncpg://user:pass@host/db"
    )
    assert (
        _to_async_db_url("postgres://user:pass@host/db")
        == "postgresql+asyncpg://user:pass@host/db"
    )
    assert _to_async_db_url("no-scheme-here") == "no-scheme-here"


# ── _get_user_timezone / _get_user_today_str ──────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_timezone_mock_user() -> None:
    from app.telegram_app import _get_user_timezone

    assert await _get_user_timezone(12345) == "UTC"


@pytest.mark.asyncio
async def test_get_user_timezone_not_authenticated() -> None:
    from app.telegram_app import _get_user_timezone

    assert await _get_user_timezone(556200) == "UTC"


@pytest.mark.asyncio
@pytest.mark.parametrize("calendar_id", ["cal-1", "primary", None, ""])
async def test_get_user_timezone_real_success(calendar_id) -> None:
    from app.telegram_app import _get_user_timezone

    telegram_id = 556201
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "old"}'
    user.default_calendar_id = calendar_id
    db.commit()
    db.close()

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_service.calendars.return_value.get.return_value.execute.return_value = {
            "timeZone": "Europe/Moscow"
        }
        mock_build.return_value = (mock_service, '{"token": "refreshed"}')

        tz = await _get_user_timezone(telegram_id)

    assert tz == "Europe/Moscow"
    mock_service.calendars.return_value.get.assert_called_once_with(
        calendarId=calendar_id or "primary"
    )
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials == '{"token": "refreshed"}'
    assert user.timezone == "Europe/Moscow"
    db.close()

    # Subsequent lookup must return the cached DB timezone without calling Google API
    with patch("app.tools._build_calendar_service") as mock_build_second:
        tz_cached = await _get_user_timezone(telegram_id)
        mock_build_second.assert_not_called()
    assert tz_cached == "Europe/Moscow"


@pytest.mark.asyncio
@pytest.mark.parametrize("original_calendar_id", ["cal-1", None, ""])
@pytest.mark.parametrize("new_timezone", [None, "Asia/Tokyo"])
async def test_timezone_fetch_does_not_overwrite_switched_calendar_cache(
    original_calendar_id, new_timezone
) -> None:
    from app.calendar_timezone import get_cached_timezone
    from app.telegram_app import _get_user_timezone

    telegram_id = 556204
    credentials = '{"token": "old"}'
    refreshed_credentials = '{"token": "refreshed"}'
    with SessionLocal() as db:
        user = get_or_create_user(db, telegram_id)
        user.google_credentials = credentials
        user.default_calendar_id = original_calendar_id
        db.commit()

    def switch_calendar_during_fetch():
        # A separate request commits a new selection while Google is responding.
        with SessionLocal() as db:
            user = get_or_create_user(db, telegram_id)
            user.default_calendar_id = "cal-2"
            user.timezone = new_timezone
            user.timezone_fetched_at = 1700000000 if new_timezone else None
            db.commit()
        return {"timeZone": "Europe/Moscow"}

    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.side_effect = (
        switch_calendar_during_fetch
    )
    with (
        patch("app.calendar_timezone.time", return_value=1700000000),
        patch(
            "app.tools._build_calendar_service",
            return_value=(service, refreshed_credentials),
        ),
    ):
        assert await _get_user_timezone(telegram_id) == "Europe/Moscow"
        service.calendars.return_value.get.assert_called_once_with(
            calendarId=original_calendar_id or "primary"
        )
        with SessionLocal() as db:
            user = get_or_create_user(db, telegram_id)
            assert user.default_calendar_id == "cal-2"
            assert user.google_credentials == refreshed_credentials
            assert user.timezone == new_timezone
            assert user.timezone_fetched_at == (1700000000 if new_timezone else None)
            assert get_cached_timezone(user) == new_timezone

        # The next lookup must use cal-2's metadata or its existing fresh cache.
        service.calendars.return_value.get.reset_mock()
        service.calendars.return_value.get.return_value.execute.side_effect = None
        service.calendars.return_value.get.return_value.execute.return_value = {
            "timeZone": "Asia/Tokyo"
        }
        assert await _get_user_timezone(telegram_id) == "Asia/Tokyo"
        if new_timezone:
            service.calendars.return_value.get.assert_not_called()
        else:
            service.calendars.return_value.get.assert_called_once_with(calendarId="cal-2")


@pytest.mark.asyncio
@pytest.mark.parametrize("fetched_at", [None, 1700000000 - 300])
async def test_user_today_refreshes_stale_calendar_timezone(fetched_at) -> None:
    from datetime import UTC, datetime

    from app.telegram_app import _get_user_today_str

    with SessionLocal() as db:
        user = get_or_create_user(db, 556203)
        user.google_credentials = '{"token": "test-token"}'
        user.default_calendar_id = "cal-1"
        user.timezone = "Europe/Moscow"
        user.timezone_fetched_at = fetched_at
        db.commit()

    mock_service = MagicMock()
    mock_service.calendars.return_value.get.return_value.execute.return_value = {
        "timeZone": "America/Los_Angeles"
    }
    instant = datetime(2026, 6, 25, 1, tzinfo=UTC)
    with (
        patch("app.calendar_timezone.time", return_value=1700000000),
        patch("app.tools._build_calendar_service", return_value=(mock_service, '{"token": "test-token"}')),
        patch("app.telegram_app.datetime") as mock_datetime,
    ):
        mock_datetime.now.side_effect = lambda tz: instant.astimezone(tz)
        assert await _get_user_today_str(556203) == "2026-06-24"

    mock_service.calendars.return_value.get.assert_called_once_with(calendarId="cal-1")
    with SessionLocal() as db:
        user = get_or_create_user(db, 556203)
        assert user.timezone == "America/Los_Angeles"
        assert user.timezone_fetched_at == 1700000000


@pytest.mark.asyncio
async def test_get_user_timezone_exception_falls_back_to_utc() -> None:
    from app.telegram_app import _get_user_timezone

    telegram_id = 556202
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    with patch("app.tools._build_calendar_service", side_effect=RuntimeError("boom")):
        tz = await _get_user_timezone(telegram_id)

    assert tz == "UTC"


@pytest.mark.asyncio
async def test_get_user_today_str_invalid_timezone_falls_back_to_utc() -> None:
    from datetime import datetime

    from app.telegram_app import _get_user_today_str

    with patch(
        "app.telegram_app._get_user_timezone", new_callable=AsyncMock
    ) as mock_tz:
        mock_tz.return_value = "Not/A_Real_Timezone"
        result = await _get_user_today_str(556203)

    # Must not raise, and must fall back to a valid UTC date string.
    datetime.strptime(result, "%Y-%m-%d")


# ── _run_agent ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_agent_reuses_existing_session() -> None:
    from app.telegram_app import _run_agent

    with (
        patch(
            "app.telegram_app.session_service.get_session", new_callable=AsyncMock
        ) as mock_get,
        patch(
            "app.telegram_app.session_service.create_session", new_callable=AsyncMock
        ) as mock_create,
        patch("app.telegram_app.runner.run_async") as mock_run_async,
    ):
        mock_get.return_value = MagicMock()
        mock_run_async.return_value = _async_gen([_make_final_event("Hello there")])

        result = await _run_agent(556300, "hi")

        mock_get.assert_called_once()
        mock_create.assert_not_called()
        assert result == "Hello there"


@pytest.mark.asyncio
async def test_run_agent_clears_session_on_event_created() -> None:
    from app.telegram_app import _run_agent

    with (
        patch(
            "app.telegram_app.session_service.get_session", new_callable=AsyncMock
        ) as mock_get,
        patch(
            "app.telegram_app.session_service.delete_session", new_callable=AsyncMock
        ) as mock_delete,
        patch("app.telegram_app.runner.run_async") as mock_run_async,
    ):
        mock_get.return_value = MagicMock()
        mock_run_async.return_value = _async_gen([_make_final_event("✅ Event created!\nDetails")])

        result = await _run_agent(556399, "Meeting tomorrow at 5pm")

        assert "✅ Event created!" in result
        mock_delete.assert_called_once()


@pytest.mark.asyncio
async def test_run_agent_creates_session_when_missing() -> None:
    from app.telegram_app import _run_agent

    with (
        patch(
            "app.telegram_app.session_service.get_session", new_callable=AsyncMock
        ) as mock_get,
        patch(
            "app.telegram_app.session_service.create_session", new_callable=AsyncMock
        ) as mock_create,
        patch("app.telegram_app.runner.run_async") as mock_run_async,
    ):
        mock_get.return_value = None
        mock_run_async.return_value = _async_gen(
            [_make_final_event("Created new session response")]
        )

        result = await _run_agent(556301, "hi")

        mock_create.assert_called_once()
        assert result == "Created new session response"


@pytest.mark.asyncio
async def test_run_agent_creates_session_on_get_session_exception() -> None:
    from app.telegram_app import _run_agent

    with (
        patch(
            "app.telegram_app.session_service.get_session", new_callable=AsyncMock
        ) as mock_get,
        patch(
            "app.telegram_app.session_service.create_session", new_callable=AsyncMock
        ) as mock_create,
        patch("app.telegram_app.runner.run_async") as mock_run_async,
    ):
        mock_get.side_effect = RuntimeError("session lookup failed")
        mock_run_async.return_value = _async_gen([_make_final_event("Fallback response")])

        result = await _run_agent(556302, "hi")

        mock_create.assert_called_once()
        assert result == "Fallback response"


@pytest.mark.asyncio
async def test_run_agent_returns_fallback_message_when_no_text_parts() -> None:
    from app.telegram_app import _run_agent

    non_final_event = MagicMock()
    non_final_event.is_final_response.return_value = False

    with (
        patch(
            "app.telegram_app.session_service.get_session", new_callable=AsyncMock
        ) as mock_get,
        patch("app.telegram_app.runner.run_async") as mock_run_async,
    ):
        mock_get.return_value = MagicMock()
        mock_run_async.return_value = _async_gen([non_final_event])

        result = await _run_agent(556303, "hi")

        assert result == "Sorry, I couldn't process that. Please try again."


@pytest.mark.asyncio
async def test_run_agent_timeout() -> None:
    from app.telegram_app import _run_agent

    async def _never_ending():
        await asyncio.sleep(999)
        yield  # pragma: no cover

    def _raise_timeout(coro, timeout=None):
        coro.close()  # Avoid an unawaited-coroutine warning since the mock never runs it.
        raise TimeoutError()

    with (
        patch(
            "app.telegram_app.session_service.get_session", new_callable=AsyncMock
        ) as mock_get,
        patch("app.telegram_app.runner.run_async") as mock_run_async,
        patch("app.telegram_app.asyncio.wait_for", side_effect=_raise_timeout),
    ):
        mock_get.return_value = MagicMock()
        mock_run_async.return_value = _never_ending()

        result = await _run_agent(556304, "hi")

        assert "timed out" in result


# ── handle_message ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_message_routes_to_agent() -> None:
    from app.telegram_app import handle_message

    db = SessionLocal()
    user = get_or_create_user(db, 556400)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "primary"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 556400
    message.text = "  Meeting tomorrow at 5pm  "
    message.chat.id = 556400
    state = AsyncMock(spec=FSMContext)

    with (
        patch(
            "app.telegram_app._get_user_today_str", new_callable=AsyncMock
        ) as mock_today,
        patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock) as mock_action,
        patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent,
        patch(
            "app.telegram_app._send_formatted_response", new_callable=AsyncMock
        ) as mock_send,
    ):
        mock_today.return_value = "2026-06-25"
        mock_run_agent.return_value = "Sure, I created that event."

        await handle_message(message, state)

    mock_action.assert_called_once_with(556400, "typing")
    mock_run_agent.assert_called_once()
    args, _ = mock_run_agent.call_args
    assert args[0] == 556400
    assert "today=2026-06-25" in args[1]
    assert "auth=ok" in args[1]
    assert "Meeting tomorrow at 5pm" in args[1]
    mock_send.assert_called_once_with(message, "Sure, I created that event.")


@pytest.mark.asyncio
async def test_handle_message_unauthenticated_short_circuits_llm() -> None:
    from app.telegram_app import handle_message

    message = AsyncMock()
    message.from_user.id = 556410
    message.text = "Meeting tomorrow at 5pm"
    state = AsyncMock(spec=FSMContext)

    with patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent:
        await handle_message(message, state)
        mock_run_agent.assert_not_called()

    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "connect your Google account" in args[0]
    assert "reply_markup" in kwargs


@pytest.mark.asyncio
async def test_handle_message_no_calendar_short_circuits_llm() -> None:
    from app.telegram_app import handle_message

    db = SessionLocal()
    user = get_or_create_user(db, 556411)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = None
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 556411
    message.text = "Meeting tomorrow at 5pm"
    state = AsyncMock(spec=FSMContext)

    with (
        patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent,
        patch("app.telegram_app._show_calendar_picker", new_callable=AsyncMock) as mock_picker,
    ):
        await handle_message(message, state)
        mock_run_agent.assert_not_called()
        mock_picker.assert_called_once_with(message, 556411, state)


@pytest.mark.asyncio
async def test_handle_message_empty_text_is_noop() -> None:
    from app.telegram_app import handle_message

    message = AsyncMock()
    message.from_user.id = 556401
    message.text = "   "
    state = AsyncMock(spec=FSMContext)

    with patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent:
        await handle_message(message, state)
        mock_run_agent.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_agent_exception_shows_generic_error() -> None:
    from app.telegram_app import handle_message

    db = SessionLocal()
    user = get_or_create_user(db, 556402)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "primary"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 556402
    message.text = "Hello"
    message.chat.id = 556402
    state = AsyncMock(spec=FSMContext)

    with (
        patch(
            "app.telegram_app._get_user_today_str", new_callable=AsyncMock
        ) as mock_today,
        patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock),
        patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent,
    ):
        mock_today.return_value = "2026-06-25"
        mock_run_agent.side_effect = RuntimeError("agent crashed")

        await handle_message(message, state)

    message.answer.assert_called_once_with(
        "⚠️ Something went wrong. Please try again in a moment."
    )


# ── date_today / date_another / date_cancel callbacks ─────────────────────────


@pytest.mark.asyncio
async def test_callback_date_today_english() -> None:
    from app.telegram_app import callback_date_today

    callback = AsyncMock()
    callback.from_user.id = 556500
    callback.message.text = "📅 Please specify the date for the event:"
    callback.message.chat.id = 556500
    state = AsyncMock(spec=FSMContext)

    with (
        patch(
            "app.telegram_app._get_user_today_str", new_callable=AsyncMock
        ) as mock_today,
        patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock),
        patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent,
        patch(
            "app.telegram_app._send_formatted_response", new_callable=AsyncMock
        ) as mock_send,
    ):
        mock_today.return_value = "2026-06-25"
        mock_run_agent.return_value = "✅ Event created!"

        await callback_date_today(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_any_call("📅 Today")
    mock_run_agent.assert_called_once()
    args, _ = mock_run_agent.call_args
    assert "today" in args[1].lower()
    mock_send.assert_called_once_with(callback.message, "✅ Event created!")
    callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_date_today_russian() -> None:
    from app.telegram_app import callback_date_today

    callback = AsyncMock()
    callback.from_user.id = 556501
    callback.message.text = "📅 Укажите дату для события:"
    callback.message.chat.id = 556501
    state = AsyncMock(spec=FSMContext)

    with (
        patch(
            "app.telegram_app._get_user_today_str", new_callable=AsyncMock
        ) as mock_today,
        patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock),
        patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent,
        patch("app.telegram_app._send_formatted_response", new_callable=AsyncMock),
    ):
        mock_today.return_value = "2026-06-25"
        mock_run_agent.return_value = "✅ Событие создано!"

        await callback_date_today(callback, state)

    callback.message.answer.assert_any_call("📅 Сегодня")


@pytest.mark.asyncio
async def test_callback_date_today_agent_exception() -> None:
    from app.telegram_app import callback_date_today

    callback = AsyncMock()
    callback.from_user.id = 556502
    callback.message.text = "📅 Please specify the date for the event:"
    callback.message.chat.id = 556502
    state = AsyncMock(spec=FSMContext)

    with (
        patch(
            "app.telegram_app._get_user_today_str", new_callable=AsyncMock
        ) as mock_today,
        patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock),
        patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent,
    ):
        mock_today.return_value = "2026-06-25"
        mock_run_agent.side_effect = RuntimeError("boom")

        await callback_date_today(callback, state)

    callback.message.answer.assert_any_call("⚠️ Something went wrong. Please try again.")


@pytest.mark.asyncio
async def test_callback_date_another_english() -> None:
    from app.telegram_app import callback_date_another

    callback = AsyncMock()
    callback.message.text = "📅 Please specify the date for the event:"
    state = AsyncMock(spec=FSMContext)

    await callback_date_another(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with(
        "Please type the date for the event (e.g. 'tomorrow', 'Friday', or a specific date like '2026-06-25')."
    )
    callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_date_another_russian() -> None:
    from app.telegram_app import callback_date_another

    callback = AsyncMock()
    callback.message.text = "📅 Укажите дату для события:"
    state = AsyncMock(spec=FSMContext)

    await callback_date_another(callback, state)

    callback.message.answer.assert_called_once_with(
        "Пожалуйста, введите дату текстом (например: 'завтра', 'в пятницу' или конкретную дату '2026-06-25')."
    )


@pytest.mark.asyncio
async def test_callback_date_cancel() -> None:
    from app.telegram_app import callback_date_cancel

    callback = AsyncMock()
    callback.from_user.id = 556503
    callback.message.text = "📅 Please specify the date for the event:"
    state = AsyncMock(spec=FSMContext)

    with patch(
        "app.telegram_app.session_service.delete_session", new_callable=AsyncMock
    ) as mock_delete:
        await callback_date_cancel(callback, state)
        mock_delete.assert_called_once()

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with("❌ Event creation cancelled.")
    callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_date_cancel_russian_and_delete_session_exception() -> None:
    from app.telegram_app import callback_date_cancel

    callback = AsyncMock()
    callback.from_user.id = 556504
    callback.message.text = "📅 Укажите дату для события:"
    state = AsyncMock(spec=FSMContext)

    with patch(
        "app.telegram_app.session_service.delete_session",
        new_callable=AsyncMock,
        side_effect=RuntimeError("session already gone"),
    ):
        await callback_date_cancel(callback, state)

    callback.message.answer.assert_called_once_with("❌ Создание события отменено.")


# ── cmd_status branches ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_status_not_connected() -> None:
    from app.telegram_app import cmd_status

    message = AsyncMock()
    message.from_user.id = 556600

    await cmd_status(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Not connected" in args[0]


@pytest.mark.asyncio
async def test_cmd_status_mock_user() -> None:
    from app.telegram_app import cmd_status

    db = SessionLocal()
    user = get_or_create_user(db, 12345)
    user.google_credentials = '{"token": "mock"}'
    user.default_calendar_id = "primary"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 12345

    await cmd_status(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Primary Calendar" in args[0]
    assert "UTC" in args[0]


@pytest.mark.asyncio
async def test_cmd_status_persists_refreshed_credentials() -> None:
    from app.telegram_app import cmd_status

    telegram_id = 556603
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "old"}'
    user.default_calendar_id = "primary"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_service.calendars.return_value.get.return_value.execute.return_value = {
            "summary": "Primary Calendar",
            "timeZone": "UTC",
        }
        mock_build.return_value = (mock_service, '{"token": "refreshed"}')

        await cmd_status(message)

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials == '{"token": "refreshed"}'
    db.close()


@pytest.mark.asyncio
async def test_cmd_status_http_error_auth_clears_credentials() -> None:
    from googleapiclient.errors import HttpError

    from app.telegram_app import cmd_status

    telegram_id = 556601
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "some-cal"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id

    mock_resp = MagicMock()
    mock_resp.status = 401
    http_error = HttpError(resp=mock_resp, content=b"unauthorized")

    with patch("app.tools._build_calendar_service", side_effect=http_error):
        await cmd_status(message)

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials is None
    assert user.default_calendar_id is None
    db.close()

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "revoked or expired" in args[0]


@pytest.mark.asyncio
async def test_cmd_status_generic_error_keeps_credentials() -> None:
    from app.telegram_app import cmd_status

    telegram_id = 556602
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "some-cal"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id

    with patch(
        "app.tools._build_calendar_service", side_effect=RuntimeError("quota exceeded")
    ):
        await cmd_status(message)

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials == '{"token": "test-token"}'
    db.close()

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Unable to fetch calendar information" in args[0]


# ── cmd_setcalendar / _show_calendar_picker ───────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_setcalendar_not_connected() -> None:
    from app.telegram_app import cmd_setcalendar

    message = AsyncMock()
    message.from_user.id = 556700
    state = AsyncMock(spec=FSMContext)

    await cmd_setcalendar(message, state)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Not connected" in args[0]


@pytest.mark.asyncio
async def test_cmd_setcalendar_connected_delegates_to_picker() -> None:
    from app.telegram_app import cmd_setcalendar

    telegram_id = 556701
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id
    state = AsyncMock(spec=FSMContext)

    with patch(
        "app.telegram_app._show_calendar_picker", new_callable=AsyncMock
    ) as mock_picker:
        await cmd_setcalendar(message, state)
        mock_picker.assert_called_once_with(message, telegram_id, state)

    state.clear.assert_called_once()


@pytest.mark.asyncio
async def test_show_calendar_picker_not_authenticated_returns_silently() -> None:
    from app.telegram_app import _show_calendar_picker

    message = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    await _show_calendar_picker(message, 556702, state)

    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_show_calendar_picker_success() -> None:
    from app.telegram_app import _show_calendar_picker

    telegram_id = 556703
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "old-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_service.calendarList.return_value.list.return_value.execute.return_value = {
            "items": [
                {"id": "cal1", "summary": "Work"},
                {"id": "cal2"},
            ]
        }
        mock_build.return_value = (mock_service, '{"token": "refreshed-token"}')

        await _show_calendar_picker(message, telegram_id, state)

    state.set_data.assert_called_once_with(
        {"calendars": [{"id": "cal1", "name": "Work"}, {"id": "cal2", "name": "cal2"}]}
    )
    state.set_state.assert_called_once()
    message.answer.assert_called_once()
    args, kwargs = message.answer.call_args
    assert "Which calendar" in args[0]
    keyboard = kwargs["reply_markup"]
    assert len(keyboard.inline_keyboard) == 2
    assert keyboard.inline_keyboard[0][0].text == "Work"
    assert keyboard.inline_keyboard[0][0].callback_data == "cal_0"

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials == '{"token": "refreshed-token"}'
    db.close()


@pytest.mark.asyncio
async def test_show_calendar_picker_no_calendars() -> None:
    from app.telegram_app import _show_calendar_picker

    telegram_id = 556704
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_service.calendarList.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        mock_build.return_value = (mock_service, '{"token": "test-token"}')

        await _show_calendar_picker(message, telegram_id, state)

    message.answer.assert_called_once_with(
        "⚠️ No calendars found in your Google account."
    )


@pytest.mark.asyncio
async def test_show_calendar_picker_generic_exception() -> None:
    from app.telegram_app import _show_calendar_picker

    telegram_id = 556705
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools._build_calendar_service", side_effect=RuntimeError("api down")):
        await _show_calendar_picker(message, telegram_id, state)

    message.answer.assert_called_once_with(
        "⚠️ An error occurred while retrieving your calendars. Please try again later."
    )

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials == '{"token": "test-token"}'
    db.close()


# ── cmd_setduration invalid input ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_setduration_invalid_arg_non_numeric() -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 556800)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "cal-abc"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 556800
    message.text = "/setduration abc"
    state = AsyncMock(spec=FSMContext)

    await cmd_setduration(message, state)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "valid positive number" in args[0]


@pytest.mark.asyncio
async def test_cmd_setduration_invalid_arg_negative() -> None:
    db = SessionLocal()
    user = get_or_create_user(db, 556801)
    user.google_credentials = '{"token": "test-token"}'
    user.default_calendar_id = "cal-abc"
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = 556801
    message.text = "/setduration -5"
    state = AsyncMock(spec=FSMContext)

    await cmd_setduration(message, state)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "valid positive number" in args[0]


# ── callback_duration_selected branches ───────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_duration_selected_cancel() -> None:
    callback = AsyncMock()
    callback.data = "dur_cancel"
    state = AsyncMock(spec=FSMContext)

    await callback_duration_selected(callback, state)

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once_with("❌ Cancelled duration change.")
    callback.answer.assert_called_once()


@pytest.mark.asyncio
async def test_callback_duration_selected_no_default_calendar() -> None:
    telegram_id = 556900
    db = SessionLocal()
    get_or_create_user(db, telegram_id)
    db.close()

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.data = "dur_60"
    state = AsyncMock(spec=FSMContext)

    await callback_duration_selected(callback, state)

    callback.answer.assert_called_once_with("Error: no default calendar set.")


@pytest.mark.asyncio
async def test_callback_duration_selected_exception() -> None:
    telegram_id = 556901
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.default_calendar_id = "cal-abc"
    db.commit()
    db.close()

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.data = "dur_60"
    state = AsyncMock(spec=FSMContext)

    with patch("app.database.set_calendar_duration", side_effect=RuntimeError("db error")):
        await callback_duration_selected(callback, state)

    callback.answer.assert_any_call("An error occurred.")
    assert callback.answer.call_count == 2


# ── cmd_upcoming branches ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock)
async def test_cmd_upcoming_generic_error(mock_chat_action: AsyncMock) -> None:
    telegram_id = 557000
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id
    message.chat.id = telegram_id

    with patch(
        "app.tools._build_calendar_service", side_effect=RuntimeError("quota exceeded")
    ):
        await cmd_upcoming(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "Error fetching events" in args[0]
    # Raw exception text must not leak into the chat message.
    assert "quota exceeded" not in args[0]
    assert "unexpected error" in args[0]


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock)
async def test_cmd_upcoming_no_events(mock_chat_action: AsyncMock) -> None:
    telegram_id = 557001
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id
    message.chat.id = telegram_id

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": []
        }
        mock_build.return_value = (mock_service, '{"token": "test-token"}')

        await cmd_upcoming(message)

    message.answer.assert_called_once_with("📅 No upcoming events found.")


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock)
async def test_cmd_upcoming_malformed_datetime_falls_back_to_raw_string(
    mock_chat_action: AsyncMock,
) -> None:
    telegram_id = 557002
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "test-token"}'
    db.commit()
    db.close()

    message = AsyncMock()
    message.from_user.id = telegram_id
    message.chat.id = telegram_id

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {
            "items": [
                {
                    "summary": "Broken Event",
                    "start": {"dateTime": "not-a-real-datetime"},
                    "end": {"dateTime": "also-not-real"},
                    "htmlLink": "",
                }
            ]
        }
        mock_build.return_value = (mock_service, '{"token": "test-token"}')

        await cmd_upcoming(message)

    message.answer.assert_called_once()
    args, _ = message.answer.call_args
    assert "not-a-real-datetime" in args[0]


# ── callback_calendar_selected branches ────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_calendar_selected_invalid_index_out_of_range() -> None:
    from app.telegram_app import callback_calendar_selected

    callback = AsyncMock()
    callback.data = "cal_5"
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {"calendars": [{"id": "primary", "name": "Primary"}]}

    await callback_calendar_selected(callback, state)

    callback.answer.assert_called_once_with("Invalid selection.")


@pytest.mark.asyncio
async def test_callback_calendar_selected_invalid_format() -> None:
    from app.telegram_app import callback_calendar_selected

    callback = AsyncMock()
    callback.data = "cal_notanumber"
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {"calendars": [{"id": "primary", "name": "Primary"}]}

    await callback_calendar_selected(callback, state)

    callback.answer.assert_called_once_with("Invalid selection.")


@pytest.mark.asyncio
async def test_callback_calendar_selected_mock_user() -> None:
    from app.telegram_app import callback_calendar_selected

    callback = AsyncMock()
    callback.from_user.id = 12345
    callback.data = "cal_0"
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {
        "calendars": [{"id": "primary", "name": "Primary Calendar"}]
    }

    await callback_calendar_selected(callback, state)

    callback.message.edit_text.assert_called_once()
    args, _ = callback.message.edit_text.call_args
    assert "Time Zone: <code>UTC</code>" in args[0]
    assert "Default Duration: <code>60 min</code>" in args[0]


@pytest.mark.asyncio
async def test_callback_calendar_selected_persists_refreshed_credentials() -> None:
    from app.telegram_app import callback_calendar_selected

    telegram_id = 557101
    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    user.google_credentials = '{"token": "old"}'
    db.commit()
    db.close()

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.data = "cal_0"
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {"calendars": [{"id": "cal-1", "name": "Cal One"}]}

    with patch("app.tools._build_calendar_service") as mock_build:
        mock_service = MagicMock()
        mock_service.calendars.return_value.get.return_value.execute.return_value = {
            "timeZone": "Europe/Moscow"
        }
        mock_build.return_value = (mock_service, '{"token": "refreshed"}')

        await callback_calendar_selected(callback, state)

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.google_credentials == '{"token": "refreshed"}'
    db.close()


@pytest.mark.asyncio
async def test_callback_calendar_selected_exception_falls_back_to_defaults() -> None:
    from app.telegram_app import callback_calendar_selected

    telegram_id = 557100
    with SessionLocal() as db:
        user = get_or_create_user(db, telegram_id)
        user.google_credentials = '{"token": "old"}'
        user.default_calendar_id = "old-calendar"
        user.timezone = "Europe/Moscow"
        db.commit()
    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.data = "cal_0"
    state = AsyncMock(spec=FSMContext)
    state.get_data.return_value = {"calendars": [{"id": "cal-xyz", "name": "Some Calendar"}]}

    with patch("app.tools._build_calendar_service", side_effect=RuntimeError("api down")):
        await callback_calendar_selected(callback, state)

    callback.message.edit_text.assert_called_once()
    args, _ = callback.message.edit_text.call_args
    assert "Time Zone: <code>UTC</code>" in args[0]
    assert "Default Duration: <code>60 min</code>" in args[0]

    db = SessionLocal()
    user = get_or_create_user(db, telegram_id)
    assert user.default_calendar_id == "cal-xyz"
    assert user.timezone is None
    db.close()

    # After the API recovers, the new calendar's timezone must be fetched.
    from app.telegram_app import _get_user_timezone

    service = MagicMock()
    service.calendars.return_value.get.return_value.execute.return_value = {
        "timeZone": "America/Los_Angeles"
    }
    with patch("app.tools._build_calendar_service", return_value=(service, '{"token": "old"}')):
        assert await _get_user_timezone(telegram_id) == "America/Los_Angeles"
    service.calendars.return_value.get.assert_called_once_with(calendarId="cal-xyz")


# ── callback_conflict_add_anyway branches ─────────────────────────────────────


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock)
async def test_callback_conflict_add_anyway_error_status(mock_chat_action: AsyncMock) -> None:
    from app.database import session_scope, set_pending_event
    from app.telegram_app import callback_conflict_add_anyway

    telegram_id = 557200
    with session_scope() as db:
        set_pending_event(
            db,
            telegram_id,
            {
                "title": "Party",
                "date": "2026-06-25",
                "time": "18:00",
                "location": None,
                "description": None,
            },
            commit=True,
        )

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.message.text = "conflict"
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools.create_calendar_event") as mock_create:
        mock_create.return_value = {
            "status": "error",
            "message": "No default calendar selected.",
        }
        await callback_conflict_add_anyway(callback, state)

    callback.message.answer.assert_called_once_with(
        "⚠️ Error creating event: No default calendar selected."
    )


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock)
async def test_callback_conflict_add_anyway_exception(mock_chat_action: AsyncMock) -> None:
    from app.database import session_scope, set_pending_event
    from app.telegram_app import callback_conflict_add_anyway

    telegram_id = 557201
    with session_scope() as db:
        set_pending_event(
            db,
            telegram_id,
            {
                "title": "Party",
                "date": "2026-06-25",
                "time": "18:00",
                "location": None,
                "description": None,
            },
            commit=True,
        )

    callback = AsyncMock()
    callback.from_user.id = telegram_id
    callback.message.text = "conflict"
    state = AsyncMock(spec=FSMContext)

    with patch("app.tools.create_calendar_event", side_effect=RuntimeError("boom")):
        await callback_conflict_add_anyway(callback, state)

    callback.message.answer.assert_called_once_with(
        "⚠️ Something went wrong. Please try again."
    )


# ── remaining time_* callback branches ─────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action", new_callable=AsyncMock)
async def test_callback_time_whole_day_agent_exception(mock_chat_action: AsyncMock) -> None:
    from app.telegram_app import callback_time_whole_day

    callback = AsyncMock()
    callback.from_user.id = 557300
    callback.message.text = "🕒 Please specify the start time for the event:"
    state = AsyncMock(spec=FSMContext)

    with (
        patch("app.telegram_app._run_agent", new_callable=AsyncMock) as mock_run_agent,
        patch("app.telegram_app._send_formatted_response", new_callable=AsyncMock),
    ):
        mock_run_agent.side_effect = RuntimeError("boom")
        await callback_time_whole_day(callback, state)

    callback.message.answer.assert_any_call("⚠️ Something went wrong. Please try again.")


@pytest.mark.asyncio
async def test_callback_time_another_russian() -> None:
    from app.telegram_app import callback_time_another

    callback = AsyncMock()
    callback.message.text = "🕒 Укажите время начала события:"
    state = AsyncMock(spec=FSMContext)

    await callback_time_another(callback, state)

    callback.message.answer.assert_called_once_with(
        "Пожалуйста, введите время начала события (например: 'в 15:00', '17:30' или 'весь день')."
    )


@pytest.mark.asyncio
async def test_callback_time_cancel_russian() -> None:
    from app.telegram_app import callback_time_cancel

    callback = AsyncMock()
    callback.from_user.id = 557301
    callback.message.text = "🕒 Укажите время начала события:"
    state = AsyncMock(spec=FSMContext)

    with patch(
        "app.telegram_app.session_service.delete_session", new_callable=AsyncMock
    ):
        await callback_time_cancel(callback, state)

    callback.message.answer.assert_called_once_with("❌ Создание события отменено.")


@pytest.mark.asyncio
async def test_callback_time_cancel_delete_session_exception_is_swallowed() -> None:
    from app.telegram_app import callback_time_cancel

    callback = AsyncMock()
    callback.from_user.id = 557302
    callback.message.text = "🕒 Please specify the start time for the event:"
    state = AsyncMock(spec=FSMContext)

    with patch(
        "app.telegram_app.session_service.delete_session",
        new_callable=AsyncMock,
        side_effect=RuntimeError("session already gone"),
    ):
        await callback_time_cancel(callback, state)

    callback.message.answer.assert_called_once_with("❌ Event creation cancelled.")





