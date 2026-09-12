import re
from typing import List, Optional
import httpx
from bs4 import BeautifulSoup
from app.config import get_settings
from logger import logger

URL_PATTERN = re.compile(
    r'(?:https?://|www\.)[a-zA-Z0-9.\-_]+(?::\d+)?(?:/[^\s<>\'"]*)?',
    re.IGNORECASE
)

def extract_urls(text: str) -> List[str]:
    """Extracts all valid HTTP/HTTPS URLs from a text string."""
    matches = URL_PATTERN.findall(text)
    urls = []
    for u in matches:
        if not u.startswith("http://") and not u.startswith("https://"):
            u = f"https://{u}"
        urls.append(u)
    return urls

async def fetch_url_content(url: str, max_chars: int = 4000) -> str:
    """
    Fetches text content from the given URL, stripping scripts, styles, and navigation tags.
    Returns cleaned markdown/plain text.
    """
    settings = get_settings()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36 ObsinotesBot/1.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    proxy = settings.PROXY_URL if settings.PROXY_URL else None

    try:
        async with httpx.AsyncClient(proxy=proxy, verify=False, timeout=12.0) as client:
            resp = await client.get(url, headers=headers, follow_redirects=True)
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, 'html.parser')

            # Extract title
            title = ""
            if soup.title and soup.title.string:
                title = soup.title.string.strip()

            # Remove non-content elements
            for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "aside", "form"]):
                tag.extract()

            # Prefer article or main content if available
            main_container = soup.find('article') or soup.find('main') or soup.body or soup
            text = main_container.get_text(separator=' ', strip=True)

            # Collapse multi-spaces
            clean_text = re.sub(r'\s+', ' ', text).strip()

            result = f"Заголовок: {title}\n\n{clean_text}" if title else clean_text
            return result[:max_chars]
    except Exception as e:
        logger.warning(f"Ошибка скрапинга страницы {url}: {e}")
        return ""