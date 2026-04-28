import uuid
from enum import Enum

from sqlalchemy import BigInteger, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import GUID, Base, JSONType, TimestampMixin

# ── Enums ─────────────────────────────────────────────────────────────────────

class ProjectStatus(str, Enum):
    active = "active"
    archived = "archived"


class PresentationStatus(str, Enum):
    upload_pending = "upload_pending"
    uploaded = "uploaded"
    processing = "processing"
    parsed = "parsed"
    ready = "ready"
    failed = "failed"


class AssetType(str, Enum):
    presentation = "presentation"
    video = "video"
    audio = "audio"
    image = "image"
    thumbnail = "thumbnail"


# ── Models ────────────────────────────────────────────────────────────────────

class Project(Base, TimestampMixin):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ProjectStatus.active, index=True
    )

    organization: Mapped["Organization"] = relationship(  # noqa: F821
        "Organization", back_populates="projects"
    )
    presentations: Mapped[list["Presentation"]] = relationship(
        "Presentation", back_populates="project", cascade="all, delete-orphan"
    )
    assets: Mapped[list["Asset"]] = relationship(
        "Asset", back_populates="project", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["Job"]] = relationship(  # noqa: F821
        "Job", back_populates="project", cascade="all, delete-orphan"
    )


class Presentation(Base, TimestampMixin):
    __tablename__ = "presentations"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=PresentationStatus.upload_pending, index=True
    )
    slide_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    project: Mapped["Project"] = relationship("Project", back_populates="presentations")
    slides: Mapped[list["Slide"]] = relationship(
        "Slide", back_populates="presentation", cascade="all, delete-orphan",
        order_by="Slide.position",
    )
    generation_config: Mapped["GenerationConfig | None"] = relationship(
        "GenerationConfig", back_populates="presentation",
        cascade="all, delete-orphan", uselist=False,
    )


class Slide(Base, TimestampMixin):
    __tablename__ = "slides"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    presentation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("presentations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)

    presentation: Mapped["Presentation"] = relationship(
        "Presentation", back_populates="slides"
    )


class GenerationConfig(Base, TimestampMixin):
    __tablename__ = "generation_configs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    presentation_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("presentations.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    voice_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="es")
    speaking_rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    resolution: Mapped[str] = mapped_column(String(20), nullable=False, default="1920x1080")
    extra: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    presentation: Mapped["Presentation"] = relationship(
        "Presentation", back_populates="generation_config"
    )


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    project: Mapped["Project"] = relationship("Project", back_populates="assets")
