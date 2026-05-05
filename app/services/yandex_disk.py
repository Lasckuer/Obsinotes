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

    async def upload_file(self, path: str, filename: str, content: bytes):
    full_path = f"{path}/{filename}"
    try:
        if not await self.y.exists(path):
            await self.y.mkdir(path)
        
        await self.y.upload(io.BytesIO(content), full_path, overwrite=True)
    except Exception as e:
        log_llm_error(f"Ошибка загрузки на Диск: {e}")

    async def get_files(self, folder: str):
        path = f"{self.base_path}/{folder}"
        files = []
        async for item in self.y.listdir(path):
            if item.type == "file":
                files.append(item.name)
        return files

    async def download_file(self, folder: str, filename: str) -> bytes:
        path = f"{self.base_path}/{folder}/{filename}"
        stream = io.BytesIO()
        await self.y.download(path, stream)
        return stream.getvalue()
