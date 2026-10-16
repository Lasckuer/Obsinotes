import io
import os
import datetime
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, CallbackQuery, User, Chat, BufferedInputFile

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from docx import Document

from app.config import get_settings
from app.handlers.messages import (
    handle_document,
    process_note_text,
    cmd_expenses,
    cmd_daily,
    handle_daily_input,
    cmd_tags,
    cmd_help,
    handle_tag_callback,
    handle_dl_note_callback,
    handle_del_note_callback,
    handle_del_confirm_callback,
    handle_del_cancel_callback,
    append_monthly_expense,
    append_daily_note,
    parse_expense_amount,
    safe_reply_with_note_actions,
    router as messages_router,
    DailyState,
    NoteState
)
from app.handlers.search import (
    handle_file_selection,
    handle_file_selection_callback,
    SearchAIState
)
from app.keyboards.inline import (
    get_note_actions_keyboard,
    get_note_fallback_keyboard,
    get_delete_confirm_keyboard,
    get_tags_keyboard,
    _truncate_callback_data
)
from app.database.db import (
    DatabaseManager,
    add_note_log,
    get_note_by_id,
    get_note_by_file_and_cat,
    get_expenses_summary,
    get_all_tags_with_counts,
    get_notes_by_tag
)
from app.services.llm import stream_summarize_document

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
    msg.caption = None
    msg.answer = AsyncMock(return_value=MagicMock())
    msg.answer_document = AsyncMock(return_value=MagicMock())
    return msg

def create_mock_callback(data: str, user: User, chat: Chat) -> CallbackQuery:
    cb = AsyncMock(spec=CallbackQuery)
    cb.data = data
    cb.from_user = user
    mock_msg = create_mock_message("", user, chat)
    mock_msg.message_id = 99
    mock_msg.edit_text = AsyncMock()
    mock_msg.delete = AsyncMock()
    cb.message = mock_msg
    cb.answer = AsyncMock()
    return cb

# =========================================================================
# 1. Multi-format Document Import (.pdf, .txt, .md, .docx)
# =========================================================================

@pytest.mark.asyncio
async def test_handle_document_pdf(mock_user, mock_chat, fsm_context, mock_bot):
    """Verifies that PDF documents are downloaded, text is extracted via pymupdf, and processed."""
    # Create an in-memory PDF (use ASCII for standard PyMuPDF font)
    pdf_doc = fitz.open()
    page1 = pdf_doc.new_page()
    page1.insert_text((50, 72), "Page 1: Important project report")
    page2 = pdf_doc.new_page()
    page2.insert_text((50, 72), "Page 2: Additional details")
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    doc_mock = MagicMock()
    doc_mock.file_id = "pdf_test_id"
    doc_mock.file_name = "Quarterly_Report.PDF"
    msg.document = doc_mock

    mock_bot.get_file.return_value = MagicMock(file_path="docs/Quarterly_Report.PDF")
    mock_bot.download_file.return_value = io.BytesIO(pdf_bytes)

    async def fake_summary_stream():
        yield "## Отчет по проекту\n\nОсновные выводы за квартал."

    with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
         patch("app.handlers.messages.stream_summarize_document", return_value=fake_summary_stream()) as mock_stream, \
         patch("app.handlers.messages.generate_semantic_filename", AsyncMock(return_value="Отчет по проекту.md")), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Отчет по проекту.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()) as mock_upload, \
         patch("app.handlers.messages.add_note_log", AsyncMock(return_value=101)):

        await handle_document(msg, fsm_context, mock_bot)

        # Verify summary stream was called
        mock_stream.assert_called_once()
        stream_input_text = mock_stream.call_args[0][0]
        assert "Page 1" in stream_input_text
        assert "Page 2" in stream_input_text


        # Verify upload to S3
        mock_upload.assert_called_once()
        assert mock_upload.call_args[0][0] == "TelegramBot/Notes"
        assert mock_upload.call_args[0][1] == "Отчет по проекту.md"

        # Verify status message updated with action buttons
        status_msg.edit_text.assert_called()
        last_call_kwargs = status_msg.edit_text.call_args[1]
        assert "reply_markup" in last_call_kwargs
        kb = last_call_kwargs["reply_markup"]
        assert any("obsidian://open?vault=" in str(btn.url) for row in kb.inline_keyboard for btn in row if btn.url)

