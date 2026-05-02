import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import GUID, Base, TimestampMixin


class ProviderType(str, Enum):
    ai = "ai"
    tts = "tts"
    avatar_video = "avatar_video"


class ProviderCredentialStatus(str, Enum):
    not_configured = "not_configured"
    configured = "configured"
    valid = "valid"
    invalid = "invalid"
    expired_or_revoked = "expired_or_revoked"


class ProviderCredential(Base, TimestampMixin):
    __tablename__ = "provider_credentials"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider_name",
            "provider_type",
            name="uq_provider_credentials_org_provider_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    provider_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_last_four: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=ProviderCredentialStatus.configured, index=True
    )
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
