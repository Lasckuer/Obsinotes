from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from app.config import get_settings
from logger import logger

class AuthMiddleware(BaseMiddleware):
    """
    Restricts access to the bot only to the configured administrator (ADMIN_ID)
    or explicitly allowed users (ALLOWED_USERS).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user and hasattr(event, "from_user"):
            user = event.from_user

        if not user:
            # If no user can be identified, do not process the event
            return None

        settings = get_settings()
        is_admin = settings.ADMIN_ID is not None and user.id == settings.ADMIN_ID
        is_allowed = user.id in settings.ALLOWED_USERS

        if not (is_admin or is_allowed):
            username_info = f"@{user.username}" if getattr(user, "username", None) else f"id={user.id}"
            logger.warning(
                f"⛔ Неавторизованный доступ отклонен для пользователя {username_info} (ID: {user.id})"
            )

            try:
                if isinstance(event, Message):
                    await event.answer("⛔ Доступ ограничен. Этот бот является персональным.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("⛔ Доступ ограничен. Этот бот является персональным.", show_alert=True)
            except Exception as e:
                logger.debug(f"Не удалось отправить уведомление о запрете доступа: {e}")
            return None

        return await handler(event, data)
