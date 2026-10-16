import pytest
import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

from app.database.db import (
    DatabaseManager,
    strip_frontmatter,
    RAGNote,
    search_rag_context,
    get_all_notes_metadata,
    delete_notes,
    commit_db
)
from app.services.s3_storage import S3StorageService
from app.handlers.messages import run_s3_sync
from app.handlers.search import handle_search_or_ai, SearchAIState
from app.utils.streaming import stream_to_message


from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.base import StorageKey

@pytest.fixture
def fsm_context(mock_user, mock_chat):
    storage = MemoryStorage()
    key = StorageKey(bot_id=1, chat_id=mock_chat.id, user_id=mock_user.id)
    return FSMContext(storage=storage, key=key)

# -------------------------------------------------------------
# 1. Delta Sync & Deleted File Pruning Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_s3_storage_list_files_with_metadata():
    storage = S3StorageService(
        endpoint_url="http://mock-s3.local",
        access_key="mock_key",
        secret_key="mock_secret",
        bucket_name="test-bucket"
    )

    class MockPaginator:
        def __init__(self, pages):
            self.pages = pages
        def paginate(self, **kwargs):
            async def _gen():
                for p in self.pages:
                    yield p
            return _gen()

    dt = datetime.datetime(2026, 10, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
    pages = [
        {
            "Contents": [
                {
                    "Key": "TelegramBot/Notes/Doc1.md",
                    "ETag": '"etag-doc-1"',
                    "LastModified": dt,
                    "Size": 128
                },
                {
                    "Key": "TelegramBot/Notes/ignore_dir/",
                    "Size": 0
                },
                {
                    "Key": "RootDoc.md",
                    "ETag": '"etag-root"',
                    "LastModified": "2026-10-15T12:00:00Z",
                    "Size": 256
                }
            ]
        }
    ]

    mock_client = MagicMock()
    mock_client.get_paginator = MagicMock(return_value=MockPaginator(pages))

    with patch.object(storage, "get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client
        mock_get_client.return_value.__aexit__.return_value = None

        meta_files = await storage.list_files_with_metadata()
        assert len(meta_files) == 2
        assert meta_files[0]["key"] == "TelegramBot/Notes/Doc1.md"
        assert meta_files[0]["etag"] == "etag-doc-1"
        assert meta_files[0]["last_modified"] == dt.isoformat()
        assert meta_files[0]["size"] == 128

        assert meta_files[1]["key"] == "RootDoc.md"
        assert meta_files[1]["etag"] == "etag-root"
        assert meta_files[1]["size"] == 256

        # Check list_files alias
        assert storage.list_files == storage.list_files_with_metadata


@pytest.mark.asyncio
async def test_run_s3_sync_delta_and_pruning(test_db: DatabaseManager):
    """
    Verifies that:
    1. First sync downloads all files and sets etag & last_modified in DB.
    2. Second sync with identical metadata downloads 0 files.
    3. Modified ETag triggers download only for the changed file.
    4. Deleted file in S3 is pruned from SQLite notes_log & notes_fts.
    """
    # Replace global db_manager with test_db for this test
    with patch("app.handlers.messages.get_all_notes_metadata", test_db.get_all_notes_metadata), \
         patch("app.handlers.messages.upsert_note_log", test_db.upsert_note_log), \
         patch("app.handlers.messages.delete_notes", test_db.delete_notes), \
         patch("app.handlers.messages.commit_db", test_db.commit):

        # Initial S3 state: NoteA.md and NoteB.md
        s3_items_v1 = [
            {"key": "NoteA.md", "etag": "etagA1", "last_modified": "2026-10-15T10:00:00", "size": 100},
            {"key": "TelegramBot/Notes/NoteB.md", "etag": "etagB1", "last_modified": "2026-10-15T10:00:00", "size": 200}
        ]
        downloads_v1 = {
            "NoteA.md": b"# Note A content",
            "TelegramBot/Notes/NoteB.md": b"# Note B content"
        }

        # --- STEP 1: First sync ---
        with patch("app.handlers.messages.storage.list_files_with_metadata", AsyncMock(return_value=s3_items_v1)), \
             patch("app.handlers.messages.storage.batch_download", AsyncMock(return_value=downloads_v1)) as mock_dl1:
            added, updated, deleted = await run_s3_sync(return_deleted=True)
            assert added == 2
            assert updated == 0
            assert deleted == 0
            assert mock_dl1.call_args[0][0] == ["NoteA.md", "TelegramBot/Notes/NoteB.md"]

        # Check DB state
        meta = await test_db.get_all_notes_metadata()
        assert len(meta) == 2
        assert ("", "NoteA.md") in meta
        assert meta[("", "NoteA.md")]["etag"] == "etagA1"

        # --- STEP 2: Second sync (no changes in S3) ---
        with patch("app.handlers.messages.storage.list_files_with_metadata", AsyncMock(return_value=s3_items_v1)), \
             patch("app.handlers.messages.storage.batch_download", AsyncMock(return_value={})) as mock_dl2:
            added, updated, deleted = await run_s3_sync(return_deleted=True)
            assert added == 0
            assert updated == 0
            assert deleted == 0
            # Delta sync: Nothing to download!
            assert mock_dl2.call_count == 0

        # --- STEP 3: NoteA was updated in S3 (etag changed) ---
        s3_items_v2 = [
            {"key": "NoteA.md", "etag": "etagA2_changed", "last_modified": "2026-10-15T11:00:00", "size": 110},
            {"key": "TelegramBot/Notes/NoteB.md", "etag": "etagB1", "last_modified": "2026-10-15T10:00:00", "size": 200}
        ]
        downloads_v2 = {
            "NoteA.md": b"# Note A content updated"
        }
        with patch("app.handlers.messages.storage.list_files_with_metadata", AsyncMock(return_value=s3_items_v2)), \
             patch("app.handlers.messages.storage.batch_download", AsyncMock(return_value=downloads_v2)) as mock_dl3:
            added, updated, deleted = await run_s3_sync(return_deleted=True)
            assert added == 0
            assert updated == 1
            assert deleted == 0
            # Only NoteA was downloaded!
            assert mock_dl3.call_args[0][0] == ["NoteA.md"]

        # --- STEP 4: NoteB was deleted in S3, NoteC was added ---
        s3_items_v3 = [
            {"key": "NoteA.md", "etag": "etagA2_changed", "last_modified": "2026-10-15T11:00:00", "size": 110},
            {"key": "NoteC.md", "etag": "etagC1", "last_modified": "2026-10-15T12:00:00", "size": 50}
        ]
        downloads_v3 = {
            "NoteC.md": b"# Note C content"
        }
        with patch("app.handlers.messages.storage.list_files_with_metadata", AsyncMock(return_value=s3_items_v3)), \
             patch("app.handlers.messages.storage.batch_download", AsyncMock(return_value=downloads_v3)) as mock_dl4:
            added, updated, deleted = await run_s3_sync(return_deleted=True)
            assert added == 1
            assert updated == 0
            assert deleted == 1  # NoteB was pruned!
            assert mock_dl4.call_args[0][0] == ["NoteC.md"]

        # Verify NoteB is gone from DB
        meta_after = await test_db.get_all_notes_metadata()
        assert ("TelegramBot/Notes", "NoteB.md") not in meta_after
        assert ("", "NoteA.md") in meta_after
        assert ("", "NoteC.md") in meta_after


@pytest.mark.asyncio
async def test_database_auto_migration_for_etag_and_last_modified(tmp_path):
    """
    Tests that init_db automatically adds 'etag' and 'last_modified'
    columns to an existing legacy table without losing data.
    """
    import aiosqlite
    db_file = tmp_path / "legacy.db"

    # Create old table without etag and last_modified
    async with aiosqlite.connect(str(db_file)) as conn:
        await conn.execute("""
            CREATE TABLE notes_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                category TEXT NOT NULL,
                tags TEXT,
                content TEXT,
                date TIMESTAMP NOT NULL
            )
        """)
        await conn.execute(
            "INSERT INTO notes_log (filename, category, tags, content, date) VALUES (?, ?, ?, ?, ?)",
            ("OldNote.md", "Notes", "tag", "Old text", "2026-01-01 00:00:00")
        )
        await conn.commit()

    # Now open with DatabaseManager and call init_db
    db = DatabaseManager(db_path=str(db_file))
    await db.init_db()

    conn = await db.get_connection()
    async with conn.execute("PRAGMA table_info(notes_log)") as cursor:
        cols = [r[1] for r in await cursor.fetchall()]
        assert "etag" in cols
        assert "last_modified" in cols

    # Verify old note is still present
    meta = await db.get_all_notes_metadata()
    assert ("Notes", "OldNote.md") in meta
    assert meta[("Notes", "OldNote.md")]["etag"] is None
    await db.close()


# -------------------------------------------------------------
# 2. Hybrid RAG Context Retrieval Tests
# -------------------------------------------------------------

def test_strip_frontmatter():
    # Standard YAML frontmatter
    md = "---\ntags: [ai, bot]\ndate: 2026-10-15\n---\n\nЗаметка о ботах."
    assert strip_frontmatter(md) == "Заметка о ботах."

    # Windows CRLF
    md_crlf = "---\r\ntags: [test]\r\n---\r\n\r\nТекст с CRLF."
    assert strip_frontmatter(md_crlf) == "Текст с CRLF."

    # Leading BOM
    md_bom = "\ufeff---\ntags: [bom]\n---\n\nЧистый текст."
    assert strip_frontmatter(md_bom) == "Чистый текст."

    # No frontmatter
    assert strip_frontmatter("Обычный текст без заголовка") == "Обычный текст без заголовка"
    assert strip_frontmatter(None) == ""


def test_rag_note_dict_and_tuple_access():
    note = RAGNote(
        filename="Python.md",
        category="Dev",
        content="Текст про Python",
        date="2026-10-15",
        tags="coding",
        note_id=42
    )
    # Dict access
    assert isinstance(note, dict)
    assert note["filename"] == "Python.md"
    assert note["content"] == "Текст про Python"
    assert note["id"] == 42
    assert note.get("category") == "Dev"

    # Tuple unpacking
    fname, cat, cont, dt = note
    assert fname == "Python.md"
    assert cat == "Dev"
    assert cont == "Текст про Python"
    assert dt == "2026-10-15"


@pytest.mark.asyncio
async def test_search_rag_context_relevance_and_frontmatter(test_db: DatabaseManager):
    """
    Tests that:
    1. search_rag_context ranks matching notes higher than non-matching ones.
    2. YAML frontmatter is stripped.
    3. If matches are fewer than limit, recent notes supplement the context.
    """
    # Insert notes
    note1 = """---
tags: [devops, k8s]
date: 2026-10-01
---
Архитектура Kubernetes и настройка кластера pods."""
    await test_db.add_note_log(
        filename="Kubernetes.md",
        category="DevOps",
        tags="k8s",
        content=note1
    )

    note2 = """---
tags: [cooking]
date: 2026-10-02
---
Рецепт вкусного яблочного пирога шарлотка."""
    await test_db.add_note_log(
        filename="Шарлотка.md",
        category="Recipes",
        tags="еда",
        content=note2
    )

    note3 = """---
tags: [devops, docker]
date: 2026-10-03
---
Как развернуть Docker контейнеры на сервере."""
    await test_db.add_note_log(
        filename="Docker.md",
        category="DevOps",
        tags="docker",
        content=note3
    )

    # Ask RAG for "Как развернуть контейнер"
    results = await test_db.search_rag_context("Как развернуть контейнер", limit=2)
    assert len(results) == 2

    # Top match must be Docker.md
    assert results[0]["filename"] == "Docker.md"
    assert "Как развернуть Docker контейнеры на сервере." in results[0]["content"]
    # Frontmatter stripped!
    assert "tags: [devops" not in results[0]["content"]
    assert "---" not in results[0]["content"]

    # When limit=5 (more than 2 matching DevOps notes), it supplements with remaining notes (Шарлотка.md)
    results_all = await test_db.search_rag_context("Kubernetes кластер", limit=5)
    filenames = [r["filename"] for r in results_all]
    assert "Kubernetes.md" in filenames
    assert "Шарлотка.md" in filenames  # Supplemented recent note!
    assert "tags:" not in results_all[-1]["content"]


@pytest.mark.asyncio
async def test_search_rag_empty_or_no_notes(test_db: DatabaseManager):
    # Empty DB
    empty_results = await test_db.search_rag_context("Любой вопрос?", limit=5)
    assert empty_results == []


@pytest.mark.asyncio
async def test_handle_search_or_ai_uses_rag_context(mock_user, mock_chat, fsm_context):
    """
    Verifies that handle_search_or_ai calls search_rag_context with limit=5
    and passes the retrieved context to stream_answer_question.
    """
    await fsm_context.set_state(SearchAIState.waiting_for_input)
    msg = AsyncMock(spec=Message)
    msg.text = "Как настроить Docker?"
    msg.from_user = mock_user
    msg.chat = mock_chat

    status_msg = AsyncMock(spec=Message)
    status_msg.edit_text = AsyncMock()
    msg.answer = AsyncMock(return_value=status_msg)

    fake_rag_notes = [
        RAGNote(
            filename="Docker.md",
            category="DevOps",
            content="Чистый текст инструкции Docker",
            date="2026-10-15"
        )
    ]

    async def fake_stream():
        yield "Ответ "
        yield "нейросети."

    with patch("app.handlers.search.search_rag_context", AsyncMock(return_value=fake_rag_notes)) as mock_rag, \
         patch("app.handlers.search.stream_answer_question", return_value=fake_stream()) as mock_stream, \
         patch("app.handlers.search.stream_to_message", AsyncMock(return_value="Ответ нейросети.")) as mock_stream_msg:

        await handle_search_or_ai(msg, fsm_context)

        mock_rag.assert_called_once_with("Как настроить Docker?", limit=5)
        # Check context fed into stream_answer_question
        called_args = mock_stream.call_args[0]
        assert called_args[0] == "Как настроить Docker?"
        assert "[DevOps / Docker.md]: Чистый текст инструкции Docker" in called_args[1]
        assert await fsm_context.get_state() is None


# -------------------------------------------------------------
# 3. Streaming Polish Tests
# -------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_to_message_growing_text():
    async def token_generator():
        yield "Часть 1. "
        yield "Часть 2. "
        yield "Финал."

    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock()

    result = await stream_to_message(msg, token_generator(), "Генерация...", update_interval=0.0)
    assert result == "Часть 1. Часть 2. Финал."
    # edit_text was called with growing text and Markdown
    assert msg.edit_text.call_count >= 1
    last_call = msg.edit_text.call_args_list[-1]
    assert "Часть 1. Часть 2. Финал." in last_call[0][0]
    assert last_call[1].get("parse_mode") == "Markdown"


@pytest.mark.asyncio
async def test_stream_to_message_markdown_error_fallback():
    async def broken_markdown_generator():
        yield "*незакрытый курсив"

    msg = AsyncMock(spec=Message)
    # First edit_text with parse_mode='Markdown' raises TelegramBadRequest (e.g. unclosed tags)
    # Second edit_text (fallback plain text) succeeds
    msg.edit_text = AsyncMock(side_effect=[
        TelegramBadRequest(method="editMessageText", message="can't parse entities"),
        None
    ])

    result = await stream_to_message(msg, broken_markdown_generator(), "Генерация...", update_interval=0.0)
    assert result == "*незакрытый курсив"
    # Fallback to plain text was called (no parse_mode)
    assert msg.edit_text.call_count == 2
    fallback_call = msg.edit_text.call_args_list[1]
    assert fallback_call[0][0] == "*незакрытый курсив"
    assert "parse_mode" not in fallback_call[1]


@pytest.mark.asyncio
async def test_stream_to_message_short_stream_final_update():
    """Verifies that short streams (< update_interval) still update message with final text."""
    async def single_chunk_generator():
        yield "Быстрый ответ нейросети."

    msg = AsyncMock(spec=Message)
    msg.edit_text = AsyncMock()

    # update_interval is 10.0s, so inside the loop it won't trigger; only the final update triggers it
    result = await stream_to_message(msg, single_chunk_generator(), "Генерация...", update_interval=10.0)
    assert result == "Быстрый ответ нейросети."
    assert msg.edit_text.call_count == 1
    assert msg.edit_text.call_args[0][0] == "Быстрый ответ нейросети."


def test_strip_frontmatter_empty_and_dots():
    """Tests empty frontmatter and YAML ... delimiter."""
    empty_fm = "---\n---\nТекст после пустых разделителей."
    assert strip_frontmatter(empty_fm) == "Текст после пустых разделителей."

    dots_fm = "---\ntitle: Тест\n...\nТекст после троеточия."
    assert strip_frontmatter(dots_fm) == "Текст после троеточия."


def test_rag_note_indexing_and_len():
    """Tests len(note) and note[0..3] integer indexing for dual tuple/dict compatibility."""
    note = RAGNote("doc.md", "Notes", "Содержимое", "2026-10-15", "tag1", note_id=10)
    assert len(note) == 4
    assert note[0] == "doc.md"
    assert note[1] == "Notes"
    assert note[2] == "Содержимое"
    assert note[3] == "2026-10-15"
    assert note["tags"] == "tag1"


@pytest.mark.asyncio
async def test_migration_and_delete_no_fts_disk_image_corruption(tmp_path):
    """
    Verifies that when an existing DB is migrated, deleting an old note does NOT
    cause 'sqlite3.DatabaseError: database disk image is malformed'.
    """
    import aiosqlite
    db_file = tmp_path / "legacy_delete.db"

    # 1. Create old table without FTS and insert a row
    async with aiosqlite.connect(str(db_file)) as conn:
        await conn.execute("""
            CREATE TABLE notes_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                category TEXT NOT NULL,
                tags TEXT,
                content TEXT,
                date TIMESTAMP NOT NULL
            )
        """)
        await conn.execute(
            "INSERT INTO notes_log (filename, category, tags, content, date) VALUES (?, ?, ?, ?, ?)",
            ("LegacyNote.md", "Notes", "test", "Legacy note content to delete", "2026-01-01 00:00:00")
        )
        await conn.commit()

    # 2. Open with DatabaseManager and initialize (triggers FTS rebuild)
    db = DatabaseManager(db_path=str(db_file))
    await db.init_db()

    # 3. Delete the legacy note — should succeed without malformed disk image error!
    deleted = await db.delete_notes([("LegacyNote.md", "Notes")])
    assert deleted == 1

    # Verify it is deleted from both notes_log and FTS
    meta = await db.get_all_notes_metadata()
    assert ("Notes", "LegacyNote.md") not in meta
    await db.close()


@pytest.mark.asyncio
async def test_upsert_note_log_preserves_and_extracts_tags(test_db: DatabaseManager):
    """Verifies that upsert extracts frontmatter tags and preserves existing tags on update."""
    note_content = """---
tags: [architecture, microservices]
date: 2026-10-15
---
Текст об архитектуре микросервисов."""

    # 1. Insert via upsert with empty tags argument -> should extract from frontmatter!
    added, updated = await test_db.upsert_note_log(
        filename="Arch.md",
        category="Dev",
        tags="",
        content=note_content,
        etag="etag1",
        last_modified="2026-10-15"
    )
    assert added is True

    # Check that tags were extracted
    conn = await test_db.get_connection()
    async with conn.execute("SELECT tags FROM notes_log WHERE filename = 'Arch.md'") as cur:
        row = await cur.fetchone()
        assert "architecture" in row[0]
        assert "microservices" in row[0]

    # 2. Update note with modified content and empty tags -> tags should be preserved
    updated_content = "Обновленный текст без frontmatter."
    added2, updated2 = await test_db.upsert_note_log(
        filename="Arch.md",
        category="Dev",
        tags="",
        content=updated_content,
        etag="etag2",
        last_modified="2026-10-16"
    )
    assert updated2 is True

    async with conn.execute("SELECT tags FROM notes_log WHERE filename = 'Arch.md'") as cur:
        row2 = await cur.fetchone()
        # Old tags preserved!
        assert "architecture" in row2[0]


@pytest.mark.asyncio
async def test_handle_search_or_ai_handles_message_not_modified(mock_user, mock_chat, fsm_context):
    """
    Verifies that if edit_text encounters 'message is not modified',
    it does NOT bubble up as an error message to the user.
    """
    await fsm_context.set_state(SearchAIState.waiting_for_input)
    msg = AsyncMock(spec=Message)
    msg.text = "Как работает RAG?"
    msg.from_user = mock_user
    msg.chat = mock_chat

    status_msg = AsyncMock(spec=Message)
    # edit_text raises TelegramBadRequest('message is not modified')
    status_msg.edit_text = AsyncMock(side_effect=TelegramBadRequest(
        method="editMessageText",
        message="Bad Request: message is not modified: specified new message content is the same"
    ))
    msg.answer = AsyncMock(return_value=status_msg)

    fake_notes = [RAGNote("Rag.md", "AI", "Текст про RAG", "2026-10-15")]

    async def fake_stream():
        yield "Ответ про RAG"

    with patch("app.handlers.search.search_rag_context", AsyncMock(return_value=fake_notes)), \
         patch("app.handlers.search.stream_answer_question", return_value=fake_stream()), \
         patch("app.handlers.search.stream_to_message", AsyncMock(return_value="Ответ про RAG")):

        await handle_search_or_ai(msg, fsm_context)

        # Did NOT display "❌ Ошибка:" to the user!
        for call in status_msg.edit_text.call_args_list:
            arg_text = call[0][0] if call[0] else ""
            assert not arg_text.startswith("❌ Ошибка:")

