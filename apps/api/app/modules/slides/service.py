import uuid

from fastapi import HTTPException, status

from app.modules.projects.service import MOCK_ORG_ID
from app.modules.slides.repository import SlideRepository
from app.modules.slides.schemas import SlideRead, SlideUpdate


class SlideService:
    def __init__(self, repo: SlideRepository) -> None:
        self.repo = repo

    def list_by_presentation(self, presentation_id: uuid.UUID) -> list[SlideRead]:
        slides = self.repo.list_by_presentation(presentation_id, MOCK_ORG_ID)
        if slides is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Presentation not found",
            )
        return [SlideRead.from_model(slide) for slide in slides]

    def get_or_404(self, slide_id: uuid.UUID) -> SlideRead:
        slide = self.repo.get_by_id(slide_id, MOCK_ORG_ID)
        if not slide:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Slide not found",
            )
        return SlideRead.from_model(slide)

    def update(self, slide_id: uuid.UUID, data: SlideUpdate) -> SlideRead:
        slide = self.repo.get_by_id(slide_id, MOCK_ORG_ID)
        if not slide:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Slide not found",
            )

        update_data = data.model_dump(exclude_unset=True)
        if "title" in update_data:
            slide.title = update_data["title"]
        if "notes" in update_data:
            slide.notes = update_data["notes"]

        metadata = dict(slide.metadata_ or {})
        if "metadata" in update_data and update_data["metadata"] is not None:
            metadata.update(update_data["metadata"])
        if "dialogue" in update_data:
            metadata["dialogue"] = update_data["dialogue"] or ""
        if "visible_text" in update_data:
            metadata["visible_text"] = update_data["visible_text"] or ""
        slide.metadata_ = metadata

        return SlideRead.from_model(self.repo.save(slide))

