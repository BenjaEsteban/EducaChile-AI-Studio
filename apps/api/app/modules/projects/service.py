import uuid
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.modules.generation.models import VideoGenerationSettings
from app.modules.projects.models import Asset, Project
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import ProjectCreate, ProjectList, ProjectUpdate
from app.providers.storage import get_storage

logger = logging.getLogger(__name__)

# Mock identities — reemplazar con JWT cuando se implemente auth
MOCK_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
MOCK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")

ALLOWED_AVATAR_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
MAX_AVATAR_BYTES = 5 * 1024 * 1024
DEFAULT_AVATAR_LAYOUT = {"x": 0.0, "y": 0.0, "width": 160.0, "height": 160.0}


class ProjectService:
    def __init__(self, repo: ProjectRepository) -> None:
        self.repo = repo

    def list(self, skip: int = 0, limit: int = 50) -> ProjectList:
        items = self.repo.list_by_org(MOCK_ORG_ID, skip=skip, limit=limit)
        total = self.repo.count_by_org(MOCK_ORG_ID)
        return ProjectList(items=items, total=total, skip=skip, limit=limit)

    def get_or_404(self, project_id: uuid.UUID) -> Project:
        project = self.repo.get_by_id(project_id, MOCK_ORG_ID)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        return project

    def create(self, data: ProjectCreate) -> Project:
        project = Project(
            organization_id=MOCK_ORG_ID,
            owner_id=MOCK_USER_ID,
            name=data.name,
            description=data.description,
        )
        return self.repo.create(project)

    def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> Project:
        project = self.get_or_404(project_id)
        # Solo actualiza los campos explícitamente enviados (exclude_unset)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(project, field, value)
        return self.repo.save(project)

    def delete(self, project_id: uuid.UUID) -> None:
        project = self.get_or_404(project_id)
        self.repo.delete(project)

    async def upload_avatar(self, project_id: uuid.UUID, file: UploadFile) -> Asset:
        project = self.get_or_404(project_id)
        mime_type = file.content_type or "application/octet-stream"
        if mime_type not in ALLOWED_AVATAR_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "INVALID_AVATAR_MIME_TYPE",
                    "message": "Avatar must be a JPG, PNG, or WEBP file.",
                },
            )

        data = await file.read()
        if len(data) > MAX_AVATAR_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "AVATAR_FILE_TOO_LARGE",
                    "message": "Avatar file must be 5 MB or smaller.",
                },
            )
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "AVATAR_UPLOAD_FAILED", "message": "Avatar file is empty."},
            )
        avatar_image_info = _probe_avatar_image(data, _extension_for_mime_type(mime_type))
        logger.info(
            "Project avatar upload image probe: project_id=%s width=%s height=%s "
            "aspect_ratio=%s warnings=%s",
            project.id,
            avatar_image_info.get("width"),
            avatar_image_info.get("height"),
            avatar_image_info.get("aspect_ratio"),
            avatar_image_info.get("warnings"),
        )

        extension = Path(file.filename or "avatar").suffix.lower()
        if not extension:
            extension = _extension_for_mime_type(mime_type)
        storage_key = f"projects/{project.id}/avatar/{uuid.uuid4()}{extension}"
        try:
            get_storage().upload_file(storage_key, data, mime_type)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "AVATAR_UPLOAD_FAILED", "message": "Avatar upload failed."},
            ) from exc

        asset = Asset(
            organization_id=project.organization_id,
            project_id=project.id,
            slide_id=None,
            asset_type="avatar_source",
            storage_key=storage_key,
            filename=file.filename or f"avatar{extension}",
            mime_type=mime_type,
            size_bytes=len(data),
            metadata_json={
                "layout": DEFAULT_AVATAR_LAYOUT.copy(),
                "image_validation": avatar_image_info,
            },
        )
        self.repo.db.add(asset)
        self.repo.db.flush()

        settings = (
            self.repo.db.query(VideoGenerationSettings)
            .filter(
                VideoGenerationSettings.project_id == project.id,
                VideoGenerationSettings.organization_id == project.organization_id,
            )
            .first()
        )
        if settings is None:
            settings = VideoGenerationSettings(
                organization_id=project.organization_id,
                project_id=project.id,
                validation_status="not_configured",
            )
            self.repo.db.add(settings)
        settings.avatar_source_asset_id = asset.id
        settings.avatar_source_url = None

        self.repo.db.commit()
        self.repo.db.refresh(asset)
        return asset

    def get_avatar(self, project_id: uuid.UUID) -> Asset:
        project = self.get_or_404(project_id)
        settings = (
            self.repo.db.query(VideoGenerationSettings)
            .filter(
                VideoGenerationSettings.project_id == project.id,
                VideoGenerationSettings.organization_id == project.organization_id,
            )
            .first()
        )
        asset = None
        if settings and settings.avatar_source_asset_id:
            asset = self.repo.db.get(Asset, settings.avatar_source_asset_id)
            if asset and (
                asset.project_id != project.id or asset.organization_id != project.organization_id
            ):
                asset = None
        if asset is None:
            asset = (
                self.repo.db.query(Asset)
                .filter(
                    Asset.project_id == project.id,
                    Asset.organization_id == project.organization_id,
                    Asset.asset_type == "avatar_source",
                )
                .order_by(Asset.created_at.desc())
                .first()
            )
        if asset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Avatar not configured for this project.",
            )
        return asset

    def update_avatar_layout(
        self,
        project_id: uuid.UUID,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> Asset:
        asset = self.get_avatar(project_id)
        metadata = dict(asset.metadata_json or {})
        metadata["layout"] = {
            "x": float(x),
            "y": float(y),
            "width": float(width),
            "height": float(height),
        }
        asset.metadata_json = metadata
        self.repo.db.add(asset)
        self.repo.db.commit()
        self.repo.db.refresh(asset)
        return asset

    def delete_avatar(self, project_id: uuid.UUID) -> None:
        project = self.get_or_404(project_id)
        try:
            asset = self.get_avatar(project_id)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                return
            raise
        try:
            get_storage().delete(asset.storage_key)
        except FileNotFoundError:
            pass

        settings = (
            self.repo.db.query(VideoGenerationSettings)
            .filter(
                VideoGenerationSettings.project_id == project.id,
                VideoGenerationSettings.organization_id == project.organization_id,
            )
            .first()
        )
        if settings and settings.avatar_source_asset_id == asset.id:
            settings.avatar_source_asset_id = None
        self.repo.db.delete(asset)
        self.repo.db.commit()


def _extension_for_mime_type(mime_type: str) -> str:
    if mime_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mime_type == "image/png":
        return ".png"
    if mime_type == "image/webp":
        return ".webp"
    return ""


def _probe_avatar_image(data: bytes, extension: str) -> dict:
    info = {"width": None, "height": None, "aspect_ratio": None, "warnings": []}
    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / f"avatar{extension or '.img'}"
        image_path.write_bytes(data)
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "json",
                    str(image_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            info["warnings"].append("Could not read avatar image dimensions")
            return info
    try:
        stream = (json.loads(result.stdout).get("streams") or [{}])[0]
    except (json.JSONDecodeError, IndexError, AttributeError):
        info["warnings"].append("Could not parse avatar image metadata")
        return info
    width = stream.get("width")
    height = stream.get("height")
    info["width"] = width
    info["height"] = height
    if isinstance(width, int) and isinstance(height, int) and height > 0:
        aspect_ratio = width / height
        info["aspect_ratio"] = round(aspect_ratio, 3)
        if width < 512 or height < 512:
            info["warnings"].append(
                "Avatar image is smaller than the recommended 512x512 resolution"
            )
        if aspect_ratio < 0.6 or aspect_ratio > 1.8:
            info["warnings"].append(
                "Avatar image aspect ratio is not ideal; use a front-facing portrait"
            )
    return info
