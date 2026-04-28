import logging
import uuid

from sqlalchemy import select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.modules.jobs.models import Job  # noqa: F401
from app.modules.organizations.models import MemberRole, Organization, OrganizationMember
from app.modules.projects.service import MOCK_ORG_ID, MOCK_USER_ID
from app.modules.users.models import User, UserRole

logger = logging.getLogger(__name__)

DEV_ORG_NAME = "EducaChile Dev Organization"
DEV_ORG_SLUG = "educachile-dev"
DEV_USER_EMAIL = "dev@educachile.local"
DEV_USER_NAME = "EducaChile Dev User"
DEV_MEMBER_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")


def seed_dev_data(db: Session) -> None:
    """Create deterministic local records used by the development-only mock identity."""
    user = db.get(User, MOCK_USER_ID)
    if user is None:
        db.add(
            User(
                id=MOCK_USER_ID,
                email=DEV_USER_EMAIL,
                hashed_password="dev-only-no-auth",
                full_name=DEV_USER_NAME,
                role=UserRole.teacher,
                is_active=True,
            )
        )

    organization = db.get(Organization, MOCK_ORG_ID)
    if organization is None:
        db.add(
            Organization(
                id=MOCK_ORG_ID,
                name=DEV_ORG_NAME,
                slug=DEV_ORG_SLUG,
            )
        )

    membership = db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == MOCK_ORG_ID,
            OrganizationMember.user_id == MOCK_USER_ID,
        )
    ).scalar_one_or_none()
    if membership is None:
        db.add(
            OrganizationMember(
                id=DEV_MEMBER_ID,
                organization_id=MOCK_ORG_ID,
                user_id=MOCK_USER_ID,
                role=MemberRole.owner,
            )
        )

    db.commit()


def run_dev_seed() -> None:
    """Run the local development seed. Safe to execute repeatedly."""
    db = SessionLocal()
    try:
        seed_dev_data(db)
        logger.info("Development seed is ready.")
    except (OperationalError, ProgrammingError) as exc:
        db.rollback()
        logger.warning("Development seed skipped; run migrations first. Detail: %s", exc)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_dev_seed()
