import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, User, Chat

from main import start
import io
from app.handlers.messages import (
    cmd_back, prompt_note, cmd_sync_s3, NoteState,
    handle_photo, handle_audio, handle_document, run_s3_sync
)
from app.handlers.search import (
    prompt_search_or_ai, handle_file_selection, handle_search_or_ai, SearchAIState
)
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

@pytest.mark.asyncio
async def test_search_ai_homograph_triggers(mock_user, mock_chat, fsm_context):
    await fsm_context.set_state(SearchAIState.waiting_for_input)

    # 1. "кактусы" - should NOT trigger AI question, should trigger search_notes
    msg = create_mock_message("кактусы", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.search.search_notes", AsyncMock(return_value=[("Кактусы.md", "TelegramBot/Notes")])) as mock_search, \
         patch("app.handlers.search.stream_answer_question") as mock_ai:
        await handle_search_or_ai(msg, fsm_context)
        mock_search.assert_called_once_with("кактусы", limit=25)
        mock_ai.assert_not_called()

    # 2. "какой-то рецепт" and "какой то рецепт" - indefinite pronouns, should trigger search_notes, not AI
    await fsm_context.set_state(SearchAIState.waiting_for_input)
    msg2 = create_mock_message("какой-то рецепт", mock_user, mock_chat)
    msg2.answer.return_value = status_msg

    with patch("app.handlers.search.search_notes", AsyncMock(return_value=[])) as mock_search2, \
         patch("app.handlers.search.stream_answer_question") as mock_ai2:
        await handle_search_or_ai(msg2, fsm_context)
        mock_search2.assert_called_once_with("какой-то рецепт", limit=25)
        mock_ai2.assert_not_called()

    # Space-separated indefinite pronoun ("какой то рецепт")
    await fsm_context.set_state(SearchAIState.waiting_for_input)
    msg2b = create_mock_message("какой то рецепт", mock_user, mock_chat)
    msg2b.answer.return_value = status_msg

    with patch("app.handlers.search.search_notes", AsyncMock(return_value=[])) as mock_search2b, \
         patch("app.handlers.search.stream_answer_question") as mock_ai2b:
        await handle_search_or_ai(msg2b, fsm_context)
        mock_search2b.assert_called_once_with("какой то рецепт", limit=25)
        mock_ai2b.assert_not_called()

    # 3. "как настроить сервер" - should trigger AI question
    await fsm_context.set_state(SearchAIState.waiting_for_input)
    msg3 = create_mock_message("как настроить сервер", mock_user, mock_chat)
    status_msg3 = AsyncMock(spec=Message)
    status_msg3.edit_text = AsyncMock()
    msg3.answer.return_value = status_msg3

    async def fake_stream():
        yield "Шаг 1: подключитесь по SSH"

    fake_rag_note = [{"filename": "server.md", "category": "DevOps", "content": "Контекст", "date": "2026-10-15"}]

    with patch("app.handlers.search.search_rag_context", AsyncMock(return_value=fake_rag_note)), \
         patch("app.handlers.search.stream_answer_question", return_value=fake_stream()) as mock_ai3, \
         patch("app.handlers.search.search_notes") as mock_search3:
        await handle_search_or_ai(msg3, fsm_context)
        mock_ai3.assert_called_once()
        mock_search3.assert_not_called()

    # 4. "какая погода" and "какое правило" - other question forms should trigger AI question
    await fsm_context.set_state(SearchAIState.waiting_for_input)
    msg4 = create_mock_message("какая погода сегодня", mock_user, mock_chat)
    status_msg4 = AsyncMock(spec=Message)
    status_msg4.edit_text = AsyncMock()
    msg4.answer.return_value = status_msg4

    with patch("app.handlers.search.search_rag_context", AsyncMock(return_value=fake_rag_note)), \
         patch("app.handlers.search.stream_answer_question", return_value=fake_stream()) as mock_ai4, \
         patch("app.handlers.search.search_notes") as mock_search4:
        await handle_search_or_ai(msg4, fsm_context)
        mock_ai4.assert_called_once()
        mock_search4.assert_not_called()

@pytest.mark.asyncio
async def test_handle_photo(mock_user, mock_chat, fsm_context, mock_bot):
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    photo_item = MagicMock()
    photo_item.file_id = "photo_123"
    msg.photo = [photo_item]
    msg.caption = "Красивый закат на море"

    mock_bot.get_file.return_value = MagicMock(file_path="photos/p123.jpg")
    mock_bot.download_file.return_value = io.BytesIO(b"fake_image_bytes")

    processed_data = {
        "category": "Photos",
        "tags": ["природа", "закат"],
        "corrected_text": "Красивый закат на море вечером",
        "filename": "Закат на море.md"
    }

    with patch("app.handlers.messages.process_text", AsyncMock(return_value=processed_data)), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Закат на море.md")) as mock_rephrase, \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()) as mock_upload, \
         patch("app.handlers.messages.add_note_log", AsyncMock()) as mock_add_log:

        await handle_photo(msg, fsm_context, mock_bot)

        # 1. Image uploaded to S3
        # 2. Markdown note uploaded to S3
        assert mock_upload.call_count == 2
        # First call is photo
        call1 = mock_upload.call_args_list[0]
        assert call1[0][0] == "TelegramBot/Photos"
        assert call1[0][1].startswith("img_")
        assert call1[0][1].endswith(".jpg")

        # Second call is final_md to S3
        call2 = mock_upload.call_args_list[1]
        assert call2[0][0] == "TelegramBot/Photos"
        assert call2[0][1] == "Закат на море.md"
        assert b"![[img_" in call2[0][2]
        assert "Красивый закат на море вечером".encode("utf-8") in call2[0][2]

        # Verify get_rephrased_filename was called for collision handling
        mock_rephrase.assert_called_once_with("Photos", "Закат на море.md", "Красивый закат на море")

        # Verify add_note_log uses consistent S3 category
        mock_add_log.assert_called_once()
        log_args = mock_add_log.call_args[1]
        assert log_args["category"] == "TelegramBot/Photos"
        assert log_args["filename"] == "Закат на море.md"

@pytest.mark.asyncio
async def test_handle_document_case_insensitive(mock_user, mock_chat, fsm_context, mock_bot):
    await fsm_context.set_state(NoteState.waiting_for_text)

    # 1. Upper case .DOCX
    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    doc_mock = MagicMock()
    doc_mock.file_id = "doc_123"
    doc_mock.file_name = "PROJECT_REPORT.DOCX"
    msg.document = doc_mock

    from docx import Document
    docx_io = io.BytesIO()
    doc = Document()
    doc.add_paragraph("Тестовый контент документа")
    doc.save(docx_io)
    docx_io.seek(0)

    mock_bot.get_file.return_value = MagicMock(file_path="docs/doc_123.docx")
    mock_bot.download_file.return_value = docx_io

    async def fake_summary_stream():
        yield "Краткое содержание документа"

    with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
         patch("app.handlers.messages.stream_summarize_document", return_value=fake_summary_stream()), \
         patch("app.handlers.messages.generate_semantic_filename", AsyncMock(return_value="Отчет.md")), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Отчет.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()) as mock_upload, \
         patch("app.handlers.messages.add_note_log", AsyncMock()):

        await handle_document(msg, fsm_context, mock_bot)
        mock_upload.assert_called_once()
        assert mock_upload.call_args[0][0] == "TelegramBot/Notes"
        assert mock_upload.call_args[0][1] == "Отчет.md"

    # 2. Unsupported file (.zip) should be rejected
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg_pdf = create_mock_message("", mock_user, mock_chat)
    status_msg_pdf = AsyncMock(spec=Message)
    status_msg_pdf.edit_text = AsyncMock()
    msg_pdf.answer.return_value = status_msg_pdf

    doc_pdf = MagicMock()
    doc_pdf.file_id = "zip_123"
    doc_pdf.file_name = "archive.zip"
    msg_pdf.document = doc_pdf

    await handle_document(msg_pdf, fsm_context, mock_bot)
    status_msg_pdf.edit_text.assert_called_once_with("❌ Поддерживаются только форматы .docx, .pdf, .txt и .md.")


