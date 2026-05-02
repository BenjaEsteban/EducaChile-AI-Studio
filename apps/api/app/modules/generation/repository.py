import uuid

from sqlalchemy.orm import Session

from app.modules.generation.models import GenerationJob, VideoGenerationSettings
from app.modules.projects.models import Asset, Presentation, Project, ProjectGenerationConfig


class GenerationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_project(self, project_id: uuid.UUID, org_id: uuid.UUID) -> Project | None:
        return (
            self.db.query(Project)
            .filter(Project.id == project_id, Project.organization_id == org_id)
            .first()
        )

    def get_generation_config(
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

    def get_latest_presentation(self, project_id: uuid.UUID) -> Presentation | None:
        return (
            self.db.query(Presentation)
            .filter(Presentation.project_id == project_id)
            .order_by(Presentation.created_at.desc())
            .first()
        )

    def create_generation_job(self, generation_job: GenerationJob) -> GenerationJob:
        self.db.add(generation_job)
        self.db.commit()
        self.db.refresh(generation_job)
        return generation_job

    def get_generation_job(
        self,
        project_id: uuid.UUID,
        generation_job_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> GenerationJob | None:
        return (
            self.db.query(GenerationJob)
            .filter(
                GenerationJob.id == generation_job_id,
                GenerationJob.project_id == project_id,
                GenerationJob.organization_id == org_id,
            )
            .first()
        )

    def get_latest_final_video(self, project_id: uuid.UUID, org_id: uuid.UUID) -> Asset | None:
        return (
            self.db.query(Asset)
            .filter(
                Asset.project_id == project_id,
                Asset.organization_id == org_id,
                Asset.asset_type == "final_video",
            )
            .order_by(Asset.created_at.desc())
            .first()
        )

    def get_video_settings(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> VideoGenerationSettings | None:
        return (
            self.db.query(VideoGenerationSettings)
            .filter(
                VideoGenerationSettings.project_id == project_id,
                VideoGenerationSettings.organization_id == org_id,
            )
            .first()
        )

    def save_video_settings(
        self,
        settings: VideoGenerationSettings,
    ) -> VideoGenerationSettings:
        self.db.add(settings)
        self.db.commit()
        self.db.refresh(settings)
        return settings

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
