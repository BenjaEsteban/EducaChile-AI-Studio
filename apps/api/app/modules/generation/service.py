from __future__ import annotations

import ipaddress
import logging
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.config import settings as app_settings
from app.modules.generation.models import GenerationJob, VideoGenerationSettings
from app.modules.generation.repository import GenerationRepository
from app.modules.generation.pipeline import resolve_saved_tts_credentials
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
from app.modules.presentations.rendering import render_slide_previews
from app.modules.projects.service import MOCK_ORG_ID
from app.providers.storage import get_storage
from app.utils.crypto import decrypt_secret, encrypt_secret
from app.workers.tasks import enqueue_generate_video

logger = logging.getLogger(__name__)

RUNNING_GENERATION_STATUSES = {
    "queued",
    "validating",
    "generating_audio",
    "generating_avatar",
    "rendering_slides",
    "composing_slide",
    "composing_video",
}
STALLED_ERROR_MESSAGE = (
    "Previous generation job stopped updating. Please try again."
)


class GenerationReadinessError(HTTPException):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": code, "message": message},
        )


class GenerationAlreadyRunningError(HTTPException):
    def __init__(self, generation_job: GenerationJob) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "GENERATION_ALREADY_RUNNING",
                "code": "GENERATION_ALREADY_RUNNING",
                "message": "A video generation job is already running.",
                "job_id": str(generation_job.id),
                "status": generation_job.status,
                "updated_at": (
                    generation_job.updated_at.isoformat()
                    if generation_job.updated_at is not None
                    else None
                ),
            },
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
        logger.info("generate-video request received: project_id=%s", project_id)
        project = self.repo.get_project(project_id, MOCK_ORG_ID)
        if project is None:
            raise HTTPException(status_code=404, detail="Project not found")

        existing_job = self.repo.get_running_generation_job(project_id, MOCK_ORG_ID)
        logger.info(
            "generate-video existing running job before stale check: project_id=%s "
            "existing_job_id=%s existing_job_status=%s existing_job_updated_at=%s",
            project_id,
            existing_job.id if existing_job else None,
            existing_job.status if existing_job else None,
            existing_job.updated_at if existing_job else None,
        )
        self._fail_stale_running_jobs(project_id)
        running_job = self.repo.get_running_generation_job(project_id, MOCK_ORG_ID)
        if running_job is not None:
            logger.info(
                "generate-video blocked by active job: project_id=%s existing_job_id=%s "
                "existing_job_status=%s existing_job_updated_at=%s",
                project_id,
                running_job.id,
                running_job.status,
                running_job.updated_at,
            )
            raise GenerationAlreadyRunningError(running_job)

        self._validate_readiness(project_id)

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
        logger.info(
            "Enqueueing generate_video: project_id=%s generation_job_id=%s "
            "queue=%s status_before_enqueue=%s",
            project_id,
            generation_job.id,
            "generation",
            generation_job.status,
        )
        job.celery_task_id = enqueue_generate_video(
            job_id=job.id,
            generation_job_id=generation_job.id,
            project_id=project_id,
        )
        self.job_repo.save(job)
        logger.info(
            "Enqueued generate_video: project_id=%s generation_job_id=%s "
            "celery_task_id=%s queue=%s status_after_enqueue=%s",
            project_id,
            generation_job.id,
            job.celery_task_id,
            "generation",
            generation_job.status,
        )
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
        self._fail_stale_running_jobs(project_id)
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
            asset = (
                self.repo.get_asset(generation_job.final_asset_id, project_id, MOCK_ORG_ID)
                if generation_job.final_asset_id is not None
                else self.repo.get_latest_final_video(project_id, MOCK_ORG_ID)
            )
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
            updated_at=generation_job.updated_at,
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
        created = settings is None
        if settings is None:
            settings = VideoGenerationSettings(
                organization_id=MOCK_ORG_ID,
                project_id=project_id,
                validation_status="not_configured",
            )

        elevenlabs_received_real, elevenlabs_received_masked = _secret_input_flags(
            data.elevenlabs_api_key
        )
        elevenlabs_key = (data.elevenlabs_api_key or "").strip()
        if _should_replace_secret(data.elevenlabs_api_key):
            settings.elevenlabs_api_key_encrypted = encrypt_secret(elevenlabs_key)
            settings.elevenlabs_api_key_last_four = elevenlabs_key[-4:]
            settings.elevenlabs_valid = False

        wavespeed_received_real, wavespeed_received_masked = _secret_input_flags(
            data.wavespeed_api_key
        )
        wavespeed_key = (data.wavespeed_api_key or "").strip()
        if _should_replace_secret(data.wavespeed_api_key):
            settings.wavespeed_api_key_encrypted = encrypt_secret(wavespeed_key)
            settings.wavespeed_api_key_last_four = wavespeed_key[-4:]
            settings.wavespeed_valid = False

        if data.elevenlabs_voice_id is not None:
            settings.elevenlabs_voice_id = data.elevenlabs_voice_id.strip() or None
            settings.elevenlabs_valid = False
        if data.avatar_source_url is not None:
            settings.avatar_source_url = data.avatar_source_url.strip() or None
        if data.avatar_source_asset_id is not None:
            settings.avatar_source_asset_id = data.avatar_source_asset_id

        settings.validation_status = (
            "saved"
            if (
                settings.elevenlabs_api_key_encrypted
                or settings.elevenlabs_voice_id
                or settings.wavespeed_api_key_encrypted
                or settings.avatar_source_url
                or settings.avatar_source_asset_id
            )
            else "not_configured"
        )
        saved = self.repo.save_video_settings(settings)
        logger.info(
            "Project video settings saved: project_id=%s organization_id=%s "
            "project_config_created_or_updated=%s elevenlabs_api_key_received_real_value=%s "
            "elevenlabs_api_key_received_masked_value=%s elevenlabs_api_key_encrypted_present_after_save=%s "
            "elevenlabs_voice_id_present_after_save=%s wavespeed_api_key_received_real_value=%s "
            "wavespeed_api_key_received_masked_value=%s wavespeed_api_key_encrypted_present_after_save=%s",
            project_id,
            MOCK_ORG_ID,
            "created" if created else "updated",
            elevenlabs_received_real,
            elevenlabs_received_masked,
            bool(saved.elevenlabs_api_key_encrypted),
            bool(saved.elevenlabs_voice_id),
            wavespeed_received_real,
            wavespeed_received_masked,
            bool(saved.wavespeed_api_key_encrypted),
        )
        return self._settings_read(saved)

    def validate_video_settings(self, project_id: uuid.UUID) -> VideoSettingsValidationRead:
        project_config, settings, tts_resolution, wavespeed_key = self._resolve_validation_inputs(
            project_id
        )
        elevenlabs_key = tts_resolution.api_key or decrypt_secret(settings.elevenlabs_api_key_encrypted)
        tts_provider = tts_resolution.provider or (app_settings.TTS_PROVIDER or "none").strip().lower()
        avatar_mode = (app_settings.AVATAR_GENERATION_MODE or "fast_lipsync").strip().lower()
        if tts_provider == "none" and not app_settings.ALLOW_DUMMY_TTS:
            settings.elevenlabs_valid = False
            settings.wavespeed_valid = _is_valid_key(wavespeed_key)
            settings.last_validated_at = datetime.now(UTC)
            settings.validation_status = "invalid"
            saved = self.repo.save_video_settings(settings)
            message = (
                f"{avatar_mode} requires a real TTS provider. Configure TTS_PROVIDER=elevenlabs "
                "or set ALLOW_DUMMY_TTS=true for local development."
            )
            return VideoSettingsValidationRead(
                **self._settings_read(saved).model_dump(),
                message=message,
            )
        if tts_provider == "elevenlabs":
            elevenlabs_configured = bool(tts_resolution.api_key or tts_resolution.voice_id)
            settings.elevenlabs_valid = _is_valid_key(elevenlabs_key) and bool(
                tts_resolution.voice_id or settings.elevenlabs_voice_id
            )
            if not settings.elevenlabs_valid:
                logger.info(
                    "Video settings validation missing ElevenLabs credentials: project_id=%s "
                    "project_config_found=%s elevenlabs_api_key_encrypted_present=%s "
                    "elevenlabs_api_key_decrypt_success=%s elevenlabs_voice_id_present=%s "
                    "credentials_source=%s missing_reason=%s",
                    project_id,
                    project_config is not None,
                    bool(project_config and project_config.elevenlabs_api_key_encrypted),
                    bool(tts_resolution.api_key),
                    bool(tts_resolution.voice_id or settings.elevenlabs_voice_id),
                    tts_resolution.credentials_source,
                    _elevenlabs_missing_reason(
                        project_config,
                        settings,
                        tts_resolution.api_key,
                        tts_resolution.voice_id,
                    ),
                )
        else:
            settings.elevenlabs_valid = True
        settings.wavespeed_valid = _is_valid_key(wavespeed_key)
        settings.last_validated_at = datetime.now(UTC)
        settings.validation_status = "valid" if settings.wavespeed_valid and settings.elevenlabs_valid else "invalid"
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
        missing_previews = self._missing_slide_previews(presentation.slides)
        if missing_previews:
            self._regenerate_missing_slide_previews(presentation)
            missing_previews = self._missing_slide_previews(presentation.slides)
        if missing_previews:
            raise GenerationReadinessError(
                "MISSING_RENDERED_PREVIEW",
                f"Slides missing rendered preview image: {missing_previews}",
            )

        project_config, settings, tts_resolution, wavespeed_key = self._resolve_validation_inputs(
            project_id
        )
        if not (app_settings.WAVESPEED_API_KEY or "").strip():
            raise GenerationReadinessError(
                "MISSING_WAVESPEED_API_KEY",
                "WAVESPEED_API_KEY is missing in worker environment",
            )
        avatar_mode = (app_settings.AVATAR_GENERATION_MODE or "fast_lipsync").strip().lower()
        if tts_resolution.provider == "none" and not app_settings.ALLOW_DUMMY_TTS:
            raise GenerationReadinessError(
                "MISSING_TTS_PROVIDER",
                f"{avatar_mode} requires a real TTS provider. Configure TTS_PROVIDER=elevenlabs or set ALLOW_DUMMY_TTS=true for local development.",
            )
        elevenlabs_key = tts_resolution.api_key or decrypt_secret(settings.elevenlabs_api_key_encrypted)
        elevenlabs_voice_id = tts_resolution.voice_id or (settings.elevenlabs_voice_id or "").strip() or None
        if tts_resolution.provider == "elevenlabs" and not (
            elevenlabs_key and elevenlabs_voice_id
        ):
            missing_reason = _elevenlabs_missing_reason(
                project_config,
                settings,
                elevenlabs_key,
                elevenlabs_voice_id,
            )
            logger.info(
                "generate-video ElevenLabs credentials missing: project_id=%s organization_id=%s "
                "project_config_found=%s project_config_id=%s missing_reason=%s "
                "elevenlabs_api_key_present=%s "
                "elevenlabs_voice_id_present=%s credentials_source=%s",
                project_id,
                MOCK_ORG_ID,
                project_config is not None,
                project_config.id if project_config is not None else None,
                missing_reason,
                bool(project_config and project_config.elevenlabs_api_key_encrypted),
                bool(
                    (project_config and project_config.voice_id)
                    or settings.elevenlabs_voice_id
                ),
                tts_resolution.credentials_source,
            )
            raise GenerationReadinessError(
                "MISSING_ELEVENLABS_API_KEY",
                "Please configure ElevenLabs API key and voice ID in project settings before generating video.",
            )
        if not _has_avatar_source(settings):
            raise GenerationReadinessError(
                "MISSING_AVATAR_ASSET",
                "Please upload an avatar image before generating the video.",
        )

    def _resolve_validation_inputs(
        self,
        project_id: uuid.UUID,
    ) -> tuple[
        ProjectGenerationConfig | None,
        VideoGenerationSettings,
        object,
        str | None,
    ]:
        project_config = self.repo.get_generation_config(project_id, MOCK_ORG_ID)
        settings = self.repo.get_video_settings(project_id, MOCK_ORG_ID)
        if settings is None:
            settings = VideoGenerationSettings(
                organization_id=MOCK_ORG_ID,
                project_id=project_id,
                validation_status="valid",
                wavespeed_valid=True,
                elevenlabs_valid=(app_settings.TTS_PROVIDER != "elevenlabs"),
            )
        tts_resolution = resolve_saved_tts_credentials(
            self.repo.db,
            project_id,
            MOCK_ORG_ID,
            app_settings,
        )
        project_wavespeed_key = (
            decrypt_secret(project_config.wavespeed_api_key_encrypted)
            if project_config is not None
            else None
        )
        wavespeed_key = (
            project_wavespeed_key
            or decrypt_secret(settings.wavespeed_api_key_encrypted)
            or app_settings.WAVESPEED_API_KEY
        )
        return project_config, settings, tts_resolution, wavespeed_key

    def _missing_slide_previews(self, slides) -> list[int]:
        storage = get_storage()
        missing: list[int] = []
        for slide in slides:
            preview_key = _slide_preview_storage_key(slide)
            if not preview_key:
                missing.append(slide.position)
                continue
            try:
                if storage.exists(preview_key):
                    continue
            except Exception:
                # Fall back to a best-effort check; missing storage access should not
                # reintroduce editable-canvas requirements.
                try:
                    storage.download_bytes(preview_key)
                    continue
                except Exception:
                    pass
            missing.append(slide.position)
        return missing

    def _regenerate_missing_slide_previews(self, presentation) -> None:
        storage = get_storage()
        try:
            pptx_bytes = storage.download_file(presentation.storage_key)
        except Exception:
            return
        try:
            preview_keys = render_slide_previews(
                pptx_bytes=pptx_bytes,
                presentation_id=presentation.id,
                original_filename=presentation.original_filename,
                storage=storage,
            )
            if not preview_keys:
                return
            for slide in presentation.slides:
                preview_key = preview_keys.get(slide.position)
                if not preview_key:
                    continue
                metadata = dict(slide.metadata_ or {})
                slide.thumbnail_key = preview_key
                metadata["rendered_image_key"] = preview_key
                metadata["slide_preview"] = {
                    "asset_type": "slide_preview",
                    "storage_key": preview_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                }
                slide.metadata_ = metadata
            self.repo.db.commit()
        except Exception as exc:
            logger.info(
                "Could not regenerate slide previews for presentation %s: %s",
                presentation.id,
                exc,
            )

    def _settings_read(
        self,
        settings: VideoGenerationSettings | None,
    ) -> VideoSettingsRead:
        if settings is None:
            env_wavespeed_valid = _is_valid_key(app_settings.WAVESPEED_API_KEY)
            return VideoSettingsRead(
                elevenlabs_api_key_masked=None,
                elevenlabs_voice_id=None,
                wavespeed_api_key_masked=_mask(
                    app_settings.WAVESPEED_API_KEY[-4:]
                    if app_settings.WAVESPEED_API_KEY and len(app_settings.WAVESPEED_API_KEY) >= 4
                    else None
                ),
                elevenlabs_valid=(app_settings.TTS_PROVIDER != "elevenlabs"),
                wavespeed_valid=env_wavespeed_valid,
                avatar_source_url=None,
                avatar_source_asset_id=None,
                using_debug_avatar_source=bool(
                    app_settings.DEBUG_AVATAR_SOURCE_URL and not app_settings.is_production
                ),
                validation_status="valid" if env_wavespeed_valid else "not_configured",
                last_validated_at=None,
                updated_at=None,
            )
        env_wavespeed_valid = _is_valid_key(app_settings.WAVESPEED_API_KEY)
        wavespeed_masked = _mask(settings.wavespeed_api_key_last_four)
        if not wavespeed_masked and app_settings.WAVESPEED_API_KEY:
            wavespeed_masked = _mask(
                app_settings.WAVESPEED_API_KEY[-4:]
                if len(app_settings.WAVESPEED_API_KEY) >= 4
                else None
            )
        wavespeed_valid = settings.wavespeed_valid or env_wavespeed_valid
        validation_status = settings.validation_status
        if not settings.wavespeed_api_key_encrypted and app_settings.WAVESPEED_API_KEY:
            validation_status = "valid" if env_wavespeed_valid else "invalid"
        return VideoSettingsRead(
            elevenlabs_api_key_masked=_mask(settings.elevenlabs_api_key_last_four),
            elevenlabs_voice_id=settings.elevenlabs_voice_id,
            wavespeed_api_key_masked=wavespeed_masked,
            avatar_source_url=settings.avatar_source_url,
            avatar_source_asset_id=settings.avatar_source_asset_id,
            using_debug_avatar_source=(
                not bool(settings.avatar_source_url or settings.avatar_source_asset_id)
                and bool(app_settings.DEBUG_AVATAR_SOURCE_URL)
                and not app_settings.is_production
            ),
            elevenlabs_valid=(
                settings.elevenlabs_valid
                if (app_settings.TTS_PROVIDER or "none").strip().lower() == "elevenlabs"
                else True
            ),
            wavespeed_valid=wavespeed_valid,
            validation_status=validation_status,  # type: ignore[arg-type]
            last_validated_at=settings.last_validated_at,
            updated_at=settings.updated_at,
        )

    def _fail_stale_running_jobs(self, project_id: uuid.UUID) -> None:
        if app_settings.is_production:
            return
        now = datetime.now(UTC)
        for generation_job in self.repo.list_running_generation_jobs(project_id, MOCK_ORG_ID):
            threshold_seconds = _stalled_threshold_seconds(generation_job)
            if not _is_stale_generation_job(generation_job, now, threshold_seconds):
                continue
            logger.warning(
                "Marking stale generation job failed: project_id=%s generation_job_id=%s "
                "status=%s updated_at=%s threshold_seconds=%s",
                project_id,
                generation_job.id,
                generation_job.status,
                generation_job.updated_at,
                threshold_seconds,
            )
            generation_job.status = "failed"
            generation_job.error_code = "GENERATION_JOB_STALLED"
            generation_job.error_message = STALLED_ERROR_MESSAGE
            generation_job.completed_at = now
            generation_job.current_step = "Generation stalled"
            self.repo.save_generation_job(generation_job)


