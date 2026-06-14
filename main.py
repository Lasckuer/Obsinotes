import asyncio
import os
import datetime
from dotenv import load_dotenv

load_dotenv()

proxy_url = os.getenv("PROXY_URL")
if proxy_url:
    os.environ["HTTP_PROXY"] = proxy_url
    os.environ["HTTPS_PROXY"] = proxy_url

os.environ["NO_PROXY"] = "127.0.0.1,localhost,host.docker.internal"

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database.db import init_db, get_due_reminders, delete_reminder, get_today_notes
from app.services.s3_storage import S3StorageService
from app.keyboards.reply import get_main_keyboard

from app.handlers import messages, files, search 

from logger import (
    logger,
    log_db_init, 
    log_s3_init, 
    log_scheduler_start,
    log_bot_start, 
    log_reminder_sent, 
    log_reminder_error, 
    log_user_start
)

session = AiohttpSession(proxy=proxy_url) if proxy_url else None
bot = Bot(token=os.getenv("BOT_TOKEN"), session=session)
dp = Dispatcher()

dp.include_router(search.router)
dp.include_router(files.router)
dp.include_router(messages.router)

async def check_reminders():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    reminders = await get_due_reminders(now)
    
    for r_id, user_id, text in reminders:
        try:
            await bot.send_message(user_id, f"🔔 Напоминание:\n\n{text}")
            await delete_reminder(r_id)
            log_reminder_sent(r_id, user_id)
        except Exception as e:
            log_reminder_error(r_id, e)

async def daily_digest():
    notes = await get_today_notes()
    if not notes:
        return
        
    text = "🌙 Дайджест за сегодня:\n\n"
    for filename, category in notes:
        text += f"- [{category}] {filename}\n"
        
    logger.info(f"Сформирован ежедневный дайджест:\n{text}")
    
async def auto_sync_job():
    logger.info("Запуск автоматической ночной синхронизации S3...")
    try:
        added, updated = await messages.run_s3_sync()
        logger.info(f"Авто-синхронизация завершена. Добавлено: {added}, Обновлено: {updated}")
    except Exception as e:
        logger.error(f"Ошибка во время авто-синхронизации S3: {e}")

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Бот для Obsidian запущен.", 
        reply_markup=get_main_keyboard()
    )
    log_user_start(message.from_user.id)

async def main():
    log_db_init()
    await init_db()
    
    log_s3_init()
    storage = S3StorageService()
    await storage.init_folders()
    
    log_scheduler_start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, 'interval', seconds=30)
    scheduler.add_job(daily_digest, 'cron', hour=22, minute=0)
    scheduler.add_job(auto_sync_job, 'cron', hour=3, minute=0)
    scheduler.start()
    
    log_bot_start()
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")