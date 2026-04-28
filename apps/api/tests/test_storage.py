import uuid

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
    res = client.post(BASE + "/presigned-upload", json={"filename": "video.mp4", "expires_in": 7200})
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
    assert client.post(BASE + "/presigned-upload", json={"filename": "../etc/passwd"}).status_code == 422


def test_presigned_upload_rejects_absolute_path(storage_client):
    client, _ = storage_client
    assert client.post(BASE + "/presigned-upload", json={"filename": "/etc/passwd"}).status_code == 422


def test_presigned_upload_rejects_empty_filename(storage_client):
    client, _ = storage_client
    assert client.post(BASE + "/presigned-upload", json={"filename": ""}).status_code == 422


def test_presigned_upload_rejects_expiry_too_short(storage_client):
    client, _ = storage_client
    assert client.post(BASE + "/presigned-upload", json={"filename": "f.pdf", "expires_in": 10}).status_code == 422


def test_presigned_upload_rejects_expiry_too_long(storage_client):
    client, _ = storage_client
    assert client.post(BASE + "/presigned-upload", json={"filename": "f.pdf", "expires_in": 999999}).status_code == 422


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
    assert client.get(BASE + "/presigned-download", params={"key": "nonexistent/file.pdf"}).status_code == 404


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
