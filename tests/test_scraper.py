import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.scraper import extract_urls, fetch_url_content

def test_extract_urls():
    text = "Посмотри вот этот сайт https://example.com/page и еще www.github.com/test для инфы"
    urls = extract_urls(text)
    assert len(urls) == 2
    assert "https://example.com/page" in urls
    assert "https://www.github.com/test" in urls

    assert extract_urls("Просто текст без ссылок") == []

@pytest.mark.asyncio
async def test_fetch_url_content():
    fake_html = """
    <html>
        <head><title>Тестовая статья</title><script>var x = 1;</script></head>
        <body>
            <header>Меню сайта</header>
            <article>
                <h1>Основной заголовок</h1>
                <p>Полезный текст статьи для Obsidian базы знаний.</p>
            </article>
            <footer>Подвал сайта</footer>
        </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.text = fake_html
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        content = await fetch_url_content("https://example.com/article")
        assert "Заголовок: Тестовая статья" in content
        assert "Полезный текст статьи" in content
        assert "Меню сайта" not in content
        assert "Подвал сайта" not in content

@pytest.mark.asyncio
async def test_fetch_url_content_error():
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=Exception("Connection timed out"))
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch("httpx.AsyncClient", return_value=mock_client):
        content = await fetch_url_content("https://broken.domain.com")
        assert content == ""
