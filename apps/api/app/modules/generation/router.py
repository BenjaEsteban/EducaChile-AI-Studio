import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.generation.repository import GenerationRepository
from app.modules.generation.schemas import (
    FinalVideoRead,
    GenerationJobRead,
    GenerationStatusRead,
    StartGenerationResponse,
    VideoSettingsRead,
    VideoSettingsUpdate,
    VideoSettingsValidationRead,
)
from app.modules.generation.service import GenerationService
from app.modules.jobs.repository import JobRepository

router = APIRouter(prefix="/projects", tags=["generation"])


def get_service(db: Session = Depends(get_db)) -> GenerationService:
    return GenerationService(
        repo=GenerationRepository(db),
        job_repo=JobRepository(db),
    )


@router.get("/{project_id}/video-settings", response_model=VideoSettingsRead)
def get_video_settings(
    project_id: uuid.UUID,
    service: GenerationService = Depends(get_service),
):
    return service.get_video_settings(project_id)


@router.put("/{project_id}/video-settings", response_model=VideoSettingsRead)
def update_video_settings(
    project_id: uuid.UUID,
    data: VideoSettingsUpdate,
    service: GenerationService = Depends(get_service),
):
    return service.update_video_settings(project_id, data)


@router.post(
    "/{project_id}/video-settings/validate",
    response_model=VideoSettingsValidationRead,
)
def validate_video_settings(
    project_id: uuid.UUID,
    service: GenerationService = Depends(get_service),
):
    return service.validate_video_settings(project_id)


@router.post("/{project_id}/generate-video", response_model=StartGenerationResponse)
def start_video_generation(
    project_id: uuid.UUID,
    service: GenerationService = Depends(get_service),
):
    return service.start(project_id)


@router.get("/{project_id}/generation-jobs/{job_id}", response_model=GenerationJobRead)
def get_generation_job(
    project_id: uuid.UUID,
    job_id: uuid.UUID,
    service: GenerationService = Depends(get_service),
):
    return service.get_job(project_id=project_id, generation_job_id=job_id)


@router.get("/{project_id}/generation-status", response_model=GenerationStatusRead)
def get_generation_status(
    project_id: uuid.UUID,
    service: GenerationService = Depends(get_service),
):
    return service.get_status(project_id)


@router.get("/{project_id}/final-video", response_model=FinalVideoRead)
def get_final_video(
    project_id: uuid.UUID,
    service: GenerationService = Depends(get_service),
):
    return service.final_video(project_id)
