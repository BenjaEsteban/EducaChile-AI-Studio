import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.jobs.models import Job, JobStatus


class JobRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        return self.db.get(Job, job_id)

    def list_by_project(
        self,
        project_id: uuid.UUID,
        status: JobStatus | None = None,
    ) -> list[Job]:
        q = self.db.query(Job).filter(Job.project_id == project_id)
        if status:
            q = q.filter(Job.status == status)
        return q.order_by(Job.created_at.desc()).all()

    def create(self, job: Job) -> Job:
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def save(self, job: Job) -> Job:
        self.db.commit()
        self.db.refresh(job)
        return job

    # ── State transitions ─────────────────────────────────────────────────────

    def mark_running(self, job: Job, celery_task_id: str) -> Job:
        job.status = JobStatus.running
        job.celery_task_id = celery_task_id
        job.started_at = datetime.now(timezone.utc)
        job.progress = 0.0
        return self.save(job)

    def update_progress(self, job: Job, progress: float) -> Job:
        job.progress = max(0.0, min(100.0, progress))
        return self.save(job)

    def mark_completed(self, job: Job, result: dict) -> Job:
        job.status = JobStatus.completed
        job.result = result
        job.progress = 100.0
        job.finished_at = datetime.now(timezone.utc)
        return self.save(job)

    def mark_failed(self, job: Job, error_message: str) -> Job:
        job.status = JobStatus.failed
        job.error_message = error_message
        job.finished_at = datetime.now(timezone.utc)
        return self.save(job)
