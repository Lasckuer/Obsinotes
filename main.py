import asyncio
import os

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
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone 

from app.database.db import init_db
from app.services.s3_storage import S3StorageService
from app.keyboards.reply import get_main_keyboard
from app.handlers import messages, search 
from app.handlers.scheduler import check_reminders, daily_digest, auto_sync_job
from logger import (
    logger, log_db_init, log_s3_init, log_scheduler_start,
    log_bot_start, log_user_start, log_redis_on_info, log_redis_off_info
)


session = AiohttpSession(proxy=proxy_url) if proxy_url else None
bot = Bot(token=os.getenv("BOT_TOKEN"), session=session)

redis_url = os.getenv("REDIS_URL")
if redis_url:
    storage = RedisStorage.from_url(redis_url)
    log_redis_on_info()
else:
    storage = MemoryStorage()
    log_redis_off_info()

dp = Dispatcher(storage=storage)

dp.include_router(search.router)
dp.include_router(messages.router)

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
    storage_service = S3StorageService()
    await storage_service.init_folders()
    
    log_scheduler_start()
    tz = timezone('Europe/Moscow') 
    scheduler = AsyncIOScheduler(timezone=tz)
    
    admin_id_str = os.getenv("ADMIN_ID")
    admin_id = int(admin_id_str) if admin_id_str and admin_id_str.isdigit() else None
    
    scheduler.add_job(check_reminders, 'interval', seconds=30, args=[bot])
    scheduler.add_job(auto_sync_job, 'cron', hour=3, minute=0)
    
    if admin_id:
        scheduler.add_job(daily_digest, 'cron', hour=22, minute=0, args=[bot, admin_id])
    else:
        logger.warning("⚠️ ADMIN_ID не указан или некорректен в .env! Дайджест отключен.")
        
    scheduler.start()
    log_bot_start()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка планировщика и закрытие сессии бота...")
        scheduler.shutdown()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")