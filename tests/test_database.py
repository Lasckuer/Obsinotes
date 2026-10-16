import pytest
import datetime
from app.database.db import DatabaseManager

@pytest.mark.asyncio
async def test_database_init(test_db: DatabaseManager):
    conn = await test_db.get_connection()
    # Check tables exist
    async with conn.execute("SELECT name FROM sqlite_master WHERE type='table'") as cursor:
        tables = [row[0] for row in await cursor.fetchall()]
        assert "reminders" in tables
        assert "expenses" in tables
        assert "notes_log" in tables

@pytest.mark.asyncio
async def test_reminders_crud(test_db: DatabaseManager):
    user_id = 111222
    now_str = "2026-09-12 10:00"

    await test_db.add_reminder(user_id, "Размять спину", now_str, recur_minutes=60)
    await test_db.add_reminder(user_id, "Купить молоко", "2026-09-12 18:00", recur_minutes=0)

    # Get due reminders at 10:00
    due = await test_db.get_due_reminders("2026-09-12 10:00")
    assert len(due) == 1
    r_id, u_id, text, recur = due[0]
    assert u_id == user_id
    assert text == "Размять спину"
    assert recur == 60

    # Update reminder time
    await test_db.update_reminder_time(r_id, "2026-09-12 11:00")
    due_after = await test_db.get_due_reminders("2026-09-12 10:00")
    assert len(due_after) == 0

    # Delete reminder
    await test_db.delete_reminder(r_id)
    due_future = await test_db.get_due_reminders("2026-09-12 12:00")
    assert len(due_future) == 0

@pytest.mark.asyncio
async def test_expenses(test_db: DatabaseManager):
    user_id = 111222
    await test_db.add_expense(user_id, 350.50, "Кофе и круассан")

    conn = await test_db.get_connection()
    async with conn.execute("SELECT user_id, amount, comment FROM expenses WHERE user_id = ?", (user_id,)) as cursor:
        rows = await cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == 350.50
        assert rows[0][2] == "Кофе и круассан"

@pytest.mark.asyncio
async def test_notes_log_and_upsert(test_db: DatabaseManager):
    # Initial insert
    is_added, is_updated = await test_db.upsert_note_log(
        filename="Проект.md",
        category="TelegramBot/Notes",
        tags="разработка",
        content="Начало проекта"
    )
    assert is_added is True
    assert is_updated is False

    # Check note_exists
    assert await test_db.note_exists("Проект.md", "Notes") is True
    assert await test_db.note_exists("Несуществующий.md", "Notes") is False

    # No change upsert
    is_added, is_updated = await test_db.upsert_note_log(
        filename="Проект.md",
        category="TelegramBot/Notes",
        tags="разработка",
        content="Начало проекта"
    )
    assert is_added is False
    assert is_updated is False

    # Update upsert
    is_added, is_updated = await test_db.upsert_note_log(
        filename="Проект.md",
        category="TelegramBot/Notes",
        tags="разработка, обновление",
        content="Обновленный текст проекта"
    )
    assert is_added is False
    assert is_updated is True

@pytest.mark.asyncio
async def test_search_notes(test_db: DatabaseManager):
    await test_db.add_note_log(
        filename="Установка Docker.md",
        category="TelegramBot/DevOps",
        tags="docker, linux, devops",
        content="Инструкция по развертыванию контейнеров в Linux."
    )
    await test_db.add_note_log(
        filename="Кулинарный рецепт.md",
        category="TelegramBot/Recipes",
        tags="еда, пицца",
        content="Рецепт теста для итальянской пиццы."
    )

    # Search by tag / keyword
    results = await test_db.search_notes("docker")
    assert len(results) == 1
    assert results[0][0] == "Установка Docker.md"

    # Search by content
    results = await test_db.search_notes("пиццы")
    assert len(results) == 1
    assert results[0][0] == "Кулинарный рецепт.md"

    # Empty query returns empty list
    assert await test_db.search_notes("   ") == []

@pytest.mark.asyncio
async def test_recent_context_and_linking(test_db: DatabaseManager):
    await test_db.add_note_log(
        filename="Первая заметка.md",
        category="Notes",
        tags="tag1",
        content="Первое содержимое"
    )
    await test_db.add_note_log(
        filename="Вторая заметка.md",
        category="Notes",
        tags="tag2",
        content="Второе содержимое"
    )

    context = await test_db.get_recent_context(limit=5)
    assert "[Notes]: Второе содержимое" in context
    assert "[Notes]: Первое содержимое" in context

    linking = await test_db.get_recent_notes_for_linking(limit=5)
    assert "- [[Вторая заметка]]: Второе содержимое..." in linking
    assert "- [[Первая заметка]]: Первое содержимое..." in linking

@pytest.mark.asyncio
async def test_recent_notes_for_linking_cleans_frontmatter(test_db: DatabaseManager):
    content_with_frontmatter = """---
tags: [ai, obsidian]
date: 2026-10-15
---

Чистый текст заметки о нейросетях и знаниях."""

    content_with_crlf = "---\r\ntags: [crlf]\r\ndate: 2026-10-15\r\n---\r\n\r\nТекст с Windows переводами строк."

    content_with_bom = "\ufeff---\ntags: [bom]\ndate: 2026-10-15\n---\n\nТекст заметки с UTF-8 BOM."

    await test_db.add_note_log(
        filename="Нейросети.md",
        category="Notes",
        tags="ai",
        content=content_with_frontmatter
    )
    await test_db.add_note_log(
        filename="Windows.md",
        category="Notes",
        tags="crlf",
        content=content_with_crlf
    )
    await test_db.add_note_log(
        filename="BOM.md",
        category="Notes",
        tags="bom",
        content=content_with_bom
    )

    linking = await test_db.get_recent_notes_for_linking(limit=5)
    assert "tags: [ai" not in linking
    assert "date: 2026-10-15" not in linking
    assert "tags: [bom" not in linking
    assert "Чистый текст заметки о нейросетях" in linking
    assert "Текст с Windows переводами строк" in linking
    assert "Текст заметки с UTF-8 BOM" in linking

