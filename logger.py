import logging
import sys
from typing import Optional

class ProxyErrorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "ProxyConnectionError" in msg or "Couldn't connect to proxy" in msg:
            record.msg = "⚠️ Прокси-сервер временно недоступен. Ожидание переподключения..."
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.levelname = "WARNING"
            record.levelno = logging.WARNING
        return True

def setup_logger(log_file: Optional[str] = None, log_level: Optional[str] = None) -> logging.Logger:
    try:
        from app.config import get_settings
        settings = get_settings()
        file_path = log_file or settings.LOG_FILE
        level_name = (log_level or settings.LOG_LEVEL).upper()
    except Exception:
        file_path = log_file or "bot_log.log"
        level_name = (log_level or "INFO").upper()

    numeric_level = getattr(logging, level_name, logging.INFO)

    # Silence verbose 3rd party loggers
    for noisy in (
        'httpx', 'apscheduler', 'aiogram', 'httpcore', 'openai',
        'boto3', 'botocore', 's3transfer', 'urllib3'
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    formatter = logging.Formatter(
        fmt='%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Console stdout handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(ProxyErrorFilter())
    root_logger.addHandler(console_handler)

    # File handler
    try:
        file_handler = logging.FileHandler(file_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(ProxyErrorFilter())
        root_logger.addHandler(file_handler)
    except Exception as e:
        console_handler.handle(
            logging.LogRecord(
                name="logger",
                level=logging.WARNING,
                pathname=__file__,
                lineno=50,
                msg=f"Не удалось открыть файл лога '{file_path}': {e}",
                args=(),
                exc_info=None,
            )
        )

    return logging.getLogger("obsidian_bot")

logger = setup_logger()

# Helper logging functions
def log_db_init():
    logger.info("Инициализация базы данных...")

def log_s3_init():
    logger.info("Подключение к S3 (SeaweedFS)...")

def log_scheduler_start():
    logger.info("Запуск планировщика задач...")

def log_bot_start():
    logger.info("Бот успешно запущен и ждет сообщений! 🚀")

def log_reminder_sent(r_id, user_id):
    logger.info(f"Напоминание {r_id} отправлено пользователю {user_id}")
    
def log_redis_off_info():
    logger.info("Redis не подключен. Используется MemoryStorage для хранения состояния бота...")
    
def log_redis_on_info():
    logger.info("Redis подключен и используется для хранения состояния бота...")
    
def log_reminder_cycled(r_id, next_time_str):
    logger.info(f"Цикличное напоминание {r_id} перенесено на {next_time_str}")

def log_reminder_error(r_id, error):
    logger.error(f"Ошибка при отправке напоминания {r_id}: {error}")

def log_user_start(user_id):
    logger.info(f"Пользователь {user_id} запустил бота.")
    
def log_llm_retry(*args, **kwargs):
    logger.warning(f"Повторная попытка запроса к LLM... {args}")
    
def log_llm_error(error_msg):
    logger.error(f"🧠 [LLM ERROR] Ошибка генерации: {error_msg}")

def log_s3_error(error_msg):
    logger.error(f"☁️ [S3 ERROR] Ошибка работы с хранилищем: {error_msg}")

def log_db_error(error_msg):
    logger.error(f"💾 [DB ERROR] Ошибка базы данных: {error_msg}")