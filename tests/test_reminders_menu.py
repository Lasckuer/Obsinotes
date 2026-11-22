import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, CallbackQuery, User, Chat

from app.handlers.messages import (
    cmd_reminders_menu,
    handle_rem_list_callback,
    handle_rem_view_callback,
    handle_rem_edit_text_callback,
    handle_new_reminder_text_input,
    handle_rem_edit_time_callback,
    handle_new_reminder_time_input,
    handle_rem_del_callback,
    handle_rem_create_callback,
    handle_create_reminder_text_input,
    handle_create_reminder_time_input,
    handle_rem_done_notification_callback,
    handle_rem_snooze_notification_callback,
    cmd_settings,
    handle_settings_status_callback,
    handle_settings_help_callback,
    parse_user_datetime,
    ReminderManagementState
)
from app.database.db import (
    add_reminder,
    get_user_reminders,
    get_reminder_by_id,
    init_db,
    close_db,
    db_manager
)

@pytest.fixture
def fsm_context(mock_user, mock_chat):
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=mock_chat.id, user_id=mock_user.id)
    return FSMContext(storage=storage, key=key)

def create_mock_message(text: str, user: User, chat: Chat) -> Message:
    msg = AsyncMock(spec=Message)
    msg.text = text
    msg.from_user = user
    msg.chat = chat
    msg.caption = None
    msg.answer = AsyncMock(return_value=MagicMock())
    return msg

def create_mock_callback(data: str, user: User, chat: Chat) -> CallbackQuery:
    cb = AsyncMock(spec=CallbackQuery)
    cb.data = data
    cb.from_user = user
    mock_msg = create_mock_message("", user, chat)
    mock_msg.message_id = 99
    mock_msg.text = "🔔 **Напоминание:**\nКупить продукты"
    mock_msg.edit_text = AsyncMock()
    mock_msg.delete = AsyncMock()
    cb.message = mock_msg
    cb.answer = AsyncMock()
    return cb

@pytest.fixture(autouse=True)
async def setup_inmemory_db():
    db_manager.db_path = ":memory:"
    await init_db()
    conn = await db_manager.get_connection()
    await conn.execute("DELETE FROM reminders")
    await conn.commit()
    yield


# =========================================================================
# 1. Date-Time Parser Tests
# =========================================================================

def test_parse_user_datetime_relative():
    # Relative minutes
    res1, recur1 = parse_user_datetime("+15м")
    assert res1 is not None
    assert recur1 == 0

    res2, recur2 = parse_user_datetime("+30 мин")
    assert res2 is not None

    # Relative hours
    res3, recur3 = parse_user_datetime("+2 часа")
    assert res3 is not None

    # Relative days
    res4, recur4 = parse_user_datetime("+1 день")
    assert res4 is not None

def test_parse_user_datetime_formats():
    # Specific date and time
    res, recur = parse_user_datetime("25.11.2026 15:30")
    assert res == "2026-11-25 15:30"
    assert recur == 0

    # Short date and time (current year)
    res_short, _ = parse_user_datetime("25.11 15:30")
    assert "15:30" in res_short

    # ISO format
    res_iso, _ = parse_user_datetime("2026-12-01 10:00")
    assert res_iso == "2026-12-01 10:00"

    # Tomorrow
    res_tmrw, _ = parse_user_datetime("завтра в 14:00")
    assert "14:00" in res_tmrw

    # Recurrence
    res_rec, rec_min = parse_user_datetime("завтра в 14:00 каждые 30 мин")
    assert rec_min == 30

    res_rec_hour, rec_hour_min = parse_user_datetime("+10м каждый час")
    assert rec_hour_min == 60

    # Invalid string
    assert parse_user_datetime("просто какой-то текст без времени") is None
    assert parse_user_datetime("") is None


# =========================================================================
# 2. Reminders Menu List & View Tests
# =========================================================================

@pytest.mark.asyncio
async def test_cmd_reminders_menu_empty(mock_user, mock_chat, fsm_context):
    msg = create_mock_message("⏰ Напоминания", mock_user, mock_chat)
    await cmd_reminders_menu(msg, fsm_context)
    msg.answer.assert_called_once()
    assert "У вас нет активных напоминаний" in msg.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_cmd_reminders_menu_with_items(mock_user, mock_chat, fsm_context):
    await add_reminder(mock_user.id, "Сделать зарядку", "2026-11-25 09:00", 0)
    await add_reminder(mock_user.id, "Полить цветы", "2026-11-25 12:00", 60)

    msg = create_mock_message("/reminders", mock_user, mock_chat)
    await cmd_reminders_menu(msg, fsm_context)
    msg.answer.assert_called_once()
    reply_text = msg.answer.call_args[0][0]
    assert "Ваши активные напоминания" in reply_text
    assert "Сделать зарядку" in reply_text
    assert "Полить цветы" in reply_text

