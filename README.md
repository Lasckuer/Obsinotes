# 🧠 NodeNotes: Telegram «Второй мозг» для Obsidian

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%20%7C%203.14-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python Version" />
  <img src="https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" alt="aiogram 3.x" />
  <img src="https://img.shields.io/badge/Obsidian-Compatible-7C3AED?style=for-the-badge&logo=obsidian&logoColor=white" alt="Obsidian" />
  <img src="https://img.shields.io/badge/Storage-S3%20%2F%20MinIO-E44D26?style=for-the-badge&logo=amazons3&logoColor=white" alt="S3 Storage" />
  <img src="https://img.shields.io/badge/LLM-Gemini%20%7C%20OpenAI%20%7C%20Ollama-00A67E?style=for-the-badge&logo=openai&logoColor=white" alt="Pluggable LLM" />
  <img src="https://img.shields.io/badge/Tests-148%20Passed-brightgreen?style=for-the-badge" alt="Tests Status" />
</p>

---

## 📖 О проекте

**NodeNotes** — это многофункциональный асинхронный Telegram-бот, превращающий ваш Telegram в полноценный персональный интерфейс для базы знаний **Obsidian**.

Бот автоматически принимает мысли, ссылки, голосовые сообщения, фотографии и документы, структурирует их с помощью нейросетей, проставляет теги, связывает между собой вики-ссылками `[[Wiki-links]]` и мгновенно сохраняет в ваше персональное S3-хранилище (SeaweedFS, MinIO, Cloudflare R2, AWS S3).

Вся база заметок синхронизируется в локальный полнотекстовый индекс SQLite (FTS5), позволяя мгновенно находить нужные записи и общаться со своей памятью через **RAG (Retrieval-Augmented Generation)**.

---

## ✨ Ключевые возможности

### 📱 Умный захват информации (Quick Capture)
- **Текстовые мысли и заметки:** автоматическое выделение сути, форматирование в Markdown, подбор тегов и категоризация (`Notes`, `Ideas`, `Finance`, `Reminders`).
- **Голосовые сообщения:** мгновенная транскрибация через **Groq Whisper** с автоматическим превращением аудио в структурированный конспект.
- **Веб-ссылки:** автоматический скрапинг содержимого веб-страниц, выделение главного текста и сохранение конспекта со ссылкой на первоисточник.
- **Фотографии и документы:** загрузка изображений в хранилище с авто-вставкой `![[image.jpg]]`, парсинг и конспектирование файлов `.pdf`, `.docx`, `.txt`, `.md`.
- **Авто-линковка графа знаний:** бот анализирует существующие заметки и автоматически добавляет релевантные вики-ссылки вида `[[Название заметки]]`.

### 🔍 Полнотекстовый поиск и сниппеты
- Индексация заметок в SQLite с поддержкой **FTS5** и резервным поиском через `LIKE`.
- Автоматическая очистка разговорных стоп-слов (*«найди заметки про...»* -> поиск по ключевым сущностям).
- Отображение **сниппетов контекста** в результатах поиска (сразу видно, в каком абзаце встретилось совпадение).
- Инлайн-кнопки для моментального скачивания `.md` файлов или открытия прямо в приложении Obsidian через `obsidian://open`.

### 🤖 Диалог с базой знаний (RAG-ассистент)
- Задавайте вопросы по своим записям на естественном языке (*«Что я планировал по ремонту?»*, *«Какие дозировки у кленбутерола?»*).
- Потоковый вывод ответа (streaming) в реальном времени.
- **Цитирование первоисточников:** бот указывает список использованных заметок и прикрепляет кнопки для их скачивания.

### 📅 Ежедневник и учет расходов
- **Ежедневник (Daily Notes):** быстрая запись задач и мыслей напрямую в файл сегодняшнего дня (`Daily/YYYY-MM-DD.md`) с отметкой времени.
- **Учет расходов:** автоматическое распознавание сумм в тексте и ведение ежемесячного файла `Finance/Расходы {MM-YYYY}.md`, сводка за день и месяц по команде `/expenses`.
- **Напоминания:** авто-распознавание времени и периодичности (*«каждый час»*, *«завтра в 15:00»*) с доставкой уведомлений в Telegram.

### 🔌 Подключаемый ИИ (Pluggable AI)
- Поддержка любых провайдеров через OpenAI-совместимый протокол:
  - **Google Gemini** (`gemini-2.5-flash`, `gemini-2.0-flash`, `gemini-1.5-flash`)
  - **OpenAI** (`gpt-4o-mini`, `gpt-4o`, `o3-mini`)
  - **DeepSeek** (`deepseek-chat`, `deepseek-reasoner`)
  - **Groq** (`llama-3.3-70b-versatile`)
  - **OpenRouter** (мульти-провайдер)
  - **Ollama / Local LLM** (`qwen2.5`, `llama3`, `mistral`)
