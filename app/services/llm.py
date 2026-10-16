import uuid
import json
import os
import re
import datetime
import asyncio
import mimetypes
from typing import Optional, Dict, Any, AsyncGenerator

import httpx
from openai import AsyncOpenAI

from app.config import get_settings
from app.database.db import note_exists
from logger import log_llm_error, logger

_internal_http_client: Optional[httpx.AsyncClient] = None
_external_http_client: Optional[httpx.AsyncClient] = None
_client: Optional[AsyncOpenAI] = None
_audio_client: Optional[AsyncOpenAI] = None

def get_llm_client() -> AsyncOpenAI:
    global _client, _internal_http_client
    if _client is None:
        settings = get_settings()
        _internal_http_client = httpx.AsyncClient(timeout=60.0)
        _client = AsyncOpenAI(
            api_key=settings.AI_API_KEY,
            base_url=settings.AI_BASE_URL,
            http_client=_internal_http_client
        )
    return _client

def get_audio_client() -> Optional[AsyncOpenAI]:
    global _audio_client, _external_http_client
    if _audio_client is None:
        settings = get_settings()
        if not settings.GROQ_API_KEY:
            return None
        proxy = settings.PROXY_URL if settings.PROXY_URL else None
        _external_http_client = httpx.AsyncClient(proxy=proxy, timeout=60.0)
        _audio_client = AsyncOpenAI(
            api_key=settings.GROQ_API_KEY,
            base_url=settings.GROQ_BASE_URL,
            http_client=_external_http_client
        )
    return _audio_client

async def close_llm_clients():
    """Gracefully closes persistent HTTP clients."""
    global _client, _audio_client, _internal_http_client, _external_http_client
    if _internal_http_client and not _internal_http_client.is_closed:
        await _internal_http_client.aclose()
    if _external_http_client and not _external_http_client.is_closed:
        await _external_http_client.aclose()
    _client = None
    _audio_client = None
    _internal_http_client = None
    _external_http_client = None