@pytest.mark.asyncio
async def test_handle_document_txt_and_md(mock_user, mock_chat, fsm_context, mock_bot):
    """Verifies that .txt and .md files are decoded and processed."""
    for ext, content in [(".txt", "Простой текстовый файл"), (".md", "# Заголовок Markdown\n\nТекст заметки")]:
        msg = create_mock_message("", mock_user, mock_chat)
        status_msg = AsyncMock(spec=Message)
        status_msg.edit_text = AsyncMock()
        msg.answer.return_value = status_msg

        doc_mock = MagicMock()
        doc_mock.file_id = f"file_{ext}"
        doc_mock.file_name = f"my_note{ext}"
        msg.document = doc_mock

        mock_bot.get_file.return_value = MagicMock(file_path=f"docs/my_note{ext}")
        mock_bot.download_file.return_value = io.BytesIO(content.encode("utf-8"))

        async def fake_summary_stream():
            yield f"Конспект из {ext}"

        with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
             patch("app.handlers.messages.stream_summarize_document", return_value=fake_summary_stream()), \
             patch("app.handlers.messages.generate_semantic_filename", AsyncMock(return_value=f"Файл_{ext}.md")), \
             patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value=f"Файл_{ext}.md")), \
             patch("app.handlers.messages.storage.upload_file", AsyncMock()) as mock_upload, \
             patch("app.handlers.messages.add_note_log", AsyncMock(return_value=102)):

            await handle_document(msg, fsm_context, mock_bot)
            mock_upload.assert_called_once()
            assert mock_upload.call_args[0][1] == f"Файл_{ext}.md"

@pytest.mark.asyncio
async def test_handle_document_pdf_max_chars_truncation(mock_user, mock_chat, fsm_context, mock_bot):
    """Verifies that massive documents are truncated to prevent token overflow."""
    # Huge text > 25,000 characters
    huge_text = "Слово " * 6000
    pdf_doc = fitz.open()
    page = pdf_doc.new_page()
    page.insert_text((50, 72), huge_text[:10000])
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    doc_mock = MagicMock()
    doc_mock.file_id = "huge_txt_id"
    doc_mock.file_name = "huge.txt"
    msg.document = doc_mock

    mock_bot.get_file.return_value = MagicMock(file_path="docs/huge.txt")
    mock_bot.download_file.return_value = io.BytesIO(huge_text.encode("utf-8"))

    async def fake_summary_stream():
        yield "Конспект огромного текста"

    with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
         patch("app.handlers.messages.stream_summarize_document", return_value=fake_summary_stream()) as mock_stream, \
         patch("app.handlers.messages.generate_semantic_filename", AsyncMock(return_value="Huge.md")), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Huge.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()), \
         patch("app.handlers.messages.add_note_log", AsyncMock(return_value=103)):

        await handle_document(msg, fsm_context, mock_bot)
        stream_input = mock_stream.call_args[0][0]
        assert "Документ обрезан до 25000 символов" in stream_input
        assert len(stream_input) <= 26000

@pytest.mark.asyncio
async def test_handle_document_empty(mock_user, mock_chat, fsm_context, mock_bot):
    """Verifies that an empty document is rejected with error."""
    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    doc_mock = MagicMock()
    doc_mock.file_id = "empty_id"
    doc_mock.file_name = "empty.txt"
    msg.document = doc_mock

    mock_bot.get_file.return_value = MagicMock(file_path="docs/empty.txt")
    mock_bot.download_file.return_value = io.BytesIO(b"   \n  \t  ")

    await handle_document(msg, fsm_context, mock_bot)
    status_msg.edit_text.assert_called_once_with("❌ Документ оказался пустым.")

# =========================================================================
# 2. Obsidian URI & Interactive Action Buttons
# =========================================================================

def test_obsidian_uri_generation():
    """Checks obsidian://open URI structure and URL encoding."""
    kb = get_note_actions_keyboard("Мой план на день.md", "TelegramBot/Notes", note_id=77, vault="Notes")
    url_button = kb.inline_keyboard[0][0]
    assert url_button.text == "🔗 Открыть в Obsidian"
    assert "obsidian://open?vault=Notes&file=TelegramBot/Notes/%D0%9C%D0%BE%D0%B9%20%D0%BF%D0%BB%D0%B0%D0%BD%20%D0%BD%D0%B0%20%D0%B4%D0%B5%D0%BD%D1%8C.md" in url_button.url

