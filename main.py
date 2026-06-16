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
from aiogram.fsm.storage.redis import RedisStorage
from pytz import timezone 

from app.database.db import init_db
from app.services.s3_storage import S3StorageService
from app.keyboards.reply import get_main_keyboard

from app.handlers import messages, search 
from app.services.scheduler import check_reminders, daily_digest, auto_sync_job

from logger import (
    logger,
    log_db_init, 
    log_s3_init, 
    log_scheduler_start,
    log_bot_start, 
    log_user_start
)

session = AiohttpSession(proxy=proxy_url) if proxy_url else None
bot = Bot(token=os.getenv("BOT_TOKEN"), session=session)

redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
redis_storage = RedisStorage.from_url(redis_url)

dp = Dispatcher(storage=redis_storage)

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
    storage = S3StorageService()
    await storage.init_folders()
    
    log_scheduler_start()
    
    tz = timezone('Europe/Moscow') 
    scheduler = AsyncIOScheduler(timezone=tz)
    
    admin_id_str = os.getenv("ADMIN_ID")
    admin_id = int(admin_id_str) if admin_id_str else None

    scheduler.add_job(check_reminders, 'interval', seconds=30, args=[bot])
    
    if admin_id:
        scheduler.add_job(daily_digest, 'cron', hour=22, minute=0, args=[bot, admin_id])
    else:
        logger.warning("⚠️ ADMIN_ID не указан в .env! Ежедневный дайджест отправляться не будет.")
        
    scheduler.add_job(auto_sync_job, 'cron', hour=3, minute=0)
    scheduler.start()

    log_bot_start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())