import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.modules.projects.models import ProjectGenerationConfig
from app.utils.crypto import mask_secret


class ProjectGenerationConfigRead(BaseModel):
    id: uuid.UUID | None
    project_id: uuid.UUID
    tts_provider: Literal["gemini", "elevenlabs"]
    video_provider: Literal["wavespeed"]
    voice_id: str | None
    voice_name: str | None
    gemini_api_key: str | None
    elevenlabs_api_key: str | None
    wavespeed_api_key: str | None
    resolution: str
    aspect_ratio: str
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def default(cls, project_id: uuid.UUID) -> "ProjectGenerationConfigRead":
        return cls(
            id=None,
            project_id=project_id,
            tts_provider="gemini",
            video_provider="wavespeed",
            voice_id=None,
            voice_name=None,
            gemini_api_key=None,
            elevenlabs_api_key=None,
            wavespeed_api_key=None,
            resolution="1920x1080",
            aspect_ratio="16:9",
            created_at=None,
            updated_at=None,
        )

    @classmethod
    def from_model(cls, config: ProjectGenerationConfig) -> "ProjectGenerationConfigRead":
        return cls(
            id=config.id,
            project_id=config.project_id,
            tts_provider=config.tts_provider,
            video_provider=config.video_provider,
            voice_id=config.voice_id,
            voice_name=config.voice_name,
            gemini_api_key=mask_secret(config.gemini_api_key_encrypted),
            elevenlabs_api_key=mask_secret(config.elevenlabs_api_key_encrypted),
            wavespeed_api_key=mask_secret(config.wavespeed_api_key_encrypted),
            resolution=config.resolution,
            aspect_ratio=config.aspect_ratio,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )


class ProjectGenerationConfigUpdate(BaseModel):
    tts_provider: Literal["gemini", "elevenlabs"] = "gemini"
    video_provider: Literal["wavespeed"] = "wavespeed"
    voice_id: str | None = None
    voice_name: str | None = None
    gemini_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    wavespeed_api_key: str | None = None
    resolution: str = "1920x1080"
    aspect_ratio: str = "16:9"