def parse_json_safely(raw_text: str) -> Dict[str, Any]:
    """
    Robust JSON parser for LLM outputs. Strips Markdown codeblocks and extracts
    the outermost JSON object if present.
    """
    cleaned = raw_text.strip()
    # Strip markdown fences if present
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Attempt to extract first JSON object {...}
    match = re.search(r"(\{.*\})", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    return {}

async def _stream_generation(prompt: str, temperature: float, max_tokens: int, fallback_text: str) -> AsyncGenerator[str, None]:
    client = get_llm_client()
    settings = get_settings()
    try:
        response = await client.chat.completions.create(
            model=settings.AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True
        )
        async for chunk in response:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
    except Exception as e:
        log_llm_error(f"{e}")
        yield fallback_text

async def process_text(
    text: str,
    delay_callback=None,
    url_content: str = "",
    recent_notes: str = ""
) -> Dict[str, Any]:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    settings = get_settings()

    url_section = f"\n\nExtracted content from linked URL:\n{url_content[:2000]}" if url_content else ""

    prompt = f"""You are a precise data formatter for Obsidian. You output ONLY valid JSON.

Date: {now_str}
Input: {text}{url_section}

Existing recent notes in user's Obsidian:
{recent_notes}

RULES FOR JSON FIELDS:
1. "category": Pick EXACTLY ONE:
   - "Reminders": ONLY IF the user explicitly asks for an alert (e.g., "напомни", "каждый час") OR specifies a strict future time for a task.
   - "Finance": ONLY IF money, purchases with prices, or budgets are mentioned.
   - "Ideas": ONLY IF it's an abstract brainstorming concept or a distant dream.
   - "Notes": DEFAULT category for everything else (everyday plans like "Надо купить билеты", facts, information, events).
2. "tags": Array of strings related to the topic (NO #).
3. "filename": Short, descriptive, STRICTLY IN RUSSIAN. Capitalize the first letter and use SPACES instead of underscores. DO NOT use "_".
4. "corrected_text": 
   - IF "category" is "Reminders": Rewrite as a DIRECT INSTRUCTION for the core action ONLY ("Тебе нужно..."). REMOVE all time markers and meta-requests like "напомни мне", "каждый час", "в 15:00". Example: "напомни каждый час разминать спину" -> "Тебе нужно размять спину!".
   - IF "category" is "Notes", "Ideas", or "Finance": KEEP the original meaning and style. Do not make it a command. Just fix typos and format beautifully in Markdown.
   - SEMANTIC LINKING: IF you find a strong, obvious logical connection (e.g., cause/effect, same project) to an EXISTING note from the list, append this to the very end of the text: \n\n**Связанные заметки:** [[Exact_Name_From_List]]
   - IF NO CONNECTION IS FOUND, do NOT add the "Связанные заметки" line at all.
5. "remind_time": "YYYY-MM-DD HH:MM" if a specific time is requested, else empty string "".
6. "recur_minutes": 60 if "every hour", 1440 if "every day", else 0.
7. "expense_amount": Number if finance, else 0.

CRITICAL EXAMPLE OF CORRECT OUTPUT:
{{
  "category": "Notes",
  "tags": ["путешествия", "покупки"],
  "corrected_text": "Надо купить билеты в Турцию.\\n\\n**Связанные заметки:** [[Путешествие в Турцию]]",
  "filename": "Билеты в Турцию.md",
  "remind_time": "",
  "recur_minutes": 0,
  "expense_amount": 0
}}
"""

    delay_task = None
    if delay_callback:
        async def _delayed_trigger():
            await asyncio.sleep(4.0)
            try:
                await delay_callback()
            except Exception:
                pass
        delay_task = asyncio.create_task(_delayed_trigger())

    client = get_llm_client()
    try:
        response = await client.chat.completions.create(
            model=settings.AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=2048
        )
        content = response.choices[0].message.content or ""
        return parse_json_safely(content)
    except Exception as e:
        log_llm_error(f"{e}")
        return {}
    finally:
        if delay_task and not delay_task.done():
            delay_task.cancel()

async def transcribe_audio(filepath: str) -> str:
    audio_client = get_audio_client()
    if not audio_client:
        logger.warning("GROQ_API_KEY не установлен. Транскрибация аудио недоступна.")
        return ""

    settings = get_settings()
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()

        ext = os.path.splitext(filepath)[1].lower()
        upload_name = os.path.basename(filepath)
        if not ext:
            ext = ".ogg"
            upload_name = f"{upload_name}.ogg"

        mime_types = {
            ".mp3": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".oga": "audio/ogg",
            ".opus": "audio/ogg",
            ".flac": "audio/flac",
            ".webm": "audio/webm",
            ".aac": "audio/aac",
            ".mp4": "audio/mp4",
            ".mpeg": "audio/mpeg",
            ".mpga": "audio/mpeg",
        }
        content_type = mime_types.get(ext) or mimetypes.guess_type(filepath)[0] or "audio/ogg"
        if ext == ".oga":
            upload_name = os.path.splitext(upload_name)[0] + ".ogg"
        file_tuple = (upload_name, file_bytes, content_type)
        response = await audio_client.audio.transcriptions.create(
            model=settings.GROQ_MODEL,
            file=file_tuple
        )
        return response.text or ""
    except Exception as e:
        log_llm_error(f"Ошибка распознавания аудио: {e}")
        return ""

async def get_rephrased_filename(category: str, filename: str, original_text: str, max_attempts: int = 3) -> str:
    """
    Ensures filename is unique within category. If duplicate exists, asks LLM
    to generate an alternative name up to max_attempts, then falls back to a unique timestamp/uuid suffix.
    """
    unique_filename = filename if filename.endswith('.md') else f"{filename}.md"
    settings = get_settings()
    client = get_llm_client()

    for attempt in range(max_attempts):
        exists = await note_exists(unique_filename, category)
        if not exists:
            return unique_filename

        prompt = f"""Предыдущее название файла '{unique_filename}' уже занято. 
Придумай ДРУГОЕ, новое короткое название для файла (строго на русском языке, используй ПРОБЕЛЫ вместо нижних подчеркиваний, расширение .md) на основе этого текста:
'{original_text[:200]}'

Выведи ТОЛЬКО название файла и ничего больше. Пример: Новое название для заметки.md"""

        try:
            response = await client.chat.completions.create(
                model=settings.AI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            ai_name = response.choices[0].message.content.strip()
            ai_name = ai_name.replace('"', '').replace("'", "").replace("`", "").strip()
            unique_filename = ai_name if ai_name.endswith('.md') else f"{ai_name}.md"
        except Exception as e:
            logger.warning(f"Ошибка запроса нового имени файла: {e}")
            break

    # If still exists after max attempts, apply deterministic unique suffix
    if await note_exists(unique_filename, category):
        base, ext = os.path.splitext(unique_filename)
        unique_filename = f"{base} {uuid.uuid4().hex[:4]}{ext}"

    return unique_filename

async def generate_semantic_filename(text_content: str) -> str:
    settings = get_settings()
    client = get_llm_client()
    prompt = f"""Придумай короткое, емкое и осмысленное название для файла (строго на русском языке, используй обычные пробелы вместо нижних подчеркиваний) на основе следующего текста:
'{text_content[:600]}'

Выведи ТОЛЬКО название файла с расширением .md в конце и больше ничего. Пример: Настройка сервера Linux.md"""
    try:
        response = await client.chat.completions.create(
            model=settings.AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4
        )
        ai_name = response.choices[0].message.content.strip()
        ai_name = ai_name.replace('"', '').replace("'", "").replace("`", "").strip()
        if not ai_name.endswith('.md'):
            ai_name += '.md'
        return ai_name
    except Exception as e:
        logger.warning(f"Не удалось сгенерировать имя файла через LLM: {e}")
        return f"note_{uuid.uuid4().hex[:4]}.md"

async def stream_answer_question(question: str, context: str) -> AsyncGenerator[str, None]:
    prompt = f"""You are a precise and honest AI assistant. Answer the user's question based ONLY on the provided "User Notes".

RULES:
1. Grounding: You MUST use ONLY the information found in the "User Notes". DO NOT use outside knowledge or invent facts.
2. Missing Info: If the notes do not contain the answer, you MUST reply exactly with: "К сожалению, в ваших заметках нет информации об этом."
3. Language: Output STRICTLY in RUSSIAN.
4. Formatting: Use clean Markdown (bullet points, **bold** text for key entities).
5. NO PREAMBLE: Start answering the question immediately. Do not say "Based on the notes" or "Here is the answer".

User Notes (Context):
{context[:3500]}

User Question: 
{question}"""

    fallback = "❌ Произошла ошибка при обращении к нейросети."
    async for chunk in _stream_generation(prompt, 0.1, 1000, fallback):
        yield chunk

ask_about_notes = stream_answer_question

async def stream_process_examiner_text(text: str, recent_notes: str = "") -> AsyncGenerator[str, None]:
    prompt = f"""You are an expert data structurer for Obsidian. Transform the raw text into a clean, readable Markdown note.

RULES FOR FORMATTING:
1. Language: Output STRICTLY in RUSSIAN.
2. NO PREAMBLE: Start directly with the Markdown text. Do not output any introductory words.
3. Structure: Use `##` for main headings, `-` for lists, and **bold** for key terms.
4. Content: DO NOT generate Q&A, flashcards, or tests.

CRITICAL RULE FOR SEMANTIC LINKING:
- Look at the "Existing recent notes" list.
- IF AND ONLY IF the raw text directly continues or heavily relates to an EXACT note name from that list, append the following template to the VERY END of your response:
\n\n**Связанные заметки:**\n- [[Exact Name From The List]]
- IF NO MATCHING NOTE IS FOUND, TERMINATE THE RESPONSE IMMEDIATELY. DO NOT WRITE "Связанные заметки", DO NOT WRITE ANYTHING ELSE.

Existing recent notes:
{recent_notes}

Raw Text to format:
{text[:3500]}"""

    fallback = f"### Оригинальный текст (ошибка генерации):\n\n{text}"
    async for chunk in _stream_generation(prompt, 0.2, 1200, fallback):
        yield chunk

async def stream_summarize_document(text: str, recent_notes: str = "") -> AsyncGenerator[str, None]:
    prompt = f"""You are an expert analyst. Summarize and structure the following document text into a clean Obsidian note.

RULES FOR FORMATTING:
1. Language: Output STRICTLY in RUSSIAN.
2. NO PREAMBLE: Start directly with the Markdown text.
3. Structure: Extract the core ideas. Use `##` for key topics, `-` for bullet points, and **bold** for key terms.

CRITICAL RULE FOR SEMANTIC LINKING:
- Look at the "Existing recent notes" list.
- IF AND ONLY IF this document directly belongs to the same project or topic as a note in that list, append this template to the VERY END of the note:
\n\n**Связанные заметки:**\n- [[Exact Name From The List]]
- IF THERE IS NO DIRECT MATCH, DO NOT ADD ANY LINKING SECTION AT ALL. STOP GENERATION IMMEDIATELY.

Existing recent notes:
{recent_notes}

Document Text:
{text[:25000]}"""

    fallback = f"### Оригинальный текст документа (ошибка генерации):\n\n{text}"
    async for chunk in _stream_generation(prompt, 0.2, 1200, fallback):
        yield chunk