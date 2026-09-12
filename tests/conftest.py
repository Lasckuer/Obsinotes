import os
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from aiogram import Bot
from aiogram.types import User, Chat, Message
from app.database.db import DatabaseManager
from app.config import Settings, reload_settings

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz123456789")
    monkeypatch.setenv("ADMIN_ID", "99999999")
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("TIMEZONE", "Europe/Moscow")
    monkeypatch.setenv("AI_BASE_URL", "https://api.test-ai.local/v1")
    monkeypatch.setenv("AI_API_KEY", "test_key")
    monkeypatch.setenv("AI_MODEL", "test_model")
    monkeypatch.setenv("S3_ENDPOINT", "http://127.0.0.1:8333")
    monkeypatch.setenv("S3_ACCESS_KEY", "test_access")
    monkeypatch.setenv("S3_SECRET_KEY", "test_secret")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    reload_settings()
    yield
    reload_settings()

@pytest_asyncio.fixture
async def test_db():
    db = DatabaseManager(db_path=":memory:")
    await db.init_db()
    yield db
    await db.close()

@pytest.fixture
def mock_bot():
    bot = AsyncMock(spec=Bot)
    bot.send_message = AsyncMock(return_value=MagicMock())
    bot.get_file = AsyncMock(return_value=MagicMock(file_path="photos/test.jpg"))
    bot.download_file = AsyncMock(return_value=MagicMock(read=lambda: b"fake_content"))
    return bot

@pytest.fixture
def mock_user():
    return User(id=12345, is_bot=False, first_name="TestUser", username="testuser")

@pytest.fixture
def mock_chat():
    return Chat(id=12345, type="private", first_name="TestUser", username="testuser")
