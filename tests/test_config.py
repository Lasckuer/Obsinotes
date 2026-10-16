import os
import pytest
from app.config import Settings, get_settings, reload_settings

def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("ADMIN_ID", raising=False)
    monkeypatch.delenv("PROXY_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    reload_settings()

    settings = get_settings()
    assert settings.DB_PATH == ":memory:"
    assert settings.S3_BUCKET_NAME == "test-bucket"
    assert settings.ADMIN_ID is None
    assert settings.PROXY_URL is None
    assert settings.has_groq is False
    assert settings.has_s3 is True
    assert settings.OBSIDIAN_VAULT_NAME == "Notes"

def test_settings_with_env(monkeypatch):
    monkeypatch.setenv("ADMIN_ID", "1234567")
    monkeypatch.setenv("PROXY_URL", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_123")
    monkeypatch.setenv("OBSIDIAN_VAULT_NAME", "WorkVault")
    reload_settings()

    settings = get_settings()
    assert settings.ADMIN_ID == 1234567
    assert settings.PROXY_URL == "socks5://127.0.0.1:1080"
    assert settings.has_groq is True
    assert settings.GROQ_API_KEY == "gsk_test_123"
    assert settings.OBSIDIAN_VAULT_NAME == "WorkVault"
