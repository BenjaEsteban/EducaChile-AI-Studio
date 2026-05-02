import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

GenerationStatus = Literal[
    "idle",
    "pending",
    "validating",
    "queued",
    "generating_audio",
    "generating_avatar",
    "rendering_slides",
    "composing_video",
    "completed",
    "failed",
    "cancelled",
]

ValidationStatus = Literal["not_configured", "saved", "valid", "invalid"]


class GenerationJobRead(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID | None
    organization_id: uuid.UUID
    project_id: uuid.UUID
    status: GenerationStatus
    progress_percentage: float
    current_step: str | None
    current_slide: int | None
    total_slides: int | None
    error_code: str | None
    error_message: str | None
    final_asset_id: uuid.UUID | None
    result: dict | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class StartGenerationResponse(BaseModel):
    generation_job: GenerationJobRead
    job_id: uuid.UUID


class FinalVideoRead(BaseModel):
    ready: bool
    asset_id: uuid.UUID | None
    url: str | None
    storage_key: str | None
    mime_type: str | None
    size_bytes: int | None


class VideoSettingsRead(BaseModel):
    elevenlabs_api_key_masked: str | None
    elevenlabs_voice_id: str | None
    wavespeed_api_key_masked: str | None
    elevenlabs_valid: bool
    wavespeed_valid: bool
    validation_status: ValidationStatus
    last_validated_at: datetime | None
    updated_at: datetime | None


class VideoSettingsUpdate(BaseModel):
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str | None = None
    wavespeed_api_key: str | None = None


class VideoSettingsValidationRead(VideoSettingsRead):
    message: str


class GenerationStatusRead(BaseModel):
    status: GenerationStatus
    progress: float
    current_slide: int | None
    total_slides: int | None
    message: str | None
    error_code: str | None
    error_message: str | None
    final_video_url: str | None