@pytest.mark.asyncio
async def test_callback_download_note(mock_user, mock_chat):
    """Verifies that clicking '📥 Скачать .md' sends the file directly to the user."""
    cb = create_mock_callback("dl_note:55", mock_user, mock_chat)

    fake_note = {
        "id": 55,
        "filename": "Заметка.md",
        "category": "TelegramBot/Notes",
        "content": "# Текст заметки",
        "date": "2026-10-16"
    }

    with patch("app.handlers.messages.get_note_by_id", AsyncMock(return_value=fake_note)), \
         patch("app.handlers.messages.storage.download_file", AsyncMock(return_value=b"# Content from S3")):

        await handle_dl_note_callback(cb)
        cb.message.answer_document.assert_called_once()
        doc_arg = cb.message.answer_document.call_args[0][0]
        assert isinstance(doc_arg, BufferedInputFile)
        assert doc_arg.filename == "Заметка.md"
        cb.answer.assert_called_once_with("📥 Файл отправлен")

@pytest.mark.asyncio
async def test_callback_delete_note_lifecycle(mock_user, mock_chat):
    """Verifies delete prompt -> cancel -> prompt -> confirm lifecycle."""
    fake_note = {
        "id": 55,
        "filename": "Заметка_на_удаление.md",
        "category": "TelegramBot/Notes",
        "content": "Текст",
        "date": "2026-10-16"
    }

    # 1. User clicks delete -> shows confirmation prompt
    cb_del = create_mock_callback("del_note:55", mock_user, mock_chat)
    with patch("app.handlers.messages.get_note_by_id", AsyncMock(return_value=fake_note)):
        await handle_del_note_callback(cb_del)
        cb_del.message.edit_text.assert_called_once()
        prompt_text = cb_del.message.edit_text.call_args[0][0]
        assert "Вы уверены, что хотите удалить" in prompt_text
        confirm_kb = cb_del.message.edit_text.call_args[1]["reply_markup"]
        assert any("del_confirm:55" == btn.callback_data for row in confirm_kb.inline_keyboard for btn in row)

    # 2. User clicks cancel -> restores action keyboard
    cb_cancel = create_mock_callback("del_cancel:55", mock_user, mock_chat)
    with patch("app.handlers.messages.get_note_by_id", AsyncMock(return_value=fake_note)):
        await handle_del_cancel_callback(cb_cancel)
        cb_cancel.message.edit_text.assert_called_once()
        restored_text = cb_cancel.message.edit_text.call_args[0][0]
        assert "Заметка сохранена" in restored_text
        cb_cancel.answer.assert_called_once_with("Удаление отменено")

    # 3. User clicks confirm -> deletes from DB and S3
    cb_confirm = create_mock_callback("del_confirm:55", mock_user, mock_chat)
    with patch("app.handlers.messages.get_note_by_id", AsyncMock(return_value=fake_note)), \
         patch("app.handlers.messages.delete_notes", AsyncMock(return_value=1)) as mock_db_del, \
         patch("app.handlers.messages.storage.delete_file", AsyncMock(return_value=True)) as mock_s3_del:

        await handle_del_confirm_callback(cb_confirm)
        mock_db_del.assert_called_once_with([("Заметка_на_удаление.md", "TelegramBot/Notes")])
        mock_s3_del.assert_called_once_with("TelegramBot/Notes", "Заметка_на_удаление.md")
        cb_confirm.message.edit_text.assert_called_once()
        del_done_text = cb_confirm.message.edit_text.call_args[0][0]
        assert "успешно удалена из базы и S3" in del_done_text
        cb_confirm.answer.assert_called_once_with("Заметка удалена")

# =========================================================================
# 3. Expense Tracking Module (/expenses)
# =========================================================================

@pytest.mark.asyncio
async def test_expense_saved_via_process_note_text(mock_user, mock_chat, fsm_context):
    """When LLM returns expense_amount > 0, it is recorded in DB and monthly Obsidian note."""
    msg = create_mock_message("Потратил 750 рублей на обед", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    llm_output = {
        "category": "Finance",
        "tags": ["еда", "расходы"],
        "corrected_text": "Потратил 750 руб. на бизнес-ланч",
        "filename": "Обед в кафе.md",
        "expense_amount": 750.0,
        "remind_time": "",
        "recur_minutes": 0
    }

    with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
         patch("app.handlers.messages.process_text", AsyncMock(return_value=llm_output)), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Обед в кафе.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()), \
         patch("app.handlers.messages.add_note_log", AsyncMock(return_value=201)), \
         patch("app.handlers.messages.add_expense", AsyncMock()) as mock_add_exp, \
         patch("app.handlers.messages.append_monthly_expense", AsyncMock()) as mock_append_exp:

        await process_note_text(msg, fsm_context, "Потратил 750 рублей на обед", status_msg)

        # 1. Recorded in SQLite DB
        mock_add_exp.assert_called_once()
        exp_args = mock_add_exp.call_args[1]
        assert exp_args["user_id"] == mock_user.id
        assert exp_args["amount"] == 750.0

        # 2. Appended to monthly S3 note
        mock_append_exp.assert_called_once_with(750.0, "Потратил 750 руб. на бизнес-ланч")

