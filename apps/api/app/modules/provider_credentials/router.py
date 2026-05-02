from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.provider_credentials.repository import ProviderCredentialRepository
from app.modules.provider_credentials.schemas import (
    ProviderCredentialRead,
    ProviderCredentialUpsert,
    ProviderCredentialValidationRead,
)
from app.modules.provider_credentials.service import ProviderCredentialService

router = APIRouter(prefix="/provider-credentials", tags=["provider-credentials"])


def get_service(db: Session = Depends(get_db)) -> ProviderCredentialService:
    return ProviderCredentialService(ProviderCredentialRepository(db))


@router.post("", response_model=ProviderCredentialRead)
def save_provider_credential(
    data: ProviderCredentialUpsert,
    service: ProviderCredentialService = Depends(get_service),
):
    return service.upsert(data)


@router.get("/status", response_model=list[ProviderCredentialRead])
def list_provider_credential_status(
    service: ProviderCredentialService = Depends(get_service),
):
    return service.list_status()


@router.post(
    "/{provider_name}/{provider_type}/validate",
    response_model=ProviderCredentialValidationRead,
)
def validate_provider_credential(
    provider_name: str,
    provider_type: str,
    service: ProviderCredentialService = Depends(get_service),
):
    return service.validate(provider_name=provider_name, provider_type=provider_type)
