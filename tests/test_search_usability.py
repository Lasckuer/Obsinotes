import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, CallbackQuery, User, Chat

from app.keyboards.inline import get_search_results_inline_kb
from app.handlers.search import (
    handle_search_or_ai,
    handle_file_selection,
    handle_file_selection_callback,
    handle_new_query_in_file_selection,
    cmd_cancel_search,
    SearchAIState
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
    msg.answer = AsyncMock(return_value=MagicMock())
    msg.answer_document = AsyncMock(return_value=MagicMock())
    return msg

def test_search_results_inline_keyboard():
    kb = get_search_results_inline_kb(7)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert len(buttons) == 7
    assert buttons[0].text == "1"
    assert buttons[0].callback_data == "doc:0"
    assert buttons[6].text == "7"
    assert buttons[6].callback_data == "doc:6"

@pytest.mark.asyncio
async def test_search_shows_inline_keyboard(mock_user, mock_chat, fsm_context):
    await fsm_context.set_state(SearchAIState.waiting_for_input)
    msg = create_mock_message("notes", mock_user, mock_chat)

    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.search.search_notes", AsyncMock(return_value=[("Note1.md", "TelegramBot/Notes"), ("Note2.md", "TelegramBot/Notes")])):
        await handle_search_or_ai(msg, fsm_context)

        # Second call to msg.answer sends the results with inline keyboard
        assert msg.answer.call_count == 2
        call_kwargs = msg.answer.call_args[1]
        inline_kb = call_kwargs.get("reply_markup")
        assert inline_kb is not None
        assert hasattr(inline_kb, "inline_keyboard")
        assert len(inline_kb.inline_keyboard[0]) == 2
        assert await fsm_context.get_state() == SearchAIState.waiting_for_file_number.state

@pytest.mark.asyncio
async def test_search_callback_selection(mock_user, mock_chat, fsm_context):
    await fsm_context.set_state(SearchAIState.waiting_for_file_number)
    await fsm_context.update_data(
        current_files=["Note1.md", "Note2.md"],
        current_categories=["TelegramBot/Notes", "TelegramBot/Notes"]
    )

    cb = AsyncMock(spec=CallbackQuery)
    cb.data = "doc:1"
    cb.from_user = mock_user
    cb.answer = AsyncMock()
    mock_msg = create_mock_message("", mock_user, mock_chat)
    cb.message = mock_msg

    with patch("app.handlers.search.storage.download_file", AsyncMock(return_value=b"# Content of Note2")):
        await handle_file_selection_callback(cb, fsm_context)

        cb.answer.assert_called_once()
        mock_msg.answer_document.assert_called_once()
        assert await fsm_context.get_state() is None

@pytest.mark.asyncio
async def test_search_callback_invalid_or_expired(mock_user, mock_chat, fsm_context):
    # State has no data
    cb = AsyncMock(spec=CallbackQuery)
    cb.data = "doc:99"
    cb.from_user = mock_user
    cb.answer = AsyncMock()

    await handle_file_selection_callback(cb, fsm_context)
    cb.answer.assert_called_once()
    assert cb.answer.call_args[1].get("show_alert") is True

@pytest.mark.asyncio
async def test_seamless_new_query_in_file_selection(mock_user, mock_chat, fsm_context):
    # User is in waiting_for_file_number but types a new search query instead of digit
    await fsm_context.set_state(SearchAIState.waiting_for_file_number)
    await fsm_context.update_data(
        current_files=["OldNote.md"],
        current_categories=["TelegramBot/Notes"]
    )

    msg = create_mock_message("новые заметки", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.search.search_notes", AsyncMock(return_value=[("NewNote.md", "TelegramBot/Notes")])) as mock_search:
        await handle_new_query_in_file_selection(msg, fsm_context)

        mock_search.assert_called_once_with("новые заметки", limit=25)
        # Check that state updated with the new search results
        data = await fsm_context.get_data()
        assert data["current_files"] == ["NewNote.md"]

@pytest.mark.asyncio
async def test_search_cancellation(mock_user, mock_chat, fsm_context):
    await fsm_context.set_state(SearchAIState.waiting_for_file_number)
    msg = create_mock_message("❌ Отмена", mock_user, mock_chat)

    await cmd_cancel_search(msg, fsm_context)
    assert await fsm_context.get_state() is None
    msg.answer.assert_called_once()
    assert "Вы вернулись в главное меню" in msg.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_search_cancellation_variations(mock_user, mock_chat, fsm_context):
    for cancel_text in ["ОТМЕНА", "В главное меню", "отменить", "❌ отмена"]:
        await fsm_context.set_state(SearchAIState.waiting_for_input)
        msg = create_mock_message(cancel_text, mock_user, mock_chat)
        await cmd_cancel_search(msg, fsm_context)
        assert await fsm_context.get_state() is None
        assert "Вы вернулись в главное меню" in msg.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_search_callback_multiple_downloads_using_cache(mock_user, mock_chat, fsm_context):
    from app.handlers.search import search_results_cache
    
    # Populate cache as if search results were just delivered
    chat_id = mock_chat.id
    msg_id = 777
    search_results_cache.set(
        chat_id, msg_id, 
        ["DocA.md", "DocB.md"], 
        ["TelegramBot/Notes", "TelegramBot/Notes"]
    )

    # 1. First button click: doc:0
    cb1 = AsyncMock(spec=CallbackQuery)
    cb1.data = "doc:0"
    cb1.from_user = mock_user
    cb1.answer = AsyncMock()
    mock_msg = create_mock_message("", mock_user, mock_chat)
    mock_msg.message_id = msg_id
    mock_msg.chat = mock_chat
    cb1.message = mock_msg

    with patch("app.handlers.search.storage.download_file", AsyncMock(return_value=b"# Content of DocA")) as mock_dl:
        await handle_file_selection_callback(cb1, fsm_context)
        mock_dl.assert_called_once_with("TelegramBot/Notes", "DocA.md")
        assert mock_msg.answer_document.call_count == 1

    # State is now None
    assert await fsm_context.get_state() is None

    # 2. Second button click: doc:1 on the same message
    cb2 = AsyncMock(spec=CallbackQuery)
    cb2.data = "doc:1"
    cb2.from_user = mock_user
    cb2.answer = AsyncMock()
    cb2.message = mock_msg

    with patch("app.handlers.search.storage.download_file", AsyncMock(return_value=b"# Content of DocB")) as mock_dl2:
        await handle_file_selection_callback(cb2, fsm_context)
        # Should succeed because search_results_cache preserved the file mapping!
        mock_dl2.assert_called_once_with("TelegramBot/Notes", "DocB.md")
        assert mock_msg.answer_document.call_count == 2
        cb2.answer.assert_called_once()
        # Verify no error alert was shown
        for call in cb2.answer.call_args_list:
            assert call[1].get("show_alert") is not True

@pytest.mark.asyncio
async def test_search_mode_allows_switching_to_other_menu_actions(monkeypatch):
    from aiogram.types import Update
    from datetime import datetime
    from app.config import reload_settings
    from app.handlers import search, messages
    from main import dp

    monkeypatch.setenv("ADMIN_ID", "111111")
    monkeypatch.setenv("ALLOWED_USERS", "")
    reload_settings()

    bot = AsyncMock()
    bot.id = 1
    user = User(id=111111, is_bot=False, first_name="Test")
    chat = Chat(id=111111, type="private")

    # Set state to SearchAIState.waiting_for_input
    storage_key = StorageKey(bot_id=bot.id, chat_id=chat.id, user_id=user.id)
    fsm = FSMContext(storage=dp.storage, key=storage_key)
    await fsm.set_state(SearchAIState.waiting_for_input)

    # User clicks "📝 Отправить заметку" while in search input state
    msg = Message(
        message_id=200,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text="📝 Отправить заметку"
    ).as_(bot)
    update = Update(update_id=200, message=msg)

    await dp.feed_update(bot, update)

    # Verify user transitioned to NoteState.waiting_for_text, NOT search error!
    assert await fsm.get_state() == messages.NoteState.waiting_for_text.state

    # Now in SearchAIState.waiting_for_file_number, user clicks "🔄 Полная синхронизация S3"
    await fsm.set_state(SearchAIState.waiting_for_file_number)
    msg_sync = Message(
        message_id=201,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text="🔄 Полная синхронизация S3"
    ).as_(bot)
    update_sync = Update(update_id=201, message=msg_sync)

    with patch("app.handlers.messages.run_s3_sync", AsyncMock(return_value=(1, 0))):
        await dp.feed_update(bot, update_sync)

    # State is cleared cleanly
    assert await fsm.get_state() is None