@pytest.mark.asyncio
async def test_note_exists_root_isolation(test_db: DatabaseManager):
    # File exists in a subfolder
    await test_db.add_note_log(
        filename="SpecificNote.md",
        category="TelegramBot/Notes",
        tags="",
        content="Subfolder content"
    )

    # Checking if it exists in root ("") should return False
    exists_in_root = await test_db.note_exists("SpecificNote.md", "")
    assert exists_in_root is False

    # Checking if it exists in "Notes" or "TelegramBot/Notes" should return True
    assert await test_db.note_exists("SpecificNote.md", "Notes") is True
    assert await test_db.note_exists("SpecificNote.md", "TelegramBot/Notes") is True

    # Now add a note truly in root ("")
    await test_db.add_note_log(
        filename="TrulyRoot.md",
        category="",
        tags="",
        content="Root content"
    )
    assert await test_db.note_exists("TrulyRoot.md", "") is True
    assert await test_db.note_exists("TrulyRoot.md", "Root") is True
    assert await test_db.note_exists("TrulyRoot.md", "Notes") is False

@pytest.mark.asyncio
async def test_database_auto_creates_parent_directory(tmp_path):
    nested_db_file = tmp_path / "deep" / "nested" / "dir" / "test.db"
    assert not nested_db_file.parent.exists()
    db = DatabaseManager(db_path=str(nested_db_file))
    await db.init_db()
    assert nested_db_file.parent.exists()
    assert nested_db_file.exists()
    await db.close()

@pytest.mark.asyncio
async def test_note_by_id_and_file_cat(test_db: DatabaseManager):
    note_id = await test_db.add_note_log(
        filename="TestNote.md",
        category="TelegramBot/Notes",
        tags="test, unit",
        content="Hello world content"
    )
    assert isinstance(note_id, int)
    assert note_id > 0

    # Retrieve by ID
    note = await test_db.get_note_by_id(note_id)
    assert note is not None
    assert note["id"] == note_id
    assert note["filename"] == "TestNote.md"
    assert note["category"] == "TelegramBot/Notes"
    assert note["tags"] == "test, unit"
    assert note["content"] == "Hello world content"

    # Non-existent ID
    assert await test_db.get_note_by_id(999999) is None

    # Retrieve by file and category
    note_by_cat = await test_db.get_note_by_file_and_cat("TestNote.md", "TelegramBot/Notes")
    assert note_by_cat is not None
    assert note_by_cat["id"] == note_id

    # Non-existent file/category
    assert await test_db.get_note_by_file_and_cat("NoSuchNote.md", "TelegramBot/Notes") is None

@pytest.mark.asyncio
async def test_expenses_summary(test_db: DatabaseManager):
    user_id = 999
    # Add expenses
    await test_db.add_expense(user_id, 250.0, "Кофе")
    await test_db.add_expense(user_id, 1500.5, "Продукты")

    summary = await test_db.get_expenses_summary(user_id)
    assert summary["today_sum"] == 1750.5
    assert summary["today_count"] == 2
    assert summary["month_sum"] == 1750.5
    assert summary["month_count"] == 2
    assert len(summary["today_records"]) == 2

    # Different user has 0
    other_summary = await test_db.get_expenses_summary(888)
    assert other_summary["today_sum"] == 0.0
    assert other_summary["today_count"] == 0

@pytest.mark.asyncio
async def test_tags_with_counts_and_get_notes_by_tag(test_db: DatabaseManager):
    await test_db.add_note_log(
        filename="NoteA.md",
        category="TelegramBot/Notes",
        tags="python, backend, ai",
        content="Coding in python"
    )
    await test_db.add_note_log(
        filename="NoteB.md",
        category="TelegramBot/Notes",
        tags="python, data",
        content="Data science and python"
    )
    await test_db.add_note_log(
        filename="NoteC.md",
        category="TelegramBot/Notes",
        tags="design",
        content="UI and design #figma"
    )

    tags = await test_db.get_all_tags_with_counts()
    tag_dict = dict(tags)
    assert tag_dict["python"] == 2
    assert tag_dict["backend"] == 1
    assert tag_dict["ai"] == 1
    assert tag_dict["data"] == 1
    assert tag_dict["design"] == 1

    # Python is the most frequent
    assert tags[0][0] == "python"
    assert tags[0][1] == 2

    # Get notes by tag
    notes_python = await test_db.get_notes_by_tag("python")
    assert len(notes_python) == 2
    fnames = [n[0] for n in notes_python]
    assert "NoteA.md" in fnames
    assert "NoteB.md" in fnames

    # Get notes by tag in content (#figma)
    notes_figma = await test_db.get_notes_by_tag("figma")
    assert len(notes_figma) == 1
    assert notes_figma[0][0] == "NoteC.md"