@pytest.mark.asyncio
async def test_cmd_expenses_summary(mock_user, mock_chat):
    """Verifies /expenses command output with records and empty states."""
    msg = create_mock_message("/expenses", mock_user, mock_chat)

    fake_summary = {
        "today_sum": 1200.0,
        "today_count": 2,
        "month_sum": 15400.0,
        "month_count": 18,
        "today_records": [
            (500.0, "Обед", "2026-10-16 13:30:00"),
            (700.0, "Такси", "2026-10-16 09:15:00")
        ]
    }

    with patch("app.handlers.messages.get_expenses_summary", AsyncMock(return_value=fake_summary)):
        await cmd_expenses(msg)
        msg.answer.assert_called_once()
        ans = msg.answer.call_args[0][0]
        assert "**1,200.00** руб. (2 записей)" in ans
        assert "**15,400.00** руб. (18 записей)" in ans
        assert "**500.00** руб. (Обед)" in ans


    # Empty summary test
    msg_empty = create_mock_message("/expenses", mock_user, mock_chat)
    empty_summary = {
        "today_sum": 0.0,
        "today_count": 0,
        "month_sum": 0.0,
        "month_count": 0,
        "today_records": []
    }
    with patch("app.handlers.messages.get_expenses_summary", AsyncMock(return_value=empty_summary)):
        await cmd_expenses(msg_empty)
        ans_empty = msg_empty.answer.call_args[0][0]
        assert "Записей о расходах пока нет" in ans_empty

@pytest.mark.asyncio
async def test_append_monthly_expense_s3():
    """Verifies that append_monthly_expense correctly appends to existing S3 note."""
    existing_md = "---\ntags: [finance]\nmonth: 2026-10\n---\n\n# Расходы за 2026-10\n\n- 2026-10-16 10:00: **300.00** — Кофе\n"
    uploaded = {}

    async def fake_upload(folder, filename, data):
        uploaded[(folder, filename)] = data
        return True

    with patch("app.handlers.messages.storage.download_file", AsyncMock(return_value=existing_md.encode("utf-8"))), \
         patch("app.handlers.messages.storage.upload_file", side_effect=fake_upload), \
         patch("app.handlers.messages.upsert_note_log", AsyncMock()):

        await append_monthly_expense(500.0, "Обед")

        now_ym = datetime.datetime.now().strftime("%Y-%m")
        key = ("TelegramBot/Finance", f"{now_ym}.md")
        assert key in uploaded
        content = uploaded[key].decode("utf-8")
        assert "- 2026-10-16 10:00: **300.00** — Кофе" in content
        assert "**500.00** — Обед" in content

# =========================================================================
# 4. Obsidian Daily Notes (/daily or append mode)
# =========================================================================

@pytest.mark.asyncio
async def test_cmd_daily_direct_append(mock_user, mock_chat, fsm_context):
    """Direct append mode: `/daily Позвонить врачу`."""
    msg = create_mock_message("/daily Позвонить врачу", mock_user, mock_chat)

    with patch("app.handlers.messages.append_daily_note", AsyncMock(return_value=("2026-10-16.md", "TelegramBot/Daily", 301))):
        await cmd_daily(msg, fsm_context)
        msg.answer.assert_called_once()
        ans = msg.answer.call_args[0][0]
        assert "Добавлено в ежедневник `TelegramBot/Daily/2026-10-16.md`" in ans
        assert "Позвонить врачу" in ans
        assert "reply_markup" in msg.answer.call_args[1]

