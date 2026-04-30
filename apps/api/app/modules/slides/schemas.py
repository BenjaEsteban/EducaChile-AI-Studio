import logging
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.projects.models import Slide
from app.providers.storage import get_storage

logger = logging.getLogger(__name__)


class SlideRead(BaseModel):
    id: uuid.UUID
    presentation_id: uuid.UUID
    position: int
    title: str | None
    notes: str | None
    thumbnail_key: str | None
    preview_image_url: str | None
    visible_text: str
    dialogue: str
    metadata: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, slide: Slide) -> "SlideRead":
        metadata = dict(slide.metadata_ or {})
        preview_image_url = None
        if slide.thumbnail_key:
            try:
                preview_image_url = get_storage().generate_presigned_download_url(
                    slide.thumbnail_key
                ).url
            except Exception as exc:
                logger.warning("Could not generate slide preview URL for %s: %s", slide.id, exc)
        return cls(
            id=slide.id,
            presentation_id=slide.presentation_id,
            position=slide.position,
            title=slide.title,
            notes=slide.notes,
            thumbnail_key=slide.thumbnail_key,
            preview_image_url=preview_image_url,
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