- Возможность указать **абсолютно любую модель** через `AI_MODEL=...` в `.env`.
- Полная поддержка **HTTP и SOCKS5 прокси** с автоматической нормализацией форматов для обхода геоблокировок из РФ.

---

## 🕹️ Интерфейс и Главное меню

Бот оснащен удобной сеткой клавиатуры 2×4:

```
┌───────────────────────────┬───────────────────────────┐
│     📝 Новая заметка      │     🔍 Поиск заметок      │
├───────────────────────────┼───────────────────────────┤
│       🤖 Спросить ИИ      │     🏷️ Каталог тегов     │
├───────────────────────────┼───────────────────────────┤
│       📅 Ежедневник       │        💰 Расходы         │
├───────────────────────────┼───────────────────────────┤
│    🔄 Синхронизация S3    │    ⚙️ Статус и справка    │
└───────────────────────────┴───────────────────────────┘
```

| Кнопка / Команда | Описание |
|---|---|
| `📝 Новая заметка` | Ожидает текст, аудио, фото, ссылку или документ для обработки нейросетью |
| `🔍 Поиск заметок` | Полнотекстовый поиск по ключевым словам, тегам и содержимому файлов |
| `🤖 Спросить ИИ` | Вопрос к вашему «Второму мозгу» с цитированием заметок |
| `🏷️ Каталог тегов` (`/tags`) | Интерактивный список всех тегов базы с количеством заметок |
| `📅 Ежедневник` (`/daily`) | Добавление записи с временной меткой в Daily Note сегодняшнего дня |
| `💰 Расходы` (`/expenses`) | Финансовый отчет: сумма трат за сегодня и за текущий месяц |
| `🔄 Синхронизация S3` (`/sync`) | Ручной запуск дельта-синхронизации базы данных с S3-хранилищем |
| `⚙️ Статус и справка` (`/status`, `/help`) | Сводка по системе + кнопка **`🧪 Проверить связь с ИИ`** (тестовый пинг) |
| `/cancel` (`🔙 В главное меню`) | Сброс текущего состояния и возврат в главное меню |

---

## 🚀 Быстрый старт

### 1. Клонирование репозитория
```bash
git clone https://github.com/Lasckuer/Obsinotes.git
cd Obsinotes
```

### 2. Настройка виртуального окружения
```bash
# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Установка зависимостей
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Конфигурация файла окружения `.env`
Скопируйте шаблон:
```bash
cp .env.example .env
```
Откройте файл `.env` в любом текстовом редакторе и настройте параметры (подробное описание ниже).

### 5. Запуск тестов
Убедитесь, что все компоненты функционируют исправно:
```bash
pytest -v
```
*(Все 148 тестов должны завершиться успешно)*.

### 6. Запуск бота
```bash
python main.py
```

---

## ⚙️ Настройка файла `.env`

### Основные настройки Telegram и Безопасности
```env
# Токен бота от @BotFather
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz

# Ваш Telegram ID (узнать можно у @userinfobot) для прав администратора и дайджестов
ADMIN_ID=123456789

# Разрешенные пользователи (через запятую). Если пусто — доступ только у ADMIN_ID
ALLOWED_USERS=123456789,987654321

# Прокси (HTTP или SOCKS5) для доступа к Telegram и облачным ИИ из РФ
# Поддерживаются форматы:
#   http://user:password@ip:port
#   socks5://user:password@ip:port
#   ip:port:user:password
PROXY_URL=http://user:pass@185.100.1.1:8080

# URL Redis для FSM-состояний (опционально, если нет — используется MemoryStorage)
# REDIS_URL=redis://localhost:6379/0
```

---

### Хранилище S3 (SeaweedFS / MinIO / AWS S3 / Cloudflare R2)
```env
S3_ENDPOINT=https://s3.your-domain.com
S3_ACCESS_KEY=your_access_key
S3_SECRET_KEY=your_secret_key
S3_BUCKET_NAME=obsidian
OBSIDIAN_VAULT_NAME=Notes
S3_SYNC_CONCURRENCY=5
```

---

### Настройка ИИ (LLM) — выбор провайдера и модели

Вы можете выбрать любого удобного провайдера и при желании указать точную модель через переменную `AI_MODEL`.

#### Вариант 1: Google Gemini (Рекомендуется)
Бесплатный API-ключ можно получить в [Google AI Studio](https://aistudio.google.com/).
```env
GEMINI_API_KEY=AIzaSy...ваш_ключ...

