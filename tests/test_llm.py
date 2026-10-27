import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.llm import (
    parse_json_safely,
    process_text,
    get_rephrased_filename,
    generate_semantic_filename,
    stream_answer_question,
    transcribe_audio,
    close_llm_clients
)

def test_parse_json_safely():
    # Standard JSON
    res = parse_json_safely('{"category": "Notes", "tags": ["test"]}')
    assert res["category"] == "Notes"
    assert res["tags"] == ["test"]

    # Fenced markdown JSON
    fenced = '```json\n{"category": "Ideas", "filename": "Идея.md"}\n```'
    res = parse_json_safely(fenced)
    assert res["category"] == "Ideas"
    assert res["filename"] == "Идея.md"

    # JSON with surrounding prose
    surrounded = 'Sure, here is the data:\n{"category": "Finance", "expense_amount": 500}\nHope this helps!'
    res = parse_json_safely(surrounded)
    assert res["category"] == "Finance"
    assert res["expense_amount"] == 500

    # Completely invalid
    assert parse_json_safely("not a json at all") == {}

@pytest.mark.asyncio
async def test_process_text():
    mock_choice = MagicMock()
    mock_choice.message.content = '{"category": "Notes", "filename": "Покупка.md", "tags": ["покупки"]}'
    mock_resp = MagicMock(choices=[mock_choice])

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        data = await process_text("Купить билеты")
        assert data.get("category") == "Notes"
        assert data.get("filename") == "Покупка.md"

        # Verify max_tokens is 2048
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs.get("max_tokens") == 2048

        # Verify heavy thought_process requirement is removed from prompt
        prompt_content = call_kwargs["messages"][0]["content"]
        assert "thought_process" not in prompt_content

@pytest.mark.asyncio
async def test_transcribe_audio_mime_detection(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "fake_groq_key")
    from app.config import reload_settings
    reload_settings()

    mock_resp = MagicMock()
    mock_resp.text = "Распознанный голос"

    mock_audio_client = AsyncMock()
    mock_audio_client.audio.transcriptions.create = AsyncMock(return_value=mock_resp)

    test_cases = [
        ("voice.ogg", "audio/ogg", "voice.ogg"),
        ("music.mp3", "audio/mpeg", "music.mp3"),
        ("speech.m4a", "audio/mp4", "speech.m4a"),
        ("sound.wav", "audio/wav", "sound.wav"),
        ("podcast.opus", "audio/ogg", "podcast.opus"),
        ("track.aac", "audio/aac", "track.aac"),
        ("raw_audio", "audio/ogg", "raw_audio.ogg"),
    ]

    with patch("app.services.llm.get_audio_client", return_value=mock_audio_client):
        for fname, expected_mime, expected_upload_name in test_cases:
            file_path = tmp_path / fname
            file_path.write_bytes(b"dummy audio data")

            res = await transcribe_audio(str(file_path))
            assert res == "Распознанный голос"

            call_kwargs = mock_audio_client.audio.transcriptions.create.call_args[1]
            file_tuple = call_kwargs["file"]
            assert file_tuple[0] == expected_upload_name
            assert file_tuple[2] == expected_mime


@pytest.mark.asyncio
async def test_get_rephrased_filename_unique():
    # If file doesn't exist, returns as-is
    with patch("app.services.llm.note_exists", AsyncMock(return_value=False)):
        res = await get_rephrased_filename("Notes", "Заметка.md", "Текст заметки")
        assert res == "Заметка.md"

@pytest.mark.asyncio
async def test_get_rephrased_filename_with_collision():
    # First check exists -> True, second check exists -> False
    side_effects = [True, False]
    with patch("app.services.llm.note_exists", AsyncMock(side_effect=side_effects)):
        mock_choice = MagicMock()
        mock_choice.message.content = "Новое название заметки.md"
        mock_resp = MagicMock(choices=[mock_choice])
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        with patch("app.services.llm.get_llm_client", return_value=mock_client):
            res = await get_rephrased_filename("Notes", "Заметка.md", "Текст заметки")
            assert res == "Новое название заметки.md"

@pytest.mark.asyncio
async def test_get_rephrased_filename_max_attempts():
    # File exists continuously; should fall back to deterministic suffix without infinite loop
    with patch("app.services.llm.note_exists", AsyncMock(return_value=True)):
        mock_choice = MagicMock()
        mock_choice.message.content = "Дубликат.md"
        mock_resp = MagicMock(choices=[mock_choice])
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

        with patch("app.services.llm.get_llm_client", return_value=mock_client):
            res = await get_rephrased_filename("Notes", "Дубликат.md", "Текст", max_attempts=2)
            assert res.startswith("Дубликат ")
            assert res.endswith(".md")

