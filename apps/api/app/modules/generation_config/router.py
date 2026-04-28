import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.generation_config.repository import ProjectGenerationConfigRepository
from app.modules.generation_config.schemas import (
    ProjectGenerationConfigRead,
    ProjectGenerationConfigUpdate,
)
from app.modules.generation_config.service import ProjectGenerationConfigService

router = APIRouter(prefix="/projects", tags=["generation-config"])


def get_service(db: Session = Depends(get_db)) -> ProjectGenerationConfigService:
    return ProjectGenerationConfigService(ProjectGenerationConfigRepository(db))


@router.get("/{project_id}/generation-config", response_model=ProjectGenerationConfigRead)
def get_generation_config(
    project_id: uuid.UUID,
    service: ProjectGenerationConfigService = Depends(get_service),
):
    return service.get(project_id)


@router.put("/{project_id}/generation-config", response_model=ProjectGenerationConfigRead)
def upsert_generation_config(
    project_id: uuid.UUID,
    data: ProjectGenerationConfigUpdate,
    service: ProjectGenerationConfigService = Depends(get_service),
):
    return service.upsert(project_id, data)
