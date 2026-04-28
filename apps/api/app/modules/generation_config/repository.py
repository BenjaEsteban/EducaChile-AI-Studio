import uuid

from sqlalchemy.orm import Session

from app.modules.projects.models import Project, ProjectGenerationConfig


class ProjectGenerationConfigRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_project(self, project_id: uuid.UUID, org_id: uuid.UUID) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.organization_id == org_id)
            .first()
        )

    def get_by_project(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> ProjectGenerationConfig | None:
        return (
            self.db.query(ProjectGenerationConfig)
            .filter(
                ProjectGenerationConfig.project_id == project_id,
                ProjectGenerationConfig.organization_id == org_id,
            )
            .first()
        )

    def save(self, config: ProjectGenerationConfig) -> ProjectGenerationConfig:
        self.db.add(config)
        self.db.commit()
        self.db.refresh(config)
        return config
