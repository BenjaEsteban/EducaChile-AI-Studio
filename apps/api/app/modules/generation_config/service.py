import logging
import uuid
from urllib.parse import urlparse

from fastapi import HTTPException, status

from app.config import settings as app_settings
from app.modules.generation_config.repository import ProjectGenerationConfigRepository
from app.modules.generation_config.schemas import (
    ProjectGenerationConfigDebugRead,
    ProjectGenerationConfigRead,
    ProjectGenerationConfigUpdate,
)
from app.modules.projects.models import ProjectGenerationConfig
from app.modules.projects.service import MOCK_ORG_ID
from app.modules.generation.pipeline import resolve_saved_tts_credentials
from app.utils.crypto import decrypt_secret, encrypt_secret, mask_secret

logger = logging.getLogger(__name__)


class ProjectGenerationConfigService:
    def __init__(self, repo: ProjectGenerationConfigRepository) -> None:
        self.repo = repo

    def get(self, project_id: uuid.UUID) -> ProjectGenerationConfigRead:
        self._ensure_project(project_id)
        config = self.repo.get_by_project(project_id, MOCK_ORG_ID)
        if config is None:
            return ProjectGenerationConfigRead.default(project_id)
        return ProjectGenerationConfigRead.from_model(config)

    def upsert(
        self,
        project_id: uuid.UUID,
        data: ProjectGenerationConfigUpdate,
    ) -> ProjectGenerationConfigRead:
        self._ensure_project(project_id)
        config = self.repo.get_by_project(project_id, MOCK_ORG_ID)
        created = config is None
        if config is None:
            config = ProjectGenerationConfig(
                project_id=project_id,
                organization_id=MOCK_ORG_ID,
            )

        config.ai_provider = data.ai_provider
        config.tts_provider = data.tts_provider
        config.video_provider = data.video_provider
        if data.voice_id is not None:
            config.voice_id = data.voice_id.strip() or None
        if data.voice_name is not None:
            config.voice_name = data.voice_name.strip() or None
        if data.avatar_id is not None:
            config.avatar_id = data.avatar_id.strip() or None
        config.resolution = data.resolution
        config.aspect_ratio = data.aspect_ratio
        config.language = data.language
        config.output_format = data.output_format
        config.subtitles_enabled = data.subtitles_enabled
        config.background_music_enabled = data.background_music_enabled
        config.status = "configured" if config.voice_id else "draft"

        gemini_received_real, gemini_received_masked = _secret_input_flags(data.gemini_api_key)
        elevenlabs_received_real, elevenlabs_received_masked = _secret_input_flags(
            data.elevenlabs_api_key
        )
        wavespeed_received_real, wavespeed_received_masked = _secret_input_flags(
            data.wavespeed_api_key
        )

        if _should_replace_secret(data.gemini_api_key):
            config.gemini_api_key_encrypted = encrypt_secret(data.gemini_api_key.strip())
        if _should_replace_secret(data.elevenlabs_api_key):
            config.elevenlabs_api_key_encrypted = encrypt_secret(data.elevenlabs_api_key.strip())
        if _should_replace_secret(data.wavespeed_api_key):
            config.wavespeed_api_key_encrypted = encrypt_secret(data.wavespeed_api_key.strip())

        saved = self.repo.save(config)
        logger.info(
            "Project generation config saved: project_id=%s organization_id=%s "
            "project_config_created_or_updated=%s elevenlabs_api_key_received_real_value=%s "
            "elevenlabs_api_key_received_masked_value=%s elevenlabs_api_key_encrypted_present_after_save=%s "
            "elevenlabs_voice_id_present_after_save=%s wavespeed_api_key_encrypted_present_after_save=%s",
            project_id,
            MOCK_ORG_ID,
            "created" if created else "updated",
            elevenlabs_received_real,
            elevenlabs_received_masked,
            bool(saved.elevenlabs_api_key_encrypted),
            bool(saved.voice_id),
            bool(saved.wavespeed_api_key_encrypted),
        )
        return ProjectGenerationConfigRead.from_model(saved)

    def debug(self, project_id: uuid.UUID) -> ProjectGenerationConfigDebugRead:
        self._ensure_project(project_id)
        config = self.repo.get_by_project(project_id, MOCK_ORG_ID)
        validate_source = _resolve_debug_credentials_source(self.repo.db, project_id)
        generate_source = _resolve_debug_credentials_source(self.repo.db, project_id)
        database_url = urlparse(app_settings.DATABASE_URL)
        elevenlabs_api_key = decrypt_secret(config.elevenlabs_api_key_encrypted) if config else None
        wavespeed_api_key = decrypt_secret(config.wavespeed_api_key_encrypted) if config else None
        return ProjectGenerationConfigDebugRead(
            project_id=project_id,
            organization_id=MOCK_ORG_ID,
            project_config_found=config is not None,
            project_config_id=config.id if config is not None else None,
            elevenlabs_api_key_encrypted_present=bool(config and config.elevenlabs_api_key_encrypted),
            elevenlabs_api_key_decrypt_success=bool(
                config and config.elevenlabs_api_key_encrypted and elevenlabs_api_key
            ),
            elevenlabs_api_key_masked=mask_secret(config.elevenlabs_api_key_encrypted) if config else None,
            elevenlabs_voice_id_present=bool(config and config.voice_id),
            elevenlabs_voice_id_value=config.voice_id if config is not None else None,
            wavespeed_api_key_encrypted_present=bool(config and config.wavespeed_api_key_encrypted),
            wavespeed_api_key_decrypt_success=bool(
                config and config.wavespeed_api_key_encrypted and wavespeed_api_key
            ),
            wavespeed_api_key_masked=mask_secret(config.wavespeed_api_key_encrypted) if config else None,
            credentials_source_for_validate_endpoint=validate_source,
            credentials_source_for_generate_video_endpoint=generate_source,
            database_url_host=database_url.hostname,
            database_name=(database_url.path or "").lstrip("/") or None,
            encryption_key_present=bool(app_settings.ENCRYPTION_KEY),
            encryption_key_length=len(app_settings.ENCRYPTION_KEY or ""),
        )

    def _ensure_project(self, project_id: uuid.UUID) -> None:
        if self.repo.get_project(project_id, MOCK_ORG_ID) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )


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


def _resolve_debug_credentials_source(
    db,
    project_id: uuid.UUID,
) -> str:
    resolution = resolve_saved_tts_credentials(db, project_id, MOCK_ORG_ID, app_settings)
    return resolution.credentials_source
