import asyncio
import os
import datetime
from dotenv import load_dotenv

load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database.db import init_db, get_due_reminders, delete_reminder
from app.services.yandex_disk import YaDiskService
from app.keyboards.reply import get_main_keyboard
from app.handlers import messages, files
from logger import (
    log_db_init, log_yadisk_init, log_scheduler_start,
    log_webhook_drop, log_bot_start, log_bot_stop,
    log_reminder_sent, log_reminder_error, log_user_start
)

proxy_url = os.getenv("PROXY_URL")
session = AiohttpSession(proxy=proxy_url) if proxy_url else None
bot = Bot(token=os.getenv("BOT_TOKEN"), session=session)
dp = Dispatcher()

async def check_reminders():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reminders = await get_due_reminders(now)
    for r_id, user_id, text in reminders:
        try:
            await bot.send_message(user_id, f"🔔 Напоминание:\n\n{text}")
            await delete_reminder(r_id)
            log_reminder_sent(r_id, user_id)
        except Exception as e:
            log_reminder_error(r_id, e)

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "Вы запустили бота, выберите нужную вам категорию",
        reply_markup=get_main_keyboard()
    )
    log_user_start(message.from_user.id)

async def main():
    log_db_init()
    await init_db()
    
    log_yadisk_init()
    ya_disk = YaDiskService()
    await ya_disk.init_folders()
    
    log_scheduler_start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_reminders, 'interval', seconds=30)
    scheduler.start()
    
    dp.include_router(files.router)
    dp.include_router(messages.router)
    
    log_webhook_drop()
    await bot.delete_webhook(drop_pending_updates=True)
    
    
    log_bot_start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log_bot_stop()