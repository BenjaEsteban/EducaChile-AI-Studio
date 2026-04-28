import uuid

from fastapi import HTTPException, status

from app.modules.generation_config.repository import ProjectGenerationConfigRepository
from app.modules.generation_config.schemas import (
    ProjectGenerationConfigRead,
    ProjectGenerationConfigUpdate,
)
from app.modules.projects.models import ProjectGenerationConfig
from app.modules.projects.service import MOCK_ORG_ID
from app.utils.crypto import encrypt_secret


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
        if config is None:
            config = ProjectGenerationConfig(
                project_id=project_id,
                organization_id=MOCK_ORG_ID,
            )

        config.tts_provider = data.tts_provider
        config.video_provider = data.video_provider
        config.voice_id = data.voice_id
        config.voice_name = data.voice_name
        config.resolution = data.resolution
        config.aspect_ratio = data.aspect_ratio

        if data.gemini_api_key is not None:
            config.gemini_api_key_encrypted = encrypt_secret(data.gemini_api_key)
        if data.elevenlabs_api_key is not None:
            config.elevenlabs_api_key_encrypted = encrypt_secret(data.elevenlabs_api_key)
        if data.wavespeed_api_key is not None:
            config.wavespeed_api_key_encrypted = encrypt_secret(data.wavespeed_api_key)

        return ProjectGenerationConfigRead.from_model(self.repo.save(config))

    def _ensure_project(self, project_id: uuid.UUID) -> None:
        if self.repo.get_project(project_id, MOCK_ORG_ID) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )
