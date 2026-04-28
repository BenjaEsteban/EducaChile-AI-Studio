import uuid

from sqlalchemy.orm import Session

from app.modules.organizations.models import Organization, OrganizationMember


class OrganizationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, org_id: uuid.UUID) -> Organization | None:
        return self.db.get(Organization, org_id)

    def get_by_slug(self, slug: str) -> Organization | None:
        return self.db.query(Organization).filter(Organization.slug == slug).first()

    def create(self, org: Organization) -> Organization:
        self.db.add(org)
        self.db.commit()
        self.db.refresh(org)
        return org

    def get_member(self, org_id: uuid.UUID, user_id: uuid.UUID) -> OrganizationMember | None:
        return (
            self.db.query(OrganizationMember)
            .filter(
                OrganizationMember.organization_id == org_id,
                OrganizationMember.user_id == user_id,
            )
            .first()
        )

    def add_member(self, member: OrganizationMember) -> OrganizationMember:
        self.db.add(member)
        self.db.commit()
        self.db.refresh(member)
        return member
