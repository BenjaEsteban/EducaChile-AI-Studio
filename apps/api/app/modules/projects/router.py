import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import (
    ProjectAvatarLayoutUpdate,
    ProjectAvatarRead,
    ProjectCreate,
    ProjectList,
    ProjectRead,
    ProjectUpdate,
)
from app.modules.projects.service import ProjectService
from app.providers.storage import get_storage

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


@router.post("/{project_id}/avatar", response_model=ProjectAvatarRead)
async def upload_project_avatar(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    service: ProjectService = Depends(get_service),
):
    asset = await service.upload_avatar(project_id, file)
    return _avatar_read(asset)


@router.get("/{project_id}/avatar", response_model=ProjectAvatarRead)
def get_project_avatar(
    project_id: uuid.UUID,
    service: ProjectService = Depends(get_service),
):
    asset = service.get_avatar(project_id)
    return _avatar_read(asset)


@router.patch("/{project_id}/avatar/layout", response_model=ProjectAvatarRead)
def update_project_avatar_layout(
    project_id: uuid.UUID,
    data: ProjectAvatarLayoutUpdate,
    service: ProjectService = Depends(get_service),
):
    asset = service.update_avatar_layout(
        project_id,
        x=data.x,
        y=data.y,
        width=data.width,
        height=data.height,
    )
    return _avatar_read(asset)


@router.delete("/{project_id}/avatar", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_avatar(
    project_id: uuid.UUID,
    service: ProjectService = Depends(get_service),
):
    service.delete_avatar(project_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    service: ProjectService = Depends(get_service),
):
    service.delete(project_id)


def _avatar_read(asset) -> ProjectAvatarRead:
    url = get_storage().generate_read_url(asset.storage_key)
    layout = _avatar_layout(asset.metadata_json)
    return ProjectAvatarRead(
        object_key=asset.storage_key,
        filename=asset.filename,
        content_type=asset.mime_type,
        url=url,
        x=layout["x"],
        y=layout["y"],
        width=layout["width"],
        height=layout["height"],
        updated_at=asset.updated_at,
        avatar_asset_id=asset.id,
        avatar_preview_url=url,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
    )


def _avatar_layout(metadata: dict | None) -> dict[str, float]:
    layout = (metadata or {}).get("layout") if isinstance(metadata, dict) else None
    if not isinstance(layout, dict):
        layout = {}
    return {
        "x": float(layout.get("x", 0.0)),
        "y": float(layout.get("y", 0.0)),
        "width": float(layout.get("width", 160.0)),
        "height": float(layout.get("height", 160.0)),
    }
