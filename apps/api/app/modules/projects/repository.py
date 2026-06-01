import uuid

from sqlalchemy.orm import Session

from app.modules.generation.models import GenerationJob
from app.modules.projects.models import Asset, Folder, Presentation, Project, Slide


class ProjectRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, project_id: uuid.UUID, org_id: uuid.UUID) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.organization_id == org_id)
            .first()
        )

    def list_by_org(
        self,
        org_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        folder_id: uuid.UUID | None = None,
    ) -> list[Project]:
        query = self.db.query(Project).filter(Project.organization_id == org_id)
        if folder_id is not None:
            query = query.filter(Project.folder_id == folder_id)
        return query.order_by(Project.created_at.desc()).offset(skip).limit(limit).all()

    def count_by_org(self, org_id: uuid.UUID, folder_id: uuid.UUID | None = None) -> int:
        query = self.db.query(Project).filter(Project.organization_id == org_id)
        if folder_id is not None:
            query = query.filter(Project.folder_id == folder_id)
        return query.count()

    def create(self, project: Project) -> Project:
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def save(self, project: Project) -> Project:
        self.db.commit()
        self.db.refresh(project)
        return project

    def delete(self, project: Project) -> None:
        self.db.delete(project)
        self.db.commit()

    def get_folder_by_id(self, folder_id: uuid.UUID, org_id: uuid.UUID) -> Folder | None:
        return (
            self.db.query(Folder)
            .filter(Folder.id == folder_id, Folder.organization_id == org_id)
            .first()
        )

    def list_folders_by_org(self, org_id: uuid.UUID) -> list[Folder]:
        return (
            self.db.query(Folder)
            .filter(Folder.organization_id == org_id)
            .order_by(Folder.created_at.asc())
            .all()
        )

    def get_latest_presentation(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> Presentation | None:
        return (
            self.db.query(Presentation)
            .filter(
                Presentation.project_id == project_id,
                Presentation.organization_id == org_id,
            )
            .order_by(Presentation.created_at.desc())
            .first()
        )

    def count_slides(self, presentation_id: uuid.UUID) -> int:
        return (
            self.db.query(Slide)
            .filter(Slide.presentation_id == presentation_id)
            .count()
        )

    def get_latest_asset_by_type(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        asset_type: str,
    ) -> Asset | None:
        return (
            self.db.query(Asset)
            .filter(
                Asset.project_id == project_id,
                Asset.organization_id == org_id,
                Asset.asset_type == asset_type,
            )
            .order_by(Asset.created_at.desc())
            .first()
        )

    def get_latest_generation_job(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> GenerationJob | None:
        return (
            self.db.query(GenerationJob)
            .filter(
                GenerationJob.project_id == project_id,
                GenerationJob.organization_id == org_id,
            )
            .order_by(GenerationJob.created_at.desc())
            .first()
        )


class PresentationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(
        self, presentation_id: uuid.UUID, org_id: uuid.UUID
    ) -> Presentation | None:
        return (
            self.db.query(Presentation)
            .filter(
                Presentation.id == presentation_id,
                Presentation.organization_id == org_id,
            )
            .first()
        )

    def get_by_id_only(self, presentation_id: uuid.UUID) -> Presentation | None:
        return self.db.get(Presentation, presentation_id)

    def list_by_project(self, project_id: uuid.UUID) -> list[Presentation]:
        return (
            self.db.query(Presentation)
            .filter(Presentation.project_id == project_id)
            .all()
        )

    def create(self, presentation: Presentation) -> Presentation:
        self.db.add(presentation)
        self.db.commit()
        self.db.refresh(presentation)
        return presentation

    def save(self, presentation: Presentation) -> Presentation:
        self.db.commit()
        self.db.refresh(presentation)
        return presentation


class AssetRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_by_project(self, project_id: uuid.UUID) -> list[Asset]:
        return self.db.query(Asset).filter(Asset.project_id == project_id).all()

    def create(self, asset: Asset) -> Asset:
        self.db.add(asset)
        self.db.commit()
        self.db.refresh(asset)
        return asset
