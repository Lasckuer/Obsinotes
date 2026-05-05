import httpx
from bs4 import BeautifulSoup

async def fetch_url_content(url: str) -> str:
    try:
        async with httpx.AsyncClient(verify=False) as client:
            resp = await client.get(url, follow_redirects=True, timeout=10.0)
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            for script in soup(["script", "style", "nav", "footer", "header"]):
                script.extract()
                
            text = soup.get_text(separator=' ', strip=True)
            return text[:4000]
    except Exception:
        return ""