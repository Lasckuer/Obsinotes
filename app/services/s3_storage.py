import os
import aioboto3
import traceback
from botocore.client import Config
from logger import log_llm_error

class S3StorageService:
    def __init__(self):
        self.session = aioboto3.Session()
        self.endpoint_url = os.getenv("S3_ENDPOINT")
        self.access_key = os.getenv("S3_ACCESS_KEY")
        self.secret_key = os.getenv("S3_SECRET_KEY")
        self.bucket_name = os.getenv("S3_BUCKET_NAME", "obsidian")
        
        self.base_path = "TelegramBot"

    def _get_client(self):
        return self.session.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=Config(
                s3={'addressing_style': 'path'},
                proxies={'http': None, 'https': None} 
            )
        )

    async def init_folders(self):
        """Проверяет наличие бакета в SeaweedFS и создает его при необходимости"""
        try:
            async with self._get_client() as s3:
                try:
                    await s3.head_bucket(Bucket=self.bucket_name)
                except Exception:
                    await s3.create_bucket(Bucket=self.bucket_name)
        except Exception as e:
            print("=== ОШИБКА ИНИЦИАЛИЗАЦИИ БАКЕТА S3 ===")
            traceback.print_exc()
            log_llm_error(f"Ошибка инициализации S3 бакета: {e}")

    async def upload_file(self, folder: str, filename: str, content):
        """Загружает файл в S3"""
        key = f"{self.base_path}/{folder}/{filename}"
        try:
            if isinstance(content, str):
                content = content.encode('utf-8')

            async with self._get_client() as s3:
                await s3.put_object(Bucket=self.bucket_name, Key=key, Body=content)
        except Exception as e:
            print("=== ОШИБКА ЗАГРУЗКИ ФАЙЛА В S3 ===")
            traceback.print_exc()
            log_llm_error(f"Ошибка загрузки файла в S3: {e}")

    async def get_files(self, folder: str) -> list:
        """Возвращает список файлов из конкретной директории"""
        prefix = f"{self.base_path}/{folder}/"
        files = []
        try:
            async with self._get_client() as s3:
                paginator = s3.get_paginator('list_objects_v2')
                async for result in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                    for item in result.get('Contents', []):
                        key = item['Key']
                        if key != prefix:
                            files.append(key.replace(prefix, ""))
        except Exception as e:
            print("=== ОШИБКА ПОЛУЧЕНИЯ СПИСКА ФАЙЛОВ S3 ===")
            traceback.print_exc()
            log_llm_error(f"Ошибка получения списка файлов: {e}")
        return files

    async def download_file(self, folder: str, filename: str) -> bytes:
        """Скачивает файл из S3"""
        key = f"{self.base_path}/{folder}/{filename}"
        try:
            async with self._get_client() as s3:
                response = await s3.get_object(Bucket=self.bucket_name, Key=key)
                return await response['Body'].read()
        except Exception as e:
            print("=== ОШИБКА СКАЧИВАНИЯ ФАЙЛА S3 ===")
            traceback.print_exc()
            log_llm_error(f"Ошибка скачивания файла: {e}")
            return b""