@pytest.mark.asyncio
async def test_cmd_daily_prompt_mode(mock_user, mock_chat, fsm_context):
    """Prompt mode: `/daily` -> wait for input -> text -> append."""
    msg = create_mock_message("/daily", mock_user, mock_chat)
    await cmd_daily(msg, fsm_context)
    assert await fsm_context.get_state() == DailyState.waiting_for_text.state
    assert "Введите запись или задачу для ежедневника" in msg.answer.call_args[0][0]

    # User inputs text
    input_msg = create_mock_message("Купить подарок маме", mock_user, mock_chat)
    with patch("app.handlers.messages.append_daily_note", AsyncMock(return_value=("2026-10-16.md", "TelegramBot/Daily", 302))):
        await handle_daily_input(input_msg, fsm_context)
        assert await fsm_context.get_state() is None
        input_msg.answer.assert_called()
        first_ans = input_msg.answer.call_args_list[0][0][0]
        assert "Добавлено в ежедневник" in first_ans
        assert "Купить подарок маме" in first_ans

@pytest.mark.asyncio
async def test_append_daily_note_format():
    """Verifies that append_daily_note creates format `- HH:MM: Text`."""
    uploaded = {}

    async def fake_upload(folder, filename, data):
        uploaded[(folder, filename)] = data
        return True

    with patch("app.handlers.messages.storage.download_file", AsyncMock(return_value=b"")), \
         patch("app.handlers.messages.storage.upload_file", side_effect=fake_upload), \
         patch("app.handlers.messages.upsert_note_log", AsyncMock()), \
         patch("app.handlers.messages.get_note_by_file_and_cat", AsyncMock(return_value={"id": 305})):

        filename, folder, note_id = await append_daily_note("Первая задача на сегодня")
        assert note_id == 305
        today_date = datetime.datetime.now().strftime("%Y-%m-%d")
        assert filename == f"{today_date}.md"
        assert folder == "TelegramBot/Daily"

        content = uploaded[(folder, filename)].decode("utf-8")
        assert "tags: [daily]" in content
        assert "Первая задача на сегодня" in content
        assert f"# Ежедневник — {today_date}" in content

# =========================================================================
# 5. Tag Explorer (/tags)
# =========================================================================

@pytest.mark.asyncio
async def test_cmd_tags_listing(mock_user, mock_chat):
    """Verifies /tags command when tags exist vs when empty."""
    msg = create_mock_message("/tags", mock_user, mock_chat)

    fake_tags = [("работа", 8), ("личное", 5), ("финансы", 2)]
    with patch("app.handlers.messages.get_all_tags_with_counts", AsyncMock(return_value=fake_tags)):
        await cmd_tags(msg)
        msg.answer.assert_called_once()
        ans = msg.answer.call_args[0][0]
        assert "Обзор тегов (Tag Explorer)" in ans
        kb = msg.answer.call_args[1]["reply_markup"]
        btn_texts = [b.text for row in kb.inline_keyboard for b in row]
        assert "#работа (8)" in btn_texts
        assert "#личное (5)" in btn_texts
        assert "#финансы (2)" in btn_texts

    # Empty tags
    msg_empty = create_mock_message("/tags", mock_user, mock_chat)
    with patch("app.handlers.messages.get_all_tags_with_counts", AsyncMock(return_value=[])):
        await cmd_tags(msg_empty)
        assert "В ваших заметках пока нет тегов" in msg_empty.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_callback_tag_selection(mock_user, mock_chat):
    """Verifies that clicking a tag displays notes and numeric download buttons."""
    cb = create_mock_callback("tag:работа", mock_user, mock_chat)

    matching_notes = [
        ("Проект X.md", "TelegramBot/Work"),
        ("Отчет за месяц.md", "TelegramBot/Reports")
    ]

    with patch("app.handlers.messages.get_notes_by_tag", AsyncMock(return_value=matching_notes)):
        await handle_tag_callback(cb)
        cb.message.edit_text.assert_called_once()
        ans = cb.message.edit_text.call_args[0][0]
        assert "Заметки с тегом **#работа** (2):" in ans
        assert "1. `[Work]` Проект X.md" in ans
        assert "2. `[Reports]` Отчет за месяц.md" in ans

        kb = cb.message.edit_text.call_args[1]["reply_markup"]
        btn_texts = [b.text for row in kb.inline_keyboard for b in row]
        assert btn_texts == ["1", "2"]
        cb.answer.assert_called_once()

# =========================================================================
# 6. Edge Cases & Robustness Fixes Verification
# =========================================================================

