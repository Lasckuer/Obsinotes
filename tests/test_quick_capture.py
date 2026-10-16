import pytest
import io
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey
from aiogram.types import Message, User, Chat

from app.handlers.messages import (
    handle_text, handle_photo, handle_audio, handle_document, cmd_back, NoteState
)
from app.keyboards.reply import get_main_keyboard

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
    return msg

@pytest.mark.asyncio
async def test_quick_capture_text_zero_fsm(mock_user, mock_chat, fsm_context):
    # State is None (Zero-FSM quick capture)
    assert await fsm_context.get_state() is None

    msg = create_mock_message("Купить молоко и хлеб", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    processed_data = {
        "category": "Notes",
        "tags": ["покупки"],
        "corrected_text": "Купить молоко и хлеб",
        "filename": "Покупки.md"
    }

    with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
         patch("app.handlers.messages.process_text", AsyncMock(return_value=processed_data)), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Покупки.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()) as mock_upload, \
         patch("app.handlers.messages.add_note_log", AsyncMock()) as mock_log:

        await handle_text(msg, fsm_context)

        mock_upload.assert_called_once()
        mock_log.assert_called_once()
        # State remains clean (None)
        assert await fsm_context.get_state() is None

@pytest.mark.asyncio
async def test_quick_capture_document_zero_fsm(mock_user, mock_chat, fsm_context, mock_bot):
    # State is None (Zero-FSM)
    assert await fsm_context.get_state() is None

    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    from docx import Document
    docx_io = io.BytesIO()
    doc = Document()
    doc.add_paragraph("Тестовый конспект")
    doc.save(docx_io)
    docx_io.seek(0)

    doc_mock = MagicMock()
    doc_mock.file_id = "doc_zero_fsm"
    doc_mock.file_name = "test.docx"
    msg.document = doc_mock

    mock_bot.get_file.return_value = MagicMock(file_path="docs/test.docx")
    mock_bot.download_file.return_value = docx_io

    async def fake_summary_stream():
        yield "Краткое содержание"

    with patch("app.handlers.messages.get_recent_notes_for_linking", AsyncMock(return_value="")), \
         patch("app.handlers.messages.stream_summarize_document", return_value=fake_summary_stream()), \
         patch("app.handlers.messages.generate_semantic_filename", AsyncMock(return_value="Конспект.md")), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="Конспект.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()) as mock_upload, \
         patch("app.handlers.messages.add_note_log", AsyncMock()):

        await handle_document(msg, fsm_context, mock_bot)
        mock_upload.assert_called_once()
        assert await fsm_context.get_state() is None

@pytest.mark.asyncio
async def test_quick_capture_photo_zero_fsm(mock_user, mock_chat, fsm_context, mock_bot):
    # State is None (Zero-FSM)
    assert await fsm_context.get_state() is None

    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    photo_item = MagicMock()
    photo_item.file_id = "photo_zero_fsm"
    msg.photo = [photo_item]
    msg.caption = "План на день"

    mock_bot.get_file.return_value = MagicMock(file_path="photos/p_zero.jpg")
    mock_bot.download_file.return_value = io.BytesIO(b"fake_image_bytes")

    processed_data = {
        "category": "Photos",
        "tags": ["планы"],
        "corrected_text": "План на день",
        "filename": "План на день.md"
    }

    with patch("app.handlers.messages.process_text", AsyncMock(return_value=processed_data)), \
         patch("app.handlers.messages.get_rephrased_filename", AsyncMock(return_value="План на день.md")), \
         patch("app.handlers.messages.storage.upload_file", AsyncMock()) as mock_upload, \
         patch("app.handlers.messages.add_note_log", AsyncMock()):

        await handle_photo(msg, fsm_context, mock_bot)
        assert mock_upload.call_count == 2
        assert await fsm_context.get_state() is None

@pytest.mark.asyncio
async def test_quick_capture_audio_zero_fsm(mock_user, mock_chat, fsm_context, mock_bot):
    # State is None (Zero-FSM)
    assert await fsm_context.get_state() is None

    msg = create_mock_message("", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    voice_item = MagicMock()
    voice_item.file_id = "voice_zero_fsm"
    msg.voice = voice_item
    msg.audio = None

    mock_bot.get_file.return_value = MagicMock(file_path="voice/v_zero.ogg")
    mock_bot.download_file.return_value = io.BytesIO(b"fake_audio_bytes")

    with patch("app.handlers.messages.transcribe_audio", AsyncMock(return_value="Заметка голосом")), \
         patch("app.handlers.messages.process_note_text", AsyncMock()) as mock_process:

        await handle_audio(msg, fsm_context, mock_bot)
        mock_process.assert_called_once()
        assert mock_process.call_args[0][2] == "Заметка голосом"

@pytest.mark.asyncio
async def test_cancellation_handlers(mock_user, mock_chat, fsm_context):
    # Test /cancel and ❌ Отмена
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg_cancel = create_mock_message("/cancel", mock_user, mock_chat)
    await cmd_back(msg_cancel, fsm_context)
    assert await fsm_context.get_state() is None
    msg_cancel.answer.assert_called_once()

    await fsm_context.set_state(NoteState.waiting_for_text)
    msg_btn = create_mock_message("❌ Отмена", mock_user, mock_chat)
    await cmd_back(msg_btn, fsm_context)
    assert await fsm_context.get_state() is None
    msg_btn.answer.assert_called_once()

    # Case-insensitive cancellation variations
    for text in ["ОТМЕНА", "В главное меню", "отменить"]:
        await fsm_context.set_state(NoteState.waiting_for_text)
        msg_var = create_mock_message(text, mock_user, mock_chat)
        await cmd_back(msg_var, fsm_context)
        assert await fsm_context.get_state() is None

@pytest.mark.asyncio
async def test_quick_capture_path_notes(mock_user, mock_chat, fsm_context):
    # Verify that file paths like /var/log/syslog are captured as notes, not dropped
    msg = create_mock_message("/var/log/syslog\nerror: connection refused", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.messages.process_note_text", AsyncMock()) as mock_process:
        await handle_text(msg, fsm_context)
        mock_process.assert_called_once()
        assert "/var/log/syslog" in mock_process.call_args[0][2]

    # In waiting_for_text state, custom note starting with slash is also captured
    await fsm_context.set_state(NoteState.waiting_for_text)
    msg2 = create_mock_message("/usr/local/bin/deploy.sh", mock_user, mock_chat)
    msg2.answer.return_value = status_msg

    with patch("app.handlers.messages.process_note_text", AsyncMock()) as mock_process2:
        await handle_text(msg2, fsm_context)
        mock_process2.assert_called_once()
        assert "/usr/local/bin/deploy.sh" in mock_process2.call_args[0][2]

@pytest.mark.asyncio
async def test_cmd_sync_clears_active_fsm_state(mock_user, mock_chat, fsm_context):
    from app.handlers.messages import cmd_sync_s3
    await fsm_context.set_state(NoteState.waiting_for_text)
    assert await fsm_context.get_state() == NoteState.waiting_for_text.state

    msg = create_mock_message("🔄 Полная синхронизация S3", mock_user, mock_chat)
    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer.return_value = status_msg

    with patch("app.handlers.messages.run_s3_sync", AsyncMock(return_value=(2, 0))):
        await cmd_sync_s3(msg, fsm_context)
        assert await fsm_context.get_state() is None
        status_msg.edit_text.assert_called_once()
