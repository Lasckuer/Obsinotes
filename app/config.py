import os
from pathlib import Path
from typing import Optional, Any, Set
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

# Load .env file if present
load_dotenv()

def _parse_allowed_users(raw_val: Any) -> Set[int]:
    if raw_val is None:
        return set()
    if isinstance(raw_val, int):
        return {raw_val}
    if isinstance(raw_val, (set, list, tuple)):
        result = set()
        for x in raw_val:
            try:
                result.add(int(x))
            except (ValueError, TypeError):
                pass
        return result
    if isinstance(raw_val, str):
        result = set()
        for item in raw_val.split(","):
            item = item.strip()
            if not item:
                continue
            try:
                result.add(int(item))
            except (ValueError, TypeError):
                pass
        return result
    return set()

def _parse_admin_id(raw_val: Any) -> Optional[int]:
    if raw_val is None or raw_val == "":
        return None
    if isinstance(raw_val, int):
        return raw_val
    if isinstance(raw_val, str):
        v = raw_val.strip()
        if v.isdigit():
            return int(v)
    return None

class Settings(BaseModel):
    # Telegram Bot
    BOT_TOKEN: str = Field(default_factory=lambda: os.getenv("BOT_TOKEN", ""))
    ADMIN_ID: Optional[int] = Field(
        default_factory=lambda: _parse_admin_id(os.getenv("ADMIN_ID"))
    )
    ALLOWED_USERS: Set[int] = Field(
        default_factory=lambda: _parse_allowed_users(os.getenv("ALLOWED_USERS"))
    )
    PROXY_URL: Optional[str] = Field(default_factory=lambda: os.getenv("PROXY_URL") or None)
    REDIS_URL: Optional[str] = Field(default_factory=lambda: os.getenv("REDIS_URL") or None)

    @field_validator("ADMIN_ID", mode="before")
    @classmethod
    def validate_admin_id(cls, v: Any) -> Optional[int]:
        return _parse_admin_id(v)

    @field_validator("ALLOWED_USERS", mode="before")
    @classmethod
    def validate_allowed_users(cls, v: Any) -> Set[int]:
        return _parse_allowed_users(v)

    # S3 Storage (SeaweedFS / MinIO / AWS S3)
    S3_ENDPOINT: Optional[str] = Field(default_factory=lambda: os.getenv("S3_ENDPOINT") or None)
    S3_ACCESS_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("S3_ACCESS_KEY") or None)
    S3_SECRET_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("S3_SECRET_KEY") or None)
    S3_BUCKET_NAME: str = Field(default_factory=lambda: os.getenv("S3_BUCKET_NAME", "obsidian"))
    OBSIDIAN_VAULT_NAME: str = Field(default_factory=lambda: os.getenv("OBSIDIAN_VAULT_NAME", "Notes"))

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