def test_parse_expense_amount_variations():
    """Verifies that parse_expense_amount handles floats, ints, strings with currency, commas, and edge cases."""
    assert parse_expense_amount(500) == 500.0
    assert parse_expense_amount(750.50) == 750.5
    assert parse_expense_amount("450 руб") == 450.0
    assert parse_expense_amount("1500,50 руб") == 1500.5
    assert parse_expense_amount("1,500.50") == 1500.5
    assert parse_expense_amount("$25.99") == 25.99
    assert parse_expense_amount("2500 р.") == 2500.0
    assert parse_expense_amount("0") == 0.0
    assert parse_expense_amount(0) == 0.0
    assert parse_expense_amount(None) == 0.0
    assert parse_expense_amount("нет расходов") == 0.0
    assert parse_expense_amount(-100) == 0.0

@pytest.mark.asyncio
async def test_commands_executable_while_in_note_state(mock_user, mock_chat, fsm_context):
    """
    CRITICAL BUG FIX VERIFICATION:
    Verifies that when a user is in NoteState.waiting_for_text, typing /daily, /expenses,
    /tags, or /help is NOT intercepted as a note text, but routes to the actual command and clears state.
    """
    await fsm_context.set_state(NoteState.waiting_for_text)
    assert await fsm_context.get_state() == NoteState.waiting_for_text.state

    # 1. /daily should clear note state and append
    msg_daily = create_mock_message("/daily Полить цветы", mock_user, mock_chat)
    with patch("app.handlers.messages.append_daily_note", AsyncMock(return_value=("2026-10-16.md", "TelegramBot/Daily", 401))), \
         patch("app.handlers.messages.safe_reply_with_note_actions", AsyncMock()) as mock_safe_reply:
        await cmd_daily(msg_daily, fsm_context)
        assert await fsm_context.get_state() is None
        mock_safe_reply.assert_called_once()
        assert "Полить цветы" in mock_safe_reply.call_args[1]["text"]

    # 2. Re-enter NoteState, test /expenses clears state
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg_exp = create_mock_message("/expenses", mock_user, mock_chat)
    fake_summary = {
        "today_sum": 0.0, "today_count": 0,
        "month_sum": 0.0, "month_count": 0,
        "today_records": []
    }
    with patch("app.handlers.messages.get_expenses_summary", AsyncMock(return_value=fake_summary)):
        await cmd_expenses(msg_exp, fsm_context)
        assert await fsm_context.get_state() is None
        msg_exp.answer.assert_called_once()

    # 3. Re-enter NoteState, test /tags clears state
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg_tags = create_mock_message("/tags", mock_user, mock_chat)
    with patch("app.handlers.messages.get_all_tags_with_counts", AsyncMock(return_value=[])):
        await cmd_tags(msg_tags, fsm_context)
        assert await fsm_context.get_state() is None

@pytest.mark.asyncio
async def test_telegram_bad_request_button_url_invalid_fallback(mock_user, mock_chat):
    """
    Verifies that if Telegram rejects obsidian:// URL button with BUTTON_URL_INVALID,
    safe_reply_with_note_actions smoothly falls back to get_note_fallback_keyboard
    (download and delete buttons) and embeds the obsidian link in the message body.
    """
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import EditMessageText

    status_msg = AsyncMock(spec=Message)
    # First attempt raises BUTTON_URL_INVALID
    # Second attempt (plain text with actions_kb) also raises BUTTON_URL_INVALID
    # Third attempt (fallback_kb without custom URL button) succeeds
    bad_req_err = TelegramBadRequest(
        method=EditMessageText(chat_id=1, message_id=1, text="test"),
        message="Bad Request: BUTTON_URL_INVALID"
    )
    status_msg.edit_text = AsyncMock(side_effect=[bad_req_err, bad_req_err, AsyncMock()])

    await safe_reply_with_note_actions(
        target=status_msg,
        text="✅ Заметка сохранена",
        filename="TestNote.md",
        category="TelegramBot/Notes",
        note_id=999,
        is_edit=True
    )

    # edit_text was retried with fallback_kb
    assert status_msg.edit_text.call_count == 3
    final_call = status_msg.edit_text.call_args_list[2]
    final_text = final_call[0][0]
    final_kb = final_call[1]["reply_markup"]

    # Obsidian link is embedded into text
    assert "obsidian://open?vault=Notes&file=TelegramBot/Notes/TestNote.md" in final_text
    # Keyboard contains Download and Delete buttons, but NO invalid URL button
    btn_callbacks = [b.callback_data for row in final_kb.inline_keyboard for b in row if b.callback_data]
    assert "dl_note:999" in btn_callbacks
    assert "del_note:999" in btn_callbacks
    assert all(b.url is None for row in final_kb.inline_keyboard for b in row)

