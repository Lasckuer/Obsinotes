# 🧠 Obsinotes: Telegram «Второй мозг» для Obsidian

## 📝 Описание
Асинхронный Telegram-бот на базе **aiogram**, созданный для бесшовной интеграции с базой знаний Obsidian через Яндекс.Диск. Бот использует **SQLite** для локального хранения контекста, а также LLM для умной категоризации, генерации названий файлов и создания кратких конспектов из сложных документов.

## ✨ Ключевые функции
- **Умный Inbox (LLM):** Автоматическая категоризация заметок, генерация тегов и коротких латинских имен файлов через Groq API (Llama-3.1).
- **Анализ документов:** Извлечение текста из PDF и Word, создание Markdown-конспектов (Summary) с сохранением ссылки на оригинал.
- **Диалог с базой знаний:** Бот отвечает на вопросы, опираясь на историю последних сохраненных записей (через SQLite).
- **Obsidian Canvas:** Автоматическая генерация `.canvas` файлов со всеми заметками за день для визуализации связей.

## 🛠 Технологический стек
- **Python** 3.14+
- **aiogram**
- **SQLite3** 
- **Groq API** (Модель llama-3.1-8b-instant для парсинга и ответов)
- **PyMuPDF & python-docx**

## 🚀 Установка и запуск

### 1. Клонирование репозитория
Клонируйте репозиторий и создайте файл конфигурации:
```bash
git clone https://github.com/Lasckuer/Obsinotes.git
cd Obsinotes
cp .env.example .env
```

### 2. Настройка окружения
Скопируйте пример файла конфигурации и заполните его:
```bash
cp .env.example .env
```
В файле `.env` укажите:
- `BOT_TOKEN`: Токен вашего бота от BotFather.
- `AI_API_KEY`: Ключ API от Groq.
- `YANDEX_TOKEN`: OAuth токен Яндекс.Диска.
- `PROXY_URL=http://login`: IP, Port, Login, Password

### 3. Установка зависимостей
```bash
python -m venv venv
source venv/bin/activate  # Для Linux/macOS
# venv\Scripts\activate   # Для Windows
pip install -r requirements.txt
```

### 4. Запуск бота
```bash
python main.py
```