import uuid
from datetime import datetime

from pydantic import BaseModel

from app.modules.jobs.models import JobStatus, JobType


class JobRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID
    presentation_id: uuid.UUID | None
    job_type: JobType
    status: JobStatus
    celery_task_id: str | None
    progress: float
    current_step: str | None
    error_message: str | None
    result: dict | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