@pytest.mark.asyncio
async def test_handle_rem_view_and_list_callbacks(mock_user, mock_chat):
    await add_reminder(mock_user.id, "Проверить почту", "2026-11-25 10:00", 0)
    rems = await get_user_reminders(mock_user.id)
    rem_id = rems[0]["id"]

    # View existing reminder
    cb_view = create_mock_callback(f"rem:view:{rem_id}", mock_user, mock_chat)
    await handle_rem_view_callback(cb_view)
    cb_view.message.edit_text.assert_called_once()
    assert "Проверить почту" in cb_view.message.edit_text.call_args[0][0]

    # Return to list
    cb_list = create_mock_callback("rem:list", mock_user, mock_chat)
    await handle_rem_list_callback(cb_list)
    cb_list.message.edit_text.assert_called_once()
    assert "Ваши активные напоминания" in cb_list.message.edit_text.call_args[0][0]

    # View non-existent reminder
    cb_fake = create_mock_callback("rem:view:99999", mock_user, mock_chat)
    await handle_rem_view_callback(cb_fake)
    cb_fake.answer.assert_called_once_with("⚠️ Напоминание не найдено или уже удалено.", show_alert=True)


# =========================================================================
# 3. Edit Text & Edit Time Tests
# =========================================================================

@pytest.mark.asyncio
async def test_edit_reminder_text_flow(mock_user, mock_chat, fsm_context):
    await add_reminder(mock_user.id, "Старый текст", "2026-11-25 10:00", 0)
    rems = await get_user_reminders(mock_user.id)
    rem_id = rems[0]["id"]

    # 1. Click edit text
    cb = create_mock_callback(f"rem:edit_text:{rem_id}", mock_user, mock_chat)
    await handle_rem_edit_text_callback(cb, fsm_context)
    state_curr = await fsm_context.get_state()
    assert state_curr == ReminderManagementState.waiting_for_new_text

    # 2. Submit new text
    msg_input = create_mock_message("Обновленный супер-текст", mock_user, mock_chat)
    await handle_new_reminder_text_input(msg_input, fsm_context)

    # State cleared
    assert await fsm_context.get_state() is None
    # DB updated
    updated_rem = await get_reminder_by_id(rem_id, mock_user.id)
    assert updated_rem["text"] == "Обновленный супер-текст"
    assert "успешно обновлен" in msg_input.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_edit_reminder_time_flow(mock_user, mock_chat, fsm_context):
    await add_reminder(mock_user.id, "Тест времени", "2026-11-25 10:00", 0)
    rems = await get_user_reminders(mock_user.id)
    rem_id = rems[0]["id"]

    # 1. Click edit time
    cb = create_mock_callback(f"rem:edit_time:{rem_id}", mock_user, mock_chat)
    await handle_rem_edit_time_callback(cb, fsm_context)
    assert await fsm_context.get_state() == ReminderManagementState.waiting_for_new_time

    # 2. Submit invalid time first
    msg_bad = create_mock_message("непонятное время", mock_user, mock_chat)
    await handle_new_reminder_time_input(msg_bad, fsm_context)
    assert "Не удалось распознать дату и время" in msg_bad.answer.call_args[0][0]
    assert await fsm_context.get_state() == ReminderManagementState.waiting_for_new_time

    # 3. Submit valid time
    msg_good = create_mock_message("26.11.2026 18:45", mock_user, mock_chat)
    await handle_new_reminder_time_input(msg_good, fsm_context)
    assert await fsm_context.get_state() is None

    updated_rem = await get_reminder_by_id(rem_id, mock_user.id)
    assert updated_rem["remind_time"] == "2026-11-26 18:45"
    assert "Время напоминания успешно обновлено" in msg_good.answer.call_args[0][0]


# =========================================================================
# 4. Deletion & Creation Tests
# =========================================================================

