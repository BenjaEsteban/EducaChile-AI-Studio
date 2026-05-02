import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import GUID, Base, JSONType, TimestampMixin


class GenerationJobStatus(str, Enum):
    pending = "pending"
    validating = "validating"
    queued = "queued"
    generating_audio = "generating_audio"
    generating_avatar = "generating_avatar"
    rendering_slides = "rendering_slides"
    composing_video = "composing_video"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class GenerationJob(Base, TimestampMixin):
    __tablename__ = "generation_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=GenerationJobStatus.pending, index=True
    )
    progress_percentage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    current_step: Mapped[str | None] = mapped_column(String(255), nullable=True)
    current_slide: Mapped[int | None] = mapped_column(nullable=True)
    total_slides: Mapped[int | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class VideoGenerationSettings(Base, TimestampMixin):
    __tablename__ = "video_generation_settings"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    elevenlabs_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    elevenlabs_api_key_last_four: Mapped[str | None] = mapped_column(String(8), nullable=True)
    elevenlabs_voice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wavespeed_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    wavespeed_api_key_last_four: Mapped[str | None] = mapped_column(String(8), nullable=True)
    validation_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="not_configured"
    )
    elevenlabs_valid: Mapped[bool] = mapped_column(nullable=False, default=False)
    wavespeed_valid: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
