import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import ProjectCreate, ProjectList, ProjectRead, ProjectUpdate
from app.modules.projects.service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def get_service(db: Session = Depends(get_db)) -> ProjectService:
    return ProjectService(ProjectRepository(db))


@router.get("/", response_model=ProjectList)
def list_projects(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    service: ProjectService = Depends(get_service),
):
    return service.list(skip=skip, limit=limit)


@router.post("/", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(
    data: ProjectCreate,
    service: ProjectService = Depends(get_service),
):
    return service.create(data)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: uuid.UUID,
    service: ProjectService = Depends(get_service),
):
    return service.get_or_404(project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: uuid.UUID,
    data: ProjectUpdate,
    service: ProjectService = Depends(get_service),
):
    return service.update(project_id, data)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    service: ProjectService = Depends(get_service),
):
    service.delete(project_id)
