from app.providers.storage.base import PresignedURL, StorageProvider, UploadedFile


class InMemoryStorageProvider(StorageProvider):
    """Implementación en memoria que satisface la interfaz sin red ni MinIO."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[bytes, str]] = {}

    def upload_file(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> UploadedFile:
        self._store[key] = (data, content_type)
        return UploadedFile(key=key, bucket="test", size_bytes=len(data), content_type=content_type)

    def download_file(self, key: str) -> bytes:
        if key not in self._store:
            raise FileNotFoundError(key)
        return self._store[key][0]

    def generate_presigned_upload_url(
        self, key: str, content_type: str = "application/octet-stream", expires_in: int = 3600
    ) -> PresignedURL:
        return PresignedURL(url=f"http://minio-test/upload/{key}", key=key, expires_in=expires_in)

    def generate_presigned_download_url(
        self, key: str, expires_in: int = 3600
    ) -> PresignedURL:
        if key not in self._store:
            raise FileNotFoundError(key)
        return PresignedURL(url=f"http://minio-test/download/{key}", key=key, expires_in=expires_in)

    def delete_file(self, key: str) -> None:
        if key not in self._store:
            raise FileNotFoundError(key)
        del self._store[key]
