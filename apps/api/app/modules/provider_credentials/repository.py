import uuid

from sqlalchemy.orm import Session

from app.modules.provider_credentials.models import ProviderCredential


class ProviderCredentialRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(
        self,
        organization_id: uuid.UUID,
        provider_name: str,
        provider_type: str,
    ) -> ProviderCredential | None:
        return (
            self.db.query(ProviderCredential)
            .filter(
                ProviderCredential.organization_id == organization_id,
                ProviderCredential.provider_name == provider_name,
                ProviderCredential.provider_type == provider_type,
            )
            .first()
        )

    def list_by_org(self, organization_id: uuid.UUID) -> list[ProviderCredential]:
        return (
            self.db.query(ProviderCredential)
            .filter(ProviderCredential.organization_id == organization_id)
            .order_by(ProviderCredential.provider_type, ProviderCredential.provider_name)
            .all()
        )

    def save(self, credential: ProviderCredential) -> ProviderCredential:
        self.db.add(credential)
        self.db.commit()
        self.db.refresh(credential)
        return credential
