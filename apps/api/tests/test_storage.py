import uuid
import types
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.storage import get_storage
from tests.fakes import InMemoryStorageProvider

BASE = "/api/v1/storage"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def storage_client():
    provider = InMemoryStorageProvider()
    app.dependency_overrides[get_storage] = lambda: provider
    with TestClient(app) as c:
        yield c, provider
    app.dependency_overrides.pop(get_storage, None)


# ── POST /storage/presigned-upload ────────────────────────────────────────────

def test_presigned_upload_returns_url(storage_client):
    client, _ = storage_client
    res = client.post(BASE + "/presigned-upload", json={
        "filename": "slides.pptx",
        "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    })
    assert res.status_code == 200
    body = res.json()
    assert body["url"].startswith("http://minio-test/upload/")
    assert "slides.pptx" in body["key"]
    assert body["method"] == "PUT"
    assert body["expires_in"] == 3600


def test_presigned_upload_custom_expiry(storage_client):
    client, _ = storage_client
    res = client.post(
        BASE + "/presigned-upload",
        json={"filename": "video.mp4", "expires_in": 7200},
    )
    assert res.status_code == 200
    assert res.json()["expires_in"] == 7200


def test_presigned_upload_key_contains_org_and_project(storage_client):
    client, _ = storage_client
    res = client.post(BASE + "/presigned-upload", json={"filename": "test.pdf"})
    key = res.json()["key"]
    parts = key.split("/")
    assert len(parts) == 3
    assert parts[2].endswith("test.pdf")


def test_presigned_upload_rejects_path_traversal(storage_client):
    client, _ = storage_client
    res = client.post(BASE + "/presigned-upload", json={"filename": "../etc/passwd"})
    assert res.status_code == 422


def test_presigned_upload_rejects_absolute_path(storage_client):
    client, _ = storage_client
    res = client.post(BASE + "/presigned-upload", json={"filename": "/etc/passwd"})
    assert res.status_code == 422


def test_presigned_upload_rejects_empty_filename(storage_client):
    client, _ = storage_client
    assert client.post(BASE + "/presigned-upload", json={"filename": ""}).status_code == 422


def test_presigned_upload_rejects_expiry_too_short(storage_client):
    client, _ = storage_client
    res = client.post(
        BASE + "/presigned-upload",
        json={"filename": "f.pdf", "expires_in": 10},
    )
    assert res.status_code == 422


def test_presigned_upload_rejects_expiry_too_long(storage_client):
    client, _ = storage_client
    res = client.post(
        BASE + "/presigned-upload",
        json={"filename": "f.pdf", "expires_in": 999999},
    )
    assert res.status_code == 422


# ── GET /storage/presigned-download ──────────────────────────────────────────

def test_presigned_download_returns_url(storage_client):
    client, provider = storage_client
    key = f"{uuid.uuid4()}/test.pdf"
    provider.upload_file(key, b"content", "application/pdf")
    res = client.get(BASE + "/presigned-download", params={"key": key})
    assert res.status_code == 200
    body = res.json()
    assert body["url"].startswith("http://minio-test/download/")
    assert body["key"] == key
    assert body["method"] == "GET"


def test_presigned_download_not_found_returns_404(storage_client):
    client, _ = storage_client
    res = client.get(
        BASE + "/presigned-download",
        params={"key": "nonexistent/file.pdf"},
    )
    assert res.status_code == 404


# ── InMemoryStorageProvider unit tests ────────────────────────────────────────

def test_provider_upload_and_download():
    provider = InMemoryStorageProvider()
    provider.upload_file("org/proj/file.txt", b"hello", "text/plain")
    assert provider.download_file("org/proj/file.txt") == b"hello"


def test_provider_delete_removes_file():
    provider = InMemoryStorageProvider()
    provider.upload_file("k", b"data", "text/plain")
    provider.delete_file("k")
    with pytest.raises(FileNotFoundError):
        provider.download_file("k")


def test_provider_download_missing_raises():
    with pytest.raises(FileNotFoundError):
        InMemoryStorageProvider().download_file("missing")


def test_provider_delete_missing_raises():
    with pytest.raises(FileNotFoundError):
        InMemoryStorageProvider().delete_file("missing")


def test_provider_presigned_download_missing_raises():
    with pytest.raises(FileNotFoundError):
        InMemoryStorageProvider().generate_presigned_download_url("missing")


def test_storage_factory_returns_minio_by_default(monkeypatch):
    import app.providers.storage as storage_module

    sentinel = object()
    storage_module._get_provider.cache_clear()
    monkeypatch.setattr("app.providers.storage.settings.STORAGE_BACKEND", "minio")
    monkeypatch.setattr(storage_module, "MinIOStorageProvider", lambda: sentinel)

    assert storage_module.get_storage() is sentinel
    storage_module._get_provider.cache_clear()


def test_storage_factory_returns_azure(monkeypatch):
    import app.providers.storage as storage_module

    class FakeAzureProvider:
        def __init__(self, connection_string, container_name, public_base_url):
            self.connection_string = connection_string
            self.container_name = container_name
            self.public_base_url = public_base_url

    fake_module = types.ModuleType("app.providers.storage.azure_blob_provider")
    fake_module.AzureBlobStorageProvider = FakeAzureProvider
    storage_module._get_provider.cache_clear()
    monkeypatch.setitem(sys.modules, "app.providers.storage.azure_blob_provider", fake_module)
    monkeypatch.setattr("app.providers.storage.settings.STORAGE_BACKEND", "azure")
    monkeypatch.setattr(
        "app.providers.storage.settings.AZURE_STORAGE_CONNECTION_STRING",
        "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=secret;EndpointSuffix=core.windows.net",
    )
    monkeypatch.setattr("app.providers.storage.settings.AZURE_STORAGE_CONTAINER", "assets")
    monkeypatch.setattr(
        "app.providers.storage.settings.AZURE_STORAGE_PUBLIC_BASE_URL",
        "https://test.blob.core.windows.net",
    )

    provider = storage_module.get_storage()

    assert isinstance(provider, FakeAzureProvider)
    assert provider.container_name == "assets"
    storage_module._get_provider.cache_clear()


def test_azure_storage_service_upload_download_and_sas(monkeypatch):
    from app.providers.storage.azure_blob_provider import AzureBlobStorageProvider

    uploaded = {}

    class FakeDownloader:
        def readall(self):
            return b"downloaded"

    class FakeBlobClient:
        def upload_blob(self, data, overwrite, content_settings):
            uploaded["data"] = data
            uploaded["overwrite"] = overwrite
            uploaded["content_type"] = content_settings.content_type

        def download_blob(self):
            return FakeDownloader()

        def exists(self):
            return True

    class FakeContainerClient:
        def create_container(self):
            return None

        def get_blob_client(self, key):
            uploaded["key"] = key
            return FakeBlobClient()

        def delete_blob(self, key):
            uploaded["deleted"] = key

    class FakeBlobServiceClient:
        @classmethod
        def from_connection_string(cls, connection_string):
            uploaded["connection_string"] = connection_string
            return cls()

        def get_container_client(self, container_name):
            uploaded["container_name"] = container_name
            return FakeContainerClient()

    class FakeContentSettings:
        def __init__(self, content_type):
            self.content_type = content_type

    class FakeBlobSasPermissions:
        def __init__(self, read=False, write=False, create=False):
            self.read = read
            self.write = write
            self.create = create

    fake_blob_module = types.ModuleType("azure.storage.blob")
    fake_blob_module.BlobServiceClient = FakeBlobServiceClient
    fake_blob_module.ContentSettings = FakeContentSettings
    fake_blob_module.BlobSasPermissions = FakeBlobSasPermissions
    fake_blob_module.generate_blob_sas = lambda **kwargs: "sig=fake"
    fake_azure_module = types.ModuleType("azure")
    fake_storage_module = types.ModuleType("azure.storage")
    fake_storage_module.blob = fake_blob_module
    fake_azure_module.storage = fake_storage_module
    monkeypatch.setitem(sys.modules, "azure", fake_azure_module)
    monkeypatch.setitem(sys.modules, "azure.storage", fake_storage_module)
    monkeypatch.setitem(sys.modules, "azure.storage.blob", fake_blob_module)

    service = AzureBlobStorageProvider(
        connection_string=(
            "DefaultEndpointsProtocol=https;AccountName=test;AccountKey=secret;"
            "EndpointSuffix=core.windows.net"
        ),
        container_name="assets",
        public_base_url="https://test.blob.core.windows.net",
    )

    service.upload_bytes("file.txt", b"hello", "text/plain")

    assert uploaded["data"] == b"hello"
    assert uploaded["content_type"] == "text/plain"
    assert service.download_bytes("file.txt") == b"downloaded"
    assert "sig=fake" in service.generate_read_url("file.txt")
