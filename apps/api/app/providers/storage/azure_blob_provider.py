from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import BinaryIO
from urllib.parse import urlparse

from app.providers.storage.base import PresignedURL, StorageProvider, UploadedFile

logger = logging.getLogger(__name__)


class AzureBlobStorageProvider(StorageProvider):
    def __init__(
        self,
        connection_string: str | None,
        container_name: str,
        public_base_url: str | None = None,
    ) -> None:
        if not connection_string:
            raise RuntimeError(
                "AZURE_STORAGE_CONNECTION_STRING is required when STORAGE_BACKEND=azure"
            )
        if not container_name:
            raise RuntimeError("AZURE_STORAGE_CONTAINER is required when STORAGE_BACKEND=azure")

        from azure.storage.blob import BlobServiceClient

        self._connection_string = connection_string
        self._container_name = container_name
        self._account_name = _connection_string_value(connection_string, "AccountName")
        self._account_key = _connection_string_value(connection_string, "AccountKey")
        if not self._account_name or not self._account_key:
            raise RuntimeError(
                "Azure service SAS generation requires AccountName and AccountKey "
                "in AZURE_STORAGE_CONNECTION_STRING"
            )
        self._public_base_url = (
            public_base_url.rstrip("/")
            if public_base_url
            else f"https://{self._account_name}.blob.core.windows.net"
        )
        self._service_client = BlobServiceClient.from_connection_string(connection_string)
        self._container_client = self._service_client.get_container_client(container_name)
        self._ensure_container()

    def _ensure_container(self) -> None:
        try:
            self._container_client.create_container()
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceExistsError":
                return
            raise

    def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> UploadedFile:
        self.upload_bytes(key, data, content_type, overwrite=True)
        return UploadedFile(
            key=key,
            bucket=self._container_name,
            size_bytes=len(data),
            content_type=content_type,
        )

    def upload_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str,
        overwrite: bool = True,
    ) -> str:
        from azure.storage.blob import ContentSettings

        blob_client = self._container_client.get_blob_client(object_key)
        blob_client.upload_blob(
            data,
            overwrite=overwrite,
            content_settings=ContentSettings(content_type=content_type),
        )
        logger.debug("Uploaded Azure blob '%s' (%d bytes)", object_key, len(data))
        return object_key

    def upload_stream(
        self,
        object_key: str,
        stream: BinaryIO,
        content_type: str,
        overwrite: bool = True,
    ) -> str:
        from azure.storage.blob import ContentSettings

        blob_client = self._container_client.get_blob_client(object_key)
        blob_client.upload_blob(
            stream,
            overwrite=overwrite,
            content_settings=ContentSettings(content_type=content_type),
        )
        logger.debug("Uploaded Azure blob stream '%s'", object_key)
        return object_key

    def download_file(self, key: str) -> bytes:
        return self.download_bytes(key)

    def download_bytes(self, object_key: str) -> bytes:
        try:
            blob_client = self._container_client.get_blob_client(object_key)
            return blob_client.download_blob().readall()
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundError":
                raise FileNotFoundError(f"Object not found: {object_key}") from exc
            raise

    def generate_presigned_upload_url(
        self,
        key: str,
        content_type: str = "application/octet-stream",
        expires_in: int = 3600,
    ) -> PresignedURL:
        url = self._generate_sas_url(key, expires_in=expires_in, write=True)
        return PresignedURL(url=url, key=key, expires_in=expires_in)

    def generate_presigned_download_url(
        self,
        key: str,
        expires_in: int = 3600,
    ) -> PresignedURL:
        url = self._generate_sas_url(key, expires_in=expires_in, write=False)
        return PresignedURL(url=url, key=key, expires_in=expires_in)

    def generate_external_download_url(
        self,
        key: str,
        expires_in: int = 3600,
    ) -> PresignedURL:
        return self.generate_presigned_download_url(key, expires_in=expires_in)

    def generate_read_url(self, object_key: str, expires_minutes: int = 60) -> str:
        return self.generate_presigned_download_url(
            object_key,
            expires_in=expires_minutes * 60,
        ).url

    def delete_file(self, key: str) -> None:
        self.delete(key)

    def delete(self, object_key: str) -> None:
        try:
            self._container_client.delete_blob(object_key)
        except Exception as exc:
            if exc.__class__.__name__ == "ResourceNotFoundError":
                return
            raise

    def exists(self, object_key: str) -> bool:
        blob_client = self._container_client.get_blob_client(object_key)
        return bool(blob_client.exists())

    def _generate_sas_url(self, key: str, expires_in: int, write: bool) -> str:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        permissions = BlobSasPermissions(read=True, write=write, create=write)
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        token = generate_blob_sas(
            account_name=self._account_name,
            container_name=self._container_name,
            blob_name=key,
            account_key=self._account_key,
            permission=permissions,
            expiry=expires_at,
        )
        return f"{self._blob_base_url(key)}?{token}"

    def _blob_base_url(self, key: str) -> str:
        parsed = urlparse(self._public_base_url)
        if parsed.path.strip("/").endswith(self._container_name):
            return f"{self._public_base_url}/{key}"
        return f"{self._public_base_url}/{self._container_name}/{key}"


def _connection_string_value(connection_string: str, name: str) -> str | None:
    prefix = f"{name}="
    for part in connection_string.split(";"):
        if part.startswith(prefix):
            return part[len(prefix):]
    return None
