import os
from pathlib import Path
from typing import Optional, Any, Set, Dict
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

PROVIDER_DEFAULTS: Dict[str, Dict[str, str]] = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-2.5-flash",
        "display_name": "Google Gemini",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "display_name": "OpenAI",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "display_name": "Groq",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "display_name": "DeepSeek",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "google/gemini-2.5-flash",
        "display_name": "OpenRouter",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "model": "gemma4:e2b",
        "display_name": "Ollama / Local",
    },
    "custom": {
        "base_url": "https://api-ai.lkserv.ru/v1",
        "model": "gemma4:e2b",
        "display_name": "Custom LLM Gateway",
    }
}

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

    # Pluggable LLM Provider & Keys
    AI_PROVIDER: str = Field(default_factory=lambda: os.getenv("AI_PROVIDER", "auto").strip().lower())
    GEMINI_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY") or None)
    OPENAI_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    DEEPSEEK_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY") or None)
    OPENROUTER_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY") or None)
    GROQ_API_KEY: Optional[str] = Field(default_factory=lambda: os.getenv("GROQ_API_KEY") or None)

    # Resolved LLM Settings
    AI_BASE_URL: str = Field(default_factory=lambda: os.getenv("AI_BASE_URL", ""))
    AI_API_KEY: str = Field(default_factory=lambda: os.getenv("AI_API_KEY", ""))
    AI_MODEL: str = Field(default_factory=lambda: os.getenv("AI_MODEL", ""))
    def model_post_init(self, __context: Any) -> None:
        LEGACY_GATEWAY_URL = "https://api-ai.lkserv.ru/v1"
        LEGACY_MODEL = "gemma4:e2b"
        LEGACY_KEY = "ollama"

        # 1. Resolve active AI provider
        provider = self.AI_PROVIDER
        if not provider or provider == "auto":
            if self.GEMINI_API_KEY:
                provider = "gemini"
            elif self.OPENAI_API_KEY:
                provider = "openai"
            elif self.DEEPSEEK_API_KEY:
                provider = "deepseek"
            elif self.OPENROUTER_API_KEY:
                provider = "openrouter"
            elif self.GROQ_API_KEY and not (os.getenv("AI_BASE_URL") and os.getenv("AI_BASE_URL") != LEGACY_GATEWAY_URL):
                provider = "groq"
            elif os.getenv("AI_BASE_URL") or os.getenv("AI_API_KEY"):
                provider = "custom"
            else:
                provider = "custom"
        self.AI_PROVIDER = provider

        preset = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["custom"])

        # 2. Resolve AI_BASE_URL
        if not self.AI_BASE_URL or (provider != "custom" and self.AI_BASE_URL == LEGACY_GATEWAY_URL):
            self.AI_BASE_URL = preset["base_url"]

        # 3. Resolve AI_MODEL
        if not self.AI_MODEL or (provider != "custom" and self.AI_MODEL == LEGACY_MODEL and provider != "ollama"):
            self.AI_MODEL = preset["model"]

        # 4. Resolve AI_API_KEY
        if provider == "gemini":
            self.AI_API_KEY = self.GEMINI_API_KEY or (self.AI_API_KEY if self.AI_API_KEY and self.AI_API_KEY != LEGACY_KEY else "")
        elif provider == "openai":
            self.AI_API_KEY = self.OPENAI_API_KEY or (self.AI_API_KEY if self.AI_API_KEY and self.AI_API_KEY != LEGACY_KEY else "")
        elif provider == "deepseek":
            self.AI_API_KEY = self.DEEPSEEK_API_KEY or (self.AI_API_KEY if self.AI_API_KEY and self.AI_API_KEY != LEGACY_KEY else "")
        elif provider == "openrouter":
            self.AI_API_KEY = self.OPENROUTER_API_KEY or (self.AI_API_KEY if self.AI_API_KEY and self.AI_API_KEY != LEGACY_KEY else "")
        elif provider == "groq":
            self.AI_API_KEY = self.GROQ_API_KEY or (self.AI_API_KEY if self.AI_API_KEY and self.AI_API_KEY != LEGACY_KEY else "")
        else:
            self.AI_API_KEY = self.AI_API_KEY or "ollama"

    @property
    def active_ai_provider_display(self) -> str:
        preset = PROVIDER_DEFAULTS.get(self.AI_PROVIDER)
        if preset:
            return preset["display_name"]
        return self.AI_PROVIDER.capitalize()

    # Audio Transcription (Groq Whisper)
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
