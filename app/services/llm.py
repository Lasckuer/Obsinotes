from openai import AsyncOpenAI
import httpx
import json
import os
import datetime
import asyncio
import re
from logger import log_llm_retry, log_llm_error

proxy_url = os.getenv("PROXY_URL")
http_client = httpx.AsyncClient(proxy=proxy_url) if proxy_url else None

client = AsyncOpenAI(
    api_key="ollama",
    base_url="https://api-ai.lkserv.ru/v1",
    http_client=httpx.AsyncClient()
)

async def process_text(text: str, delay_callback=None, url_content: str = "") -> dict:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    prompt = f"""
    Current date and time: {now_str}.
    
    User Input: "{text}"
    Website Content (if applicable): "{url_content}"
    
    Analyze the input and return ONLY a JSON object.
    Rules:
    - "category": Choose one of ["Ideas", "Reminders", "Notes", "Links", "Workouts", "Finance"].
      * If URL Content is provided, categorize as "Links" and make "corrected_text" a Markdown card with Title, URL, and a brief summary.
      * If workout (e.g., deadlift, snowboarding), categorize as "Workouts" and format "corrected_text" as a Markdown table.
      * If it's an expense/income, categorize as "Finance".
    - "corrected_text": The formatted content for Obsidian.
    - "tags": Array of relevant hashtags without the # symbol.
    - "reminder_time": "YYYY-MM-DD HH:MM:SS" if it's a reminder, else null.
    - "filename": A concise, descriptive filename (2-4 words) in Latin characters, 
        reflecting the CORE TOPIC. Do not just slugify the input. 
        Example: "workout_chest_80kg" or "pixel9_smartphone_review".
        Only Russian language
    - "expense_amount": Float number if category is Finance, else null.
    - "expense_comment": String explaining what was bought if Finance, else null.
    """
    
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model="gemma3:4b",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            if content:
                return json.loads(content)
                
        except Exception as e:
            error_msg = str(e)
            if ("503" in error_msg or "429" in error_msg) and attempt < max_retries - 1:
                wait_time = 15 if attempt == 0 else 45 
                log_llm_retry(attempt + 1, max_retries, wait_time)
                
                if delay_callback and attempt == 1:
                    await delay_callback()
                await asyncio.sleep(wait_time)
            else:
                log_llm_error(e)
                break 
    
    safe_name = re.sub(r'[^\w\s]', '', text[:30]).strip().replace(' ', '_').lower()
    if not safe_name:
        safe_name = "note"
        
    return {
        "category": "Notes",
        "corrected_text": text + (f"\n\nURL Content: {url_content}" if url_content else ""),
        "tags": ["raw_note", "ai_error"],
        "reminder_time": None,
        "filename": safe_name,
        "expense_amount": None,
        "expense_comment": None
    }
    
async def answer_question(question: str, context: str) -> str:
    """Отвечает на вопрос пользователя, основываясь на истории заметок"""
    prompt = f"""
    Ты — персональный ассистент. Твоя задача — ответить на вопрос, используя предоставленный контекст из заметок пользователя.
    Если в контексте нет ответа, так и скажи.
    Отвечай кратко и по делу на русском языке.
    
    КОНТЕКСТ ЗАМЕТОК:
    {context}
    
    ВОПРОС: {question}
    """
    try:
        response = await client.chat.completions.create(
            model="gemma3:4b",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        log_llm_error(e)
        return "Не удалось получить ответ от ИИ."

async def summarize_document(text: str) -> dict:
    """Делает краткий конспект документа"""
    prompt = f"""
    Проанализируй следующий текст из документа и верни JSON:
    "summary": краткий конспект на русском языке (Markdown)
    "tags": список подходящих тегов
    "filename": VERY CONCISE descriptive name in Latin (max 3 words) 
        reflecting the document's main subject.Only Russian language
    
    ТЕКСТ:
    {text[:5000]} 
    """
    try:
        response = await client.chat.completions.create(
            model="gemma3:4b",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        log_llm_error(e)
        return None

async def transcribe_audio(file_path: str) -> str:
    try:
        with open(file_path, "rb") as audio_file:
            response = await client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=audio_file
            )
            return response.text
    except Exception as e:
        log_llm_error(e)
        return ""

async def process_examiner_text(text: str) -> dict:
    max_text_length = 10000 
    if len(text) > max_text_length:
        text = text[:max_text_length] + "\n\n... [ЧАСТЬ ТЕКСТА ОБРЕЗАНА ИЗ-ЗА ЛИМИТОВ API GROQ]"

    prompt = f"""
    Ты — строгий экзаменатор. Тебе отправлен текст. Твоя задача — извлечь факты и даты (особенно по истории).
    Будь ОЧЕНЬ лаконичным, пиши только самое главное. Не лей воду, иначе ответ не поместится в лимиты.
    Сделай максимум 5-7 самых важных флеш-карточек.
    
    ВАЖНОЕ ПРАВИЛО: Твой ответ должен быть ТОЛЬКО сырым JSON объектом, без оберток ```json ... ```. 
    Все переносы строк внутри поля markdown_content должны быть строго экранированы как \\n.
    Любые кавычки внутри текста должны быть экранированы как \\".
    
    Сформируй ответ строго по этому шаблону:
    {{
        "filename": "лаконичное_имя_файла_на_латинице",
        "markdown_content": "Структурированный Markdown-конспект.\\n\\nВыделяй главные даты жирным (**дата**).\\n\\n## Флеш-карточки\\n**В:** Вопрос?\\n**О:** Ответ"
    }}
    
    ТЕКСТ ДЛЯ АНАЛИЗА:
    {text}
    """
    try:
        response = await client.chat.completions.create(
            model="gemma3:4b",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=2500
        )
        
        raw_content = response.choices[0].message.content.strip()
        if raw_content.startswith("```json"):
            raw_content = raw_content[7:]
        if raw_content.endswith("```"):
            raw_content = raw_content[:-3]
            
        return json.loads(raw_content.strip())
        
    except Exception as e:
        log_llm_error(f"Ошибка парсинга JSON Экзаменатора: {e}")
        return None