import asyncio
from typing import List, Dict, Optional, Union, Any
from contextlib import asynccontextmanager
import aioboto3
from botocore.client import Config
from app.config import get_settings
from logger import log_s3_error, logger

_SENTINEL = object()

class S3StorageService:
    def __init__(
        self,
        endpoint_url: Any = _SENTINEL,
        access_key: Any = _SENTINEL,
        secret_key: Any = _SENTINEL,
        bucket_name: Any = _SENTINEL
    ):
        settings = get_settings()
        self.endpoint_url = settings.S3_ENDPOINT if endpoint_url is _SENTINEL else endpoint_url
        self.access_key = settings.S3_ACCESS_KEY if access_key is _SENTINEL else access_key
        self.secret_key = settings.S3_SECRET_KEY if secret_key is _SENTINEL else secret_key
        self.bucket_name = settings.S3_BUCKET_NAME if bucket_name is _SENTINEL else (bucket_name or settings.S3_BUCKET_NAME)

        self.session = aioboto3.Session()
        self._config = Config(
            s3={'addressing_style': 'path'},
            proxies={'http': None, 'https': None},
            retries={'max_attempts': 3, 'mode': 'standard'}
        )

    def is_configured(self) -> bool:
        return bool(self.endpoint_url and self.access_key and self.secret_key)

    @asynccontextmanager
    async def get_client(self):
        """Async context manager yielding an S3 client instance."""
        if not self.is_configured():
            logger.warning("S3 хранилище не настроено (проверьте S3_* переменные окружения).")
            raise ValueError("S3 credentials not configured.")

        async with self.session.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=self._config
        ) as client:
            yield client

    def _get_client(self):
        """Backwards-compatibility method returning client context."""
        return self.get_client()

    async def init_folders(self) -> bool:
        """Ensures the target bucket exists, creating it if necessary."""
        if not self.is_configured():
            return False
        try:
            async with self.get_client() as s3:
                try:
                    await s3.head_bucket(Bucket=self.bucket_name)
                except Exception:
                    await s3.create_bucket(Bucket=self.bucket_name)
            return True
        except Exception as e:
            log_s3_error(f"Init bucket error: {e}")
            return False

    init_bucket = init_folders

    async def upload_file(self, folder: str, filename: str, content: Union[str, bytes]) -> bool:
        """Uploads content to S3 bucket at folder/filename."""
        if not self.is_configured():
            return False
        key = f"{folder.strip('/')}/{filename}" if folder else filename
        try:
            body_bytes = content.encode('utf-8') if isinstance(content, str) else content
            async with self.get_client() as s3:
                await s3.put_object(Bucket=self.bucket_name, Key=key, Body=body_bytes)
            return True
        except Exception as e:
            log_s3_error(f"Не удалось загрузить файл {filename}: {e}")
            return False

    async def get_files(self, folder: str) -> List[str]:
        """Lists file keys within a specific folder prefix."""
        if not self.is_configured():
            return []
        prefix = f"{folder.strip('/')}/" if folder else ""
        files = []
        try:
            async with self.get_client() as s3:
                paginator = s3.get_paginator('list_objects_v2')
                async for result in paginator.paginate(Bucket=self.bucket_name, Prefix=prefix):
                    for item in result.get('Contents', []):
                        key = item['Key']
                        if key != prefix:
                            files.append(key[len(prefix):])
        except Exception as e:
            log_s3_error(f"Get files error: {e}")
        return files

    async def get_all_files(self) -> List[str]:
        """Lists all non-directory object keys in the bucket."""
        if not self.is_configured():
            return []
        files = []
        try:
            async with self.get_client() as s3:
                paginator = s3.get_paginator('list_objects_v2')
                async for result in paginator.paginate(Bucket=self.bucket_name):
                    for item in result.get('Contents', []):
                        key = item['Key']
                        if not key.endswith('/'):
                            files.append(key)
        except Exception as e:
            log_s3_error(f"Get all files error: {e}")
        return files

    async def list_files_with_metadata(self, folder: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists all non-directory objects with metadata (key, etag, last_modified, size).
        If folder is specified, filters by prefix.
        """
        if not self.is_configured():
            return []
        prefix = f"{folder.strip('/')}/" if folder else ""
        files: List[Dict[str, Any]] = []
        try:
            async with self.get_client() as s3:
                paginator = s3.get_paginator('list_objects_v2')
                paginate_kwargs: Dict[str, Any] = {"Bucket": self.bucket_name}
                if prefix:
                    paginate_kwargs["Prefix"] = prefix
                async for result in paginator.paginate(**paginate_kwargs):
                    for item in result.get('Contents', []):
                        key = item['Key']
                        if not key.endswith('/'):
                            raw_etag = item.get('ETag', '')
                            etag = raw_etag.strip('"') if isinstance(raw_etag, str) else str(raw_etag)
                            lm = item.get('LastModified')
                            lm_str = lm.isoformat() if hasattr(lm, 'isoformat') else (str(lm) if lm else "")
                            size = item.get('Size', 0)
                            files.append({
                                'key': key,
                                'etag': etag,
                                'last_modified': lm_str,
                                'size': size
                            })
        except Exception as e:
            log_s3_error(f"List files with metadata error: {e}")
        return files

    list_files = list_files_with_metadata

    async def download_file_by_key(self, key: str) -> bytes:
        """Downloads and returns raw bytes of an object by its exact key."""
        if not self.is_configured():
            return b""
        try:
            async with self.get_client() as s3:
                response = await s3.get_object(Bucket=self.bucket_name, Key=key)
                return await response['Body'].read()
        except Exception as e:
            log_s3_error(f"Download by key error '{key}': {e}")
            return b""

    async def download_file(self, folder: str, filename: str) -> bytes:
        """Downloads file at folder/filename."""
        if not folder or folder == "Root":
            key = filename
        else:
            key = f"{folder.strip('/')}/{filename}"
        data = await self.download_file_by_key(key)
        if not data and not key.startswith("TelegramBot/"):
            data = await self.download_file_by_key(f"TelegramBot/{key}")
        return data

    async def batch_download(self, keys: List[str], max_concurrency: int = 5) -> Dict[str, bytes]:
        """
        Downloads multiple files concurrently with bounded parallelism.
        Returns a dict mapping key -> bytes.
        """
        if not self.is_configured() or not keys:
            return {}

        semaphore = asyncio.Semaphore(max_concurrency)
        results: Dict[str, bytes] = {}

        async def _download_one(key: str):
            async with semaphore:
                data = await self.download_file_by_key(key)
                if data:
                    results[key] = data

        tasks = [_download_one(k) for k in keys]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    async def delete_file_by_key(self, key: str) -> bool:
        """Deletes an object by its exact key."""
        if not self.is_configured():
            return False
        try:
            async with self.get_client() as s3:
                await s3.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception as e:
            log_s3_error(f"Delete by key error '{key}': {e}")
            return False

    async def delete_file(self, folder: str, filename: str) -> bool:
        """Deletes file at folder/filename."""
        if not folder or folder == "Root":
            key = filename
        else:
            key = f"{folder.strip('/')}/{filename}"
        res = await self.delete_file_by_key(key)
        if not res and not key.startswith("TelegramBot/"):
            res = await self.delete_file_by_key(f"TelegramBot/{key}")
        return res