import io
import logging
import uuid
import xml.etree.ElementTree as ET

from pptx import Presentation as PptxPresentation
from sqlalchemy import delete

import app.models  # noqa: F401
from app.modules.composer.service import ComposerService
from app.modules.generation.models import GenerationJob, VideoGenerationSettings
from app.modules.presentations.rendering import render_slide_backgrounds, render_slide_previews
from app.modules.projects.models import Asset, Presentation, PresentationStatus, Slide
from app.modules.tts.adapters import get_tts_provider
from app.modules.video.adapters import get_avatar_video_provider
from app.providers.storage import get_storage
from app.utils.crypto import decrypt_secret
from app.workers.base_task import JobTask
from app.workers.celery_app import celery_app
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)

DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PRESENTATIONML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
XML_NS = {"a": DRAWINGML_NS, "p": PRESENTATIONML_NS}
SCHEME_COLOR_ALIASES = {
    "tx1": "dk1",
    "tx2": "dk2",
    "bg1": "lt1",
    "bg2": "lt2",
    "text1": "dk1",
    "text2": "dk2",
    "background1": "lt1",
    "background2": "lt2",
    "accent_1": "accent1",
    "accent_2": "accent2",
    "accent_3": "accent3",
    "accent_4": "accent4",
    "accent_5": "accent5",
    "accent_6": "accent6",
}


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
            theme_colors = _extract_theme_colors(deck)

            self.set_progress(job_id, 65.0, "Extracting slides")
            slide_records = [
                _extract_slide(
                    slide=slide,
                    slide_number=index,
                    slide_width=int(deck.slide_width),
                    slide_height=int(deck.slide_height),
                    theme_colors=theme_colors,
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
            background_keys = render_slide_backgrounds(
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
                                "background_image_key": background_keys.get(
                                    record["slide_number"]
                                ),
                                "canvas": {
                                    "version": 1,
                                    "width": 960,
                                    "height": _scale_emu(
                                        int(deck.slide_height),
                                        int(deck.slide_width),
                                        960,
                                    )
                                    or 540,
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
                                    "background": {
                                        "type": "background",
                                        "key": background_keys.get(record["slide_number"]),
                                    },
                                    "text_blocks": record["text_blocks"],
                                    "elements": [
                                        {
                                            "id": "background",
                                            "type": "background",
                                            "x": 0,
                                            "y": 0,
                                            "width": 960,
                                            "height": _scale_emu(
                                                int(deck.slide_height),
                                                int(deck.slide_width),
                                                960,
                                            )
                                            or 540,
                                            "zIndex": 0,
                                            "src": background_keys.get(record["slide_number"]),
                                        },
                                        *record["elements"],
                                        {
                                            "id": "avatar",
                                            "type": "avatar",
                                            "x": 700,
                                            "y": 290,
                                            "width": 200,
                                            "height": 200,
                                            "zIndex": 1000,
                                        },
                                    ],
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


def _extract_slide(
    slide,
    slide_number: int,
    slide_width: int,
    slide_height: int,
    theme_colors: dict[str, str] | None = None,
) -> dict:
    visible_text = _extract_visible_text(slide)
    speaker_notes = _extract_speaker_notes(slide)
    title = _extract_title(slide, visible_text)
    text_blocks = _extract_text_blocks(slide, slide_width, slide_height, theme_colors or {})
    elements = _text_blocks_to_elements(text_blocks)

    return {
        "slide_number": slide_number,
        "title": title,
        "visible_text": visible_text,
        "speaker_notes": speaker_notes,
        "dialogue": speaker_notes,
        "text_blocks": text_blocks,
        "elements": elements,
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


def _extract_text_blocks(
    slide,
    slide_width: int,
    slide_height: int,
    theme_colors: dict[str, str] | None = None,
) -> list[dict]:
    blocks: list[dict] = []
    title_shape = getattr(slide.shapes, "title", None)
    canvas_width = 960
    canvas_height = _scale_emu(slide_height, slide_width, canvas_width) or 540

    for index, shape in enumerate(slide.shapes):
        if not getattr(shape, "has_text_frame", False):
            continue

        text = _normalize_text(shape.text)
        if not text:
            continue

        block_type = "title" if title_shape is not None and shape == title_shape else "body"
        style = _extract_text_style(shape, block_type, theme_colors or {})
        blocks.append(
            {
                "id": f"{block_type}-{index}",
                "type": block_type,
                "text": text,
                "shape_index": index,
                "x": _scale_emu(getattr(shape, "left", 0), slide_width, canvas_width),
                "y": _scale_emu(getattr(shape, "top", 0), slide_height, canvas_height),
                "width": _scale_emu(getattr(shape, "width", 0), slide_width, canvas_width),
                "height": _scale_emu(getattr(shape, "height", 0), slide_height, canvas_height),
                "fontSize": style["fontSize"],
                "fontWeight": style["fontWeight"],
                "fontFamily": style["fontFamily"],
                "originalFontFamily": style["originalFontFamily"],
                "fallbackFontFamily": style["fallbackFontFamily"],
                "color": style["color"],
                "originalColor": style["originalColor"],
                "textAlign": style["textAlign"],
                "lineHeight": style["lineHeight"],
                "letterSpacing": style["letterSpacing"],
                "bold": style["bold"],
                "italic": style["italic"],
                "underline": style["underline"],
                "style": style,
            }
        )

    return blocks


def _text_blocks_to_elements(text_blocks: list[dict]) -> list[dict]:
    elements: list[dict] = []
    for z_index, block in enumerate(text_blocks, 10):
        elements.append(
            {
                "id": f"text-{block['shape_index']}",
                "type": "text",
                "role": block["type"],
                "shape_index": block["shape_index"],
                "x": block["x"],
                "y": block["y"],
                "width": block["width"],
                "height": block["height"],
                "rotation": 0,
                "zIndex": z_index,
                "text": block["text"],
                "style": block["style"],
            }
        )
    return elements


def _extract_text_style(shape, block_type: str, theme_colors: dict[str, str]) -> dict:
    font = None
    paragraph = None
    run = None
    if getattr(shape, "has_text_frame", False):
        for candidate in shape.text_frame.paragraphs:
            if _normalize_text(candidate.text):
                paragraph = candidate
                for candidate_run in candidate.runs:
                    if _normalize_text(candidate_run.text):
                        run = candidate_run
                        font = candidate_run.font
                        break
                if font is None:
                    font = getattr(candidate, "font", None)
                break

    original_font = _font_name(font)
    bold = bool(getattr(font, "bold", False)) if font else block_type == "title"
    italic = bool(getattr(font, "italic", False)) if font else False
    underline = bool(getattr(font, "underline", False)) if font else False
    font_size = _font_size(font, 34 if block_type == "title" else 22)
    font_weight = "700" if bold or block_type == "title" else "400"
    color = _resolve_text_color(
        run=run,
        paragraph=paragraph,
        shape=shape,
        font=font,
        theme_colors=theme_colors,
        fallback="#111827" if block_type == "title" else "#1f2937",
    )
    return {
        "fontFamily": original_font or "Arial",
        "originalFontFamily": original_font,
        "fallbackFontFamily": "Arial",
        "fontSize": font_size,
        "fontWeight": font_weight,
        "color": color,
        "originalColor": color,
        "bold": bold,
        "italic": italic,
        "underline": underline,
        "textAlign": _paragraph_alignment(paragraph),
        "lineHeight": 1.15,
        "letterSpacing": 0,
        "backgroundColor": "transparent",
    }


def _font_name(font) -> str | None:
    name = getattr(font, "name", None) if font else None
    return str(name) if name else None


def _font_size(font, fallback: int) -> int:
    size = getattr(font, "size", None) if font else None
    if size is None:
        return fallback
    return max(8, round(float(size.pt)))


def _resolve_text_color(
    run,
    paragraph,
    shape,
    font,
    theme_colors: dict[str, str],
    fallback: str,
) -> str:
    color = _color_from_run_xml(run, theme_colors)
    if color:
        return color

    color = _font_color(font, theme_colors)
    if color:
        return color

    color = _color_from_paragraph_xml(paragraph, theme_colors)
    if color:
        return color

    color = _color_from_shape_xml(shape, theme_colors)
    if color:
        return color

    color = _color_from_placeholder_styles(shape, theme_colors)
    if color:
        return color

    return fallback


def _font_color(font, theme_colors: dict[str, str]) -> str | None:
    color = getattr(font, "color", None) if font else None
    try:
        rgb = color.rgb if color else None
    except AttributeError:
        rgb = None
    if rgb:
        return f"#{rgb}".upper()

    try:
        theme_color = color.theme_color if color else None
    except AttributeError:
        theme_color = None
    if theme_color:
        return _resolve_scheme_color(str(getattr(theme_color, "name", theme_color)), theme_colors)
    return None


def _color_from_run_xml(run, theme_colors: dict[str, str]) -> str | None:
    if run is None:
        return None
    run_element = getattr(run, "_r", None)
    if run_element is None:
        return None
    rpr = run_element.find("a:rPr", namespaces=XML_NS)
    return _color_from_solid_fill(rpr, theme_colors)


def _color_from_paragraph_xml(paragraph, theme_colors: dict[str, str]) -> str | None:
    if paragraph is None:
        return None
    paragraph_element = getattr(paragraph, "_p", None)
    if paragraph_element is None:
        return None
    for path in ("a:pPr/a:defRPr", "a:pPr/a:endParaRPr"):
        color = _color_from_solid_fill(
            paragraph_element.find(path, namespaces=XML_NS),
            theme_colors,
        )
        if color:
            return color
    return None


def _color_from_shape_xml(shape, theme_colors: dict[str, str]) -> str | None:
    shape_element = getattr(shape, "_element", None)
    if shape_element is None:
        return None
    for path in (
        ".//a:lstStyle/a:lvl1pPr/a:defRPr",
        ".//a:lstStyle/a:lvl2pPr/a:defRPr",
        ".//a:lstStyle/a:defPPr/a:defRPr",
        ".//a:defRPr",
    ):
        color = _color_from_solid_fill(shape_element.find(path, namespaces=XML_NS), theme_colors)
        if color:
            return color
    return None


def _color_from_placeholder_styles(shape, theme_colors: dict[str, str]) -> str | None:
    if not getattr(shape, "is_placeholder", False):
        return None
    try:
        placeholder_idx = shape.placeholder_format.idx
        layout = shape.part.slide_layout
    except (AttributeError, KeyError):
        return None

    for placeholder_collection in (
        getattr(layout, "placeholders", []),
        getattr(getattr(layout, "slide_master", None), "placeholders", []),
    ):
        for placeholder in placeholder_collection:
            try:
                if placeholder.placeholder_format.idx != placeholder_idx:
                    continue
            except (AttributeError, KeyError):
                continue
            color = _color_from_shape_xml(placeholder, theme_colors)
            if color:
                return color
    return None


def _color_from_solid_fill(element, theme_colors: dict[str, str]) -> str | None:
    if element is None:
        return None
    solid_fill = element.find(".//a:solidFill", namespaces=XML_NS)
    if solid_fill is None and _local_name(getattr(element, "tag", "")) == "solidFill":
        solid_fill = element
    if solid_fill is None:
        return None

    srgb = solid_fill.find("a:srgbClr", namespaces=XML_NS)
    if srgb is not None:
        value = srgb.get("val")
        if value:
            return _apply_luminance(_hex(value), srgb)

    scheme = solid_fill.find("a:schemeClr", namespaces=XML_NS)
    if scheme is not None:
        value = scheme.get("val")
        resolved = _resolve_scheme_color(value, theme_colors)
        if resolved:
            return _apply_luminance(resolved, scheme)

    return None


def _extract_theme_colors(deck) -> dict[str, str]:
    colors: dict[str, str] = {}
    try:
        theme_parts = [
            part
            for part in deck.part.package.iter_parts()
            if "theme" in str(part.partname)
        ]
    except AttributeError:
        return colors

    for theme_part in theme_parts:
        try:
            root = ET.fromstring(theme_part.blob)
        except ET.ParseError:
            continue
        color_scheme = root.find(".//a:clrScheme", namespaces=XML_NS)
        if color_scheme is None:
            continue
        for child in list(color_scheme):
            name = _local_name(child.tag)
            srgb = child.find("a:srgbClr", namespaces=XML_NS)
            if srgb is not None and srgb.get("val"):
                colors[name] = _hex(srgb.get("val"))
                continue
            sys_color = child.find("a:sysClr", namespaces=XML_NS)
            if sys_color is not None and sys_color.get("lastClr"):
                colors[name] = _hex(sys_color.get("lastClr"))
    return colors


def _resolve_scheme_color(value: str | None, theme_colors: dict[str, str]) -> str | None:
    if not value:
        return None
    key = str(value)
    normalized = key.lower().replace(" ", "").replace("-", "_")
    candidates = [
        key,
        normalized,
        normalized.replace("_", ""),
        SCHEME_COLOR_ALIASES.get(normalized),
        SCHEME_COLOR_ALIASES.get(normalized.replace("_", "")),
    ]
    for candidate in candidates:
        if candidate and candidate in theme_colors:
            return theme_colors[candidate]
    return None


def _apply_luminance(hex_color: str, element) -> str:
    rgb = _rgb_tuple(hex_color)
    if rgb is None:
        return hex_color

    lum_mod = _color_modifier(element, "lumMod", 100000)
    lum_off = _color_modifier(element, "lumOff", 0)
    adjusted = [
        round(channel * (lum_mod / 100000) + 255 * (lum_off / 100000))
        for channel in rgb
    ]
    return _rgb_hex(tuple(clamp for clamp in (_clamp_channel(value) for value in adjusted)))


def _color_modifier(element, name: str, fallback: int) -> int:
    child = element.find(f"a:{name}", namespaces=XML_NS)
    if child is None:
        return fallback
    try:
        return int(child.get("val", fallback))
    except (TypeError, ValueError):
        return fallback


def _hex(value: str | None) -> str:
    if not value:
        return "#000000"
    clean = value.strip().lstrip("#").upper()
    if len(clean) == 3:
        clean = "".join(char * 2 for char in clean)
    return f"#{clean[:6].ljust(6, '0')}"


def _rgb_tuple(hex_color: str) -> tuple[int, int, int] | None:
    clean = hex_color.strip().lstrip("#")
    if len(clean) != 6:
        return None
    try:
        return tuple(int(clean[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None


def _rgb_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _clamp_channel(value: int) -> int:
    return max(0, min(255, value))


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _paragraph_alignment(paragraph) -> str:
    alignment = getattr(paragraph, "alignment", None)
    if alignment is None:
        return "left"
    name = getattr(alignment, "name", "")
    if name == "CENTER":
        return "center"
    if name == "RIGHT":
        return "right"
    if name in {"JUSTIFY", "DISTRIBUTE"}:
        return "justify"
    return "left"


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


class GenerateVideoTask(JobTask):
    name = "app.workers.tasks.generate_video"

    def run_job(
        self,
        job_id: uuid.UUID,
        generation_job_id: str,
        project_id: str,
        **kwargs,
    ) -> dict:
        generation_uuid = uuid.UUID(generation_job_id)
        project_uuid = uuid.UUID(project_id)
        storage = get_storage()
        composer = ComposerService()

        try:
            self.set_progress(job_id, 5.0, "Validating configuration")
            _update_generation_job(generation_uuid, "validating", 5.0, "Validating configuration")

            with worker_db_session() as db:
                generation_job = db.get(GenerationJob, generation_uuid)
                if not generation_job:
                    raise RuntimeError("Generation job not found")
                project = generation_job.project_id
                presentation = (
                    db.query(Presentation)
                    .filter(Presentation.project_id == project_uuid)
                    .order_by(Presentation.created_at.desc())
                    .first()
                )
                if presentation is None:
                    raise RuntimeError("PRESENTATION_NOT_FOUND")
                settings = (
                    db.query(VideoGenerationSettings)
                    .filter(VideoGenerationSettings.project_id == project_uuid)
                    .first()
                )
                if settings is None:
                    raise RuntimeError("VIDEO_SETTINGS_NOT_CONFIGURED")
                elevenlabs_api_key = decrypt_secret(settings.elevenlabs_api_key_encrypted)
                wavespeed_api_key = decrypt_secret(settings.wavespeed_api_key_encrypted)
                if not elevenlabs_api_key or not settings.elevenlabs_voice_id:
                    raise RuntimeError("INVALID_ELEVENLABS_CREDENTIALS")
                if not wavespeed_api_key:
                    raise RuntimeError("INVALID_WAVESPEED_CREDENTIALS")
                slides = list(presentation.slides)
                organization_id = presentation.organization_id

            tts_provider = get_tts_provider("elevenlabs")
            avatar_provider = get_avatar_video_provider("wavespeed")
            audio_assets: list[tuple[uuid.UUID, bytes, float]] = []
            slide_videos: list[bytes] = []
            total_slides = len(slides)

            self.set_progress(job_id, 20.0, "Generating audio")
            _update_generation_job(
                generation_uuid,
                "generating_audio",
                20.0,
                "Generating ElevenLabs audio",
                total_slides=total_slides,
            )
            for index, slide in enumerate(slides, 1):
                _update_generation_job(
                    generation_uuid,
                    "generating_audio",
                    20.0 + (15.0 * ((index - 1) / max(total_slides, 1))),
                    f"Generating audio for slide {index}",
                    current_slide=index,
                    total_slides=total_slides,
                )
                metadata = slide.metadata_ or {}
                dialogue = str(metadata.get("dialogue") or slide.notes or "")
                audio_bytes, duration = tts_provider.generate_audio(
                    text=dialogue,
                    voice_id=settings.elevenlabs_voice_id,
                    language="es",
                )
                key = (
                    f"orgs/{organization_id}/projects/{project}/generation/"
                    f"{generation_uuid}/audio/slide-{index}.wav"
                )
                storage.upload_file(key, audio_bytes, "audio/wav")
                _create_asset(
                    organization_id=organization_id,
                    project_id=project,
                    slide_id=slide.id,
                    asset_type="tts_audio",
                    storage_key=key,
                    filename=f"slide-{index}.wav",
                    mime_type="audio/wav",
                    size_bytes=len(audio_bytes),
                    duration_seconds=duration,
                    metadata_json={"dialogue": dialogue, "provider": "elevenlabs"},
                )
                audio_assets.append((slide.id, audio_bytes, duration))

            self.set_progress(job_id, 45.0, "Generating avatar clips")
            _update_generation_job(
                generation_uuid,
                "generating_avatar",
                45.0,
                "Generating WaveSpeed avatar clips",
                total_slides=total_slides,
            )
            for index, (slide_id, audio_bytes, duration) in enumerate(audio_assets, 1):
                _update_generation_job(
                    generation_uuid,
                    "generating_avatar",
                    35.0 + (25.0 * ((index - 1) / max(total_slides, 1))),
                    f"Generating avatar clip for slide {index}",
                    current_slide=index,
                    total_slides=total_slides,
                )
                clip = avatar_provider.generate_avatar_clip(
                    audio_bytes=audio_bytes,
                    duration_seconds=duration,
                    avatar_id=None,
                )
                key = (
                    f"orgs/{organization_id}/projects/{project}/generation/"
                    f"{generation_uuid}/avatar/slide-{index}.mp4"
                )
                storage.upload_file(key, clip, "video/mp4")
                _create_asset(
                    organization_id=organization_id,
                    project_id=project,
                    slide_id=slide_id,
                    asset_type="avatar_clip",
                    storage_key=key,
                    filename=f"avatar-slide-{index}.mp4",
                    mime_type="video/mp4",
                    size_bytes=len(clip),
                    duration_seconds=duration,
                    metadata_json={"provider": "wavespeed"},
                )

            self.set_progress(job_id, 65.0, "Composing slide videos")
            _update_generation_job(
                generation_uuid,
                "rendering_slides",
                60.0,
                "Rendering edited slides",
                total_slides=total_slides,
            )
            for index, slide in enumerate(slides, 1):
                _update_generation_job(
                    generation_uuid,
                    "rendering_slides",
                    60.0 + (20.0 * ((index - 1) / max(total_slides, 1))),
                    f"Rendering slide {index}",
                    current_slide=index,
                    total_slides=total_slides,
                )
                metadata = slide.metadata_ or {}
                background_key = metadata.get("background_image_key")
                background_bytes = (
                    storage.download_file(background_key)
                    if isinstance(background_key, str) and background_key
                    else None
                )
                slide_image = composer.render_slide_image(
                    background_bytes=background_bytes,
                    slide_metadata=metadata,
                    resolution="1080p",
                )
                render_key = (
                    f"orgs/{organization_id}/projects/{project}/generation/"
                    f"{generation_uuid}/renders/slide-{index}.png"
                )
                storage.upload_file(render_key, slide_image, "image/png")
                _create_asset(
                    organization_id=organization_id,
                    project_id=project,
                    slide_id=slide.id,
                    asset_type="slide_render",
                    storage_key=render_key,
                    filename=f"slide-{index}.png",
                    mime_type="image/png",
                    size_bytes=len(slide_image),
                    metadata_json={"canvas": metadata.get("canvas")},
                )

                _slide_id, audio_bytes, duration = audio_assets[index - 1]
                _update_generation_job(
                    generation_uuid,
                    "composing_video",
                    80.0 + (15.0 * ((index - 1) / max(total_slides, 1))),
                    f"Composing video for slide {index}",
                    current_slide=index,
                    total_slides=total_slides,
                )
                slide_video = composer.compose_slide_video(slide_image, audio_bytes, duration)
                slide_video_key = (
                    f"orgs/{organization_id}/projects/{project}/generation/"
                    f"{generation_uuid}/slides/slide-{index}.mp4"
                )
                storage.upload_file(slide_video_key, slide_video, "video/mp4")
                _create_asset(
                    organization_id=organization_id,
                    project_id=project,
                    slide_id=slide.id,
                    asset_type="slide_video",
                    storage_key=slide_video_key,
                    filename=f"slide-{index}.mp4",
                    mime_type="video/mp4",
                    size_bytes=len(slide_video),
                    duration_seconds=duration,
                )
                slide_videos.append(slide_video)

            self.set_progress(job_id, 90.0, "Creating final MP4")
            _update_generation_job(
                generation_uuid,
                "composing_video",
                90.0,
                "Creating final MP4",
                total_slides=total_slides,
            )
            final_video = composer.concatenate_slide_videos(slide_videos)
            final_key = (
                f"orgs/{organization_id}/projects/{project}/output/final-{generation_uuid}.mp4"
            )
            storage.upload_file(final_key, final_video, "video/mp4")
            final_asset = _create_asset(
                organization_id=organization_id,
                project_id=project,
                slide_id=None,
                asset_type="final_video",
                storage_key=final_key,
                filename="final.mp4",
                mime_type="video/mp4",
                size_bytes=len(final_video),
            )

            result = {"final_video_key": final_key, "final_asset_id": str(final_asset.id)}
            _complete_generation_job(generation_uuid, final_asset.id, result)
            self.set_progress(job_id, 100.0, "Completed")
            return result
        except Exception as exc:
            _fail_generation_job(generation_uuid, "VIDEO_GENERATION_FAILED", str(exc))
            logger.exception("Video generation job %s failed: %s", generation_job_id, exc)
            raise


generate_video = celery_app.register_task(GenerateVideoTask())


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


def enqueue_generate_video(
    job_id: uuid.UUID,
    generation_job_id: uuid.UUID,
    project_id: uuid.UUID,
) -> str:
    result = generate_video.apply_async(
        kwargs={
            "job_id": str(job_id),
            "generation_job_id": str(generation_job_id),
            "project_id": str(project_id),
        },
        queue="generation",
    )
    return result.id


def _update_generation_job(
    generation_job_id: uuid.UUID,
    status: str,
    progress: float,
    current_step: str,
    current_slide: int | None = None,
    total_slides: int | None = None,
) -> None:
    with worker_db_session() as db:
        generation_job = db.get(GenerationJob, generation_job_id)
        if generation_job:
            generation_job.status = status
            generation_job.progress_percentage = progress
            generation_job.current_step = current_step
            if current_slide is not None:
                generation_job.current_slide = current_slide
            if total_slides is not None:
                generation_job.total_slides = total_slides
            db.commit()


def _complete_generation_job(
    generation_job_id: uuid.UUID,
    final_asset_id: uuid.UUID,
    result: dict,
) -> None:
    with worker_db_session() as db:
        generation_job = db.get(GenerationJob, generation_job_id)
        if generation_job:
            generation_job.status = "completed"
            generation_job.progress_percentage = 100.0
            generation_job.current_step = "Completed"
            generation_job.final_asset_id = final_asset_id
            generation_job.result = result
            db.commit()


def _fail_generation_job(
    generation_job_id: uuid.UUID,
    error_code: str,
    error_message: str,
) -> None:
    with worker_db_session() as db:
        generation_job = db.get(GenerationJob, generation_job_id)
        if generation_job:
            generation_job.status = "failed"
            generation_job.error_code = error_code
            generation_job.error_message = error_message[:2000]
            db.commit()


def _create_asset(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    slide_id: uuid.UUID | None,
    asset_type: str,
    storage_key: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    duration_seconds: float | None = None,
    metadata_json: dict | None = None,
) -> Asset:
    with worker_db_session() as db:
        asset = Asset(
            organization_id=organization_id,
            project_id=project_id,
            slide_id=slide_id,
            asset_type=asset_type,
            storage_key=storage_key,
            filename=filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            duration_seconds=duration_seconds,
            metadata_json=metadata_json,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return asset
