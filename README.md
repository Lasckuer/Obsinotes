# 🧠 NodeNotes: Telegram «Второй мозг» для Obsidian

## 📝 Описание
Асинхронный Telegram-бот, разработанный для создания персональной базы знаний в Obsidian. В отличие от стандартных решений, этот бот работает полностью автономно(за исключением обработки аудио): использует локальные **Large Language Models (LLM)** для обработки информации и объектное **хранилище S3** вместо облачных дисков. Бот автоматически категоризирует заметки, строит «граф знаний» через умные ссылки [[Wiki-links]] и помогает структурировать поток мыслей.

## ✨ Ключевые функции
- **Автономная обработка (Local LLM):** Использует локальные модели (например, Gemma 3:4b) через API-шлюз для парсинга, категоризации и написания конспектов.
- **Интеграция с S3:** Хранение данных в S3-совместимом хранилище, обеспечивающее полный контроль над файлами.
- **Умная связность (Auto-Linking):** Бот анализирует новые записи в контексте старых и автоматически проставляет Вики-ссылки [[Название]], создавая связанные графы в Obsidian.
- **Работа с данными:** Принимает текст, фото, голосовые сообщения и документы .docx, преобразуя их в структурированный Markdown.
- **Диалог с памятью:** Поиск и ответы на вопросы по личной базе знаний с использованием RAG-подхода (через SQLite).
- **Синхронизация:** Глубокая синхронизация между базой данных бота и облачным хранилищем.

## 🛠 Технологический стек
- **Python** 3.14+
- **aiogram** (Telegram API)
- **SQLite3** (Локальная база логов и напоминаний)
- **Ollama / Local LLM** (Для обработки текста)
- **S3** (Персональное объектное хранилище)
- **APScheduler** (Автоматизация задач и синхронизация)

## 🚀 Установка и запуск

### 1. Клонирование репозитория
Клонируйте репозиторий и создайте файл конфигурации:
```bash
git clone https://github.com/Lasckuer/Obsinotes.git
cd Obsinotes
cp .env.example .env
```

### 2. Настройка окружения
В файле `.env` укажите параметры:
- `BOT_TOKEN`: Токен вашего бота от BotFather.
- `ADMIN_ID`: Ваш Telegram ID для доступа и ежедневных дайджестов.
- `S3_ENDPOINT / S3_ACCESS_KEY / S3_SECRET_KEY / S3_BUCKET_NAME`: Данные вашего S3 хранилища.
- `PROXY_URL`: HTTP/SOCKS5 прокси (если требуется для доступа к Telegram или облачным LLM).

#### Подключение ИИ и выбор модели:
Вы можете использовать любого провайдера и указать любую модель через переменную `AI_MODEL` в `.env`:

- **Google Gemini (Рекомендуется):**
  Получите бесплатный ключ в [Google AI Studio](https://aistudio.google.com/) и укажите:
  ```env
  GEMINI_API_KEY=AIzaSy...
  # Опционально: любая модель (gemini-2.5-flash, gemini-1.5-flash, gemini-2.5-pro, gemini-2.0-flash)
  AI_MODEL=gemini-2.5-flash
  ```
- **OpenAI:**
  ```env
  OPENAI_API_KEY=sk-...
  AI_MODEL=gpt-4o-mini  # или gpt-4o, o3-mini
  ```
- **DeepSeek:**
  ```env
  DEEPSEEK_API_KEY=sk-...
  AI_MODEL=deepseek-chat  # или deepseek-reasoner
  ```
- **Groq / OpenRouter:**
  Укажите соответствующий ключ `GROQ_API_KEY` или `OPENROUTER_API_KEY` и при необходимости `AI_MODEL=...`.
- **Локальная Ollama / Свой шлюз:**
  ```env
  AI_BASE_URL=http://localhost:11434/v1
  AI_API_KEY=ollama
  AI_MODEL=llama3:8b
  ```
- `GROQ_API_KEY`: Быстрая и бесплатная транскрибация аудио через Groq Whisper ([console.groq.com](https://console.groq.com/)).


### 3. Установка зависимостей
```bash
python -m venv venv
source venv/bin/activate  # Для Linux/macOS
# venv\Scripts\activate   # Для Windows
pip install -r requirements.txt
```

### 4. Запуск тестов
```bash
pytest
```

### 5. Запуск бота
```bash
python main.py
```
