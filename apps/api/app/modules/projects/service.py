from __future__ import annotations

import uuid
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile, status

from app.modules.generation.models import VideoGenerationSettings
from app.modules.projects.models import Asset, Folder, Project
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import (
    FolderCreate,
    FolderRename,
    ProjectOpenStateRead,
    FolderTreeNode,
    FolderTreeRead,
    ProjectCreate,
    ProjectList,
    ProjectMoveRequest,
    ProjectUpdate,
)
from app.providers.storage import get_storage

logger = logging.getLogger(__name__)

# Mock identities — reemplazar con JWT cuando se implemente auth
MOCK_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
MOCK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
DEFAULT_UNASSIGNED_FOLDER_NAME = "Sin Nombre"

ALLOWED_AVATAR_MIME_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
}
MAX_AVATAR_BYTES = 5 * 1024 * 1024
# Default avatar position in canvas coordinates (960×540 space, matching the
# pipeline canvas defaults). Bottom-right quadrant — mirrors the pipeline fallback.
DEFAULT_AVATAR_LAYOUT = {
    "x": 720.0,
    "y": 310.0,
    "width": 200.0,
    "height": 200.0,
    "border_radius": 0.0,
    "fit_mode": "custom",
}


class ProjectService:
    def __init__(self, repo: ProjectRepository) -> None:
        self.repo = repo

    def list(
        self,
        skip: int = 0,
        limit: int = 50,
        folder_id: uuid.UUID | None = None,
    ) -> ProjectList:
        self._ensure_default_folder_and_assign_unscoped_projects()
        if folder_id is not None:
            self._get_folder_or_404(folder_id)
        items = self.repo.list_by_org(MOCK_ORG_ID, skip=skip, limit=limit, folder_id=folder_id)
        total = self.repo.count_by_org(MOCK_ORG_ID, folder_id=folder_id)
        return ProjectList(items=items, total=total, skip=skip, limit=limit)

    def get_or_404(self, project_id: uuid.UUID) -> Project:
        project = self.repo.get_by_id(project_id, MOCK_ORG_ID)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        return project

    def get_open_state(self, project_id: uuid.UUID) -> ProjectOpenStateRead:
        project = self.get_or_404(project_id)
        latest_presentation = self.repo.get_latest_presentation(project.id, project.organization_id)
        has_presentation = latest_presentation is not None
        presentation_id = latest_presentation.id if latest_presentation else None
        presentation_status = latest_presentation.status if latest_presentation else None
        slide_count = (
            self.repo.count_slides(latest_presentation.id)
            if latest_presentation is not None
            else 0
        )
        has_slides = slide_count > 0

        final_video_asset = self.repo.get_latest_asset_by_type(
            project.id,
            project.organization_id,
            "final_video",
        )
        has_generated_video = final_video_asset is not None
        generated_video_url = (
            get_storage().generate_read_url(final_video_asset.storage_key)
            if final_video_asset is not None
            else None
        )

        latest_generation_job = self.repo.get_latest_generation_job(
            project.id,
            project.organization_id,
        )

        return ProjectOpenStateRead(
            project_id=project.id,
            has_presentation=has_presentation,
            presentation_id=presentation_id,
            presentation_status=presentation_status,
            slide_count=slide_count,
            has_slides=has_slides,
            has_generated_video=has_generated_video,
            generated_video_asset_id=final_video_asset.id if final_video_asset else None,
            generated_video_url=generated_video_url,
            latest_generation_job_id=(
                latest_generation_job.id if latest_generation_job else None
            ),
            latest_generation_status=(
                latest_generation_job.status if latest_generation_job else None
            ),
        )

    def create(self, data: ProjectCreate) -> Project:
        folder_id = self._resolve_target_folder_id(data.folder_id)
        project = Project(
            organization_id=MOCK_ORG_ID,
            owner_id=MOCK_USER_ID,
            name=data.name,
            description=data.description,
            folder_id=folder_id,
        )
        return self.repo.create(project)

    def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> Project:
        project = self.get_or_404(project_id)
        # Solo actualiza los campos explícitamente enviados (exclude_unset)
        for field, value in data.model_dump(exclude_unset=True).items():
            if field == "folder_id":
                value = self._resolve_target_folder_id(value)
            setattr(project, field, value)
        return self.repo.save(project)

    def delete(self, project_id: uuid.UUID) -> None:
        project = self.get_or_404(project_id)
        self.repo.delete(project)

    def move_project(self, project_id: uuid.UUID, data: ProjectMoveRequest) -> Project:
        project = self.get_or_404(project_id)
        project.folder_id = self._resolve_target_folder_id(data.folder_id)
        return self.repo.save(project)

    def list_folder_tree(self) -> FolderTreeRead:
        self._ensure_default_folder_and_assign_unscoped_projects()
        folders = self.repo.list_folders_by_org(MOCK_ORG_ID)
        nodes: dict[uuid.UUID, FolderTreeNode] = {}
        for folder in folders:
            nodes[folder.id] = FolderTreeNode(
                id=folder.id,
                parent_folder_id=folder.parent_folder_id,
                name=folder.name,
                created_at=folder.created_at,
                updated_at=folder.updated_at,
                children=[],
            )
        roots: list[FolderTreeNode] = []
        for folder in folders:
            node = nodes[folder.id]
            if folder.parent_folder_id and folder.parent_folder_id in nodes:
                nodes[folder.parent_folder_id].children.append(node)
            else:
                roots.append(node)
        return FolderTreeRead(items=roots)

    def create_folder(self, data: FolderCreate) -> Folder:
        parent_id = data.parent_folder_id
        if parent_id is not None:
            self._get_folder_or_404(parent_id)
        self._ensure_unique_folder_name(data.name, parent_id)
        folder = Folder(
            organization_id=MOCK_ORG_ID,
            parent_folder_id=parent_id,
            name=data.name.strip(),
        )
        self.repo.db.add(folder)
        self.repo.db.commit()
        self.repo.db.refresh(folder)
        return folder

    def rename_folder(self, folder_id: uuid.UUID, data: FolderRename) -> Folder:
        folder = self._get_folder_or_404(folder_id)
        self._ensure_unique_folder_name(data.name, folder.parent_folder_id, exclude_id=folder.id)
        folder.name = data.name.strip()
        self.repo.db.add(folder)
        self.repo.db.commit()
        self.repo.db.refresh(folder)
        return folder

    def delete_folder(self, folder_id: uuid.UUID, cascade: bool = False) -> None:
        folder = self._get_folder_or_404(folder_id)
        fallback_folder = self._get_or_create_default_folder()
        fallback_folder_id = (
            fallback_folder.id
            if folder.id != fallback_folder.id
            else None
        )
        folder_ids = self._folder_subtree_ids(folder.id)
        has_subfolders = len(folder_ids) > 1
        has_projects = (
            self.repo.db.query(Project)
            .filter(
                Project.organization_id == MOCK_ORG_ID,
                Project.folder_id.in_(folder_ids),
            )
            .count()
            > 0
        )
        if not cascade and (has_subfolders or has_projects):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "FOLDER_NOT_EMPTY",
                    "message": "Folder contains subfolders or projects. Retry with cascade=true to delete safely.",
                },
            )

        self.repo.db.query(Project).filter(
            Project.organization_id == MOCK_ORG_ID,
            Project.folder_id.in_(folder_ids),
        ).update({Project.folder_id: fallback_folder_id}, synchronize_session=False)
        self.repo.db.delete(folder)
        self.repo.db.commit()

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
        border_radius: float = 0.0,
        fit_mode: str = "custom",
    ) -> Asset:
        asset = self.get_avatar(project_id)
        metadata = dict(asset.metadata_json or {})
        metadata["layout"] = {
            "x": float(x),
            "y": float(y),
            "width": float(width),
            "height": float(height),
            "border_radius": float(border_radius),
            "fit_mode": str(fit_mode),
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

    def _get_folder_or_404(self, folder_id: uuid.UUID) -> Folder:
        folder = self.repo.get_folder_by_id(folder_id, MOCK_ORG_ID)
        if folder is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found",
            )
        return folder

    def _folder_subtree_ids(self, root_folder_id: uuid.UUID) -> list[uuid.UUID]:
        folders = self.repo.list_folders_by_org(MOCK_ORG_ID)
        children_map: dict[uuid.UUID, list[uuid.UUID]] = {}
        for folder in folders:
            if folder.parent_folder_id is None:
                continue
            children_map.setdefault(folder.parent_folder_id, []).append(folder.id)

        collected: list[uuid.UUID] = []
        stack = [root_folder_id]
        seen: set[uuid.UUID] = set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            collected.append(current)
            stack.extend(children_map.get(current, []))
        return collected

    def _ensure_unique_folder_name(
        self,
        name: str,
        parent_folder_id: uuid.UUID | None,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        normalized = name.strip().lower()
        query = self.repo.db.query(Folder).filter(Folder.organization_id == MOCK_ORG_ID)
        if parent_folder_id is None:
            query = query.filter(Folder.parent_folder_id.is_(None))
        else:
            query = query.filter(Folder.parent_folder_id == parent_folder_id)
        if exclude_id is not None:
            query = query.filter(Folder.id != exclude_id)
        siblings = query.all()
        if any(folder.name.strip().lower() == normalized for folder in siblings):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "FOLDER_NAME_CONFLICT",
                    "message": "A folder with this name already exists at this level.",
                },
            )

    def _resolve_target_folder_id(self, folder_id: uuid.UUID | None) -> uuid.UUID:
        if folder_id is not None:
            self._get_folder_or_404(folder_id)
            return folder_id
        default_folder = self._ensure_default_folder_and_assign_unscoped_projects()
        return default_folder.id

    def _ensure_default_folder_and_assign_unscoped_projects(self) -> Folder:
        default_folder = self._get_or_create_default_folder()
        updated_rows = (
            self.repo.db.query(Project)
            .filter(
                Project.organization_id == MOCK_ORG_ID,
                Project.folder_id.is_(None),
            )
            .update({Project.folder_id: default_folder.id}, synchronize_session=False)
        )
        if updated_rows:
            self.repo.db.commit()
        return default_folder

    def _get_or_create_default_folder(self) -> Folder:
        folders = self.repo.list_folders_by_org(MOCK_ORG_ID)
        normalized_default_name = DEFAULT_UNASSIGNED_FOLDER_NAME.strip().lower()

        default_roots = [
            folder
            for folder in folders
            if folder.parent_folder_id is None
            and folder.name.strip().lower() == normalized_default_name
        ]
        if default_roots:
            canonical = min(default_roots, key=lambda folder: folder.created_at)
            renamed = False
            if canonical.name != DEFAULT_UNASSIGNED_FOLDER_NAME:
                canonical.name = DEFAULT_UNASSIGNED_FOLDER_NAME
                self.repo.db.add(canonical)
                renamed = True
            duplicates = [folder for folder in default_roots if folder.id != canonical.id]
            changed = False
            for duplicate in duplicates:
                (
                    self.repo.db.query(Project)
                    .filter(
                        Project.organization_id == MOCK_ORG_ID,
                        Project.folder_id == duplicate.id,
                    )
                    .update({Project.folder_id: canonical.id}, synchronize_session=False)
                )
                (
                    self.repo.db.query(Folder)
                    .filter(
                        Folder.organization_id == MOCK_ORG_ID,
                        Folder.parent_folder_id == duplicate.id,
                    )
                    .update({Folder.parent_folder_id: canonical.id}, synchronize_session=False)
                )
                self.repo.db.delete(duplicate)
                changed = True
            if changed or renamed:
                self.repo.db.commit()
                self.repo.db.refresh(canonical)
            return canonical

        folder = Folder(
            organization_id=MOCK_ORG_ID,
            parent_folder_id=None,
            name=DEFAULT_UNASSIGNED_FOLDER_NAME,
        )
        self.repo.db.add(folder)
        self.repo.db.commit()
        self.repo.db.refresh(folder)
        return folder


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
