import uuid

from fastapi import HTTPException, status

from app.modules.jobs.models import Job, JobStatus, JobType
from app.modules.jobs.repository import JobRepository


class JobService:
    def __init__(self, repo: JobRepository) -> None:
        self.repo = repo

    def get_or_404(self, job_id: uuid.UUID) -> Job:
        job = self.repo.get_by_id(job_id)
        if not job:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
        return job

    def create(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        job_type: JobType,
        presentation_id: uuid.UUID | None = None,
    ) -> Job:
        job = Job(
            organization_id=org_id,
            project_id=project_id,
            presentation_id=presentation_id,
            job_type=job_type,
            status=JobStatus.queued,
        )
        return self.repo.create(job)
