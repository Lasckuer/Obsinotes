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
external_http_client = httpx.AsyncClient(proxy=proxy_url) if proxy_url else httpx.AsyncClient()
internal_http_client = httpx.AsyncClient()

client = AsyncOpenAI(
    api_key="ollama",
    base_url="https://api-ai.lkserv.ru/v1",
    http_client=internal_http_client
)

audio_client = AsyncOpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1",
    http_client=external_http_client
)

AI_MODEL = os.getenv("AI_MODEL", "gemma4:e2b")

async def _stream_generation(prompt: str, temperature: float, max_tokens: int, fallback_text: str):
    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True
        )
        async for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content
    except Exception as e:
        log_llm_error(f"{e}")
        yield fallback_text

async def process_text(text: str, delay_callback=None, url_content: str = "", recent_notes: str = "") -> dict:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    prompt = f"""You are a precise data formatter for Obsidian. You output ONLY valid JSON.

Date: {now_str}
Input: {text}

Existing recent notes in user's Obsidian:
{recent_notes}

RULES FOR JSON FIELDS:
1. "thought_process": You MUST use this field to think step-by-step BEFORE filling other fields.
   - Step 1: Determine the intent. Is it an explicit request for an alarm, an expense, a creative thought, or just a plan/information?
   - Step 2: Compare the input's core topic with the EXACT names in "Existing recent notes". Is there a direct logical connection or continuation?
2. "category": Pick EXACTLY ONE based on Step 1:
   - "Reminders": ONLY IF the user explicitly asks for an alert (e.g., "напомни", "каждый час") OR specifies a strict future time for a task.
   - "Finance": ONLY IF money, purchases with prices, or budgets are mentioned.
   - "Ideas": ONLY IF it's an abstract brainstorming concept or a distant dream.
   - "Notes": DEFAULT category for everything else (everyday plans like "Надо купить билеты", facts, information, events).
3. "tags": Array of strings related to the topic (NO #).
4. "filename": Short, descriptive, STRICTLY IN RUSSIAN. Capitalize the first letter and use SPACES instead of underscores. DO NOT use "_".
5. "corrected_text": 
   - IF "category" is "Reminders": Rewrite as a DIRECT INSTRUCTION for the core action ONLY ("Тебе нужно..."). REMOVE all time markers and meta-requests like "напомни мне", "каждый час", "в 15:00". Example: "напомни каждый час разминать спину" -> "Тебе нужно размять спину!".
   - IF "category" is "Notes", "Ideas", or "Finance": KEEP the original meaning and style. Do not make it a command. Just fix typos and format beautifully in Markdown.
   - SEMANTIC LINKING: IF Step 2 found a strong, obvious logical connection (e.g., cause/effect, same project) to an EXISTING note, you MUST append this to the very end of the text: \n\n**Связанные заметки:** [[Exact_Name_From_List]]
   - IF NO CONNECTION IS FOUND, do NOT add the "Связанные заметки" line at all.
6. "remind_time": "YYYY-MM-DD HH:MM" if a specific time is requested, else empty string "".
7. "recur_minutes": 60 if "every hour", 1440 if "every day", else 0.
8. "expense_amount": Number if finance, else 0.

CRITICAL EXAMPLE OF CORRECT OUTPUT:
{{
  "thought_process": "Step 1: Input says 'Надо купить билеты в Турцию'. No explicit 'remind me' or time, so it's a general plan -> category 'Notes'. Step 2: Checking recent notes. I see 'Путешествие в Турцию'. There is a direct logical connection (buying tickets for the planned trip). I will link them.",
  "category": "Notes",
  "tags": ["путешествия", "покупки"],
  "corrected_text": "Надо купить билеты в Турцию.\\n\\n**Связанные заметки:** [[Путешествие в Турцию]]",
  "filename": "Билеты в Турцию.md",
  "remind_time": "",
  "recur_minutes": 0,
  "expense_amount": 0
}}
"""

    if delay_callback:
        asyncio.create_task(delay_callback())

    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=800
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        log_llm_error(f"{e}")
        return {}

async def transcribe_audio(filepath: str) -> str:
    try:
        with open(filepath, "rb") as f:
            file_bytes = f.read()
        
        file_tuple = (os.path.basename(filepath), file_bytes, "audio/ogg")
        
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
Придумай ДРУГОЕ, новое короткое название для файла (строго на русском языке, используй ПРОБЕЛЫ вместо нижних подчеркиваний, расширение .md) на основе этого текста:
'{original_text[:200]}'

Выведи ТОЛЬКО название файла и ничего больше. Пример: Новое название для заметки.md"""
            
            try:
                response = await client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7
                )
                ai_name = response.choices[0].message.content.strip()
                unique_filename = ai_name if ai_name.endswith('.md') else f"{ai_name}.md"
            except Exception:
                base, ext = os.path.splitext(unique_filename)
                unique_filename = f"{base} {uuid.uuid4().hex[:4]}{ext}"
                
    return unique_filename

async def stream_answer_question(question: str, context: str):
    prompt = f"""Ты — умный ИИ-ассистент. Ответь на вопрос пользователя, основываясь ТОЛЬКО на предоставленных ниже заметках.
Если в заметках нет ответа на вопрос, честно скажи об этом.
Отвечай строго на РУССКОМ языке, используй Markdown для красивого форматирования.

Заметки пользователя (контекст):
{context[:3500]}

Вопрос пользователя: 
{question}"""

    fallback = "❌ Произошла ошибка при обращении к нейросети."
    async for chunk in _stream_generation(prompt, 0.3, 1000, fallback):
        yield chunk

async def stream_process_examiner_text(text: str, recent_notes: str = ""):
    prompt = f"""You are an expert assistant helping to structure notes for Obsidian.
Process the following text and format it beautifully in Markdown.

Existing recent notes in user's Obsidian:
{recent_notes}

RULES:
1. Output strictly in RUSSIAN (Отвечай строго на РУССКОМ языке).
2. Format as a clean, structured note (use ## headings, bullet points, and bold text for key terms).
3. DO NOT generate Q&A, flashcards, or tests.
4. Output ONLY the raw Markdown text. Do not add JSON or any introductory words.
5. SEMANTIC LINKING: Compare the text with "Existing recent notes". If there is a deep thematic connection, you MUST add a section at the very end of your response using exactly this syntax:
\n\n**Связанные заметки:** [[Exact Name From The List]]

Text to format:
{text[:3500]}"""

    fallback = f"### Оригинальный текст (ошибка генерации красоты):\n\n{text}"
    async for chunk in _stream_generation(prompt, 0.2, 1500, fallback):
        yield chunk

async def stream_summarize_document(text: str, recent_notes: str = ""):
    prompt = f"""You are an expert assistant helping to structure notes for Obsidian.
Process the following text from a document and format it beautifully in Markdown.

Existing recent notes in user's Obsidian:
{recent_notes}

RULES:
1. Output strictly in RUSSIAN (Отвечай строго на РУССКОМ языке).
2. Format as a clean, structured note (use ## headings, bullet points, and bold text for key terms).
3. DO NOT generate Q&A, flashcards, or tests.
4. Output ONLY the raw Markdown text. Do not add JSON or any introductory words.
5. SEMANTIC LINKING: Compare the text with "Existing recent notes". If there is a deep thematic connection, you MUST add a section at the very end of your response using exactly this syntax:
\n\n**Связанные заметки:** [[Exact Name From The List]]

Text:
{text[:3500]}"""

    fallback = f"### Оригинальный текст документа (ошибка генерации):\n\n{text}"
    async for chunk in _stream_generation(prompt, 0.2, 1500, fallback):
        yield chunk