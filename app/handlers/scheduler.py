import datetime
from pytz import timezone
from aiogram import Bot
from app.config import get_settings
from app.database.db import get_due_reminders, delete_reminder, get_today_notes, update_reminder_time
from app.handlers import messages
from logger import logger, log_reminder_sent, log_reminder_error, log_reminder_cycled

def get_current_timezone():
    settings = get_settings()
    try:
        return timezone(settings.TIMEZONE)
    except Exception:
        return timezone('Europe/Moscow')

async def check_reminders(bot: Bot):
    tz = get_current_timezone()
    now_dt = datetime.datetime.now(tz)
    now_str = now_dt.strftime("%Y-%m-%d %H:%M")

    try:
        reminders = await get_due_reminders(now_str)
    except Exception as e:
        logger.error(f"Ошибка получения списка напоминаний: {e}")
        return

    for r_id, user_id, text, recur_minutes in reminders:
        try:
            await bot.send_message(user_id, f"🔔 **Напоминание:**\n{text}", parse_mode="Markdown")

            if recur_minutes and int(recur_minutes) > 0:
                next_dt = now_dt + datetime.timedelta(minutes=int(recur_minutes))
                next_time_str = next_dt.strftime("%Y-%m-%d %H:%M")
                await update_reminder_time(r_id, next_time_str)
                log_reminder_cycled(r_id, next_time_str)
            else:
                await delete_reminder(r_id)

            log_reminder_sent(r_id, user_id)
        except Exception as e:
            log_reminder_error(r_id, e)

async def daily_digest(bot: Bot, admin_id: int):
    if not admin_id:
        logger.warning("ADMIN_ID не задан. Дайджест пропущен.")
        return

    try:
        notes = await get_today_notes()
    except Exception as e:
        logger.error(f"Ошибка получения заметок для дайджеста: {e}")
        return

    if not notes:
        return

    tz = get_current_timezone()
    today_str = datetime.datetime.now(tz).strftime("%d.%m.%Y")

    items = [f"- [{category.replace('TelegramBot/', '')}] {filename}" for filename, category in notes]
    text = f"🌙 **Дайджест за {today_str}:**\n\n" + "\n".join(items)

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