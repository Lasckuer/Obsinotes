import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.handlers.scheduler import check_reminders, daily_digest, auto_sync_job

@pytest.mark.asyncio
async def test_check_reminders_single():
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    reminders_data = [(1, 999, "Купить хлеб", 0)]

    with patch("app.handlers.scheduler.get_due_reminders", AsyncMock(return_value=reminders_data)), \
         patch("app.handlers.scheduler.delete_reminder", AsyncMock()) as mock_del, \
         patch("app.handlers.scheduler.update_reminder_time", AsyncMock()) as mock_upd:

        await check_reminders(mock_bot)

        mock_bot.send_message.assert_called_once()
        mock_del.assert_called_once_with(1)
        mock_upd.assert_not_called()

@pytest.mark.asyncio
async def test_check_reminders_recurring():
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    reminders_data = [(2, 999, "Разминка", 30)]

    with patch("app.handlers.scheduler.get_due_reminders", AsyncMock(return_value=reminders_data)), \
         patch("app.handlers.scheduler.delete_reminder", AsyncMock()) as mock_del, \
         patch("app.handlers.scheduler.update_reminder_time", AsyncMock()) as mock_upd:

        await check_reminders(mock_bot)

        mock_bot.send_message.assert_called_once()
        mock_del.assert_not_called()
        mock_upd.assert_called_once()

@pytest.mark.asyncio
async def test_check_reminders_markdown_fallback():
    from aiogram.exceptions import TelegramBadRequest
    mock_bot = AsyncMock()
    # First call with Markdown fails, second call without parse_mode succeeds
    mock_bot.send_message = AsyncMock(side_effect=[
        TelegramBadRequest(method=None, message="Can't find end of underline entity"),
        MagicMock()
    ])

    reminders_data = [(3, 999, "Невалидный _текст markdown", 0)]

    with patch("app.handlers.scheduler.get_due_reminders", AsyncMock(return_value=reminders_data)), \
         patch("app.handlers.scheduler.delete_reminder", AsyncMock()) as mock_del:

        await check_reminders(mock_bot)

        assert mock_bot.send_message.call_count == 2
        # Verify fallback call did not use Markdown parse_mode
        second_call_kwargs = mock_bot.send_message.call_args_list[1][1]
        assert second_call_kwargs.get("parse_mode") is None
        mock_del.assert_called_once_with(3)

@pytest.mark.asyncio
async def test_check_reminders_bot_blocked_forbidden():
    from aiogram.exceptions import TelegramForbiddenError
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(side_effect=TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user"))

    reminders_data = [(4, 999, "Напоминание заблокировавшему", 0)]

    with patch("app.handlers.scheduler.get_due_reminders", AsyncMock(return_value=reminders_data)), \
         patch("app.handlers.scheduler.delete_reminder", AsyncMock()) as mock_del:

        await check_reminders(mock_bot)

        mock_bot.send_message.assert_called_once()
        # Ensure reminder is deleted even when user blocked bot, preventing infinite 30s loop
        mock_del.assert_called_once_with(4)


@pytest.mark.asyncio
async def test_check_reminders_chat_not_found_deletes():
    from aiogram.exceptions import TelegramBadRequest
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(side_effect=TelegramBadRequest(method=None, message="Bad Request: chat not found"))

    reminders_data = [(5, 888, "Напоминание несуществующему чату", 0)]

    with patch("app.handlers.scheduler.get_due_reminders", AsyncMock(return_value=reminders_data)), \
         patch("app.handlers.scheduler.delete_reminder", AsyncMock()) as mock_del:

        await check_reminders(mock_bot)

        # Chat not found is permanent; reminder must be deleted to prevent 30s repetition loop
        mock_del.assert_called_once_with(5)

@pytest.mark.asyncio
async def test_check_reminders_fallback_bad_request_deletes():
    from aiogram.exceptions import TelegramBadRequest
    mock_bot = AsyncMock()
    # Both first call and fallback plain text call fail with TelegramBadRequest
    mock_bot.send_message = AsyncMock(side_effect=[
        TelegramBadRequest(method=None, message="Can't parse entities"),
        TelegramBadRequest(method=None, message="Bad Request: some fatal telegram error")
    ])

    reminders_data = [(6, 777, "Фатальная ошибка", 0)]

    with patch("app.handlers.scheduler.get_due_reminders", AsyncMock(return_value=reminders_data)), \
         patch("app.handlers.scheduler.delete_reminder", AsyncMock()) as mock_del:

        await check_reminders(mock_bot)

        assert mock_bot.send_message.call_count == 2
        mock_del.assert_called_once_with(6)

@pytest.mark.asyncio
async def test_check_reminders_long_text_truncated():
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    huge_text = "А" * 5000
    reminders_data = [(7, 999, huge_text, 0)]

    with patch("app.handlers.scheduler.get_due_reminders", AsyncMock(return_value=reminders_data)), \
         patch("app.handlers.scheduler.delete_reminder", AsyncMock()):

        await check_reminders(mock_bot)

        sent_text = mock_bot.send_message.call_args[0][1]
        assert len(sent_text) <= 4096
        assert sent_text.endswith("...")

@pytest.mark.asyncio
async def test_daily_digest():
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    notes_data = [("Заметка 1.md", "TelegramBot/Notes"), ("Заметка 2.md", "TelegramBot/Ideas")]

    with patch("app.handlers.scheduler.get_today_notes", AsyncMock(return_value=notes_data)):
        await daily_digest(mock_bot, admin_id=12345)
        mock_bot.send_message.assert_called_once()
        sent_text = mock_bot.send_message.call_args[0][1]
        assert "Дайджест" in sent_text
        assert "Заметка 1.md" in sent_text

@pytest.mark.asyncio
async def test_daily_digest_markdown_fallback():
    from aiogram.exceptions import TelegramBadRequest
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock(side_effect=[
        TelegramBadRequest(method=None, message="Can't parse entities in message"),
        MagicMock()
    ])

    notes_data = [("note_with_special_chars_123.md", "TelegramBot/Notes")]

    with patch("app.handlers.scheduler.get_today_notes", AsyncMock(return_value=notes_data)):
        await daily_digest(mock_bot, admin_id=12345)
        assert mock_bot.send_message.call_count == 2
        # Second call sent without parse_mode
        fallback_kwargs = mock_bot.send_message.call_args_list[1][1]
        assert fallback_kwargs.get("parse_mode") is None

@pytest.mark.asyncio
async def test_daily_digest_no_notes():
    mock_bot = AsyncMock()
    with patch("app.handlers.scheduler.get_today_notes", AsyncMock(return_value=[])):
        await daily_digest(mock_bot, admin_id=12345)
        mock_bot.send_message.assert_not_called()

@pytest.mark.asyncio
async def test_auto_sync_job():
    with patch("app.handlers.messages.run_s3_sync", AsyncMock(return_value=(5, 2))):
        await auto_sync_job()
