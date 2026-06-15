import datetime
import pytz
from aiogram import Bot
from app.database.db import get_due_reminders, delete_reminder, get_today_notes
from app.handlers import messages
from logger import logger, log_reminder_sent, log_reminder_error

async def check_reminders(bot: Bot):
    tz = pytz.timezone('Europe/Moscow')
    now = datetime.datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    
    reminders = await get_due_reminders(now)
    
    for r_id, user_id, text in reminders:
        try:
            await bot.send_message(user_id, f"🔔 **Напоминание:**\n{text}", parse_mode="Markdown")
            await delete_reminder(r_id)
            log_reminder_sent(r_id, user_id)
        except Exception as e:
            log_reminder_error(r_id, e)

async def daily_digest(bot: Bot, admin_id: int):
    if not admin_id:
        logger.error("ADMIN_ID не задан. Некому отправлять дайджест!")
        return

    notes = await get_today_notes()
    if not notes:
        return
        
    tz = pytz.timezone('Europe/Moscow')
    today_str = datetime.datetime.now(tz).strftime("%d.%m.%Y")
    
    text = f"🌙 **Дайджест за {today_str}:**\n\n"
    for filename, category in notes:
        text += f"- [{category}] {filename}\n"
        
    try:
        await bot.send_message(admin_id, text, parse_mode="Markdown")
        logger.info("Ежедневный дайджест успешно отправлен.")
    except Exception as e:
        logger.error(f"Ошибка отправки дайджеста: {e}")

async def auto_sync_job():
    logger.info("Запуск автоматической ночной синхронизации S3...")
    try:
        added, updated = await messages.run_s3_sync()
        logger.info(f"Авто-синхронизация завершена. Добавлено: {added}, Обновлено: {updated}")
    except Exception as e:
        logger.error(f"Ошибка во время авто-синхронизации S3: {e}")