# Выберите одну из официальных моделей (по умолчанию: gemini-2.5-flash):
AI_MODEL=gemini-2.5-flash        # Быстрая, современная, гибридные рассуждения
# AI_MODEL=gemini-2.0-flash      # Новейшая стабильная модель
# AI_MODEL=gemini-2.0-flash-lite # Самая легкая и быстрая модель
# AI_MODEL=gemini-1.5-flash      # Проверенная стабильная рабочая лошадка
# AI_MODEL=gemini-1.5-pro        # Флагманская модель для глубокого анализа
```
> [!IMPORTANT]
> Если вы находитесь в РФ, для работы с Google Gemini обязательно укажите зарубежный `PROXY_URL` в `.env`, так как Google блокирует прямые запросы с российских IP.

#### Вариант 2: OpenAI
```env
OPENAI_API_KEY=sk-...
AI_MODEL=gpt-4o-mini  # или gpt-4o, o3-mini
```

#### Вариант 3: DeepSeek
```env
DEEPSEEK_API_KEY=sk-...
AI_MODEL=deepseek-chat  # или deepseek-reasoner
```

#### Вариант 4: Groq
```env
GROQ_API_KEY=gsk_...
AI_MODEL=llama-3.3-70b-versatile
```

#### Вариант 5: OpenRouter (Единый мульти-шлюз)
```env
OPENROUTER_API_KEY=sk-or-v1-...
AI_MODEL=google/gemini-2.5-flash
```

#### Вариант 6: Локальная Ollama / Собственный сервер
```env
AI_PROVIDER=ollama
AI_BASE_URL=http://localhost:11434/v1
AI_MODEL=qwen2.5:14b  # или llama3:8b, mistral
AI_API_KEY=ollama
```

---

### Распознавание аудиосообщений (Groq Whisper)
Быстрая и бесплатная транскрибация голосовых сообщений через Whisper Large v3:
```env
# Бесплатный ключ на https://console.groq.com/
GROQ_API_KEY=gsk_...
GROQ_MODEL=whisper-large-v3
```

---

## 🔗 Интеграция с Obsidian

Для полноценной синхронизации хранилища между ботом и вашими устройствами (ПК, iOS, Android):

1. **Плагин Remotely Save в Obsidian:**
   - Установите комьюнити-плагин **Remotely Save** в Obsidian.
   - Выберите метод синхронизации **S3**.
   - Укажите те же `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` и `S3_BUCKET_NAME`.
   - Включите авто-синхронизацию при запуске и каждые несколько минут.
2. **Мгновенное открытие заметок:**
   - При создании заметки бот присылает кнопку **«🔗 Открыть в Obsidian»**.
   - Нажатие на неё запускает протокол `obsidian://open?vault=...&file=...` и открывает свежую заметку прямо в установленном приложении Obsidian на вашем компьютере или смартфоне!

---

## 🐳 Запуск через Docker Compose

Если вы разворачиваете бота на сервере, удобнее всего использовать Docker:

```bash
# Сборка и запуск в фоновом режиме
docker compose up -d --build

# Просмотр логов работы
docker compose logs -f bot

# Остановка
docker compose down
```

---

## 🛠️ Запуск в качестве Systemd-сервиса (Linux)

Создайте файл сервиса:
```bash
sudo nano /etc/systemd/system/obsinotes.service
```

Вставьте конфигурацию:
```ini
[Unit]
Description=Obsinotes Telegram Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/Obsinotes
ExecStart=/home/ubuntu/Obsinotes/.venv/bin/python main.py
Restart=always
RestartSec=10
EnvironmentFile=/home/ubuntu/Obsinotes/.env

[Install]
WantedBy=multi-user.target
```

Активируйте и запустите:
```bash
sudo systemctl daemon-reload
sudo systemctl enable obsinotes
sudo systemctl start obsinotes
sudo systemctl status obsinotes
```

---

## 🧪 Тестирование и качество кода

В репозитории реализован полный набор модульных и интеграционных тестов с изоляцией базы данных и моками сетевых запросов:

```bash
# Запуск тестов
pytest

# Запуск с детальным отчетом
pytest -v

# Проверка компиляции байткода
python -m compileall app main.py
```

---

## 📄 Лицензия

Проект распространяется под свободной лицензией **MIT**. Подробности в файле [LICENSE](LICENSE).
