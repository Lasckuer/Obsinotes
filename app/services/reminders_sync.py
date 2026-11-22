import json
import asyncio
from typing import Optional, List, Dict, Any
from app.services.s3_storage import S3StorageService
from logger import logger

S3_REMINDERS_FOLDER = "TelegramBot/System"
S3_REMINDERS_FILE = "reminders.json"

async def backup_reminders_to_s3(storage: Optional[S3StorageService] = None) -> bool:
    """
    Backs up all active reminders from SQLite into S3 storage as JSON.
    This ensures reminders survive container redeployments, restarts, and disk resets.
    """
    storage = storage or S3StorageService()
    if not storage.is_configured():
        return False

    try:
        from app.database.db import db_manager
        conn = await db_manager.get_connection()
        async with conn.execute(
            "SELECT id, user_id, text, remind_time, recur_minutes FROM reminders ORDER BY remind_time ASC"
        ) as cursor:
            rows = await cursor.fetchall()

        data: List[Dict[str, Any]] = [
            {
                "id": r[0],
                "user_id": r[1],
                "text": r[2],
                "remind_time": r[3],
                "recur_minutes": r[4]
            }
            for r in rows
        ]

        json_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')
        success = await storage.upload_file(S3_REMINDERS_FOLDER, S3_REMINDERS_FILE, json_bytes)
        if success:
            logger.debug(f"Бэкап {len(data)} напоминаний успешно сохранен в S3 ({S3_REMINDERS_FOLDER}/{S3_REMINDERS_FILE}).")
        return success
    except Exception as e:
        logger.warning(f"Не удалось сохранить бэкап напоминаний в S3: {e}")
        return False


async def restore_reminders_from_s3(storage: Optional[S3StorageService] = None) -> int:
    """
    Restores reminders from S3 storage into SQLite if they are missing locally.
    Called during bot startup and after S3 sync.
    """
    storage = storage or S3StorageService()
    if not storage.is_configured():
        return 0

    try:
        content_bytes = await storage.download_file(S3_REMINDERS_FOLDER, S3_REMINDERS_FILE)
        if not content_bytes:
            return 0

        raw_text = content_bytes.decode('utf-8', errors='ignore').strip()
        if not raw_text:
            return 0

        data = json.loads(raw_text)
        if not isinstance(data, list):
            return 0

        from app.database.db import db_manager
        conn = await db_manager.get_connection()
        restored_count = 0

        for item in data:
            if not isinstance(item, dict):
                continue
            user_id = item.get("user_id")
            text = item.get("text")
            remind_time = item.get("remind_time")
            recur_minutes = item.get("recur_minutes", 0)

            if not user_id or not text or not remind_time:
                continue

            # Check if reminder already exists in SQLite
            async with conn.execute(
                "SELECT 1 FROM reminders WHERE user_id = ? AND text = ? AND remind_time = ? LIMIT 1",
                (user_id, text, remind_time)
            ) as cursor:
                if await cursor.fetchone():
                    continue

            await conn.execute(
                "INSERT INTO reminders (user_id, text, remind_time, recur_minutes) VALUES (?, ?, ?, ?)",
                (user_id, text, remind_time, recur_minutes)
            )
            restored_count += 1

        if restored_count > 0:
            await conn.commit()
            logger.info(f"🔄 Восстановлено {restored_count} напоминаний из S3-хранилища ({S3_REMINDERS_FOLDER}/{S3_REMINDERS_FILE})!")

        return restored_count
    except Exception as e:
        logger.warning(f"Не удалось восстановить напоминания из S3: {e}")
        return 0


def trigger_reminders_backup():
    """Fire-and-forget helper to back up reminders in the background."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(backup_reminders_to_s3())
    except RuntimeError:
        pass
