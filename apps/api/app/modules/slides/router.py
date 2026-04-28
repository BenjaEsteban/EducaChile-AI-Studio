import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.modules.slides.repository import SlideRepository
from app.modules.slides.schemas import SlideRead, SlideUpdate
from app.modules.slides.service import SlideService

router = APIRouter(tags=["slides"])


def get_service(db: Session = Depends(get_db)) -> SlideService:
    return SlideService(SlideRepository(db))


@router.get("/presentations/{presentation_id}/slides", response_model=list[SlideRead])
def list_presentation_slides(
    presentation_id: uuid.UUID,
    service: SlideService = Depends(get_service),
):
    return service.list_by_presentation(presentation_id)


@router.get("/slides/{slide_id}", response_model=SlideRead)
def get_slide(
    slide_id: uuid.UUID,
    service: SlideService = Depends(get_service),
):
    return service.get_or_404(slide_id)


@router.patch("/slides/{slide_id}", response_model=SlideRead)
def update_slide(
    slide_id: uuid.UUID,
    data: SlideUpdate,
    service: SlideService = Depends(get_service),
):
    return service.update(slide_id, data)
