import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.jobs.repository import JobRepository
from app.modules.jobs.schemas import JobRead
from app.modules.jobs.service import JobService

router = APIRouter(prefix="/jobs", tags=["jobs"])


def get_service(db: Session = Depends(get_db)) -> JobService:
    return JobService(JobRepository(db))


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: uuid.UUID, service: JobService = Depends(get_service)):
    return service.get_or_404(job_id)
