import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file if present
load_dotenv()

class Settings(BaseModel):
    # Telegram Bot
    BOT_TOKEN: str = Field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    ADMIN_ID: Optional[int] = Field(
        default_factory=lambda: int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") and os.getenv("ADMIN_ID").isdigit() else None
    )
    PROXY_URL: Optional[str] = Field(default_factory=lambda: os.getenv("PROXY_URL") or None)
    REDIS_URL: Optional[str] = Field(default_factory=lambda: os.getenv("REDIS_URL") or None)

    # S3 Storage (SeaweedFS / MinIO / AWS S3)
    S3_ENDPOINT: Optional[str] = Field(default_factory=lambda: os.getenv("S3_ENDPOINT") or None)
    S3_ACCESS_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("S3_ACCESS_KEY") or None)
    S3_SECRET_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("S3_SECRET_KEY") or None)
    S3_BUCKET_NAME: str = Field(default_factory=lambda: os.getenv("S3_BUCKET_NAME", "obsidian"))

    # LLM Settings
    AI_BASE_URL: str = Field(default_factory=lambda: os.getenv("AI_BASE_URL", "https://api-ai.lkserv.ru/v1"))
    AI_API_KEY: str = Field(default_factory=lambda: os.getenv("AI_API_KEY", "ollama"))
    AI_MODEL: str = Field(default_factory=lambda: os.getenv("AI_MODEL", "gemma4:e2b"))

    # Audio Transcription (Groq Whisper)
    GROQ_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("GROQ_API_KEY") or None)
    GROQ_BASE_URL: str = Field(default_factory=lambda: os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"))
    GROQ_MODEL: str = Field(default_factory=lambda: os.getenv("GROQ_MODEL", "whisper-large-v3"))

    # Database & Runtime
    DB_PATH: str = Field(default_factory=lambda: os.getenv("DB_PATH", "database.db"))
    TIMEZONE: str = Field(default_factory=lambda: os.getenv("TIMEZONE", "Europe/Moscow"))
    LOG_LEVEL: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    LOG_FILE: str = Field(default_factory=lambda: os.getenv("LOG_FILE", "bot_log.log"))
    
    # Concurrency
    S3_SYNC_CONCURRENCY: int = Field(
        default_factory=lambda: int(os.getenv("S3_SYNC_CONCURRENCY", "5"))
    )

    @property
    def has_s3(self) -> bool:
        return bool(self.S3_ENDPOINT and self.S3_ACCESS_KEY and self.S3_SECRET_KEY)

    @property
    def has_groq(self) -> bool:
        return bool(self.GROQ_API_KEY)


_settings_instance: Optional[Settings] = None

def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance

def reload_settings() -> Settings:
    """Useful in tests to force reload from modified environment variables."""
    global _settings_instance
    _settings_instance = Settings()
    return _settings_instance
