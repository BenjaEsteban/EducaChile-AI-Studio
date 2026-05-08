from functools import lru_cache

from app.config import settings
from app.providers.storage.base import StorageProvider
from app.providers.storage.minio_provider import MinIOStorageProvider


@lru_cache(maxsize=1)
def _get_provider() -> StorageProvider:
    """Instancia única del provider (singleton por proceso)."""
    if settings.STORAGE_BACKEND.lower() == "azure":
        from app.providers.storage.azure_blob_provider import AzureBlobStorageProvider

        return AzureBlobStorageProvider(
            connection_string=settings.AZURE_STORAGE_CONNECTION_STRING,
            container_name=settings.AZURE_STORAGE_CONTAINER,
            public_base_url=settings.AZURE_STORAGE_PUBLIC_BASE_URL,
        )
    return MinIOStorageProvider()


def get_storage() -> StorageProvider:
    """Dependencia FastAPI. Inyectar con Depends(get_storage)."""
    return _get_provider()
