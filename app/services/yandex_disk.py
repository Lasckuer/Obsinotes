import yadisk
import io
import os
from logger import log_llm_error

class YaDiskService:
    def __init__(self):
        self.y = yadisk.AsyncClient(token=os.getenv("YADISK_TOKEN"))
        self.base_path = "/Notes/TelegramBot"
        self.folders = ["Notes", "Workouts", "Attachments", "Ideas", "Links", "Reminders", "Finance"]
        
    async def init_folders(self):
        """Рекурсивно создает структуру папок при запуске[cite: 8]"""
        try:
            if not await self.y.check_token():
                raise ValueError("Неверный токен Яндекс.Диска")

            path_parts = self.base_path.split('/')
            current_path = ""
            for part in path_parts:
                if not part: continue
                current_path += f"/{part}"
                if not await self.y.exists(current_path):
                    await self.y.mkdir(current_path)

            for folder in self.folders:
                full_path = f"{self.base_path}/{folder}"
                if not await self.y.exists(full_path):
                    await self.y.mkdir(full_path)
        except Exception as e:
            log_llm_error(f"Ошибка инициализации папок: {e}")

    async def upload_file(self, folder: str, filename: str, content: bytes):
        """Загружает файл в Notes/TelegramBot/{folder}[cite: 12]"""
        full_dir_path = f"{self.base_path}/{folder}"
        full_file_path = f"{full_dir_path}/{filename}"
        
        try:
            if not await self.y.exists(full_dir_path):
                await self.y.mkdir(full_dir_path)
            
            await self.y.upload(io.BytesIO(content), full_file_path, overwrite=True)
        except Exception as e:
            log_llm_error(f"Ошибка при загрузке на Yandex.Disk: {e}")

    async def get_files(self, folder: str):
        """Возвращает список файлов из Notes/TelegramBot/{folder}[cite: 12]"""
        path = f"{self.base_path}/{folder}"
        files = []
        try:
            if not await self.y.exists(path):
                return []
            async for item in self.y.listdir(path):
                if item.type == "file":
                    files.append(item.name)
        except Exception as e:
            log_llm_error(f"Ошибка получения списка файлов: {e}")
        return files

    async def download_file(self, folder: str, filename: str):
        """Скачивает файл из Notes/TelegramBot/{folder}[cite: 12]"""
        path = f"{self.base_path}/{folder}/{filename}"
        try:
            out = io.BytesIO()
            await self.y.download(path, out)
            return out.getvalue()
        except Exception as e:
            log_llm_error(f"Ошибка при скачивании файла: {e}")
            return None