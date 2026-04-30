import io
import logging
import uuid

from pptx import Presentation as PptxPresentation
from sqlalchemy import delete

import app.models  # noqa: F401
from app.modules.presentations.rendering import render_slide_previews
from app.modules.projects.models import Presentation, PresentationStatus, Slide
from app.providers.storage import get_storage
from app.workers.base_task import JobTask
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)


# ── ping ──────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.ping", bind=True)
def ping(self, message: str = "pong") -> dict:
    """Task de prueba — verifica que el worker está vivo y puede procesar tasks."""
    logger.info("ping recibido: %s", message)
    return {"message": message, "worker": self.request.hostname}


# ── parse_presentation ────────────────────────────────────────────────────────

class ParsePresentationTask(JobTask):
    """Parsea una presentación PPTX y crea registros Slide."""

    name = "app.workers.tasks.parse_presentation"

    def run_job(self, job_id: uuid.UUID, presentation_id: str, **kwargs) -> dict:
        presentation_uuid = uuid.UUID(presentation_id)
        logger.info("Iniciando parse de presentación %s", presentation_id)

        try:
            self.set_progress(job_id, 10.0, "Loading presentation record")
            with worker_db_session() as db:
                presentation = db.get(Presentation, presentation_uuid)
                if not presentation:
                    raise ValueError(f"Presentation not found: {presentation_id}")

                presentation.status = PresentationStatus.processing
                storage_key = presentation.storage_key
                original_filename = presentation.original_filename
                db.commit()

            self.set_progress(job_id, 25.0, "Downloading presentation from storage")
            storage = get_storage()
            pptx_bytes = storage.download_file(storage_key)

            self.set_progress(job_id, 45.0, "Opening PPTX")
            deck = PptxPresentation(io.BytesIO(pptx_bytes))

            self.set_progress(job_id, 65.0, "Extracting slides")
            slide_records = [
                _extract_slide(
                    slide=slide,
                    slide_number=index,
                    slide_width=int(deck.slide_width),
                    slide_height=int(deck.slide_height),
                )
                for index, slide in enumerate(deck.slides, 1)
            ]

            self.set_progress(job_id, 75.0, "Rendering slide previews")
            preview_keys = render_slide_previews(
                pptx_bytes=pptx_bytes,
                presentation_id=presentation_uuid,
                original_filename=original_filename,
                storage=storage,
            )

            self.set_progress(job_id, 85.0, "Saving parsed slides")
            with worker_db_session() as db:
                presentation = db.get(Presentation, presentation_uuid)
                if not presentation:
                    raise ValueError(f"Presentation not found: {presentation_id}")

                db.execute(delete(Slide).where(Slide.presentation_id == presentation_uuid))
                for record in slide_records:
                    db.add(
                        Slide(
                            presentation_id=presentation_uuid,
                            position=record["slide_number"],
                            title=record["title"],
                            notes=record["speaker_notes"] or None,
                            thumbnail_key=preview_keys.get(record["slide_number"]),
                            metadata_={
                                "slide_number": record["slide_number"],
                                "visible_text": record["visible_text"],
                                "dialogue": record["dialogue"],
                                "rendered_image_key": preview_keys.get(record["slide_number"]),
                                "canvas": {
                                    "avatar": {
                                        "enabled": True,
                                        "x": 700,
                                        "y": 290,
                                        "width": 200,
                                        "height": 200,
                                    },
                                    "text": {
                                        "title": record["title"] or "",
                                        "visible_text": record["visible_text"],
                                    },
                                    "text_blocks": record["text_blocks"],
                                },
                            },
                        )
                    )

                presentation.slide_count = len(slide_records)
                presentation.status = PresentationStatus.parsed
                db.commit()

            self.set_progress(job_id, 100.0, "Parsed")
            return {
                "presentation_id": presentation_id,
                "slide_count": len(slide_records),
                "parsed": True,
            }
        except Exception as exc:
            error_message = str(exc)
            with worker_db_session() as db:
                presentation = db.get(Presentation, presentation_uuid)
                if presentation:
                    presentation.status = PresentationStatus.failed
                    db.commit()
            logger.exception("Parse de presentación %s falló: %s", presentation_id, exc)
            raise RuntimeError(error_message) from exc


def _extract_slide(slide, slide_number: int, slide_width: int, slide_height: int) -> dict:
    visible_text = _extract_visible_text(slide)
    speaker_notes = _extract_speaker_notes(slide)
    title = _extract_title(slide, visible_text)
    text_blocks = _extract_text_blocks(slide, slide_width, slide_height)

    return {
        "slide_number": slide_number,
        "title": title,
        "visible_text": visible_text,
        "speaker_notes": speaker_notes,
        "dialogue": speaker_notes,
        "text_blocks": text_blocks,
    }


def _extract_visible_text(slide) -> str:
    parts: list[str] = []
    for shape in slide.shapes:
        if not getattr(shape, "has_text_frame", False):
            continue
        text = _normalize_text(shape.text)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _extract_speaker_notes(slide) -> str:
    if not getattr(slide, "has_notes_slide", False):
        return ""

    notes_slide = slide.notes_slide
    notes_text_frame = getattr(notes_slide, "notes_text_frame", None)
    if notes_text_frame is None:
        return ""
    return _normalize_text(notes_text_frame.text)


def _extract_title(slide, visible_text: str) -> str | None:
    title_shape = getattr(slide.shapes, "title", None)
    if title_shape is not None:
        title = _normalize_text(title_shape.text)
        if title:
            return title[:500]

    for line in visible_text.splitlines():
        if line.strip():
            return line.strip()[:500]
    return None


def _extract_text_blocks(slide, slide_width: int, slide_height: int) -> list[dict]:
    blocks: list[dict] = []
    title_shape = getattr(slide.shapes, "title", None)

    for index, shape in enumerate(slide.shapes):
        if not getattr(shape, "has_text_frame", False):
            continue

        text = _normalize_text(shape.text)
        if not text:
            continue

        block_type = "title" if title_shape is not None and shape == title_shape else "body"
        blocks.append(
            {
                "id": f"{block_type}-{index}",
                "type": block_type,
                "text": text,
                "x": _scale_emu(getattr(shape, "left", 0), slide_width, 960),
                "y": _scale_emu(getattr(shape, "top", 0), slide_height, 540),
                "width": _scale_emu(getattr(shape, "width", 0), slide_width, 960),
                "height": _scale_emu(getattr(shape, "height", 0), slide_height, 540),
                "fontSize": 34 if block_type == "title" else 22,
                "fontWeight": "700" if block_type == "title" else "400",
                "color": "#111827" if block_type == "title" else "#1f2937",
                "textAlign": "left",
            }
        )

    return blocks


def _scale_emu(value: int, source_size: int, target_size: int) -> int:
    if source_size <= 0:
        return 0
    return round((int(value) / source_size) * target_size)


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


# Registrar la task en Celery
parse_presentation = celery_app.register_task(ParsePresentationTask())


# ── Función helper para encolar jobs ─────────────────────────────────────────

def enqueue_parse_presentation(job_id: uuid.UUID, presentation_id: uuid.UUID) -> str:
    """Encola un job de parse_presentation y retorna el celery_task_id."""
    result = parse_presentation.apply_async(
        kwargs={
            "job_id": str(job_id),
            "presentation_id": str(presentation_id),
        },
        queue="presentations",
    )
    return result.id
