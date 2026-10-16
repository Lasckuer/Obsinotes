import pytest
from unittest.mock import AsyncMock, MagicMock
from aiogram.types import Message, CallbackQuery, User, Chat
from app.config import Settings, reload_settings, get_settings
from app.middlewares.auth import AuthMiddleware

@pytest.fixture
def auth_middleware():
    return AuthMiddleware()

@pytest.mark.asyncio
async def test_auth_middleware_admin_allowed(auth_middleware, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "111111")
    monkeypatch.setenv("ALLOWED_USERS", "")
    reload_settings()

    user = User(id=111111, is_bot=False, first_name="Admin", username="admin")
    msg = AsyncMock(spec=Message)
    msg.from_user = user
    msg.answer = AsyncMock()

    handler = AsyncMock(return_value="handler_called")
    result = await auth_middleware(handler, msg, {"event_from_user": user})

    assert result == "handler_called"
    handler.assert_called_once()
    msg.answer.assert_not_called()

@pytest.mark.asyncio
async def test_auth_middleware_allowed_user_allowed(auth_middleware, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "111111")
    monkeypatch.setenv("ALLOWED_USERS", "222222, 333333")
    reload_settings()

    user = User(id=222222, is_bot=False, first_name="Allowed", username="allowed")
    msg = AsyncMock(spec=Message)
    msg.from_user = user
    msg.answer = AsyncMock()

    handler = AsyncMock(return_value="handler_called")
    result = await auth_middleware(handler, msg, {"event_from_user": user})

    assert result == "handler_called"
    handler.assert_called_once()
    msg.answer.assert_not_called()

@pytest.mark.asyncio
async def test_auth_middleware_unauthorized_message_rejected(auth_middleware, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "111111")
    monkeypatch.setenv("ALLOWED_USERS", "222222")
    reload_settings()

    intruder = User(id=999999, is_bot=False, first_name="Intruder", username="intruder")
    msg = AsyncMock(spec=Message)
    msg.from_user = intruder
    msg.answer = AsyncMock()

    handler = AsyncMock()
    result = await auth_middleware(handler, msg, {"event_from_user": intruder})

    assert result is None
    handler.assert_not_called()
    msg.answer.assert_called_once()
    assert "⛔ Доступ ограничен" in msg.answer.call_args[0][0]

@pytest.mark.asyncio
async def test_auth_middleware_unauthorized_callback_rejected(auth_middleware, monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "111111")
    monkeypatch.setenv("ALLOWED_USERS", "222222")
    reload_settings()

    intruder = User(id=999999, is_bot=False, first_name="Intruder", username="intruder")
    cb = AsyncMock(spec=CallbackQuery)
    cb.from_user = intruder
    cb.answer = AsyncMock()

    handler = AsyncMock()
    result = await auth_middleware(handler, cb, {"event_from_user": intruder})

    assert result is None
    handler.assert_not_called()
    cb.answer.assert_called_once()
    assert "⛔ Доступ ограничен" in cb.answer.call_args[0][0]
    assert cb.answer.call_args[1].get("show_alert") is True

@pytest.mark.asyncio
async def test_auth_middleware_no_user(auth_middleware):
    event = MagicMock(spec=[])  # Object with no from_user
    handler = AsyncMock()
    result = await auth_middleware(handler, event, {})
    assert result is None
    handler.assert_not_called()

def test_settings_allowed_users_parsing(monkeypatch):
    monkeypatch.setenv("ALLOWED_USERS", "100, 200, invalid, 300, -400")
    reload_settings()
    settings = get_settings()
    assert settings.ALLOWED_USERS == {100, 200, 300, -400}

    # Empty env fallback
    monkeypatch.delenv("ALLOWED_USERS", raising=False)
    reload_settings()
    settings2 = get_settings()
    assert settings2.ALLOWED_USERS == set()

    # Direct int parsing and whitespace ADMIN_ID
    monkeypatch.setenv("ADMIN_ID", " 987654 ")
    monkeypatch.setenv("ALLOWED_USERS", " 55555 ")
    reload_settings()
    settings3 = get_settings()
    assert settings3.ADMIN_ID == 987654
    assert settings3.ALLOWED_USERS == {55555}

    # Direct Settings instantiation with int
    settings_direct = Settings(ALLOWED_USERS=12345, ADMIN_ID="  777 ")
    assert settings_direct.ALLOWED_USERS == {12345}
    assert settings_direct.ADMIN_ID == 777

@pytest.mark.asyncio
async def test_auth_middleware_handles_answer_exception(auth_middleware, monkeypatch):
    from aiogram.exceptions import TelegramForbiddenError
    monkeypatch.setenv("ADMIN_ID", "111111")
    monkeypatch.setenv("ALLOWED_USERS", "")
    reload_settings()

    intruder = User(id=999999, is_bot=False, first_name="Intruder")
    msg = AsyncMock(spec=Message)
    msg.from_user = intruder
    msg.answer = AsyncMock(side_effect=TelegramForbiddenError(method=None, message="Forbidden: bot was blocked by the user"))

    handler = AsyncMock()
    # Should not raise exception
    result = await auth_middleware(handler, msg, {"event_from_user": intruder})
    assert result is None
    handler.assert_not_called()

@pytest.mark.asyncio
async def test_dispatcher_auth_middleware_blocks_unauthorized(monkeypatch):
    from aiogram import Dispatcher
    from aiogram.types import Update
    from datetime import datetime

    monkeypatch.setenv("ADMIN_ID", "111111")
    monkeypatch.setenv("ALLOWED_USERS", "")
    reload_settings()

    dp = Dispatcher()
    dp.message.middleware(AuthMiddleware())
    
    handled = False
    @dp.message()
    async def dummy_handler(message: Message):
        nonlocal handled
        handled = True

    bot = AsyncMock()
    user = User(id=888888, is_bot=False, first_name="Stranger")
    chat = Chat(id=888888, type="private")
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=chat,
        from_user=user,
        text="Hello"
    ).as_(bot)
    update = Update(update_id=1, message=message)

    await dp.feed_update(bot, update)
    assert not handled
    assert bot.call_count == 1
    sent_method = bot.call_args[0][0]
    assert "⛔ Доступ ограничен" in sent_method.text

