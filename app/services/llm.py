from openai import AsyncOpenAI
import httpx
import json
import os
import datetime
import asyncio
from logger import log_llm_error

proxy_url = os.getenv("PROXY_URL")
http_client = httpx.AsyncClient(proxy=proxy_url) if proxy_url else None

client = AsyncOpenAI(
    api_key="ollama",
    base_url="https://api-ai.lkserv.ru/v1",
    http_client=httpx.AsyncClient()
)

async def process_text(text: str, delay_callback=None, url_content: str = "") -> dict:
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    prompt = f"""You are a data parser. Output ONLY valid JSON.
Date: {now_str}
Input: {text}
URL: {url_content}

{{"category": "one of: Ideas, Reminders, Notes, Links, Workouts, Finance",
"tags": "#tag1, #tag2",
"corrected_text": "formatted markdown in Russian",
"filename": "short_latin_name.md",
"remind_time": "YYYY-MM-DD HH:MM or empty string",
"expense_amount": float or 0}}"""

    try:
        if delay_callback:
            asyncio.create_task(delay_callback())
            
        response = await client.chat.completions.create(
            model="gemma3:4b",
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

async def answer_question(query: str, context: list) -> str:
    context_lines = []
    for c in context:
        if len(c) >= 2:
            category = c[0]
            content = c[1]
            date = c[2] if len(c) >= 3 else "No date"
            context_lines.append(f"[{category}] {date}: {content}")
            
    context_str = "\n".join(context_lines)[:3000]
    
    prompt = f"""Context:
{context_str}

Question: {query}

Answer in Russian clearly and concisely based ONLY on the context. If you don't know, say so."""

    try:
        response = await client.chat.completions.create(
            model="gemma3:4b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        log_llm_error(f"{e}")
        return "Ошибка при обращении к ИИ."

async def summarize_document(text: str, filename: str) -> dict:
    text_chunk = text[:3500]
    prompt = f"""Extract key facts and 3-5 flashcards from this text. Output ONLY valid JSON.
{{"filename": "{filename}_summary.md",
"markdown_content": "## Summary\\n...\\n## Flashcards\\n**Q:** ...\\n**A:** ..."}}

Text:
{text_chunk}"""

    try:
        response = await client.chat.completions.create(
            model="gemma3:4b",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=1000
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception as e:
        log_llm_error(f"{e}")
        return {"filename": f"{filename}.md", "markdown_content": "Ошибка генерации."}

async def process_examiner_text(text: str) -> str:
    prompt = f"""Extract main points and create 5 flashcards from the text. Answer in Russian markdown.
Text: {text[:3000]}"""

    try:
        response = await client.chat.completions.create(
            model="gemma3:4b",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=800
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        log_llm_error(f"{e}")
        return "Ошибка генерации."

async def transcribe_audio(file_path: str) -> str:
    try:
        with open(file_path, "rb") as audio_file:
            response = await client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file
            )
        return response.text
    except Exception as e:
        log_llm_error(f"{e}")
        return ""