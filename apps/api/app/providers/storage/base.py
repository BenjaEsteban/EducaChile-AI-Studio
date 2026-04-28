from abc import ABC, abstractmethod
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

    @abstractmethod
    def download_file(self, key: str) -> bytes:
        """Descarga el contenido de un objeto como bytes."""

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

    @abstractmethod
    def delete_file(self, key: str) -> None:
        """Elimina un objeto del storage."""
