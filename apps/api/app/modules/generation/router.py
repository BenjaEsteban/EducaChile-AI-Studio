import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.generation.debug import (
    list_generation_debug_assets,
    run_elevenlabs_debug,
    run_ffmpeg_debug,
    run_wavespeed_debug,
)
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


@router.post("/{project_id}/debug/elevenlabs-test")
def debug_elevenlabs_test(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    return run_elevenlabs_debug(project_id, db)


@router.post("/{project_id}/debug/wavespeed-test")
def debug_wavespeed_test(
    project_id: uuid.UUID,
    avatar_source_url: str | None = None,
    db: Session = Depends(get_db),
):
    return run_wavespeed_debug(project_id, db, avatar_source_url=avatar_source_url)


@router.post("/{project_id}/debug/ffmpeg-compose-test")
def debug_ffmpeg_compose_test(project_id: uuid.UUID):
    return run_ffmpeg_debug(project_id)


@router.get("/{project_id}/debug/generation-assets")
def debug_generation_assets(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    return list_generation_debug_assets(project_id, db)
