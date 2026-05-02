import uuid

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.jobs.repository import JobRepository
from app.modules.presentations.service import PresentationUploadService
from app.modules.projects.repository import PresentationRepository, ProjectRepository
from app.modules.projects.schemas import (
    ConfirmUploadResponse,
    InitUploadRequest,
    InitUploadResponse,
)
from app.modules.storage.service import StorageService
from app.providers.storage import StorageProvider, get_storage

router = APIRouter(tags=["presentations"])


def get_service(
    db: Session = Depends(get_db),
    provider: StorageProvider = Depends(get_storage),
) -> PresentationUploadService:
    return PresentationUploadService(
        presentation_repo=PresentationRepository(db),
        project_repo=ProjectRepository(db),
        job_repo=JobRepository(db),
        storage=StorageService(provider),
    )


@router.post(
    "/projects/{project_id}/presentations/init-upload",
    response_model=InitUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
def init_upload(
    project_id: uuid.UUID,
    body: InitUploadRequest,
    service: PresentationUploadService = Depends(get_service),
):
    """Crea un registro Presentation en estado upload_pending y devuelve
    una presigned PUT URL para que el cliente suba el archivo directamente a MinIO."""
    return service.init_upload(project_id=project_id, request=body)


@router.post(
    "/presentations/{presentation_id}/upload-file",
    status_code=status.HTTP_204_NO_CONTENT,
)
def upload_file(
    presentation_id: uuid.UUID,
    file: UploadFile = File(...),
    service: PresentationUploadService = Depends(get_service),
):
    """Development-friendly upload proxy.

    The app still creates a storage-backed Presentation first, but the browser sends the
    PPT/PPTX to the API instead of directly to the raw MinIO presigned URL.
    """
    service.upload_file(presentation_id=presentation_id, file=file)


@router.post(
    "/presentations/{presentation_id}/confirm-upload",
    response_model=ConfirmUploadResponse,
    status_code=status.HTTP_200_OK,
)
def confirm_upload(
    presentation_id: uuid.UUID,
    service: PresentationUploadService = Depends(get_service),
):
    """Confirma que el archivo fue subido exitosamente.
    Cambia el status a 'uploaded' y encola un Job de tipo parse_presentation."""
    return service.confirm_upload(presentation_id=presentation_id)
