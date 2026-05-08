from abc import ABC, abstractmethod
from typing import BinaryIO
from dataclasses import dataclass


@dataclass
class UploadedFile:
    key: str
    bucket: str
    size_bytes: int
    content_type: str


@dataclass
class PresignedURL:
    url: str
    key: str
    expires_in: int  # segundos


class StorageProvider(ABC):
    """Interfaz de almacenamiento de objetos.

    Todas las implementaciones deben ser stateless respecto al bucket:
    el bucket se configura en el constructor y no se expone en los métodos.
    Los archivos nunca son públicos — el acceso siempre es vía URL firmada.
    """

    @abstractmethod
    def upload_file(
        self,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> UploadedFile:
        """Sube bytes al storage y devuelve metadata del objeto creado."""

    def upload_bytes(
        self,
        object_key: str,
        data: bytes,
        content_type: str,
        overwrite: bool = True,
    ) -> str:
        self.upload_file(object_key, data, content_type)
        return object_key

    def upload_stream(
        self,
        object_key: str,
        stream: BinaryIO,
        content_type: str,
        overwrite: bool = True,
    ) -> str:
        self.upload_file(object_key, stream.read(), content_type)
        return object_key

    @abstractmethod
    def download_file(self, key: str) -> bytes:
        """Descarga el contenido de un objeto como bytes."""

    def download_bytes(self, object_key: str) -> bytes:
        return self.download_file(object_key)

    @abstractmethod
    def generate_presigned_upload_url(
        self,
        key: str,
        content_type: str = "application/octet-stream",
        expires_in: int = 3600,
    ) -> PresignedURL:
        """Genera una URL firmada para que el cliente suba directamente al storage."""

    @abstractmethod
    def generate_presigned_download_url(
        self,
        key: str,
        expires_in: int = 3600,
    ) -> PresignedURL:
        """Genera una URL firmada para que el cliente descargue un objeto."""

    def generate_read_url(
        self,
        object_key: str,
        expires_minutes: int = 60,
    ) -> str:
        return self.generate_presigned_download_url(
            object_key,
            expires_in=expires_minutes * 60,
        ).url

    @abstractmethod
    def generate_external_download_url(
        self,
        key: str,
        expires_in: int = 3600,
    ) -> PresignedURL:
        """Genera una URL firmada accesible por proveedores externos."""

    @abstractmethod
    def delete_file(self, key: str) -> None:
        """Elimina un objeto del storage."""

    def delete(self, object_key: str) -> None:
        self.delete_file(object_key)

    def exists(self, object_key: str) -> bool:
        try:
            self.download_file(object_key)
        except FileNotFoundError:
            return False
        return True
