import uuid
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from app.modules.projects.models import AssetType, PresentationStatus, ProjectStatus

# ── Project ───────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("name cannot be empty")
        return v


class ProjectRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    owner_id: uuid.UUID | None
    name: str
    description: str | None
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectRead]
    total: int
    skip: int
    limit: int


# ── Presentation ──────────────────────────────────────────────────────────────

ALLOWED_PRESENTATION_EXTENSIONS = frozenset({".ppt", ".pptx"})
ALLOWED_PRESENTATION_CONTENT_TYPES = frozenset({
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/octet-stream",  # algunos clientes envían esto para .pptx
})


class PresentationCreate(BaseModel):
    title: str
    original_filename: str
    storage_key: str


class InitUploadRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=500)
    content_type: str = Field(
        default="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        max_length=200,
    )

    @field_validator("filename")
    @classmethod
    def validate_extension(cls, v: str) -> str:
        ext = Path(v).suffix.lower()
        if ext not in ALLOWED_PRESENTATION_EXTENSIONS:
            raise ValueError(f"Solo se permiten archivos .ppt o .pptx, recibido: '{ext}'")
        if ".." in v or v.startswith("/"):
            raise ValueError("filename inválido")
        return v

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, v: str) -> str:
        if v not in ALLOWED_PRESENTATION_CONTENT_TYPES:
            raise ValueError(f"content_type no permitido: '{v}'")
        return v


class InitUploadResponse(BaseModel):
    presentation_id: uuid.UUID
    upload_url: str
    storage_key: str
    expires_in: int
    method: str = "PUT"


class ConfirmUploadResponse(BaseModel):
    model_config = {"from_attributes": True}

    presentation_id: uuid.UUID
    status: PresentationStatus
    job_id: uuid.UUID


class PresentationRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    original_filename: str
    status: PresentationStatus
    slide_count: int
    created_at: datetime
    updated_at: datetime


# ── Slide ─────────────────────────────────────────────────────────────────────

class SlideRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    presentation_id: uuid.UUID
    position: int
    title: str | None
    notes: str | None
    thumbnail_key: str | None
    duration_seconds: float | None


# ── GenerationConfig ──────────────────────────────────────────────────────────

class GenerationConfigCreate(BaseModel):
    voice_id: str | None = None
    language: str = "es"
    speaking_rate: float = 1.0
    resolution: str = "1920x1080"
    extra: dict | None = None


class GenerationConfigRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    presentation_id: uuid.UUID
    voice_id: str | None
    language: str
    speaking_rate: float
    resolution: str
    extra: dict | None


# ── Asset ─────────────────────────────────────────────────────────────────────

class AssetRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    project_id: uuid.UUID
    asset_type: AssetType
    storage_key: str
    filename: str
    mime_type: str | None
    size_bytes: int | None
    created_at: datetime
