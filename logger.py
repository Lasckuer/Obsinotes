import logging
import sys

class ProxyErrorFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if "ProxyConnectionError" in msg or "Couldn't connect to proxy" in msg:
            record.msg = "⚠️ Прокси-сервер временно недоступен. Ожидание переподключения..."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.levelname = "WARNING"
            record.levelno = logging.WARNING
        return True

def setup_logger():
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('yadisk').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    logging.getLogger('aiogram').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('openai').setLevel(logging.WARNING)

    log = logging.getLogger()
    log.setLevel(logging.INFO)

    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if log.hasHandlers():
        log.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ProxyErrorFilter())
    log.addHandler(console_handler)

    file_handler = logging.FileHandler("bot_log.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.addFilter(ProxyErrorFilter())
    log.addHandler(file_handler)

    return logging.getLogger("obsidian_bot")

logger = setup_logger()

def log_db_init():
    logger.info("Инициализация базы данных...")

def log_s3_init():
    logger.info("Подключение к S3 (SeaweedFS)...")

def log_scheduler_start():
    logger.info("Запуск планировщика задач...")

def log_webhook_drop():
    logger.info("Очистка старых обновлений Telegram...")

def log_bot_start():
    logger.info("Бот успешно запущен и ждет сообщений! 🚀")

def log_bot_stop():
    logger.info("Бот остановлен пользователем.")

def log_reminder_sent(r_id, user_id):
    logger.info(f"Напоминание {r_id} отправлено пользователю {user_id}")

def log_reminder_error(r_id, error):
    logger.error(f"Ошибка при отправке напоминания {r_id}: {error}")

def log_user_start(user_id):
    logger.info(f"Пользователь {user_id} запустил бота")

def log_llm_retry(attempt, max_retries, wait_time):
    logger.warning(f"Лимит ИИ. Попытка {attempt}/{max_retries}. Ждем {wait_time} сек...")

def log_llm_error(error):
    logger.error(f"Ошибка при запросе к ИИ: {error}")