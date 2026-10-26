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

def test_search_query_cleaning():
    from app.database.db import clean_search_query, sanitize_fts_token, extract_snippet

    # Conversational query
    res = clean_search_query("Найди заметки про кленбутерол")
    assert res == ["кленбутерол"]

    # Complex multi-keyword query
    res2 = clean_search_query("Поищи о сервере linux и nginx")
    assert "сервере" in res2
    assert "linux" in res2
    assert "nginx" in res2
    assert "поищи" not in res2

    # Query consisting only of stop words should fall back to non-empty tokens
    res3 = clean_search_query("заметки")
    assert res3 == ["заметки"]

    # FTS token sanitization
    assert sanitize_fts_token('hello"world*:') == 'helloworld'
    assert sanitize_fts_token("тест^123") == "тест123"

    # Snippet extraction
    content = "---\ntags: [med]\n---\n\nПрепарат кленбутерол применяется в спортивной медицине для ускорения метаболизма."
    snippet = extract_snippet(content, ["кленбутерол"], max_chars=50)
    assert "кленбутерол" in snippet.lower()
    assert "---" not in snippet

@pytest.mark.asyncio
async def test_search_empty_db_prompts_s3_sync(mock_user, mock_chat, fsm_context):
    from app.handlers.search import handle_search_or_ai, SearchAIState

    await fsm_context.set_state(SearchAIState.waiting_for_input)
    await fsm_context.update_data(search_mode="search")

    msg = create_mock_message("любой запрос", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.search.search_notes", AsyncMock(return_value=[])), \
         patch("app.handlers.search.get_total_notes_count", AsyncMock(return_value=0)):
        await handle_search_or_ai(msg, fsm_context)
        # Verify user was prompted about empty DB and S3 sync
        last_answer = msg.answer.call_args[0][0]
        assert "базе данных пока нет заметок" in last_answer
        assert "Синхронизация S3" in last_answer

@pytest.mark.asyncio
async def test_status_command_and_ping_callback(mock_user, mock_chat):
    from app.handlers.messages import cmd_status_and_help, handle_test_ai_callback

    msg = create_mock_message("/status", mock_user, mock_chat)
    with patch("app.handlers.messages.get_total_notes_count", AsyncMock(return_value=42)), \
         patch("app.handlers.messages.get_today_notes", AsyncMock(return_value=[("T.md", "Notes")])):
        await cmd_status_and_help(msg)
        ans = msg.answer.call_args[0][0]
        assert "Статус системы и справка" in ans
        assert "42" in ans
        assert "reply_markup" in msg.answer.call_args[1]

    # Test callback test_ai
    cb = AsyncMock(spec=CallbackQuery)
    cb.data = "test_ai"
    cb.from_user = mock_user
    cb.answer = AsyncMock()
    cb_msg = create_mock_message("", mock_user, mock_chat)
    cb_msg.edit_text = AsyncMock()
    cb.message = cb_msg

    with patch("app.handlers.messages.test_ai_connection", AsyncMock(return_value=(True, "OK", 0.15))):
        await handle_test_ai_callback(cb)
        cb.answer.assert_called_once()
        assert cb_msg.edit_text.call_count >= 1
        last_edit = cb_msg.edit_text.call_args[0][0]
        assert "Результат проверки связи с ИИ" in last_edit
        assert "0.15 сек" in last_edit

@pytest.mark.asyncio
async def test_daily_format_callback(mock_user, mock_chat, fsm_context):
    from app.handlers.messages import handle_daily_format_callback, handle_daily_input, DailyState

    await fsm_context.set_state(DailyState.waiting_for_text)

    cb = AsyncMock(spec=CallbackQuery)
    cb.data = "daily_fmt:todo"
    cb.from_user = mock_user
    cb.answer = AsyncMock()
    cb_msg = create_mock_message("", mock_user, mock_chat)
    cb_msg.edit_text = AsyncMock()
    cb.message = cb_msg

    await handle_daily_format_callback(cb, fsm_context)
    data = await fsm_context.get_data()
    assert data.get("daily_prefix") == "- [ ] "

    # Now user sends todo text
    input_msg = create_mock_message("Купить молоко", mock_user, mock_chat)
    with patch("app.handlers.messages.append_daily_note", AsyncMock(return_value=("2026-10-26.md", "TelegramBot/Daily", 101))) as mock_append:
        await handle_daily_input(input_msg, fsm_context)
        mock_append.assert_called_once_with("- [ ] Купить молоко")

def test_search_query_cleaning_special_terms():
    from app.database.db import clean_search_query

    # Terms with programming symbols
    res_cpp = clean_search_query("C++")
    assert "C" in res_cpp or "C++" in res_cpp

    # Single character queries
    res_c = clean_search_query("C")
    assert "C" in res_c

    # Pure symbols
    res_sym = clean_search_query("---")
    assert res_sym == ["---"]

@pytest.mark.asyncio
async def test_handle_sync_now_callback(mock_user, mock_chat):
    from app.handlers.messages import handle_sync_now_callback

    cb = AsyncMock(spec=CallbackQuery)
    cb.data = "sync_now"
    cb.from_user = mock_user
    cb.answer = AsyncMock()
    cb_msg = create_mock_message("", mock_user, mock_chat)
    cb_msg.edit_text = AsyncMock()
    cb.message = cb_msg

    with patch("app.handlers.messages.run_s3_sync", AsyncMock(return_value=(5, 2, 1))):
        await handle_sync_now_callback(cb)
        cb.answer.assert_called_once()
        assert cb_msg.edit_text.call_count >= 1
        last_edit = cb_msg.edit_text.call_args[0][0]
        assert "Синхронизация S3 завершена" in last_edit
        assert "Добавлено заметок: **5**" in last_edit
        assert "Удалено устаревших: **1**" in last_edit

@pytest.mark.asyncio
async def test_search_ai_suppresses_sources_when_no_info(mock_user, mock_chat, fsm_context):
    from app.handlers.search import handle_search_or_ai, SearchAIState

    await fsm_context.set_state(SearchAIState.waiting_for_input)
    await fsm_context.update_data(search_mode="ai")

    msg = create_mock_message("Как настроить kubernetes?", mock_user, mock_chat)
    status_msg = create_mock_message("", mock_user, mock_chat)
    status_msg.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status_msg)

    fake_rag = [
        {"filename": "Рецепт.md", "category": "Recipes", "content": "Рецепт борща", "date": "2026-10-26"}
    ]

    with patch("app.handlers.search.search_rag_context", AsyncMock(return_value=fake_rag)), \
         patch("app.handlers.search.stream_to_message", AsyncMock(return_value="К сожалению, в ваших заметках нет информации об этом.")):
        await handle_search_or_ai(msg, fsm_context)
        edit_args = status_msg.edit_text.call_args
        assert edit_args is not None
        displayed_text = edit_args[0][0]
        assert "нет информации" in displayed_text
        assert "Использованные заметки" not in displayed_text
        # No inline download keyboard when no info found
        assert edit_args[1].get("reply_markup") is None