@pytest.mark.asyncio
async def test_handle_audio_extension_detection(mock_user, mock_chat, fsm_context, mock_bot):
    await fsm_context.set_state(NoteState.waiting_for_text)

    # 1. Voice message -> should use .ogg
    msg_voice = create_mock_message("", mock_user, mock_chat)
    status_msg_voice = AsyncMock(spec=Message)
    status_msg_voice.edit_text = AsyncMock()
    msg_voice.answer.return_value = status_msg_voice

    voice_mock = MagicMock()
    voice_mock.file_id = "v123"
    msg_voice.voice = voice_mock
    msg_voice.audio = None

    mock_bot.get_file.return_value = MagicMock(file_path="voice/file.oga")
    mock_bot.download_file.return_value = io.BytesIO(b"dummy_voice")

    with patch("app.handlers.messages.transcribe_audio", AsyncMock(return_value="Текст из голоса")) as mock_transcribe, \
         patch("app.handlers.messages.process_note_text", AsyncMock()) as mock_process:

        await handle_audio(msg_voice, fsm_context, mock_bot)
        transcribe_path = mock_transcribe.call_args[0][0]
        assert transcribe_path.endswith(".ogg")
        mock_process.assert_called_once()

    # 2. Audio file (.mp3) -> should preserve .mp3 extension
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg_audio = create_mock_message("", mock_user, mock_chat)
    status_msg_audio = AsyncMock(spec=Message)
    status_msg_audio.edit_text = AsyncMock()
    msg_audio.answer.return_value = status_msg_audio

    audio_mock = MagicMock()
    audio_mock.file_id = "a123"
    audio_mock.file_name = "recording.MP3"
    msg_audio.voice = None
    msg_audio.audio = audio_mock

    mock_bot.get_file.return_value = MagicMock(file_path="music/audio.mp3")
    mock_bot.download_file.return_value = io.BytesIO(b"dummy_mp3")

    with patch("app.handlers.messages.transcribe_audio", AsyncMock(return_value="Текст из mp3")) as mock_transcribe_audio, \
         patch("app.handlers.messages.process_note_text", AsyncMock()) as mock_process_audio:

        await handle_audio(msg_audio, fsm_context, mock_bot)
        transcribe_path_audio = mock_transcribe_audio.call_args[0][0]
        assert transcribe_path_audio.endswith(".mp3")
        mock_process_audio.assert_called_once()

