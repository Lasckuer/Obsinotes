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

AI_MODEL = os.getenv("AI_MODEL", "llama3.2:3b")

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
    
    prompt = f"""You are a strict data formatter for Obsidian. You output ONLY valid JSON.
Never wrap the output in markdown code blocks like ```json.

Date: {now_str}
Input: {text}

Existing recent notes list (Use EXACT names for linking):
{recent_notes}

RULES FOR JSON FIELDS:
1. "thought_process": Write a single sentence explaining the chosen category and if a semantic link was found.
2. "category": "Reminders", "Finance", "Ideas", or "Notes" (Default for plans, facts, info).
3. "tags": Array of strings related to the topic (NO #).
4. "filename": Short descriptive name in Russian. 
   - CRITICAL: Must start with a CAPITAL LETTER.
   - CRITICAL: Use regular spaces only. DO NOT use underscores "_".
   - Must end with ".md".
5. "corrected_text": Original meaning with fixed typos in Markdown.
   - SEMANTIC LINKING: Check the "Existing recent notes list". If the input directly continues, relates to, or mentions a topic from that list, you MUST append this exact markdown to the very end of the text: \\n\\n**Связанные заметки:** [[Exact_Name_From_List]]
   - If no strong connection is found, DO NOT add the block.
6. "remind_time": "YYYY-MM-DD HH:MM" or "".
7. "recur_minutes": 60, 1440, or 0.
8. "expense_amount": Number or 0.

OUTPUT FORMAT EXAMPLE:
{{
  "thought_process": "Input is about server setup. Found matching note 'Домашний сервер'. Adding link.",
  "category": "Notes",
  "tags": ["linux", "сервер"],
  "filename": "Настройка сети на сервере.md",
  "corrected_text": "Нужно настроить проброс портов на роутере.\\n\\n**Связанные заметки:** [[Домашний сервер]]",
  "remind_time": "",
  "recur_minutes": 0,
  "expense_amount": 0
}}"""

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

СТРОГИЕ ПРАВИЛА:
1. Выведи ТОЛЬКО название файла с расширением .md в конце. Никаких вводных слов, кавычек или пояснений.
2. Язык: Строго русский.
3. Регистр: Первая буква названия ОБЯЗАТЕЛЬНО должна быть ЗАГЛАВНОЙ (Большой).
4. Символы: Используй только обычные пробелы. ЗАПРЕЩЕНО использовать нижнее подчеркивание "_" вместо пробелов.

Пример правильного ответа:
Умный дом и автоматизация.md"""
            
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

async def generate_semantic_filename(text_content: str) -> str:
    prompt = f"""Придумай короткое, емкое название для файла на основе текста.

Текст:
'{text_content[:600]}'

СТРОГИЕ ПРАВИЛА:
1. Выведи ТОЛЬКО название файла с расширением .md в конце. Никаких вводных слов, кавычек или пояснений.
2. Язык: Строго русский.
3. Регистр: Первая буква названия ОБЯЗАТЕЛЬНО должна быть ЗАГЛАВНОЙ (Большой).
4. Символы: Используй только обычные пробелы. ЗАПРЕЩЕНО использовать нижнее подчеркивание "_" вместо пробелов.

Пример правильного ответа:
Умный дом и автоматизация.md"""
    try:
        response = await client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4
        )
        ai_name = response.choices[0].message.content.strip()
        ai_name = ai_name.replace('"', '').replace("'", "").replace("`", "").strip()
        if not ai_name.endswith('.md'):
            ai_name += '.md'
        return ai_name
    except Exception as e:
        print(f"⚠️ Не удалось сгенерировать имя файла через LLM: {e}")
        return f"note_{uuid.uuid4().hex[:4]}.md"

async def stream_answer_question(question: str, context: str):
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
    async for chunk in _stream_generation(prompt, 0.1, 1000, fallback): # Температуру лучше снизить до 0.1 для точности фактов
        yield chunk

async def stream_process_examiner_text(text: str, recent_notes: str = ""):
    prompt = f"""You are an expert data structurer for Obsidian. Transform the raw text into a clean, readable Markdown note.

RULES FOR FORMATTING:
1. Language: Output STRICTLY in RUSSIAN.
2. NO PREAMBLE: Start directly with the Markdown text. Do not output any introductory words.
3. Structure: Use `##` for main headings, `-` for lists, and **bold** for key terms.
4. Content: DO NOT generate Q&A, flashcards, or tests.

CRITICAL RULE FOR SEMANTIC LINKING:
- Look at the "Existing recent notes" list.
- If and only if this text directly relates to an EXACT note name from that list, append the following section to the VERY END of your markdown output:
\n\n**Связанные заметки:**\n- [[Exact Name From The List]]
- If no matching note is found in the list, simply finish the text normally. Do NOT add any linking section.

Existing recent notes:
{recent_notes}

Raw Text to format:
{text[:3500]}"""

    fallback = f"### Оригинальный текст (ошибка генерации красоты):\n\n{text}"
    async for chunk in _stream_generation(prompt, 0.2, 1500, fallback):
        yield chunk

async def stream_summarize_document(text: str, recent_notes: str = ""):
    prompt = f"""You are an expert analyst. Summarize and structure the following document text into a clean Obsidian note.

RULES FOR FORMATTING:
1. Language: Output STRICTLY in RUSSIAN.
2. NO PREAMBLE: Start directly with the Markdown text.
3. Structure: Extract the core ideas. Use `##` for key topics, `-` for bullet points, and **bold** for key terms.

CRITICAL RULE FOR SEMANTIC LINKING:
- Look at the "Existing recent notes" list.
- If and only if this text directly relates to an EXACT note name from that list, append the following section to the VERY END of your markdown output:
\n\n**Связанные заметки:**\n- [[Exact Name From The List]]
- If no matching note is found in the list, simply finish the text normally. Do NOT add any linking section.

Existing recent notes:
{recent_notes}

Document Text:
{text[:3500]}"""

    fallback = f"### Оригинальный текст документа (ошибка генерации):\n\n{text}"
    async for chunk in _stream_generation(prompt, 0.2, 1500, fallback):
        yield chunk