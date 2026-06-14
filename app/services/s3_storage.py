import os
import aioboto3
from botocore.client import Config
from logger import log_llm_error

class S3StorageService:
    def __init__(self):
        self.session = aioboto3.Session()
        self.endpoint_url = os.getenv("S3_ENDPOINT")
        self.access_key = os.getenv("S3_ACCESS_KEY")
        self.secret_key = os.getenv("S3_SECRET_KEY")
        self.bucket_name = os.getenv("S3_BUCKET_NAME", "obsidian")

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
        try:
            async with self._get_client() as s3:
                try:
                    await s3.head_bucket(Bucket=self.bucket_name)
                except Exception:
                    await s3.create_bucket(Bucket=self.bucket_name)
        except Exception as e:
            log_llm_error(f"{e}")

    async def upload_file(self, folder: str, filename: str, content):
        key = f"{folder}/{filename}" if folder else filename
        key = key.replace("//", "/")
        try:
            if isinstance(content, str):
                content = content.encode('utf-8')
            async with self._get_client() as s3:
                await s3.put_object(Bucket=self.bucket_name, Key=key, Body=content)
        except Exception as e:
            log_llm_error(f"{e}")

    async def get_files(self, folder: str) -> list:
        prefix = f"{folder}/" if folder else ""
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
            log_llm_error(f"{e}")
        return files

    async def get_all_files(self) -> list:
        files = []
        try:
            async with self._get_client() as s3:
                paginator = s3.get_paginator('list_objects_v2')
                async for result in paginator.paginate(Bucket=self.bucket_name):
                    for item in result.get('Contents', []):
                        key = item['Key']
                        if not key.endswith('/'):
                            files.append(key)
        except Exception as e:
            log_llm_error(f"{e}")
        return files

    async def download_file(self, folder: str, filename: str) -> bytes:
        key = f"{folder}/{filename}" if folder else filename
        key = key.replace("//", "/")
        try:
            async with self._get_client() as s3:
                response = await s3.get_object(Bucket=self.bucket_name, Key=key)
                return await response['Body'].read()
        except Exception as e:
            log_llm_error(f"{e}")
            return b""
            
    async def download_file_by_key(self, key: str) -> bytes:
        try:
            async with self._get_client() as s3:
                response = await s3.get_object(Bucket=self.bucket_name, Key=key)
                return await response['Body'].read()
        except Exception as e:
            log_llm_error(f"{e}")
            return b""