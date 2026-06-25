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
    from app.telegram_app import callback_conflict_add_anyway
    from app.database import session_scope, set_pending_event, get_pending_event

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
        )

    callback.message.edit_reply_markup.assert_called_once_with(reply_markup=None)
    callback.message.answer.assert_called_once()
    args, kwargs = callback.message.answer.call_args
    assert "✅ Event created!" in args[0]
    assert "Conflicting Party" in args[0]
    assert "https://calendar.google.com/event?id=123" in args[0]
    callback.answer.assert_called_once()
    with session_scope() as db:
        assert get_pending_event(db, telegram_id) is None


@pytest.mark.asyncio
@patch("app.telegram_app.bot.send_chat_action")
async def test_callback_conflict_add_anyway_russian_success(mock_chat_action: MagicMock) -> None:
    from app.telegram_app import callback_conflict_add_anyway
    from app.database import session_scope, set_pending_event, get_pending_event

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
    args, kwargs = callback.message.answer.call_args
    assert "✅ Событие создано!" in args[0]
    assert "Встреча" in args[0]
    callback.answer.assert_called_once()
    with session_scope() as db:
        assert get_pending_event(db, telegram_id) is None


@pytest.mark.asyncio
async def test_callback_conflict_add_anyway_no_pending() -> None:
    from app.telegram_app import callback_conflict_add_anyway
    from app.database import session_scope, set_pending_event

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
    from app.telegram_app import callback_conflict_another
    from app.database import session_scope, set_pending_event, get_pending_event

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
    from app.telegram_app import callback_conflict_another
    from app.database import session_scope, set_pending_event, get_pending_event

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
    from app.telegram_app import _show_calendar_picker
    from google.auth.exceptions import RefreshError

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
async def test_cmd_status_auth_error() -> None:
    from app.telegram_app import cmd_status
    from google.auth.exceptions import RefreshError

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





