import uuid

from sqlalchemy.orm import Session

from app.modules.projects.models import Presentation, Slide


class SlideRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_by_presentation(
        self,
        presentation_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> list[Slide] | None:
        presentation = (
            self.db.query(Presentation)
            .filter(
                Presentation.id == presentation_id,
                Presentation.organization_id == org_id,
            )
            .first()
        )
        if presentation is None:
            return None

        return (
            self.db.query(Slide)
            .filter(Slide.presentation_id == presentation_id)
            .order_by(Slide.position.asc())
            .all()
        )

    def get_by_id(self, slide_id: uuid.UUID, org_id: uuid.UUID) -> Slide | None:
        return (
            self.db.query(Slide)
            .join(Presentation, Presentation.id == Slide.presentation_id)
            .filter(
                Slide.id == slide_id,
                Presentation.organization_id == org_id,
            )
            .first()
        )

    def save(self, slide: Slide) -> Slide:
        self.db.commit()
        self.db.refresh(slide)
        return slide

    def list_by_presentation_id(self, presentation_id: uuid.UUID) -> list[Slide]:
        return (
            self.db.query(Slide)
            .filter(Slide.presentation_id == presentation_id)
            .order_by(Slide.position.asc())
            .all()
        )

    def commit(self) -> None:
        self.db.commit()