@pytest.mark.asyncio
async def test_tag_exact_boundary_no_false_positives():
    """
    Verifies that get_notes_by_tag('work') matches exact tags ('work', 'work, project')
    and #work in content, but does NOT match substrings like 'homework' or 'network'.
    """
    db = DatabaseManager(":memory:")
    conn = await db.get_connection()
    await conn.execute("CREATE TABLE notes_log (id INTEGER PRIMARY KEY, filename TEXT, category TEXT, tags TEXT, content TEXT, date TEXT)")

    await conn.execute("INSERT INTO notes_log (filename, category, tags, content) VALUES ('note_work.md', 'Work', 'work, dev', 'text')")
    await conn.execute("INSERT INTO notes_log (filename, category, tags, content) VALUES ('note_hw.md', 'School', 'homework', 'text')")
    await conn.execute("INSERT INTO notes_log (filename, category, tags, content) VALUES ('note_net.md', 'IT', 'network', 'text')")
    await conn.execute("INSERT INTO notes_log (filename, category, tags, content) VALUES ('note_exact.md', 'Life', 'work', 'text')")
    await conn.execute("INSERT INTO notes_log (filename, category, tags, content) VALUES ('note_hash.md', 'Work', 'daily', 'Check #work plan')")
    await conn.commit()

    matching = await db.get_notes_by_tag("work")
    matched_files = [m[0] for m in matching]

    assert "note_work.md" in matched_files
    assert "note_exact.md" in matched_files
    assert "note_hash.md" in matched_files
    assert "note_hw.md" not in matched_files
    assert "note_net.md" not in matched_files
    await db.close()

def test_tag_cleaning_and_byte_safe_keyboard():
    """
    Verifies that tags with brackets/quotes are cleaned and that get_tags_keyboard
    never produces callback_data longer than 64 UTF-8 bytes even with long Unicode strings.
    """
    very_long_tag = "очень_длинный_русский_тег_для_проверки_лимита_байтов_в_телеграм"
    safe_part = _truncate_callback_data("tag:", very_long_tag, max_bytes=64)
    cb_data = f"tag:{safe_part}"
    assert len(cb_data.encode('utf-8')) <= 64

    kb = get_tags_keyboard([(very_long_tag, 5), ("короткий", 2)])
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode('utf-8')) <= 64

@pytest.mark.asyncio
async def test_search_download_db_fallback(mock_user, mock_chat, fsm_context):
    """
    Verifies that if S3 is offline or download returns empty bytes,
    handle_file_selection falls back to the database content.
    """
    msg = create_mock_message("1", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    msg.answer.return_value = status_msg

    await fsm_context.set_state(SearchAIState.waiting_for_file_number)
    await fsm_context.update_data(
        current_files=["OfflineNote.md"],
        current_categories=["TelegramBot/Notes"]
    )

    fake_note = {
        "id": 88,
        "filename": "OfflineNote.md",
        "category": "TelegramBot/Notes",
        "content": "# Saved Offline Note Content",
        "date": "2026-10-16"
    }

    with patch("app.handlers.search.storage.download_file", AsyncMock(return_value=b"")), \
         patch("app.handlers.search.get_note_by_file_and_cat", AsyncMock(return_value=fake_note)):

        await handle_file_selection(msg, fsm_context)
        msg.answer_document.assert_called_once()
        doc = msg.answer_document.call_args[0][0]
        assert isinstance(doc, BufferedInputFile)
        assert doc.filename == "OfflineNote.md"
        assert doc.data == b"# Saved Offline Note Content"

@pytest.mark.asyncio
async def test_cmd_help(mock_user, mock_chat, fsm_context):
    """Verifies that /help command displays shortcuts and commands."""
    msg = create_mock_message("/help", mock_user, mock_chat)
    await fsm_context.set_state(NoteState.waiting_for_text)
    await cmd_help(msg, fsm_context)

    assert await fsm_context.get_state() is None
    msg.answer.assert_called_once()
    ans = msg.answer.call_args[0][0]
    assert "/daily" in ans
    assert "/expenses" in ans
    assert "/tags" in ans
    assert "/sync" in ans