@pytest.mark.asyncio
async def test_delete_reminder_callback(mock_user, mock_chat):
    await add_reminder(mock_user.id, "Удаляемое напоминание", "2026-11-25 10:00", 0)
    rems = await get_user_reminders(mock_user.id)
    rem_id = rems[0]["id"]

    cb = create_mock_callback(f"rem:del:{rem_id}", mock_user, mock_chat)
    await handle_rem_del_callback(cb)

    rems_after = await get_user_reminders(mock_user.id)
    assert len(rems_after) == 0
    cb.answer.assert_called_once_with("🗑️ Напоминание удалено!", show_alert=False)

@pytest.mark.asyncio
async def test_create_reminder_combined_input(mock_user, mock_chat, fsm_context):
    cb = create_mock_callback("rem:create", mock_user, mock_chat)
    await handle_rem_create_callback(cb, fsm_context)
    assert await fsm_context.get_state() == ReminderManagementState.waiting_for_create_text

    # Combined input with time
    msg = create_mock_message("28.11.2026 11:00", mock_user, mock_chat)
    await handle_create_reminder_text_input(msg, fsm_context)
    assert await fsm_context.get_state() is None

    rems = await get_user_reminders(mock_user.id)
    assert len(rems) == 1
    assert rems[0]["remind_time"] == "2026-11-28 11:00"

@pytest.mark.asyncio
async def test_create_reminder_two_step_input(mock_user, mock_chat, fsm_context):
    cb = create_mock_callback("rem:create", mock_user, mock_chat)
    await handle_rem_create_callback(cb, fsm_context)

    # 1. Text only
    msg_text = create_mock_message("Записаться на ТО автомобиля", mock_user, mock_chat)
    await handle_create_reminder_text_input(msg_text, fsm_context)
    assert await fsm_context.get_state() == ReminderManagementState.waiting_for_create_time

    # 2. Time only
    msg_time = create_mock_message("29.11.2026 14:00", mock_user, mock_chat)
    await handle_create_reminder_time_input(msg_time, fsm_context)
    assert await fsm_context.get_state() is None

    rems = await get_user_reminders(mock_user.id)
    assert len(rems) == 1
    assert rems[0]["text"] == "Записаться на ТО автомобиля"
    assert rems[0]["remind_time"] == "2026-11-29 14:00"


# =========================================================================
# 5. Notification Done & Snooze Callbacks
# =========================================================================

@pytest.mark.asyncio
async def test_notification_done_and_snooze(mock_user, mock_chat):
    await add_reminder(mock_user.id, "Купить продукты", "2026-11-25 10:00", 0)
    rems = await get_user_reminders(mock_user.id)
    rem_id = rems[0]["id"]

    # Done callback
    cb_done = create_mock_callback(f"rem:done:{rem_id}", mock_user, mock_chat)
    await handle_rem_done_notification_callback(cb_done)
    cb_done.answer.assert_called_once_with("Отлично! Напоминание выполнено 👍")
    assert len(await get_user_reminders(mock_user.id)) == 0

    # Snooze callback
    cb_snooze = create_mock_callback(f"rem:snooze:123:60", mock_user, mock_chat)
    await handle_rem_snooze_notification_callback(cb_snooze)
    snoozed_rems = await get_user_reminders(mock_user.id)
    assert len(snoozed_rems) == 1
    assert "Купить продукты" in snoozed_rems[0]["text"]


# =========================================================================
# 6. Settings Menu & Hub Tests
# =========================================================================

@pytest.mark.asyncio
async def test_cmd_settings_and_callbacks(mock_user, mock_chat, fsm_context):
    msg = create_mock_message("⚙️ Настройки", mock_user, mock_chat)
    await cmd_settings(msg, fsm_context)
    msg.answer.assert_called_once()
    assert "Центр настроек и управления" in msg.answer.call_args[0][0]

    # Status callback
    cb_status = create_mock_callback("settings:status", mock_user, mock_chat)
    await handle_settings_status_callback(cb_status)
    cb_status.message.edit_text.assert_called_once()
    assert "Подробный статус системы" in cb_status.message.edit_text.call_args[0][0]

    # Help callback
    cb_help = create_mock_callback("settings:help", mock_user, mock_chat)
    await handle_settings_help_callback(cb_help)
    cb_help.message.edit_text.assert_called_once()
    assert "Справка по возможностям бота" in cb_help.message.edit_text.call_args[0][0]
