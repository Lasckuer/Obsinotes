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
async def test_daily_digest_no_notes():
    mock_bot = AsyncMock()
    with patch("app.handlers.scheduler.get_today_notes", AsyncMock(return_value=[])):
        await daily_digest(mock_bot, admin_id=12345)
        mock_bot.send_message.assert_not_called()

@pytest.mark.asyncio
async def test_auto_sync_job():
    with patch("app.handlers.messages.run_s3_sync", AsyncMock(return_value=(5, 2))):
        await auto_sync_job()
