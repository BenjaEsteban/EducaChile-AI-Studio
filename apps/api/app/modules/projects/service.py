import uuid

from fastapi import HTTPException, status

from app.modules.projects.models import Project
from app.modules.projects.repository import ProjectRepository
from app.modules.projects.schemas import ProjectCreate, ProjectList, ProjectUpdate

# Mock identities — reemplazar con JWT cuando se implemente auth
MOCK_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
MOCK_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


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
