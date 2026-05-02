from datetime import UTC, datetime

from fastapi import HTTPException, status

from app.modules.projects.service import MOCK_ORG_ID, MOCK_USER_ID
from app.modules.provider_credentials.adapters import get_provider_adapter
from app.modules.provider_credentials.models import ProviderCredential
from app.modules.provider_credentials.repository import ProviderCredentialRepository
from app.modules.provider_credentials.schemas import (
    ProviderCredentialRead,
    ProviderCredentialUpsert,
    ProviderCredentialValidationRead,
)
from app.utils.crypto import decrypt_secret, encrypt_secret


def mask_api_key(last_four: str | None) -> str | None:
    if not last_four:
        return None
    return f"************{last_four}"


class ProviderCredentialService:
    def __init__(self, repo: ProviderCredentialRepository) -> None:
        self.repo = repo

    def list_status(self) -> list[ProviderCredentialRead]:
        configured = {
            (credential.provider_name, credential.provider_type): credential
            for credential in self.repo.list_by_org(MOCK_ORG_ID)
        }
        expected = [
            ("gemini", "ai"),
            ("gemini", "tts"),
            ("elevenlabs", "tts"),
            ("wavespeed", "avatar_video"),
        ]
        return [
            self._read(configured.get((provider_name, provider_type)), provider_name, provider_type)
            for provider_name, provider_type in expected
        ]

    def upsert(self, data: ProviderCredentialUpsert) -> ProviderCredentialRead:
        api_key = data.api_key.strip()
        credential = self.repo.get(MOCK_ORG_ID, data.provider_name, data.provider_type)
        if credential is None:
            credential = ProviderCredential(
                organization_id=MOCK_ORG_ID,
                provider_name=data.provider_name,
                provider_type=data.provider_type,
                encrypted_api_key=encrypt_secret(api_key) or "",
                key_last_four=api_key[-4:],
                status="configured",
                created_by=MOCK_USER_ID,
                updated_by=MOCK_USER_ID,
            )
        else:
            credential.encrypted_api_key = encrypt_secret(api_key) or ""
            credential.key_last_four = api_key[-4:]
            credential.status = "configured"
            credential.updated_by = MOCK_USER_ID
        return self._read(self.repo.save(credential), data.provider_name, data.provider_type)

    def validate(
        self,
        provider_name: str,
        provider_type: str,
    ) -> ProviderCredentialValidationRead:
        credential = self.repo.get(MOCK_ORG_ID, provider_name, provider_type)
        if credential is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Provider credential is not configured",
            )
        api_key = decrypt_secret(credential.encrypted_api_key)
        if not api_key:
            credential.status = "invalid"
            self.repo.save(credential)
            return ProviderCredentialValidationRead(
                provider_name=provider_name,
                provider_type=provider_type,
                status="invalid",
                valid=False,
                message="Stored API key could not be decrypted",
                last_validated_at=credential.last_validated_at,
            )

        result = get_provider_adapter(provider_name).validate_key(api_key)
        credential.status = result.status
        credential.last_validated_at = datetime.now(UTC)
        self.repo.save(credential)
        return ProviderCredentialValidationRead(
            provider_name=provider_name,
            provider_type=provider_type,
            status=result.status,
            valid=result.ok,
            message=result.message,
            last_validated_at=credential.last_validated_at,
        )

    def _read(
        self,
        credential: ProviderCredential | None,
        provider_name: str,
        provider_type: str,
    ) -> ProviderCredentialRead:
        if credential is None:
            return ProviderCredentialRead(
                id=None,
                provider_name=provider_name,
                provider_type=provider_type,
                masked_api_key=None,
                key_last_four=None,
                status="not_configured",
                last_validated_at=None,
                updated_at=None,
            )
        return ProviderCredentialRead(
            id=credential.id,
            provider_name=credential.provider_name,
            provider_type=credential.provider_type,
            masked_api_key=mask_api_key(credential.key_last_four),
            key_last_four=credential.key_last_four,
            status=credential.status,
            last_validated_at=credential.last_validated_at,
            updated_at=credential.updated_at,
        )
