import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.services.s3_storage import S3StorageService

@pytest.fixture
def s3_service():
    return S3StorageService(
        endpoint_url="http://localhost:8333",
        access_key="fake_key",
        secret_key="fake_secret",
        bucket_name="test-bucket"
    )

def test_s3_is_configured(s3_service):
    assert s3_service.is_configured() is True

    unconfigured = S3StorageService(endpoint_url=None, access_key=None, secret_key=None)
    assert unconfigured.is_configured() is False

@pytest.mark.asyncio
async def test_init_folders(s3_service):
    mock_client = AsyncMock()
    mock_client.head_bucket = AsyncMock(return_value={})

    with patch.object(s3_service, "get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client
        res = await s3_service.init_folders()
        assert res is True
        mock_client.head_bucket.assert_called_once_with(Bucket="test-bucket")

@pytest.mark.asyncio
async def test_upload_file(s3_service):
    mock_client = AsyncMock()
    mock_client.put_object = AsyncMock(return_value={})

    with patch.object(s3_service, "get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client
        res = await s3_service.upload_file("Notes", "test.md", "hello world")
        assert res is True
        mock_client.put_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="Notes/test.md",
            Body=b"hello world"
        )

@pytest.mark.asyncio
async def test_download_file(s3_service):
    mock_body = AsyncMock()
    mock_body.read = AsyncMock(return_value=b"# My Note")
    mock_client = AsyncMock()
    mock_client.get_object = AsyncMock(return_value={"Body": mock_body})

    with patch.object(s3_service, "get_client") as mock_get_client:
        mock_get_client.return_value.__aenter__.return_value = mock_client
        content = await s3_service.download_file("Notes", "test.md")
        assert content == b"# My Note"

@pytest.mark.asyncio
async def test_batch_download(s3_service):
    async def fake_download(key):
        return f"content of {key}".encode("utf-8")

    with patch.object(s3_service, "download_file_by_key", side_effect=fake_download):
        keys = ["Notes/1.md", "Notes/2.md", "Notes/3.md"]
        res = await s3_service.batch_download(keys, max_concurrency=2)
        assert len(res) == 3
        assert res["Notes/1.md"] == b"content of Notes/1.md"
        assert res["Notes/2.md"] == b"content of Notes/2.md"
        assert res["Notes/3.md"] == b"content of Notes/3.md"