def _mask(last_four: str | None) -> str | None:
    return f"************{last_four}" if last_four else None


def _secret_input_flags(value: str | None) -> tuple[bool, bool]:
    if value is None:
        return False, False
    stripped = value.strip()
    if not stripped:
        return False, False
    if stripped.startswith("*"):
        return False, True
    return True, False


def _should_replace_secret(value: str | None) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("*"):
        return False
    return True


def _elevenlabs_missing_reason(
    project_config,
    settings_row: VideoGenerationSettings | None,
    api_key: str | None,
    voice_id: str | None,
) -> str:
    if project_config is None:
        if settings_row is None:
            return "project_config_missing"
        if not settings_row.elevenlabs_api_key_encrypted:
            return "encrypted_key_missing"
    if project_config is not None and not project_config.elevenlabs_api_key_encrypted:
        return "encrypted_key_missing"
    if not api_key:
        return "decrypt_failed"
    if not (voice_id or "").strip():
        return "voice_id_missing"
    if not app_settings.ELEVENLABS_API_KEY and not app_settings.ELEVENLABS_VOICE_ID:
        return "env_fallback_missing"
    return "unknown"


def _is_valid_key(value: str | None) -> bool:
    if not value:
        return False
    lowered = value.lower()
    return len(value.strip()) >= 8 and not any(
        marker in lowered for marker in ("invalid", "expired", "revoked")
    )


