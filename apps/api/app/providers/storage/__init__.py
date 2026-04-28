from functools import lru_cache

from app.providers.storage.base import StorageProvider
from app.providers.storage.minio_provider import MinIOStorageProvider


@lru_cache(maxsize=1)
def _get_provider() -> StorageProvider:
    """Instancia única del provider (singleton por proceso)."""
    return MinIOStorageProvider()


def get_storage() -> StorageProvider:
    """Dependencia FastAPI. Inyectar con Depends(get_storage)."""
    return _get_provider()
