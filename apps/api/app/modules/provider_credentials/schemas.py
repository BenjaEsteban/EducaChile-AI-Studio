import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal["gemini", "elevenlabs", "wavespeed"]
ProviderTypeValue = Literal["ai", "tts", "avatar_video"]
CredentialStatus = Literal[
    "not_configured",
    "configured",
    "valid",
    "invalid",
    "expired_or_revoked",
]


class ProviderCredentialUpsert(BaseModel):
    provider_name: ProviderName
    provider_type: ProviderTypeValue
    # Optional so a credential's voice_id can be updated without re-sending the
    # secret. When the credential does not yet exist, an api_key is required
    # (enforced in the service layer).
    api_key: str | None = Field(default=None)
    # Only meaningful for ElevenLabs; ignored for other providers.
    voice_id: str | None = Field(default=None)


class ProviderCredentialRead(BaseModel):
    id: uuid.UUID | None
    provider_name: str
    provider_type: str
    masked_api_key: str | None
    key_last_four: str | None
    voice_id: str | None = None
    status: CredentialStatus
    last_validated_at: datetime | None
    updated_at: datetime | None


class ProviderCredentialValidationRead(BaseModel):
    provider_name: str
    provider_type: str
    status: CredentialStatus
    valid: bool
    message: str
    last_validated_at: datetime | None
