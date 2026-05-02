import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.modules.projects.models import ProjectGenerationConfig
from app.utils.crypto import mask_secret


class ProjectGenerationConfigRead(BaseModel):
    id: uuid.UUID | None
    project_id: uuid.UUID
    ai_provider: Literal["gemini"]
    tts_provider: Literal["gemini", "elevenlabs"]
    video_provider: Literal["wavespeed"]
    voice_id: str | None
    voice_name: str | None
    avatar_id: str | None
    gemini_api_key: str | None
    elevenlabs_api_key: str | None
    wavespeed_api_key: str | None
    resolution: str
    aspect_ratio: str
    language: str
    output_format: Literal["mp4"]
    subtitles_enabled: bool
    background_music_enabled: bool
    status: Literal["draft", "configured", "ready_for_generation"]
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def default(cls, project_id: uuid.UUID) -> "ProjectGenerationConfigRead":
        return cls(
            id=None,
            project_id=project_id,
            ai_provider="gemini",
            tts_provider="gemini",
            video_provider="wavespeed",
            voice_id=None,
            voice_name=None,
            avatar_id=None,
            gemini_api_key=None,
            elevenlabs_api_key=None,
            wavespeed_api_key=None,
            resolution="1920x1080",
            aspect_ratio="16:9",
            language="es",
            output_format="mp4",
            subtitles_enabled=False,
            background_music_enabled=False,
            status="draft",
            created_at=None,
            updated_at=None,
        )

    @classmethod
    def from_model(cls, config: ProjectGenerationConfig) -> "ProjectGenerationConfigRead":
        return cls(
            id=config.id,
            project_id=config.project_id,
            ai_provider=config.ai_provider,
            tts_provider=config.tts_provider,
            video_provider=config.video_provider,
            voice_id=config.voice_id,
            voice_name=config.voice_name,
            avatar_id=config.avatar_id,
            gemini_api_key=mask_secret(config.gemini_api_key_encrypted),
            elevenlabs_api_key=mask_secret(config.elevenlabs_api_key_encrypted),
            wavespeed_api_key=mask_secret(config.wavespeed_api_key_encrypted),
            resolution=config.resolution,
            aspect_ratio=config.aspect_ratio,
            language=config.language,
            output_format=config.output_format,
            subtitles_enabled=config.subtitles_enabled,
            background_music_enabled=config.background_music_enabled,
            status=config.status,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )


class ProjectGenerationConfigUpdate(BaseModel):
    ai_provider: Literal["gemini"] = "gemini"
    tts_provider: Literal["gemini", "elevenlabs"] = "gemini"
    video_provider: Literal["wavespeed"] = "wavespeed"
    voice_id: str | None = None
    voice_name: str | None = None
    avatar_id: str | None = None
    gemini_api_key: str | None = None
    elevenlabs_api_key: str | None = None
    wavespeed_api_key: str | None = None
    resolution: str = "1920x1080"
    aspect_ratio: str = "16:9"
    language: str = "es"
    output_format: Literal["mp4"] = "mp4"
    subtitles_enabled: bool = False
    background_music_enabled: bool = False
