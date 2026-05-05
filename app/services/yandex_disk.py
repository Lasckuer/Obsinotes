import yadisk
import io
import os

class YaDiskService:
    def __init__(self):
        self.y = yadisk.AsyncClient(token=os.getenv("YADISK_TOKEN"))
        self.base_path = "/Obsidian"
        self.folders = ["Ideas", "Reminders", "Notes", "Attachments", "Links", "Workouts", "Finance"]

    async def init_folders(self):
        if not await self.y.check_token():
            raise ValueError("Invalid Yandex Disk Token")
        
        if not await self.y.exists(self.base_path):
            await self.y.mkdir(self.base_path)

        for folder in self.folders:
            path = f"{self.base_path}/{folder}"
            if not await self.y.exists(path):
                await self.y.mkdir(path)

   import io
from logger import log_llm_error

class YaDiskService:
    def __init__(self):
        self.y = yadisk.AsyncClient(token=os.getenv("YADISK_TOKEN"))
        self.base_path = "/Obsidian"
        self.folders = ["Ideas", "Reminders", "Notes", "Attachments", "Links", "Workouts", "Finance"]

    async def init_folders(self):
        if not await self.y.check_token():
            raise ValueError("Invalid Yandex Disk Token")
        
        if not await self.y.exists(self.base_path):
            await self.y.mkdir(self.base_path)

        for folder in self.folders:
            path = f"{self.base_path}/{folder}"
            if not await self.y.exists(path):
                await self.y.mkdir(path)

    async def upload_file(self, path: str, filename: str, content: bytes):
        """Загружает файл на Яндекс.Диск, создавая папки при необходимости"""
        full_path = f"{path}/{filename}"
        
        try:
            parts = path.split('/')
            current_path = ""
            for part in parts:
                if not part: continue
                current_path += f"/{part}"
                if not await self.y.exists(current_path):
                    await self.y.mkdir(current_path)
            
            await self.y.upload(io.BytesIO(content), full_path, overwrite=True)
            
        except Exception as e:
            log_llm_error(f"Ошибка при загрузке на Yandex.Disk: {e}")

    async def get_files(self, path: str):
        """Возвращает список имен файлов в указанной директории"""
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

    async def download_file(self, path: str, filename: str):
        """Скачивает файл и возвращает его содержимое в байтах"""
        full_path = f"{path}/{filename}"
        try:
            out = io.BytesIO()
            await self.y.download(full_path, out)
            return out.getvalue()
        except Exception as e:
            log_llm_error(f"Ошибка при скачивании файла: {e}")
            return None
