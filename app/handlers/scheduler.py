import datetime
from pytz import timezone
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
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
        sent = False
        try:
            safe_text = text if len(text) <= 4000 else f"{text[:3990]}..."
            try:
                await bot.send_message(user_id, f"🔔 **Напоминание:**\n{safe_text}", parse_mode="Markdown")
                sent = True
            except TelegramBadRequest as tb_err:
                tb_msg = str(tb_err).lower()
                if "chat not found" in tb_msg or "user is deactivated" in tb_msg:
                    logger.warning(f"Чат {user_id} не найден или деактивирован. Удаление напоминания {r_id}: {tb_err}")
                    await delete_reminder(r_id)
                    continue

                logger.warning(f"Ошибка форматирования Markdown для напоминания {r_id}: {tb_err}. Отправка без разметки...")
                try:
                    await bot.send_message(user_id, f"🔔 Напоминание:\n{safe_text}", parse_mode=None)
                    sent = True
                except TelegramBadRequest as tb_err2:
                    logger.error(f"Не удалось отправить напоминание {r_id} даже без разметки: {tb_err2}. Удаляем напоминание во избежание бесконечного цикла.")
                    await delete_reminder(r_id)
                    continue
        except TelegramForbiddenError as fe:
            logger.warning(f"Бот заблокирован пользователем {user_id}. Удаление напоминания {r_id}: {fe}")
            await delete_reminder(r_id)
            log_reminder_error(r_id, fe)
            continue
        except Exception as e:
            log_reminder_error(r_id, e)
            continue

        if sent:
            try:
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

    items = [
        f"- [{(category.replace('TelegramBot/', '') if category and category != 'Root' else 'Корень')}] {filename}"
        for filename, category in notes
    ]
    text = f"🌙 **Дайджест за {today_str}:**\n\n" + "\n".join(items)

    try:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown")
            logger.info("Ежедневный дайджест успешно отправлен.")
        except TelegramBadRequest as tb_err:
            logger.warning(f"Ошибка Markdown в дайджесте: {tb_err}. Отправка без разметки...")
            clean_text = text.replace("**", "")
            await bot.send_message(admin_id, clean_text, parse_mode=None)
            logger.info("Ежедневный дайджест отправлен без разметки.")
    except Exception as e:
        logger.error(f"Ошибка отправки дайджеста: {e}")

async def auto_sync_job():
    logger.info("Запуск автоматической ночной синхронизации S3...")
    try:
        added, updated = await messages.run_s3_sync()
        logger.info(f"Авто-синхронизация завершена. Добавлено: {added}, Обновлено: {updated}")
    except Exception as e:
        logger.error(f"Ошибка во время авто-синхронизации S3: {e}")