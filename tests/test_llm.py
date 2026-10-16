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
