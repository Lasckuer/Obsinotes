import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.database.db import (
    DatabaseManager,
    extract_date_from_note,
    parse_iso_datetime,
    add_reminder,
    get_user_reminders,
    init_db
)
from app.services.reminders_sync import (
    backup_reminders_to_s3,
    restore_reminders_from_s3,
    S3_REMINDERS_FOLDER,
    S3_REMINDERS_FILE
)
from app.handlers.scheduler import daily_digest
from app.config import get_settings

# =========================================================================
# 1. Date Extraction Tests
# =========================================================================

def test_extract_date_from_frontmatter():
    # ISO date in frontmatter
    md1 = "---\ntags: [test]\ndate: 2024-05-15\n---\n# Note Content"
    assert extract_date_from_note(md1) == "2024-05-15 12:00:00"

    # Full timestamp in frontmatter
    md2 = "---\ndate: 2023-11-01 14:30:00\n---\nText"
    assert extract_date_from_note(md2) == "2023-11-01 14:30:00"

    # Russian date in frontmatter
    md3 = "---\ncreated: 10.02.2024\n---\nText"
    assert extract_date_from_note(md3) == "2024-02-10 12:00:00"

    # No date in frontmatter
    md4 = "---\ntags: [a]\n---\nJust content"
    assert extract_date_from_note(md4) is None
    assert extract_date_from_note("") is None

def test_parse_iso_datetime():
    # S3 ISO format
    iso_str = "2024-08-20T10:15:30.000Z"
    assert parse_iso_datetime(iso_str) == "2024-08-20 10:15:30"

    # Date only
    assert parse_iso_datetime("2024-08-20") == "2024-08-20 12:00:00"
    assert parse_iso_datetime(None) is None


# =========================================================================
# 2. S3 Sync Preserves Historical Date (Does Not Overwrite With Today)
# =========================================================================

@pytest.mark.asyncio
async def test_upsert_note_log_preserves_historical_date():
    db = DatabaseManager(db_path=":memory:")
    await db.init_db()

    # Note with 2023 date in frontmatter
    content_2023 = "---\ndate: 2023-01-15\n---\nOld note content"
    await db.upsert_note_log(
        filename="OldNote.md",
        category="Notes",
        tags="test",
        content=content_2023,
        etag="etag1",
        last_modified="2023-01-15T12:00:00Z"
    )

    # Note with S3 last_modified from 2024, no frontmatter
    content_2024 = "Note without frontmatter"
    await db.upsert_note_log(
        filename="S3Note.md",
        category="Notes",
        tags="",
        content=content_2024,
        etag="etag2",
        last_modified="2024-06-10T15:00:00Z"
    )

    conn = await db.get_connection()
    async with conn.execute("SELECT filename, date FROM notes_log") as cursor:
        rows = dict(await cursor.fetchall())

    assert "2023-01-15" in rows["OldNote.md"]
    assert "2024-06-10" in rows["S3Note.md"]

    # Verify get_today_notes does NOT include these old notes
    today_notes = await db.get_today_notes()
    today_files = [f for f, c in today_notes]
    assert "OldNote.md" not in today_files
    assert "S3Note.md" not in today_files

    await db.close()


# =========================================================================
# 3. S3 Reminders Backup and Restore
# =========================================================================

@pytest.mark.asyncio
async def test_reminders_s3_backup_and_restore(test_db):
    mock_storage = AsyncMock()
    uploaded_payloads = {}

    async def mock_upload(folder, filename, content):
        uploaded_payloads[f"{folder}/{filename}"] = content
        return True

    mock_storage.upload_file = AsyncMock(side_effect=mock_upload)
    mock_storage.is_configured = MagicMock(return_value=True)

    # 1. Add reminders to database
    await test_db.add_reminder(111, "Позвонить партнеру", "2026-11-25 15:00", 0)
    await test_db.add_reminder(222, "Купить билет", "2026-11-26 10:00", 60)

    # 2. Trigger backup
    with patch("app.database.db.db_manager", test_db):
        success = await backup_reminders_to_s3(mock_storage)
        assert success is True

    backup_key = f"{S3_REMINDERS_FOLDER}/{S3_REMINDERS_FILE}"
    assert backup_key in uploaded_payloads
    saved_json = json.loads(uploaded_payloads[backup_key].decode('utf-8'))
    assert len(saved_json) == 2
    assert saved_json[0]["text"] == "Позвонить партнеру"
    assert saved_json[1]["text"] == "Купить билет"

    # 3. Simulate redeploy: create a brand new fresh database (simulating wiped container)
    fresh_db = DatabaseManager(db_path=":memory:")
    await fresh_db.init_db()

    # Verify fresh database is empty
    assert len(await fresh_db.get_user_reminders(111)) == 0

    # 4. Restore from S3 into fresh DB
    mock_storage.download_file = AsyncMock(return_value=uploaded_payloads[backup_key])
    with patch("app.database.db.db_manager", fresh_db):
        restored_count = await restore_reminders_from_s3(mock_storage)
        assert restored_count == 2

        user1_rems = await fresh_db.get_user_reminders(111)
        assert len(user1_rems) == 1
        assert user1_rems[0]["text"] == "Позвонить партнеру"

        user2_rems = await fresh_db.get_user_reminders(222)
        assert len(user2_rems) == 1
        assert user2_rems[0]["text"] == "Купить билет"

    await fresh_db.close()


# =========================================================================
# 4. Daily Digest Configuration and Limiting
# =========================================================================

@pytest.mark.asyncio
async def test_daily_digest_disabled_setting(monkeypatch):
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    settings = get_settings()
    monkeypatch.setattr(settings, "ENABLE_DAILY_DIGEST", False)

    # Even with notes, if disabled, nothing is sent
    with patch("app.handlers.scheduler.get_today_notes", AsyncMock(return_value=[("Note1.md", "Notes")])):
        await daily_digest(mock_bot, admin_id=12345)
        mock_bot.send_message.assert_not_called()

@pytest.mark.asyncio
async def test_daily_digest_capped_output(monkeypatch):
    mock_bot = AsyncMock()
    mock_bot.send_message = AsyncMock()

    settings = get_settings()
    monkeypatch.setattr(settings, "ENABLE_DAILY_DIGEST", True)

    # 40 notes today
    many_notes = [(f"Note_{i}.md", "Notes") for i in range(40)]
    with patch("app.handlers.scheduler.get_today_notes", AsyncMock(return_value=many_notes)):
        await daily_digest(mock_bot, admin_id=12345)
        mock_bot.send_message.assert_called_once()
        sent_text = mock_bot.send_message.call_args[0][1]
        assert "Дайджест" in sent_text
        assert "и еще 15 заметок" in sent_text
