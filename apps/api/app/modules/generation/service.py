import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.modules.generation.models import GenerationJob, VideoGenerationSettings
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
from app.modules.jobs.models import Job, JobStatus, JobType
from app.modules.jobs.repository import JobRepository
from app.modules.projects.models import PresentationStatus
from app.modules.projects.service import MOCK_ORG_ID
from app.providers.storage import get_storage
from app.utils.crypto import decrypt_secret, encrypt_secret
from app.workers.tasks import enqueue_generate_video


class GenerationReadinessError(HTTPException):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": code, "message": message},
        )


class GenerationService:
    def __init__(
        self,
        repo: GenerationRepository,
        job_repo: JobRepository,
    ) -> None:
        self.repo = repo
        self.job_repo = job_repo

    def start(self, project_id: uuid.UUID) -> StartGenerationResponse:
        self._validate_readiness(project_id)
        project = self.repo.get_project(project_id, MOCK_ORG_ID)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        job = self.job_repo.create(
            Job(
                organization_id=MOCK_ORG_ID,
                project_id=project_id,
                job_type=JobType.generate_video,
                status=JobStatus.queued,
                progress=0.0,
                current_step="Queued",
            )
        )
        generation_job = self.repo.create_generation_job(
            GenerationJob(
                organization_id=MOCK_ORG_ID,
                project_id=project_id,
                job_id=job.id,
                status="queued",
                progress_percentage=0.0,
                current_step="Queued",
            )
        )
        job.celery_task_id = enqueue_generate_video(
            job_id=job.id,
            generation_job_id=generation_job.id,
            project_id=project_id,
        )
        self.job_repo.save(job)
        return StartGenerationResponse(
            generation_job=GenerationJobRead.model_validate(generation_job),
            job_id=job.id,
        )

    def get_job(self, project_id: uuid.UUID, generation_job_id: uuid.UUID) -> GenerationJobRead:
        generation_job = self.repo.get_generation_job(
            project_id=project_id,
            generation_job_id=generation_job_id,
            org_id=MOCK_ORG_ID,
        )
        if generation_job is None:
            raise HTTPException(status_code=404, detail="Generation job not found")
        return GenerationJobRead.model_validate(generation_job)

    def get_status(self, project_id: uuid.UUID) -> GenerationStatusRead:
        generation_job = self.repo.get_latest_generation_job(project_id, MOCK_ORG_ID)
        if generation_job is None:
            return GenerationStatusRead(
                status="idle",
                progress=0.0,
                current_slide=None,
                total_slides=None,
                message=None,
                error_code=None,
                error_message=None,
                final_video_url=None,
            )

        final_video_url = None
        if generation_job.status == "completed":
            asset = self.repo.get_latest_final_video(project_id, MOCK_ORG_ID)
            if asset is not None:
                final_video_url = get_storage().generate_presigned_download_url(
                    asset.storage_key
                ).url

        return GenerationStatusRead(
            status=generation_job.status,
            progress=generation_job.progress_percentage,
            current_slide=generation_job.current_slide,
            total_slides=generation_job.total_slides,
            message=generation_job.current_step,
            error_code=generation_job.error_code,
            error_message=generation_job.error_message,
            final_video_url=final_video_url,
        )

    def final_video(self, project_id: uuid.UUID) -> FinalVideoRead:
        asset = self.repo.get_latest_final_video(project_id, MOCK_ORG_ID)
        if asset is None:
            return FinalVideoRead(
                ready=False,
                asset_id=None,
                url=None,
                storage_key=None,
                mime_type=None,
                size_bytes=None,
            )
        url = get_storage().generate_presigned_download_url(asset.storage_key).url
        return FinalVideoRead(
            ready=True,
            asset_id=asset.id,
            url=url,
            storage_key=asset.storage_key,
            mime_type=asset.mime_type,
            size_bytes=asset.size_bytes,
        )

    def get_video_settings(self, project_id: uuid.UUID) -> VideoSettingsRead:
        project = self.repo.get_project(project_id, MOCK_ORG_ID)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        return self._settings_read(self.repo.get_video_settings(project_id, MOCK_ORG_ID))

    def update_video_settings(
        self,
        project_id: uuid.UUID,
        data: VideoSettingsUpdate,
    ) -> VideoSettingsRead:
        project = self.repo.get_project(project_id, MOCK_ORG_ID)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        settings = self.repo.get_video_settings(project_id, MOCK_ORG_ID)
        if settings is None:
            settings = VideoGenerationSettings(
                organization_id=MOCK_ORG_ID,
                project_id=project_id,
                validation_status="not_configured",
            )

        elevenlabs_key = (data.elevenlabs_api_key or "").strip()
        if elevenlabs_key:
            settings.elevenlabs_api_key_encrypted = encrypt_secret(elevenlabs_key)
            settings.elevenlabs_api_key_last_four = elevenlabs_key[-4:]
            settings.elevenlabs_valid = False

        wavespeed_key = (data.wavespeed_api_key or "").strip()
        if wavespeed_key:
            settings.wavespeed_api_key_encrypted = encrypt_secret(wavespeed_key)
            settings.wavespeed_api_key_last_four = wavespeed_key[-4:]
            settings.wavespeed_valid = False

        if data.elevenlabs_voice_id is not None:
            settings.elevenlabs_voice_id = data.elevenlabs_voice_id.strip() or None
            settings.elevenlabs_valid = False

        settings.validation_status = (
            "saved"
            if (
                settings.elevenlabs_api_key_encrypted
                and settings.elevenlabs_voice_id
                and settings.wavespeed_api_key_encrypted
            )
            else "not_configured"
        )
        return self._settings_read(self.repo.save_video_settings(settings))

    def validate_video_settings(self, project_id: uuid.UUID) -> VideoSettingsValidationRead:
        settings = self.repo.get_video_settings(project_id, MOCK_ORG_ID)
        if settings is None:
            raise GenerationReadinessError(
                "VIDEO_SETTINGS_NOT_CONFIGURED",
                "Video settings must be saved before validation",
            )

        elevenlabs_key = decrypt_secret(settings.elevenlabs_api_key_encrypted)
        wavespeed_key = decrypt_secret(settings.wavespeed_api_key_encrypted)
        settings.elevenlabs_valid = _is_valid_key(elevenlabs_key) and bool(
            settings.elevenlabs_voice_id
        )
        settings.wavespeed_valid = _is_valid_key(wavespeed_key)
        settings.last_validated_at = datetime.now(UTC)
        settings.validation_status = (
            "valid" if settings.elevenlabs_valid and settings.wavespeed_valid else "invalid"
        )
        saved = self.repo.save_video_settings(settings)
        message = (
            "Credentials are valid"
            if saved.validation_status == "valid"
            else "Credentials are incomplete or invalid"
        )
        return VideoSettingsValidationRead(
            **self._settings_read(saved).model_dump(),
            message=message,
        )

    def _validate_readiness(self, project_id: uuid.UUID) -> None:
        project = self.repo.get_project(project_id, MOCK_ORG_ID)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")
        presentation = self.repo.get_latest_presentation(project_id)
        if presentation is None or presentation.status != PresentationStatus.parsed:
            raise GenerationReadinessError("PRESENTATION_NOT_PARSED", "PPT must be parsed first")
        if not presentation.slides:
            raise GenerationReadinessError("NO_SLIDES", "Presentation has no parsed slides")
        missing_dialogue = [
            slide.position
            for slide in presentation.slides
            if not ((slide.metadata_ or {}).get("dialogue") or slide.notes)
        ]
        if missing_dialogue:
            raise GenerationReadinessError(
                "MISSING_DIALOGUE",
                f"Slides missing dialogue: {missing_dialogue}",
            )
        missing_canvas = [
            slide.position
            for slide in presentation.slides
            if not (slide.metadata_ or {}).get("canvas")
        ]
        if missing_canvas:
            raise GenerationReadinessError(
                "MISSING_CANVAS_STATE",
                f"Slides missing editable canvas state: {missing_canvas}",
            )

        settings = self.repo.get_video_settings(project_id, MOCK_ORG_ID)
        if settings is None:
            raise GenerationReadinessError(
                "VIDEO_SETTINGS_NOT_CONFIGURED",
                "Video settings must be saved before generation",
            )
        if not settings.elevenlabs_api_key_encrypted:
            raise GenerationReadinessError(
                "MISSING_ELEVENLABS_API_KEY",
                "ElevenLabs API key is required",
            )
        if not settings.elevenlabs_voice_id:
            raise GenerationReadinessError(
                "MISSING_ELEVENLABS_VOICE_ID",
                "ElevenLabs voice ID is required",
            )
        if not settings.wavespeed_api_key_encrypted:
            raise GenerationReadinessError(
                "MISSING_WAVESPEED_API_KEY",
                "WaveSpeed API key is required",
            )
        if not settings.elevenlabs_valid:
            raise GenerationReadinessError(
                "INVALID_ELEVENLABS_CREDENTIALS",
                "ElevenLabs credentials must be validated before generation",
            )
        if not settings.wavespeed_valid:
            raise GenerationReadinessError(
                "INVALID_WAVESPEED_CREDENTIALS",
                "WaveSpeed credentials must be validated before generation",
            )

    def _settings_read(
        self,
        settings: VideoGenerationSettings | None,
    ) -> VideoSettingsRead:
        if settings is None:
            return VideoSettingsRead(
                elevenlabs_api_key_masked=None,
                elevenlabs_voice_id=None,
                wavespeed_api_key_masked=None,
                elevenlabs_valid=False,
                wavespeed_valid=False,
                validation_status="not_configured",
                last_validated_at=None,
                updated_at=None,
            )
        return VideoSettingsRead(
            elevenlabs_api_key_masked=_mask(settings.elevenlabs_api_key_last_four),
            elevenlabs_voice_id=settings.elevenlabs_voice_id,
            wavespeed_api_key_masked=_mask(settings.wavespeed_api_key_last_four),
            elevenlabs_valid=settings.elevenlabs_valid,
            wavespeed_valid=settings.wavespeed_valid,
            validation_status=settings.validation_status,  # type: ignore[arg-type]
            last_validated_at=settings.last_validated_at,
            updated_at=settings.updated_at,
        )


def _mask(last_four: str | None) -> str | None:
    return f"************{last_four}" if last_four else None


def _is_valid_key(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return len(value.strip()) >= 8 and not any(
        marker in lowered for marker in ("invalid", "expired", "revoked")
    )
