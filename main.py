import asyncio
import os
from typing import Optional
from pytz import timezone

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.middlewares.auth import AuthMiddleware
from app.database.db import init_db, close_db
from app.services.s3_storage import S3StorageService
from app.services.llm import close_llm_clients
from app.keyboards.reply import get_main_keyboard
from app.handlers import messages, search
from app.handlers.scheduler import check_reminders, daily_digest, auto_sync_job
from logger import (
    logger, log_db_init, log_s3_init, log_scheduler_start,
    log_bot_start, log_user_start, log_redis_on_info, log_redis_off_info
)

settings = get_settings()

# Setup proxy if configured
if settings.PROXY_URL:
    os.environ["HTTP_PROXY"] = settings.PROXY_URL
    os.environ["HTTPS_PROXY"] = settings.PROXY_URL

os.environ["NO_PROXY"] = "127.0.0.1,localhost,host.docker.internal"

session = AiohttpSession(proxy=settings.PROXY_URL) if settings.PROXY_URL else None
bot_token = settings.BOT_TOKEN or "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz123456789"
bot = Bot(token=bot_token, session=session)

if settings.REDIS_URL:
    storage = RedisStorage.from_url(settings.REDIS_URL)
    log_redis_on_info()
else:
    storage = MemoryStorage()
    log_redis_off_info()

dp = Dispatcher(storage=storage)

# Register security & access control middleware
dp.message.middleware(AuthMiddleware())
dp.callback_query.middleware(AuthMiddleware())

start_router = Router()

@start_router.message(CommandStart())
async def start(message: Message, state: Optional[FSMContext] = None):
    if state is not None:
        await state.clear()
    await message.answer(
        "👋 Бот для Obsidian запущен.\n"
        "Отправьте текст, фото, документ (.pdf, .docx, .txt, .md) или аудиосообщение.\n\n"
        "Быстрые команды:\n"
        "• /daily — ежедневник (задачи и мысли на день)\n"
        "• /expenses — сводка расходов за день и месяц\n"
        "• /tags — каталог заметок по тегам\n"
        "• /sync — синхронизация с S3\n"
        "• /status — статус бота и проверка связи с ИИ\n"
        "• /help — подробная справка",
        reply_markup=get_main_keyboard()
    )
    if message.from_user:
        log_user_start(message.from_user.id)

dp.include_router(start_router)
dp.include_router(search.router)
dp.include_router(messages.router)

async def main():
    log_db_init()
    await init_db()

    log_s3_init()
    storage_service = S3StorageService()
    await storage_service.init_folders()

    # Register bot commands menu in Telegram
    try:
        from aiogram.types import BotCommand
        bot_commands = [
            BotCommand(command="daily", description="Добавить запись в ежедневник"),
            BotCommand(command="expenses", description="Сводка расходов за день и месяц"),
            BotCommand(command="tags", description="Обзор заметок по тегам"),
            BotCommand(command="sync", description="Синхронизация с S3"),
            BotCommand(command="status", description="Статус бота и проверка связи с ИИ"),
            BotCommand(command="cancel", description="Отмена действия"),
            BotCommand(command="help", description="Справка по командам"),
        ]
        await bot.set_my_commands(bot_commands)
    except Exception as e:
        logger.warning(f"Не удалось установить меню команд: {e}")

    log_scheduler_start()
    tz = timezone(settings.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)

    scheduler.add_job(check_reminders, 'interval', seconds=30, args=[bot])
    scheduler.add_job(auto_sync_job, 'cron', hour=3, minute=0)

    if settings.ADMIN_ID:
        scheduler.add_job(daily_digest, 'cron', hour=22, minute=0, args=[bot, settings.ADMIN_ID])
    else:
        logger.warning("⚠️ ADMIN_ID не указан или некорректен в .env! Дайджест отключен.")

    scheduler.start()
    log_bot_start()

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        logger.info("Остановка планировщика и освобождение ресурсов...")
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass

        try:
            await bot.session.close()
        except Exception:
            pass

        try:
            await close_db()
        except Exception:
            pass

        try:
            await close_llm_clients()
        except Exception:
            pass

        logger.info("Все ресурсы освобождены. Бот остановлен.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")