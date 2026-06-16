import uuid
import aiosqlite
from openai import AsyncOpenAI
import httpx
import json
import os
import datetime
import asyncio
from logger import log_llm_error

proxy_url = os.getenv("PROXY_URL")
http_client = httpx.AsyncClient(proxy=proxy_url) if proxy_url else None

external_http_client = httpx.AsyncClient(proxy=proxy_url) if proxy_url else None

client = AsyncOpenAI(
    api_key="ollama",
    base_url="https://api-ai.lkserv.ru/v1",
    http_client=httpx.AsyncClient()
)

audio_client = AsyncOpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
    http_client=external_http_client
)

AI_MODEL = os.getenv("AI_MODEL", "gemma3:4b")

async def process_text(text: str, delay_callback=None, url_content: str = "", recent_notes: str = "") -> dict:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    prompt = f"""You are a data parser. Output ONLY valid JSON.
Date: {now_str}
Input: {text}
URL: {url_content}

Existing recent notes in user's Obsidian:
{recent_notes}

{{
"category": "Choose ONLY ONE: Finance, Ideas, Notes, Reminders",
"tags": ["tag1", "tag2"],
"corrected_text": "formatted markdown in Russian",
"filename": "понятное_название_из_сути_заметки.md",
"remind_time": "YYYY-MM-DD HH:MM or empty string",
"expense_amount": float or 0
}}

RULES:
1. "category": Must be one of the four: Finance, Ideas, Notes, Reminders. DO NOT list them all, pick only the best one.
2. "tags": list of strings WITHOUT the # symbol.
3. "filename": short, descriptive filename in Russian.
4. NEVER leave "corrected_text" empty.
5. IMPORTANT: If the Input conceptually relates to any of the "Existing recent notes", append this exact text at the end of "corrected_text": '\\n\\n**Связанные заметки:** [[Name]]' (use the exact Name from the list).
"""

    try:
        if delay_callback:
            asyncio.create_task(delay_callback())
            
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=800
        )
        
        raw_content = response.choices[0].message.content.strip()
        return json.loads(raw_content)
    except Exception as e:
        log_llm_error(f"{e}")
        return {}

async def transcribe_audio(filepath: str) -> str:
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
            
        filename = os.path.basename(filepath)
        
        file_tuple = (filename, file_bytes, "audio/ogg")
        
        response = await audio_client.audio.transcriptions.create(
            model="whisper-large-v3", 
            file=file_tuple
        )
        
        return response.text
    except Exception as e:
        log_llm_error(f"Ошибка распознавания аудио: {e}")
        return ""

async def get_rephrased_filename(category: str, filename: str, original_text: str) -> str:
    unique_filename = filename
    
    async with aiosqlite.connect("database.db") as db:
        while True:
            async with db.execute(
                "SELECT 1 FROM notes_log WHERE filename = ? AND category LIKE ?", 
                (unique_filename, f"%{category}")
            ) as cursor:
                if not await cursor.fetchone():
                    break
            
            prompt = f"""Предыдущее название файла '{unique_filename}' уже занято. 
Придумай ДРУГОЕ, новое короткое название для файла (на русском языке, без пробелов, расширение .md) на основе этого текста:
'{original_text[:200]}'

Выведи ТОЛЬКО название файла и ничего больше. Пример: новое_название.md"""
            
            try:
                response = await client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7
                )
                ai_name = response.choices[0].message.content.strip().replace(" ", "_")
                unique_filename = ai_name if ai_name.endswith('.md') else f"{ai_name}.md"
            except Exception:
                base, ext = os.path.splitext(unique_filename)
                unique_filename = f"{base}_{uuid.uuid4().hex[:4]}{ext}"
                
    return unique_filename
    
async def stream_answer_question(question: str, context: str):
    """Генерирует потоковый ответ на вопрос пользователя (эффект печати)"""
    prompt = f"""Ты — умный ИИ-ассистент. Ответь на вопрос пользователя, основываясь ТОЛЬКО на предоставленных ниже заметках.
Если в заметках нет ответа на вопрос, честно скажи об этом.
Отвечай строго на РУССКОМ языке, используй Markdown для красивого форматирования.

Заметки пользователя (контекст):
{context[:3500]}

Вопрос пользователя: 
{question}"""

    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1000,
            stream=True
        )
        
        async for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
                
    except Exception as e:
        log_llm_error(f"Ошибка в stream_answer_question: {e}")
        yield "❌ Произошла ошибка при обращении к нейросети."
        
async def stream_process_examiner_text(text: str, recent_notes: str = ""):
    prompt = f"""You are an expert assistant helping to structure notes for Obsidian.
Process the following text and format it beautifully in Markdown.

Existing recent notes in user's Obsidian:
{recent_notes}

RULES:
1. Output strictly in RUSSIAN.
2. Format as a clean, structured note.
3. DO NOT generate Q&A, flashcards, or tests.
4. IMPORTANT: If the text conceptually relates to any of the "Existing recent notes", add a section at the very end of the markdown:
## Связанные заметки
- [[Exact name of the note from the list]]

Text to format:
{text[:3500]}"""

    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1500,
            stream=True
        )
        async for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
    except Exception as e:
        log_llm_error(f"Ошибка в stream_process_examiner_text: {e}")
        yield f"### Оригинальный текст (ошибка генерации красоты):\n\n{text}"
        
async def stream_summarize_document(text: str, recent_notes: str = ""):
    text_chunk = text[:3500]
    prompt = f"""You are an expert assistant helping to structure notes for Obsidian.
Process the following text from a document and format it beautifully in Markdown.

Existing recent notes in user's Obsidian:
{recent_notes}

RULES:
1. Output strictly in RUSSIAN (Отвечай строго на РУССКОМ языке).
2. Format as a clean, structured note (use ## headings, bullet points, and bold text for key terms).
3. DO NOT generate Q&A, flashcards, or tests.
4. Output ONLY the raw Markdown text. Do not add JSON or any introductory words.
5. IMPORTANT: If the text conceptually relates to any of the "Existing recent notes", add a section at the very end of the markdown:
## Связанные заметки
- [[Exact name of the note from the list]]

Text:
{text_chunk}"""

    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1500,
            stream=True
        )
        async for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
    except Exception as e:
        log_llm_error(f"{e}")
        yield f"### Оригинальный текст документа (ошибка генерации):\n\n{text}"