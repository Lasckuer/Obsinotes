import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, User, Chat

from main import start
from app.handlers.messages import cmd_back, prompt_note, cmd_sync_s3, NoteState
from app.handlers.search import prompt_search_or_ai, handle_file_selection, SearchAIState
from app.utils.streaming import stream_to_message

@pytest.fixture
def fsm_context(mock_bot, mock_user, mock_chat):
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=mock_chat.id, user_id=mock_user.id)
    return FSMContext(storage=storage, key=key)

def create_mock_message(text: str, user: User, chat: Chat) -> Message:
    msg = AsyncMock(spec=Message)
    msg.text = text
    msg.from_user = user
    msg.chat = chat
    msg.answer = AsyncMock(return_value=MagicMock())
    msg.answer_document = AsyncMock(return_value=MagicMock())
    return msg

@pytest.mark.asyncio
async def test_cmd_start(mock_user, mock_chat):
    msg = create_mock_message("/start", mock_user, mock_chat)
    await start(msg)
    msg.answer.assert_called_once()
    assert "Бот для Obsidian запущен" in msg.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_cmd_back(mock_user, mock_chat, fsm_context):
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg = create_mock_message("🔙 В главное меню", mock_user, mock_chat)
    await cmd_back(msg, fsm_context)
    assert await fsm_context.get_state() is None
    msg.answer.assert_called_once()

@pytest.mark.asyncio
async def test_prompt_note(mock_user, mock_chat, fsm_context):
    msg = create_mock_message("📝 Отправить заметку", mock_user, mock_chat)
    await prompt_note(msg, fsm_context)
    assert await fsm_context.get_state() == NoteState.waiting_for_text.state
    msg.answer.assert_called_once()

@pytest.mark.asyncio
async def test_prompt_search(mock_user, mock_chat, fsm_context):
    msg = create_mock_message("🔍 Поиск / 🤖 ИИ", mock_user, mock_chat)
    await prompt_search_or_ai(msg, fsm_context)
    assert await fsm_context.get_state() == SearchAIState.waiting_for_input.state
    msg.answer.assert_called_once()

@pytest.mark.asyncio
async def test_cmd_sync_s3(mock_user, mock_chat):
    msg = create_mock_message("/sync", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.messages.run_s3_sync", AsyncMock(return_value=(3, 1))):
        await cmd_sync_s3(msg)
        status_msg.edit_text.assert_called_once()
        assert "Добавлено заметок: **3**" in status_msg.edit_text.call_args[0][0]

@pytest.mark.asyncio
async def test_handle_file_selection(mock_user, mock_chat, fsm_context):
    await fsm_context.set_state(SearchAIState.waiting_for_file_number)
    await fsm_context.update_data(
        current_files=["test.md"],
        current_categories=["TelegramBot/Notes"]
    )

    msg = create_mock_message("1", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.search.storage.download_file", AsyncMock(return_value=b"# Content")):
        await handle_file_selection(msg, fsm_context)
        msg.answer_document.assert_called_once()
        assert await fsm_context.get_state() is None

@pytest.mark.asyncio
async def test_stream_to_message():
    async def fake_stream():
        yield "Привет "
        yield "мир!"

    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock()

    result = await stream_to_message(msg, fake_stream(), "Тест", update_interval=0.0)
    assert result == "Привет мир!"
