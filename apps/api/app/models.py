"""Central SQLAlchemy model registration.

Import this module before mapper configuration in processes that touch the DB
(API, Alembic, Celery workers). The imported names are intentionally re-exported
so string-based relationships can resolve consistently.
"""

from app.modules.jobs.models import Job
from app.modules.organizations.models import Organization, OrganizationMember
from app.modules.projects.models import (
    Asset,
    GenerationConfig,
    Presentation,
    Project,
    ProjectGenerationConfig,
    Slide,
)
from app.modules.users.models import User

__all__ = [
    "Asset",
    "GenerationConfig",
    "Job",
    "Organization",
    "OrganizationMember",
    "Presentation",
    "Project",
    "ProjectGenerationConfig",
    "Slide",
    "User",
]