def _has_avatar_source(settings: VideoGenerationSettings) -> bool:
    if settings.avatar_source_url or settings.avatar_source_asset_id:
        return True
    return bool(app_settings.DEBUG_AVATAR_SOURCE_URL and not app_settings.is_production)


def _has_public_provider_asset_endpoint() -> bool:
    if app_settings.STORAGE_BACKEND.lower() == "azure":
        endpoint = app_settings.AZURE_STORAGE_PUBLIC_BASE_URL
    else:
        endpoint = app_settings.EXTERNAL_PROVIDER_ASSET_BASE_URL or app_settings.MINIO_PUBLIC_ENDPOINT
    return _is_public_http_endpoint(endpoint)


def _is_public_http_endpoint(endpoint: str | None) -> bool:
    if not endpoint:
        return False
    parsed = urlparse(endpoint)
    if parsed.scheme in {"http", "https"}:
        hostname = parsed.hostname
    else:
        hostname = endpoint.split("/", 1)[0].split(":", 1)[0]
    if not hostname:
        return False
    lowered_host = hostname.lower()
    if lowered_host in {"localhost", "127.0.0.1", "0.0.0.0", "minio"}:
        return False
    if lowered_host.endswith(".local"):
        return False
    try:
        ip_address = ipaddress.ip_address(lowered_host)
        return not (
            ip_address.is_private
            or ip_address.is_loopback
            or ip_address.is_link_local
        )
    except ValueError:
        return True


def _slide_preview_storage_key(slide) -> str | None:
    metadata = slide.metadata_ or {}
    slide_preview = metadata.get("slide_preview")
    if isinstance(slide_preview, dict):
        preview_key = slide_preview.get("storage_key")
        if isinstance(preview_key, str) and preview_key:
            return preview_key
    rendered_key = metadata.get("rendered_image_key")
    if isinstance(rendered_key, str) and rendered_key:
        return rendered_key
    if isinstance(slide.thumbnail_key, str) and slide.thumbnail_key:
        return slide.thumbnail_key
    return None


def _stalled_threshold_seconds(generation_job: GenerationJob) -> int:
    if generation_job.status in {"generating_avatar", "composing_slide", "composing_video"}:
        return max(1, int(app_settings.PROVIDER_OPERATION_STALLED_AFTER_SECONDS))
    return max(1, int(app_settings.GENERATION_STALLED_AFTER_SECONDS))


def _is_stale_generation_job(generation_job: GenerationJob, now: datetime, threshold_seconds: int) -> bool:
    if generation_job.status not in RUNNING_GENERATION_STATUSES:
        return False
    updated_at = generation_job.updated_at
    if updated_at is None:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return now - updated_at > timedelta(seconds=threshold_seconds)