@pytest.mark.asyncio
async def test_generate_semantic_filename():
    mock_choice = MagicMock()
    mock_choice.message.content = '"Работа с сервером.md"'
    mock_resp = MagicMock(choices=[mock_choice])
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        name = await generate_semantic_filename("Настройка сервера...")
        assert name == "Работа с сервером.md"

@pytest.mark.asyncio
async def test_stream_answer_question():
    async def fake_stream():
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="Ответ "))])
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="ИИ."))])

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        chunks = []
        async for c in stream_answer_question("Вопрос?", "Контекст"):
            chunks.append(c)
        assert "".join(chunks) == "Ответ ИИ."

@pytest.mark.asyncio
async def test_close_llm_clients():
    await close_llm_clients()

def test_ai_provider_resolution(monkeypatch):
    from app.config import reload_settings

    # 1. Gemini auto-detection from GEMINI_API_KEY
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyFakeGeminiKey")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    s = reload_settings()
    assert s.AI_PROVIDER == "gemini"
    assert "generativelanguage.googleapis.com" in s.AI_BASE_URL
    assert s.AI_MODEL == "gemini-2.5-flash"
    assert s.AI_API_KEY == "AIzaSyFakeGeminiKey"
    assert s.active_ai_provider_display == "Google Gemini"

    # 2. OpenAI explicit provider
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-openai")
    s = reload_settings()
    assert s.AI_PROVIDER == "openai"
    assert s.AI_BASE_URL == "https://api.openai.com/v1"
    assert s.AI_MODEL == "gpt-4o-mini"
    assert s.AI_API_KEY == "sk-fake-openai"
    assert s.active_ai_provider_display == "OpenAI"

    # 3. Groq explicit provider
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_fake_groq")
    s = reload_settings()
    assert s.AI_PROVIDER == "groq"
    assert s.AI_BASE_URL == "https://api.groq.com/openai/v1"
    assert s.AI_MODEL == "llama-3.3-70b-versatile"
    assert s.AI_API_KEY == "gsk_fake_groq"

    # 4. Ollama explicit provider with custom model override
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.setenv("AI_MODEL", "qwen2.5:7b")
    s = reload_settings()
    assert s.AI_PROVIDER == "ollama"
    assert s.AI_BASE_URL == "http://localhost:11434/v1"
    assert s.AI_MODEL == "qwen2.5:7b"
    assert s.AI_API_KEY == "ollama"

@pytest.mark.asyncio
async def test_test_ai_connection_success():
    from app.services.llm import test_ai_connection
    mock_resp = MagicMock()
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        success, msg, latency = await test_ai_connection()
        assert success is True
        assert "успешно" in msg.lower()
        assert latency >= 0.0

@pytest.mark.asyncio
async def test_test_ai_connection_auth_error():
    from app.services.llm import test_ai_connection
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("401 Unauthorized: Invalid API Key"))

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        success, msg, latency = await test_ai_connection()
        assert success is False
        assert "Ошибка авторизации" in msg

@pytest.mark.asyncio
async def test_get_llm_client_uses_proxy(monkeypatch):
    from app.config import reload_settings
    import app.services.llm as llm_mod

    await llm_mod.close_llm_clients()
    monkeypatch.setenv("PROXY_URL", "http://user:pass@proxy.example.com:8080")
    reload_settings()

    client = llm_mod.get_llm_client()
    assert client is not None
    assert llm_mod._internal_http_client is not None
    # Clean up
    await llm_mod.close_llm_clients()
    monkeypatch.delenv("PROXY_URL", raising=False)
    reload_settings()

def test_provider_presets_override_legacy_env_defaults(monkeypatch):
    from app.config import reload_settings

    # Simulate an existing .env that contained the old default gateway settings
    monkeypatch.setenv("AI_BASE_URL", "https://api-ai.lkserv.ru/v1")
    monkeypatch.setenv("AI_API_KEY", "ollama")
    monkeypatch.setenv("AI_MODEL", "gemma4:e2b")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyGeminiOverrideKey")

    s = reload_settings()
    assert s.AI_PROVIDER == "gemini"
    assert "generativelanguage.googleapis.com" in s.AI_BASE_URL
    assert s.AI_MODEL == "gemini-2.5-flash"
    assert s.AI_API_KEY == "AIzaSyGeminiOverrideKey"

    # Clean up
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_BASE_URL", raising=False)
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    reload_settings()

