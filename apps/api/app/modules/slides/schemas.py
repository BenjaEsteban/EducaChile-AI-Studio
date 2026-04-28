import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.projects.models import Slide


class SlideRead(BaseModel):
    id: uuid.UUID
    presentation_id: uuid.UUID
    position: int
    title: str | None
    notes: str | None
    visible_text: str
    dialogue: str
    metadata: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, slide: Slide) -> "SlideRead":
        metadata = dict(slide.metadata_ or {})
        return cls(
            id=slide.id,
            presentation_id=slide.presentation_id,
            position=slide.position,
            title=slide.title,
            notes=slide.notes,
            visible_text=metadata.get("visible_text") or "",
            dialogue=metadata.get("dialogue") or "",
            metadata=metadata,
            created_at=slide.created_at,
            updated_at=slide.updated_at,
        )


class SlideUpdate(BaseModel):
    title: str | None = None
    notes: str | None = None
    dialogue: str | None = None
    visible_text: str | None = None
    metadata: dict | None = Field(default=None)

