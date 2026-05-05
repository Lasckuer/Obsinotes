from openai import AsyncOpenAI
import httpx
import json
import os
import datetime
import asyncio
from logger import logger

proxy_url = os.getenv("PROXY_URL")

http_client = httpx.AsyncClient(proxy=proxy_url) if proxy_url else None

client = AsyncOpenAI(
    api_key=os.getenv("GEMINI_API_KEY"),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    http_client=http_client
)

async def process_text(text: str, delay_callback=None) -> dict:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prompt = f"""
    Текущая системная дата и время: {now_str}. 
    Ориентируйся на это время при расчете напоминаний!
    
    Analyze the following text: "{text}".
    Return ONLY a JSON object with the following keys:
    "category": exactly one of ["Ideas", "Reminders", "Notes"]
    "corrected_text": the input text corrected for grammar, punctuation, and presentation
    "tags": a list of relevant strings for Obsidian tags
    "reminder_time": in "YYYY-MM-DD HH:MM:SS" format ONLY if it is a reminder (calculate based on current time), otherwise null
    "filename": a short, meaningful file name describing the content, in lowercase latin letters, using underscores (e.g., "game_download", "shop_idea")
    """
    
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            response = await client.chat.completions.create(
                model="gemini-2.5-flash",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            error_msg = str(e)
            if ("503" in error_msg or "429" in error_msg) and attempt < max_retries - 1:
                wait_time = 3 ** attempt 
                logger.warning(f"Лимит Gemini. Попытка {attempt + 1}/{max_retries}. Ждем {wait_time} сек...")
                
                if delay_callback and attempt == 1:
                    await delay_callback()
                    
                await asyncio.sleep(wait_time)
            else:
                logger.error(f"Ошибка при запросе к Gemini: {e}")
                return {
                    "category": "Notes",
                    "corrected_text": text,
                    "tags": ["#raw_note", "#ai_error"],
                    "reminder_time": None,
                    "filename": "raw_note"
                }