@pytest.mark.asyncio
async def test_test_ai_connection_gemini_auth_error():
    from app.services.llm import test_ai_connection
    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(
        side_effect=Exception("Error code: 400 - API key not valid. Please pass a valid API key.")
    )

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        success, msg, latency = await test_ai_connection()
        assert success is False
        assert "Ошибка авторизации" in msg

def test_parse_json_safely_embedded_in_fences():
    from app.services.llm import parse_json_safely

    text = (
        "Here is the processed note according to your schema:\n"
        "```json\n"
        "{\n"
        '  "category": "Notes",\n'
        '  "filename": "План на день.md",\n'
        '  "tags": ["daily", "plans"]\n'
        "}\n"
        "```\n"
        "Please let me know if you need more changes."
    )
    result = parse_json_safely(text)
    assert result.get("category") == "Notes"
    assert result.get("filename") == "План на день.md"
    assert result.get("tags") == ["daily", "plans"]


def test_custom_ai_model_env_override(monkeypatch):
    """Verifies that user-specified AI_MODEL and provider-specific model variables override defaults."""
    from app.config import reload_settings

    # 1. Custom AI_MODEL for Google Gemini
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyCustomKey")
    monkeypatch.setenv("AI_MODEL", "gemini-1.5-pro")
    s = reload_settings()
    assert s.AI_PROVIDER == "gemini"
    assert s.AI_MODEL == "gemini-1.5-pro"

    # 2. Specific GEMINI_MODEL override
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.0-flash")
    s = reload_settings()
    assert s.AI_MODEL == "gemini-2.0-flash"
    monkeypatch.delenv("GEMINI_MODEL", raising=False)

    # 3. Custom AI_MODEL for OpenAI
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-custom-openai")
    monkeypatch.setenv("AI_MODEL", "gpt-4o")
    s = reload_settings()
    assert s.AI_PROVIDER == "openai"
    assert s.AI_MODEL == "gpt-4o"

    # 4. Specific OPENAI_MODEL override
    monkeypatch.setenv("OPENAI_MODEL", "o3-mini")
    s = reload_settings()
    assert s.AI_MODEL == "o3-mini"
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    # 5. Custom AI_MODEL for DeepSeek
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek")
    monkeypatch.setenv("AI_MODEL", "deepseek-reasoner")
    s = reload_settings()
    assert s.AI_PROVIDER == "deepseek"
    assert s.AI_MODEL == "deepseek-reasoner"

    # Cleanup
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AI_MODEL", raising=False)
    reload_settings()


@pytest.mark.asyncio
async def test_test_ai_connection_timeout(monkeypatch):
    import asyncio
    from app.services.llm import test_ai_connection
    from app.config import reload_settings

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTestKey")
    reload_settings()

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=asyncio.TimeoutError())

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        success, msg, latency = await test_ai_connection()
        assert success is False
        assert "Таймаут подключения" in msg
        assert "gemini-2.5-flash" in msg

    # Cleanup
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    reload_settings()


@pytest.mark.asyncio
async def test_test_ai_connection_location_error():
    from app.services.llm import test_ai_connection

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("User location is not supported for the API use."))

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        success, msg, latency = await test_ai_connection()
        assert success is False
        assert "Географическое ограничение" in msg
        assert "User location not supported" in msg


@pytest.mark.asyncio
async def test_test_ai_connection_model_not_found():
    from app.services.llm import test_ai_connection

    mock_client = AsyncMock()
    mock_client.chat.completions.create = AsyncMock(side_effect=Exception("404 models/gemini-3.5-flash-lite is not found for API version v1beta"))

    with patch("app.services.llm.get_llm_client", return_value=mock_client):
        success, msg, latency = await test_ai_connection()
        assert success is False
        assert "не найдена" in msg
        assert "gemini-2.5-flash" in msg


@pytest.mark.asyncio
async def test_get_llm_client_invalidates_on_proxy_change(monkeypatch):
    from app.config import reload_settings
    import app.services.llm as llm_mod

    await llm_mod.close_llm_clients()
    monkeypatch.setenv("PROXY_URL", "http://proxy1:8080")
    reload_settings()
    c1 = llm_mod.get_llm_client()
    assert llm_mod._cached_proxy == "http://proxy1:8080"

    # Change proxy
    monkeypatch.setenv("PROXY_URL", "http://proxy2:9090")
    reload_settings()
    c2 = llm_mod.get_llm_client()
    assert llm_mod._cached_proxy == "http://proxy2:9090"
    assert c2 is not c1

    # Cleanup
    await llm_mod.close_llm_clients()
    monkeypatch.delenv("PROXY_URL", raising=False)
    reload_settings()




