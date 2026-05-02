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
    api_key: str = Field(min_length=1)


class ProviderCredentialRead(BaseModel):
    id: uuid.UUID | None
    provider_name: str
    provider_type: str
    masked_api_key: str | None
    key_last_four: str | None
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
