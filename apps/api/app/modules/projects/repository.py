import uuid

from sqlalchemy.orm import Session

from app.modules.projects.models import Asset, Presentation, Project


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
        self, org_id: uuid.UUID, skip: int = 0, limit: int = 50
    ) -> list[Project]:
        return (
            self.db.query(Project)
            .filter(Project.organization_id == org_id)
            .order_by(Project.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def count_by_org(self, org_id: uuid.UUID) -> int:
        return self.db.query(Project).filter(Project.organization_id == org_id).count()

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