@pytest.mark.asyncio
async def test_run_s3_sync_root_category():
    files = [
        {"key": "RootFile.md", "etag": "etag1", "last_modified": "2026-10-15", "size": 10},
        {"key": "TelegramBot/Notes/SubFile.md", "etag": "etag2", "last_modified": "2026-10-15", "size": 20}
    ]
    downloaded = {
        "RootFile.md": b"# Root content",
        "TelegramBot/Notes/SubFile.md": b"# Sub content"
    }

    with patch("app.handlers.messages.storage.list_files_with_metadata", AsyncMock(return_value=files)), \
         patch("app.handlers.messages.storage.batch_download", AsyncMock(return_value=downloaded)), \
         patch("app.handlers.messages.upsert_note_log", AsyncMock(return_value=(True, False))) as mock_upsert:

        added, updated = await run_s3_sync()
        assert added == 2
        assert updated == 0

        calls = mock_upsert.call_args_list
        assert calls[0][1]["filename"] == "RootFile.md"
        assert calls[0][1]["category"] == ""
        assert calls[1][1]["filename"] == "SubFile.md"
        assert calls[1][1]["category"] == "TelegramBot/Notes"

@pytest.mark.asyncio
async def test_process_note_text_markdown_fallback(mock_user, mock_chat, fsm_context):
    from app.handlers.messages import process_note_text
    from aiogram.exceptions import TelegramBadRequest

    msg = create_mock_message("Заметка с _ошибкой_разметки", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    # The confirmation message throws TelegramBadRequest with Markdown, then succeeds with plain text
    status_msg.edit_text = AsyncMock(side_effect=[
        None, # "🧠 Думаю..."
        TelegramBadRequest(method=None, message="Can't parse entities"),
        MagicMock() # plain text fallback succeeds
    ])

    processed_data = {
        "category": "Notes",
        "tags": ["тест"],
        "corrected_text": "Текст заметки",
        "filename": "Заметка_с_ошибкой.md"
    }

    with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
         patch("app.handlers.messages.process_text", AsyncMock(return_value=processed_data)), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Заметка_с_ошибкой.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()), \
         patch("app.handlers.messages.add_note_log", AsyncMock()):

        await process_note_text(msg, fsm_context, "Заметка с _ошибкой_разметки", status_msg)
        # Edit text should have been called 3 times: 1. "Думаю...", 2. Markdown attempt, 3. Plain text fallback
        assert status_msg.edit_text.call_count == 3
        # Final call had no parse_mode
        third_call_kwargs = status_msg.edit_text.call_args_list[2][1]
        assert "parse_mode" not in third_call_kwargs or third_call_kwargs.get("parse_mode") is None

@pytest.mark.asyncio
async def test_handle_file_selection_deleted_status_msg_error(mock_user, mock_chat, fsm_context):
    from app.handlers.search import handle_file_selection, SearchAIState
    await fsm_context.set_state(SearchAIState.waiting_for_file_number)
    await fsm_context.update_data(
        current_files=["Note.md"],
        current_categories=["TelegramBot/Notes"]
    )

    msg = create_mock_message("1", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.delete = AsyncMock() # delete succeeds
    msg.answer.return_value = status_msg
    # Document sending fails with exception
    msg.answer_document = AsyncMock(side_effect=Exception("Connection to Telegram failed"))

    with patch("app.handlers.search.storage.download_file", AsyncMock(return_value=b"# Content")):
        await handle_file_selection(msg, fsm_context)
        # Verify status_msg.delete was called
        status_msg.delete.assert_called_once()
        # Verify error message was sent via message.answer, not status_msg.edit_text
        assert any("Ошибка скачивания" in str(call) for call in msg.answer.call_args_list)

