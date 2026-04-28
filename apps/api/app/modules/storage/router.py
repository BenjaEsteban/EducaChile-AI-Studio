import uuid

from fastapi import APIRouter, Depends

from app.modules.projects.service import MOCK_ORG_ID, MOCK_USER_ID  # noqa: F401
from app.modules.storage.schemas import (
    PresignedDownloadResponse,
    PresignedUploadRequest,
    PresignedUploadResponse,
)
from app.modules.storage.service import StorageService
from app.providers.storage import StorageProvider, get_storage

router = APIRouter(prefix="/storage", tags=["storage"])

# Mock project_id para pruebas — reemplazar con parámetro real cuando exista auth
_STUB_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")


def get_service(provider: StorageProvider = Depends(get_storage)) -> StorageService:
    return StorageService(provider)


@router.post("/presigned-upload", response_model=PresignedUploadResponse)
def presigned_upload(
    body: PresignedUploadRequest,
    service: StorageService = Depends(get_service),
):
    """Genera una URL firmada (PUT) para que el cliente suba un archivo directamente a MinIO.

    El cliente debe hacer PUT a la URL devuelta con el archivo en el body
    y el header `Content-Type` que indicó en el request.
    """
    result = service.presigned_upload(
        request=body,
        org_id=MOCK_ORG_ID,
        project_id=_STUB_PROJECT_ID,
    )
    return PresignedUploadResponse(
        url=result.url,
        key=result.key,
        expires_in=result.expires_in,
    )


@router.get("/presigned-download", response_model=PresignedDownloadResponse)
def presigned_download(
    key: str,
    expires_in: int = 3600,
    service: StorageService = Depends(get_service),
):
    """Genera una URL firmada (GET) para descargar un objeto por su key."""
    result = service.presigned_download(key=key, expires_in=expires_in)
    return PresignedDownloadResponse(
        url=result.url,
        key=result.key,
        expires_in=result.expires_in,
    )
