import uuid
from dataclasses import dataclass

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.modules.jobs.models import Job, JobStatus, JobType
from app.modules.jobs.repository import JobRepository
from app.modules.projects.models import Presentation, PresentationStatus
from app.modules.projects.repository import PresentationRepository, ProjectRepository
from app.modules.projects.schemas import (
    ConfirmUploadResponse,
    InitUploadRequest,
    InitUploadResponse,
)
from app.modules.projects.service import MOCK_ORG_ID
from app.modules.storage.service import StorageService
from app.workers.tasks import enqueue_parse_presentation

_PRESIGNED_EXPIRES = 3600  # 1 hora para completar el upload


@dataclass
class _UploadDeps:
    db: Session
    storage: StorageService


class PresentationUploadService:
    def __init__(
        self,
        presentation_repo: PresentationRepository,
        project_repo: ProjectRepository,
        job_repo: JobRepository,
        storage: StorageService,
    ) -> None:
        self._presentations = presentation_repo
        self._projects = project_repo
        self._jobs = job_repo
        self._storage = storage

    # ── init_upload ───────────────────────────────────────────────────────────

    def init_upload(
        self,
        project_id: uuid.UUID,
        request: InitUploadRequest,
    ) -> InitUploadResponse:
        # 1. Verificar que el proyecto existe y pertenece al org del usuario
        project = self._projects.get_by_id(project_id, MOCK_ORG_ID)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # 2. Construir la storage key con jerarquía org/project/file
        storage_key = self._storage.build_key(
            org_id=MOCK_ORG_ID,
            project_id=project_id,
            filename=request.filename,
        )

        # 3. Crear registro Presentation en DB con status upload_pending
        #    antes de generar la URL — si falla la URL, el registro queda
        #    en upload_pending y puede reintentarse.
        presentation = Presentation(
            project_id=project_id,
            organization_id=MOCK_ORG_ID,
            title=request.filename,          # título provisional = nombre del archivo
            original_filename=request.filename,
            storage_key=storage_key,
            status=PresentationStatus.upload_pending,
        )
        presentation = self._presentations.create(presentation)

        # 4. Generar presigned PUT URL (no requiere que el objeto exista)
        presigned = self._storage._provider.generate_presigned_upload_url(
            key=storage_key,
            content_type=request.content_type,
            expires_in=_PRESIGNED_EXPIRES,
        )

        return InitUploadResponse(
            presentation_id=presentation.id,
            upload_url=presigned.url,
            storage_key=storage_key,
            expires_in=_PRESIGNED_EXPIRES,
        )

    # ── upload_file ───────────────────────────────────────────────────────────

    def upload_file(self, presentation_id: uuid.UUID, file: UploadFile) -> None:
        presentation = self._presentations.get_by_id_only(presentation_id)
        if not presentation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Presentation not found",
            )

        if presentation.status != PresentationStatus.upload_pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot upload file: presentation is in status "
                    f"'{presentation.status}', expected 'upload_pending'"
                ),
            )

        content = file.file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded presentation is empty",
            )

        self._storage.upload(
            key=presentation.storage_key,
            data=content,
            content_type=file.content_type or "application/octet-stream",
        )

    # ── confirm_upload ────────────────────────────────────────────────────────

    def confirm_upload(self, presentation_id: uuid.UUID) -> ConfirmUploadResponse:
        # 1. Buscar la presentación
        presentation = self._presentations.get_by_id_only(presentation_id)
        if not presentation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Presentation not found",
            )

        # 2. Solo se puede confirmar desde upload_pending
        if presentation.status != PresentationStatus.upload_pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot confirm upload: presentation is in status "
                    f"'{presentation.status}', expected 'upload_pending'"
                ),
            )

        try:
            self._storage.download(presentation.storage_key)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Cannot confirm upload: presentation file is not available in storage",
                ) from exc
            raise

        # 3. Cambiar status a uploaded
        presentation.status = PresentationStatus.uploaded
        self._presentations.save(presentation)

        # 4. Crear Job de parsing y encolarlo en Celery
        job = Job(
            organization_id=presentation.organization_id,
            project_id=presentation.project_id,
            presentation_id=presentation.id,
            job_type=JobType.parse_presentation,
            status=JobStatus.queued,
        )
        job = self._jobs.create(job)

        # Encolar en Celery — el worker lo procesará cuando esté disponible
        celery_task_id = enqueue_parse_presentation(job.id, presentation.id)
        job.celery_task_id = celery_task_id
        self._jobs.save(job)

        return ConfirmUploadResponse(
            presentation_id=presentation.id,
            status=PresentationStatus.uploaded,
            job_id=job.id,
        )
