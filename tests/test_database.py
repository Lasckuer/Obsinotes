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
