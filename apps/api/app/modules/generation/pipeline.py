from __future__ import annotations

import ipaddress
import json
import logging
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.modules.composer.service import ComposerService
from app.modules.generation.models import GenerationJob, VideoGenerationSettings
from app.modules.projects.models import Asset, Presentation, Slide
from app.modules.tts.adapters import TTSProviderError, get_tts_provider
from app.modules.video.adapters import AvatarVideoProviderError, get_avatar_video_provider
from app.utils.crypto import decrypt_secret

logger = logging.getLogger(__name__)


@dataclass
class GenerationContext:
    generation_job_id: uuid.UUID
    project_id: uuid.UUID
    organization_id: uuid.UUID
    presentation: Presentation
    slides: list[Slide]
    settings: VideoGenerationSettings
    elevenlabs_api_key: str
    elevenlabs_voice_id: str
    wavespeed_api_key: str
    avatar_source_url: str
    avatar_source_storage_key: str | None
    output_prefix: str


class PipelineError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str | None = None,
        slide_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.slide_index = slide_index


def validate_generation_job(
    db: Session,
    job: GenerationJob,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    storage,
) -> GenerationContext:
    logger.info("Generation job %s: validating", job.id)
    update_generation_job_progress(
        db,
        job,
        status="validating",
        progress_percentage=5.0,
        current_step="Validating generation request",
    )
    presentation = (
        db.query(Presentation)
        .filter(Presentation.project_id == project_id)
        .order_by(Presentation.created_at.desc())
        .first()
    )
    if presentation is None:
        raise PipelineError("validation_failed", "Presentation not found", stage="validating")
    settings_row = (
        db.query(VideoGenerationSettings)
        .filter(VideoGenerationSettings.project_id == project_id)
        .first()
    )
    if settings_row is None:
        raise PipelineError(
            "validation_failed",
            "Video settings are not configured",
            stage="validating",
        )
    elevenlabs_api_key = decrypt_secret(settings_row.elevenlabs_api_key_encrypted)
    wavespeed_api_key = decrypt_secret(settings_row.wavespeed_api_key_encrypted)
    if not elevenlabs_api_key:
        raise PipelineError(
            "validation_failed",
            "ElevenLabs API key is missing",
            stage="validating",
        )
    if not settings_row.elevenlabs_voice_id:
        raise PipelineError(
            "validation_failed",
            "ElevenLabs voice ID is missing",
            stage="validating",
        )
    if not wavespeed_api_key:
        raise PipelineError(
            "validation_failed",
            "WaveSpeed API key is missing",
            stage="validating",
        )
    avatar_source_url, avatar_source_storage_key = _avatar_source_url(db, settings_row, storage)
    if not avatar_source_url:
        raise PipelineError(
            "validation_failed",
            "Please upload an avatar image before generating the video.",
            stage="validating",
        )
    slides = list(presentation.slides)
    if not slides:
        raise PipelineError("validation_failed", "Presentation has no slides", stage="validating")
    return GenerationContext(
        generation_job_id=job.id,
        project_id=project_id,
        organization_id=organization_id,
        presentation=presentation,
        slides=slides,
        settings=settings_row,
        elevenlabs_api_key=elevenlabs_api_key,
        elevenlabs_voice_id=settings_row.elevenlabs_voice_id,
        wavespeed_api_key=wavespeed_api_key,
        avatar_source_url=avatar_source_url,
        avatar_source_storage_key=avatar_source_storage_key,
        output_prefix=f"orgs/{organization_id}/projects/{project_id}/generation/{job.id}",
    )


def generate_audio_for_slide(
    db: Session,
    storage,
    job: GenerationJob,
    context: GenerationContext,
    slide: Slide,
    slide_index: int,
    total_slides: int,
) -> Asset:
    existing = _find_valid_asset(
        db,
        storage,
        context,
        slide,
        slide_index,
        "slide_audio",
    )
    if existing:
        logger.info(
            "Generation job %s: reusing existing audio asset for slide %s",
            job.id,
            slide_index,
        )
        _stage_progress(db, job, "generating_audio", slide_index, total_slides, 5, 20)
        return existing

    logger.info(
        "Generation job %s: generating audio for slide %s/%s",
        job.id,
        slide_index,
        total_slides,
    )
    _stage_progress(db, job, "generating_audio", slide_index - 1, total_slides, 5, 20, slide_index)
    metadata = slide.metadata_ or {}
    dialogue = str(metadata.get("dialogue") or slide.notes or "")
    try:
        audio_bytes, _duration = get_tts_provider("elevenlabs").generate_audio(
            text=dialogue,
            voice_id=context.elevenlabs_voice_id,
            language="es",
            api_key=context.elevenlabs_api_key,
        )
    except TTSProviderError as exc:
        raise PipelineError(
            "audio_generation_failed",
            str(exc),
            stage="generating_audio",
            slide_index=slide_index,
        ) from exc
    audio_info = _probe_media_info(audio_bytes, ".mp3")
    duration = float(audio_info.get("duration_seconds") or 0)
    if duration <= 0 or not audio_info.get("has_audio"):
        raise PipelineError(
            "audio_generation_failed",
            f"Audio for slide {slide_index} is invalid",
            stage="generating_audio",
            slide_index=slide_index,
        )
    key = f"{context.output_prefix}/audio/slide-{slide_index}.mp3"
    storage.upload_file(key, audio_bytes, "audio/mpeg")
    asset = _create_asset(
        db,
        context=context,
        slide=slide,
        asset_type="slide_audio",
        storage_key=key,
        filename=f"slide-{slide_index}.mp3",
        mime_type="audio/mpeg",
        size_bytes=len(audio_bytes),
        duration_seconds=duration,
        metadata_json={
            "slide_position": slide_index,
            "generation_job_id": str(job.id),
            "provider": "elevenlabs",
            "voice_id": context.elevenlabs_voice_id,
            "audio_probe": audio_info,
        },
    )
    _stage_progress(db, job, "generating_audio", slide_index, total_slides, 5, 20, slide_index)
    return asset


def generate_avatar_clip_for_slide(
    db: Session,
    storage,
    job: GenerationJob,
    context: GenerationContext,
    slide: Slide,
    slide_index: int,
    total_slides: int,
    audio_asset: Asset,
) -> Asset:
    existing = _find_valid_asset(
        db,
        storage,
        context,
        slide,
        slide_index,
        "generated_avatar_clip",
    )
    if existing:
        logger.info(
            "Generation job %s: reusing existing avatar clip for slide %s",
            job.id,
            slide_index,
        )
        _stage_progress(db, job, "generating_avatar", slide_index, total_slides, 25, 35)
        return existing

    logger.info(
        "Generation job %s: generating avatar clip for slide %s/%s",
        job.id,
        slide_index,
        total_slides,
    )
    _stage_progress(
        db,
        job,
        "generating_avatar",
        slide_index - 1,
        total_slides,
        25,
        35,
        slide_index,
    )
    audio_bytes = storage.download_bytes(audio_asset.storage_key)
    audio_url = _external_asset_url(storage, audio_asset.storage_key)
    try:
        clip, avatar_metadata = _generate_avatar_with_motion_fallback(
            audio_bytes=audio_bytes,
            duration=float(audio_asset.duration_seconds or 0),
            avatar_source_url=context.avatar_source_url,
            wavespeed_api_key=context.wavespeed_api_key,
            audio_url=audio_url,
            slide_index=slide_index,
            generation_uuid=job.id,
        )
    except AvatarVideoProviderError as exc:
        raise PipelineError(
            "avatar_generation_failed",
            str(exc),
            stage="generating_avatar",
            slide_index=slide_index,
        ) from exc
    avatar_info = _probe_media_info(clip, ".mp4")
    duration = float(avatar_info.get("duration_seconds") or 0)
    if duration <= 0 or not avatar_info.get("has_video"):
        raise PipelineError(
            "avatar_generation_failed",
            f"Avatar clip for slide {slide_index} is invalid",
            stage="generating_avatar",
            slide_index=slide_index,
        )
    motion = avatar_metadata.get("motion_analysis") or _analyze_video_motion(clip)
    if motion.get("almost_static"):
        logger.warning(
            "Generated avatar clip appears almost static: generation_job=%s slide=%s score=%s",
            job.id,
            slide_index,
            motion.get("motion_score"),
        )
    key = f"{context.output_prefix}/avatar/slide-{slide_index}.mp4"
    storage.upload_file(key, clip, "video/mp4")
    asset = _create_asset(
        db,
        context=context,
        slide=slide,
        asset_type="generated_avatar_clip",
        storage_key=key,
        filename=f"avatar-slide-{slide_index}.mp4",
        mime_type="video/mp4",
        size_bytes=len(clip),
        duration_seconds=duration,
        metadata_json={
            **avatar_metadata,
            "slide_position": slide_index,
            "generation_job_id": str(job.id),
            "provider": "wavespeed",
            "model_used": avatar_metadata.get("model"),
            "ffprobe": avatar_info,
            "motion_analysis": motion,
        },
    )
    _stage_progress(db, job, "generating_avatar", slide_index, total_slides, 25, 35, slide_index)
    return asset


def compose_segment_for_slide(
    db: Session,
    storage,
    job: GenerationJob,
    context: GenerationContext,
    slide: Slide,
    slide_index: int,
    total_slides: int,
    audio_asset: Asset,
    avatar_clip_asset: Asset,
) -> Asset:
    existing = _find_valid_asset(
        db,
        storage,
        context,
        slide,
        slide_index,
        "slide_segment_video",
    )
    if existing:
        logger.info(
            "Generation job %s: reusing existing slide segment for slide %s",
            job.id,
            slide_index,
        )
        _stage_progress(db, job, "composing_slide", slide_index, total_slides, 60, 25)
        return existing

    logger.info(
        "Generation job %s: composing segment for slide %s/%s",
        job.id,
        slide_index,
        total_slides,
    )
    _stage_progress(db, job, "composing_slide", slide_index - 1, total_slides, 60, 25, slide_index)
    composer = ComposerService()
    metadata = slide.metadata_ or {}
    slide_image, slide_preview_source = _load_slide_preview_image(storage, slide, metadata)
    logger.info(
        "Generation job %s: selected slide preview for slide %s source=%s key=%s includes_text=%s",
        job.id,
        slide_index,
        slide_preview_source.get("source"),
        slide_preview_source.get("storage_key"),
        slide_preview_source.get("includes_text"),
    )
    if slide_preview_source.get("warning"):
        logger.warning(
            "Generation job %s slide %s: %s",
            job.id,
            slide_index,
            slide_preview_source["warning"],
        )
    composition_used_generated_avatar_clip = True
    composition_fallback_reason = None
    avatar_source_asset_id = str(avatar_clip_asset.id)
    avatar_source_storage_key = avatar_clip_asset.storage_key
    avatar_clip_info: dict = {}
    try:
        audio_bytes = storage.download_bytes(audio_asset.storage_key)
        audio_info = _probe_media_info(audio_bytes, ".mp3")
        avatar_clip, fallback_reason, avatar_clip_info = _load_avatar_clip_or_static_fallback(
            storage=storage,
            context=context,
            avatar_clip_asset=avatar_clip_asset,
            duration_seconds=float(audio_asset.duration_seconds or 0),
        )
        if fallback_reason:
            composition_used_generated_avatar_clip = False
            composition_fallback_reason = fallback_reason
            avatar_source_asset_id = None
            avatar_source_storage_key = None
            logger.warning(
                "Composing slide %s using avatar STATIC IMAGE fallback: %s",
                slide_index,
                fallback_reason,
            )
        else:
            logger.info(
                "Composing slide %s using avatar VIDEO overlay: %s",
                slide_index,
                avatar_clip_asset.storage_key,
            )
        logger.info(
            "Generation job %s: composing slide %s with slide preview source=%s audio=%s "
            "audio_duration=%s avatar_duration=%s",
            job.id,
            slide_index,
            slide_preview_source.get("storage_key"),
            audio_asset.storage_key,
            audio_info.get("duration_seconds"),
            avatar_clip_info.get("duration_seconds"),
        )
        segment = composer.compose_slide_video(
            slide_image_bytes=slide_image,
            avatar_clip_bytes=avatar_clip,
            audio_bytes=audio_bytes,
            duration_seconds=float(audio_asset.duration_seconds or 0),
            avatar_overlay=_avatar_overlay_from_metadata(metadata, "1080p"),
            resolution="1080p",
        )
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            "slide_composition_failed",
            _ffmpeg_error_message(exc),
            stage="composing_slide",
            slide_index=slide_index,
        ) from exc
    segment_info = _probe_media_info(segment, ".mp4")
    segment_motion = _analyze_video_motion(segment)
    avatar_motion = (avatar_clip_asset.metadata_json or {}).get("motion_analysis") or {}
    composition_motion_warning = None
    if (
        composition_used_generated_avatar_clip
        and avatar_motion
        and not avatar_motion.get("almost_static")
        and segment_motion.get("almost_static")
    ):
        composition_motion_warning = (
            "Generated avatar clip appears animated, but composed segment appears almost static."
        )
        logger.warning(
            "Generation job %s slide %s: %s avatar_motion=%s segment_motion=%s",
            job.id,
            slide_index,
            composition_motion_warning,
            avatar_motion,
            segment_motion,
        )
    duration = float(segment_info.get("duration_seconds") or 0)
    if duration <= 0 or not segment_info.get("has_video") or not segment_info.get("has_audio"):
        raise PipelineError(
            "slide_composition_failed",
            f"Composed segment for slide {slide_index} is invalid",
            stage="composing_slide",
            slide_index=slide_index,
        )
    key = f"{context.output_prefix}/segments/slide-{slide_index}.mp4"
    storage.upload_file(key, segment, "video/mp4")
    asset = _create_asset(
        db,
        context=context,
        slide=slide,
        asset_type="slide_segment_video",
        storage_key=key,
        filename=f"slide-segment-{slide_index}.mp4",
        mime_type="video/mp4",
        size_bytes=len(segment),
        duration_seconds=duration,
        metadata_json={
            "slide_position": slide_index,
            "generation_job_id": str(job.id),
            "slide_preview_source": slide_preview_source,
            "selected_slide_preview_asset": slide_preview_source,
            "slide_preview_asset_type": slide_preview_source.get("asset_type"),
            "slide_preview_storage_key": slide_preview_source.get("storage_key"),
            "slide_preview_metadata": slide_preview_source,
            "composition_used_full_slide_preview": bool(
                slide_preview_source.get("includes_text")
            ),
            "composition_preview_warning": slide_preview_source.get("warning"),
            "composition_asset_used": (
                "generated_avatar_clip"
                if composition_used_generated_avatar_clip
                else "static_avatar_fallback"
            ),
            "composition_used_generated_avatar_clip": composition_used_generated_avatar_clip,
            "composition_fallback_reason": composition_fallback_reason,
            "avatar_clip_asset_id": avatar_source_asset_id,
            "avatar_clip_storage_key": avatar_source_storage_key,
            "avatar_clip_ffprobe": avatar_clip_info,
            "avatar_clip_duration_seconds": avatar_clip_info.get("duration_seconds"),
            "audio_asset_id": str(audio_asset.id),
            "audio_duration_seconds": audio_info.get("duration_seconds"),
            "ffprobe": segment_info,
            "segment_motion_analysis": segment_motion,
            "segment_appears_static": segment_motion.get("almost_static"),
            "composition_motion_warning": composition_motion_warning,
        },
    )
    _stage_progress(db, job, "composing_slide", slide_index, total_slides, 60, 25, slide_index)
    return asset


def compose_final_video(
    db: Session,
    storage,
    job: GenerationJob,
    context: GenerationContext,
    segment_assets: list[Asset],
) -> Asset:
    existing = _find_valid_final_asset(db, storage, context)
    if existing:
        logger.info("Generation job %s: reusing existing final video asset", job.id)
        return existing
    logger.info(
        "Generation job %s: composing final video from %s segment assets",
        job.id,
        len(segment_assets),
    )
    update_generation_job_progress(
        db,
        job,
        status="composing_video",
        progress_percentage=90.0,
        current_step="Creating final MP4",
        total_slides=len(segment_assets),
    )
    composer = ComposerService()
    try:
        segment_bytes = [storage.download_bytes(asset.storage_key) for asset in segment_assets]
        final_video = composer.concatenate_slide_videos(segment_bytes)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            "final_composition_failed",
            _ffmpeg_error_message(exc),
            stage="composing_video",
        ) from exc
    final_info = _probe_media_info(final_video, ".mp4")
    duration = float(final_info.get("duration_seconds") or 0)
    if duration <= 0 or not final_info.get("has_video") or not final_info.get("has_audio"):
        raise PipelineError(
            "final_composition_failed",
            "Final concatenated MP4 is invalid",
            stage="composing_video",
        )
    key = f"orgs/{context.organization_id}/projects/{context.project_id}/output/final_{job.id}.mp4"
    storage.upload_file(key, final_video, "video/mp4")
    final_asset = _create_asset(
        db,
        context=context,
        slide=None,
        asset_type="final_video",
        storage_key=key,
        filename="final.mp4",
        mime_type="video/mp4",
        size_bytes=len(final_video),
        duration_seconds=duration,
        metadata_json={
            "generation_job_id": str(job.id),
            "segment_asset_ids": [str(asset.id) for asset in segment_assets],
            "total_slides": len(segment_assets),
            "ffprobe": final_info,
        },
    )
    job.final_asset_id = final_asset.id
    job.result = {"final_video_key": key, "final_asset_id": str(final_asset.id)}
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("Generation job %s: completed final video %s", job.id, key)
    full_preview_count = sum(
        1
        for asset in segment_assets
        if (asset.metadata_json or {}).get("composition_used_full_slide_preview") is True
    )
    generated_overlay_count = sum(
        1
        for asset in segment_assets
        if (asset.metadata_json or {}).get("composition_used_generated_avatar_clip") is True
    )
    static_fallback_count = len(segment_assets) - generated_overlay_count
    low_motion_count = sum(
        1
        for asset in segment_assets
        if ((asset.metadata_json or {}).get("segment_motion_analysis") or {}).get(
            "almost_static"
        )
    )
    logger.info(
        "Generation job %s summary: total_slides=%s full_slide_preview_used=%s/%s "
        "generated_avatar_video_overlay_used=%s/%s static_avatar_fallback_used=%s/%s "
        "low_motion_segments=%s/%s final_video=%s",
        job.id,
        len(segment_assets),
        full_preview_count,
        len(segment_assets),
        generated_overlay_count,
        len(segment_assets),
        static_fallback_count,
        len(segment_assets),
        low_motion_count,
        len(segment_assets),
        key,
    )
    update_generation_job_progress(
        db,
        job,
        status="completed",
        progress_percentage=100.0,
        current_step="Completed",
        total_slides=len(segment_assets),
    )
    return final_asset


def update_generation_job_progress(
    db: Session,
    job: GenerationJob,
    status: str,
    progress_percentage: float,
    current_step: str,
    current_slide: int | None = None,
    total_slides: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    job.status = status
    job.progress_percentage = progress_percentage
    job.current_step = current_step
    job.current_slide = current_slide
    job.total_slides = total_slides
    if error_code is not None:
        job.error_code = error_code
    if error_message is not None:
        job.error_message = error_message[:2000]
    if status in {"completed", "failed"}:
        job.completed_at = datetime.now(UTC)
    db.add(job)
    db.commit()
    db.refresh(job)


def mark_generation_failed(
    db: Session,
    job: GenerationJob,
    error_code: str,
    error_message: str,
    *,
    current_step: str | None = None,
    current_slide: int | None = None,
) -> None:
    update_generation_job_progress(
        db,
        job,
        status="failed",
        progress_percentage=float(job.progress_percentage or 0),
        current_step=current_step or job.current_step or "Generation failed",
        current_slide=current_slide if current_slide is not None else job.current_slide,
        total_slides=job.total_slides,
        error_code=error_code,
        error_message=error_message,
    )


def _stage_progress(
    db: Session,
    job: GenerationJob,
    status: str,
    completed: int,
    total: int,
    start: float,
    span: float,
    current_slide: int | None = None,
) -> None:
    progress = start + (completed / max(total, 1)) * span
    update_generation_job_progress(
        db,
        job,
        status=status,
        progress_percentage=round(progress, 2),
        current_step=_step_message(status, current_slide, total),
        current_slide=current_slide,
        total_slides=total,
    )


def _step_message(status: str, current_slide: int | None, total: int) -> str:
    stage = {
        "generating_audio": "Generating audio",
        "generating_avatar": "Generating avatar clip",
        "composing_slide": "Composing slide segment",
    }.get(status, status)
    if current_slide is None:
        return stage
    return f"{stage} for slide {current_slide} of {total}"


def _find_valid_asset(
    db: Session,
    storage,
    context: GenerationContext,
    slide: Slide,
    slide_index: int,
    asset_type: str,
) -> Asset | None:
    assets = (
        db.query(Asset)
        .filter(
            Asset.organization_id == context.organization_id,
            Asset.project_id == context.project_id,
            Asset.slide_id == slide.id,
            Asset.asset_type == asset_type,
        )
        .order_by(Asset.created_at.desc())
        .all()
    )
    for asset in assets:
        metadata = asset.metadata_json or {}
        if metadata.get("generation_job_id") != str(context.generation_job_id):
            continue
        if metadata.get("slide_position") != slide_index:
            continue
        if asset_type == "slide_segment_video" and (
            metadata.get("composition_asset_used") is None
            or metadata.get("slide_preview_source") is None
            or metadata.get("composition_used_full_slide_preview") is not True
        ):
            logger.info(
                "Generation job %s: regenerating slide segment asset without a full rendered "
                "slide preview for slide %s",
                context.generation_job_id,
                slide_index,
            )
            continue
        if storage_object_exists(storage, asset.storage_key):
            return asset
        logger.warning(
            "Generation job %s: DB asset exists but blob is missing, regenerating: %s",
            context.generation_job_id,
            asset.storage_key,
        )
    return None


def _find_valid_final_asset(db: Session, storage, context: GenerationContext) -> Asset | None:
    assets = (
        db.query(Asset)
        .filter(
            Asset.organization_id == context.organization_id,
            Asset.project_id == context.project_id,
            Asset.asset_type == "final_video",
        )
        .order_by(Asset.created_at.desc())
        .all()
    )
    for asset in assets:
        metadata = asset.metadata_json or {}
        if metadata.get("generation_job_id") != str(context.generation_job_id):
            continue
        if not metadata.get("segment_asset_ids"):
            logger.info(
                "Generation job %s: regenerating legacy final video asset",
                context.generation_job_id,
            )
            continue
        if storage_object_exists(storage, asset.storage_key):
            return asset
    return None


def storage_object_exists(storage, storage_key: str) -> bool:
    try:
        return bool(storage.exists(storage_key))
    except AttributeError:
        try:
            storage.download_bytes(storage_key)
        except FileNotFoundError:
            return False
        return True


def _create_asset(
    db: Session,
    context: GenerationContext,
    slide: Slide | None,
    asset_type: str,
    storage_key: str,
    filename: str,
    mime_type: str,
    size_bytes: int,
    duration_seconds: float | None = None,
    metadata_json: dict | None = None,
) -> Asset:
    asset = Asset(
        organization_id=context.organization_id,
        project_id=context.project_id,
        slide_id=slide.id if slide is not None else None,
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


def _load_slide_preview_image(storage, slide: Slide, metadata: dict) -> tuple[bytes, dict]:
    slide_preview = metadata.get("slide_preview") if isinstance(metadata, dict) else None
    candidates = [
        {
            "source": "slide_preview.storage_key",
            "storage_key": slide_preview.get("storage_key") if isinstance(slide_preview, dict) else None,
            "asset_type": "slide_preview",
            "includes_text": True,
            "render_source": "ppt_render",
        },
        {
            "source": "rendered_image_key",
            "storage_key": metadata.get("rendered_image_key"),
            "asset_type": "rendered_slide_preview",
            "includes_text": True,
            "render_source": "ppt_render",
        },
        {
            "source": "thumbnail_key",
            "storage_key": slide.thumbnail_key,
            "asset_type": "slide_preview",
            "includes_text": True,
            "render_source": "ppt_render",
        },
    ]
    seen_keys: set[str] = set()
    for candidate in candidates:
        source_name = candidate["source"]
        storage_key = candidate.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key:
            continue
        if storage_key in seen_keys:
            continue
        seen_keys.add(storage_key)
        try:
            image_bytes = storage.download_bytes(storage_key)
        except FileNotFoundError:
            logger.warning(
                "Slide %s preview candidate missing in storage: source=%s key=%s",
                slide.id,
                source_name,
                storage_key,
            )
            continue
        if image_bytes:
            preview_source = {
                key: value
                for key, value in candidate.items()
                if key != "storage_key" or isinstance(value, str)
            }
            preview_source["storage_key"] = storage_key
            return image_bytes, preview_source
    raise PipelineError(
        "slide_composition_failed",
        f"Slide {slide.position} has no full rendered PPT preview image available",
        stage="composing_slide",
        slide_index=slide.position,
    )


def _load_avatar_clip_or_static_fallback(
    storage,
    context: GenerationContext,
    avatar_clip_asset: Asset,
    duration_seconds: float,
) -> tuple[bytes, str | None, dict]:
    try:
        clip = storage.download_bytes(avatar_clip_asset.storage_key)
    except FileNotFoundError:
        fallback_clip, reason = _static_avatar_fallback_clip(
            storage,
            context,
            duration_seconds,
            "generated avatar clip blob is missing",
        )
        return fallback_clip, reason, _probe_media_info(fallback_clip, ".mp4")
    clip_info = _probe_media_info(clip, ".mp4")
    if float(clip_info.get("duration_seconds") or 0) <= 0 or not clip_info.get("has_video"):
        fallback_clip, reason = _static_avatar_fallback_clip(
            storage,
            context,
            duration_seconds,
            "generated avatar clip is invalid or unreadable",
        )
        return fallback_clip, reason, _probe_media_info(fallback_clip, ".mp4")
    return clip, None, clip_info


def _static_avatar_fallback_clip(
    storage,
    context: GenerationContext,
    duration_seconds: float,
    reason: str,
) -> tuple[bytes, str]:
    if not context.avatar_source_storage_key:
        raise PipelineError(
            "slide_composition_failed",
            f"{reason}; no static avatar asset is available for fallback",
            stage="composing_slide",
        )
    image_bytes = storage.download_bytes(context.avatar_source_storage_key)
    return _image_to_video_clip(image_bytes, duration_seconds), reason


def _image_to_video_clip(image_bytes: bytes, duration_seconds: float) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / "avatar.png"
        output_path = Path(tmp) / "avatar-static.mp4"
        image_path.write_bytes(image_bytes)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-t",
                str(max(duration_seconds, 0.5)),
                "-vf",
                "scale=720:-2,setsar=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return output_path.read_bytes()


def _avatar_source_url(
    db: Session,
    settings_row: VideoGenerationSettings,
    storage,
) -> tuple[str | None, str | None]:
    if settings_row.avatar_source_url:
        _validate_external_provider_url(settings_row.avatar_source_url)
        return settings_row.avatar_source_url, None
    if settings_row.avatar_source_asset_id:
        asset = db.get(Asset, settings_row.avatar_source_asset_id)
        if asset and storage_object_exists(storage, asset.storage_key):
            return _external_asset_url(storage, asset.storage_key), asset.storage_key
    if app_settings.DEBUG_AVATAR_SOURCE_URL and not app_settings.is_production:
        _validate_external_provider_url(app_settings.DEBUG_AVATAR_SOURCE_URL)
        return app_settings.DEBUG_AVATAR_SOURCE_URL, None
    return None, None


def _external_asset_url(storage, storage_key: str) -> str:
    url = storage.generate_external_download_url(storage_key).url
    _validate_external_provider_url(url)
    return url


def _validate_external_provider_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise PipelineError(
            "validation_failed",
            "WaveSpeed requires a public URL for audio/avatar assets.",
            stage="validating",
        )
    lowered_host = hostname.lower()
    blocked = lowered_host in {"localhost", "127.0.0.1", "0.0.0.0", "minio"}
    try:
        ip_address = ipaddress.ip_address(lowered_host)
        blocked = blocked or ip_address.is_private or ip_address.is_loopback
    except ValueError:
        pass
    if blocked:
        raise PipelineError(
            "validation_failed",
            "WaveSpeed requires a public URL for audio/avatar assets. "
            "Configure a public tunnel or external storage.",
            stage="validating",
        )


def _generate_avatar_with_motion_fallback(
    audio_bytes: bytes,
    duration: float,
    avatar_source_url: str,
    wavespeed_api_key: str,
    audio_url: str,
    slide_index: int,
    generation_uuid: uuid.UUID,
) -> tuple[bytes, dict]:
    provider = get_avatar_video_provider("wavespeed")
    primary_model = app_settings.DEFAULT_LIPSYNC_MODEL
    clip = provider.generate_avatar_clip(
        audio_bytes=audio_bytes,
        duration_seconds=duration,
        avatar_id=avatar_source_url,
        api_key=wavespeed_api_key,
        audio_url=audio_url,
        avatar_source_url=avatar_source_url,
        model_name=primary_model,
        prompt=app_settings.LIPSYNC_PROMPT,
    )
    motion = _analyze_video_motion(clip)
    metadata = {
        "model": primary_model,
        "model_used": primary_model,
        "fallback_used": False,
        "motion_analysis": motion,
        "prompt": app_settings.LIPSYNC_PROMPT,
    }
    fallback_model = (app_settings.FALLBACK_LIPSYNC_MODEL or "").strip()
    if motion.get("almost_static") and fallback_model:
        logger.warning(
            "Primary WaveSpeed lipsync result appears almost static; retrying fallback model: "
            "generation_job=%s slide=%s primary_model=%s fallback_model=%s score=%s",
            generation_uuid,
            slide_index,
            primary_model,
            fallback_model,
            motion.get("motion_score"),
        )
        fallback_clip = provider.generate_avatar_clip(
            audio_bytes=audio_bytes,
            duration_seconds=duration,
            avatar_id=avatar_source_url,
            api_key=wavespeed_api_key,
            audio_url=audio_url,
            avatar_source_url=avatar_source_url,
            model_name=fallback_model,
            prompt=app_settings.LIPSYNC_PROMPT,
        )
        fallback_motion = _analyze_video_motion(fallback_clip)
        if float(fallback_motion.get("motion_score") or 0) > float(motion.get("motion_score") or 0):
            return fallback_clip, {
                "model": fallback_model,
                "model_used": fallback_model,
                "primary_model": primary_model,
                "fallback_used": True,
                "primary_motion_analysis": motion,
                "motion_analysis": fallback_motion,
                "prompt": app_settings.LIPSYNC_PROMPT,
            }
        metadata["fallback_attempted"] = True
        metadata["fallback_model"] = fallback_model
        metadata["fallback_motion_analysis"] = fallback_motion
    return clip, metadata


def _probe_media_info(media_bytes: bytes, suffix: str) -> dict:
    info = {
        "duration_seconds": 0.0,
        "has_video": False,
        "has_audio": False,
        "video_codec": None,
        "audio_codec": None,
        "width": None,
        "height": None,
    }
    if not media_bytes:
        return info
    with tempfile.TemporaryDirectory() as tmp:
        media_path = Path(tmp) / f"asset{suffix}"
        media_path.write_bytes(media_bytes)
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration:stream=codec_type,codec_name,width,height",
                    "-of",
                    "json",
                    str(media_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return info
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return info
    try:
        info["duration_seconds"] = float((payload.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        info["duration_seconds"] = 0.0
    for stream in payload.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") == "video" and not info["has_video"]:
            info["has_video"] = True
            info["video_codec"] = stream.get("codec_name")
            info["width"] = stream.get("width")
            info["height"] = stream.get("height")
        if stream.get("codec_type") == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["audio_codec"] = stream.get("codec_name")
    return info


def _analyze_video_motion(media_bytes: bytes) -> dict:
    info = _probe_media_info(media_bytes, ".mp4")
    duration = float(info.get("duration_seconds") or 0)
    if not media_bytes or duration <= 0 or not info.get("has_video"):
        return {"motion_score": 0.0, "almost_static": True, "sample_count": 0}
    sample_times = [0.15, max(0.15, duration / 2), max(0.15, duration - 0.15)]
    frames: list[bytes] = []
    with tempfile.TemporaryDirectory() as tmp:
        media_path = Path(tmp) / "avatar.mp4"
        media_path.write_bytes(media_bytes)
        for sample_time in sample_times:
            try:
                result = subprocess.run(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-ss",
                        f"{sample_time:.3f}",
                        "-i",
                        str(media_path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=64:64,format=gray",
                        "-f",
                        "rawvideo",
                        "-",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=30,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                continue
            if result.stdout:
                frames.append(result.stdout)
    if len(frames) < 2:
        return {"motion_score": 0.0, "almost_static": True, "sample_count": len(frames)}
    scores = [
        _mean_abs_frame_diff(frames[index], frames[index + 1])
        for index in range(len(frames) - 1)
    ]
    motion_score = max(scores) if scores else 0.0
    return {
        "motion_score": round(motion_score, 4),
        "almost_static": motion_score < 1.5,
        "sample_count": len(frames),
        "threshold": 1.5,
    }


def _mean_abs_frame_diff(left: bytes, right: bytes) -> float:
    length = min(len(left), len(right))
    if length <= 0:
        return 0.0
    return sum(abs(left[index] - right[index]) for index in range(length)) / length


def _avatar_overlay_from_metadata(metadata: dict, resolution: str) -> dict[str, int]:
    output_width, output_height = _resolution_size_for_generation(resolution)
    canvas = metadata.get("canvas") if isinstance(metadata, dict) else None
    canvas_width = 960.0
    canvas_height = 540.0
    avatar = None
    if isinstance(canvas, dict):
        canvas_width = float(canvas.get("width") or canvas_width)
        canvas_height = float(canvas.get("height") or canvas_height)
        avatar = canvas.get("avatar") if isinstance(canvas.get("avatar"), dict) else None
        if avatar is None:
            elements = canvas.get("elements") or []
            avatar = next(
                (
                    element
                    for element in elements
                    if isinstance(element, dict) and element.get("type") == "avatar"
                ),
                None,
            )
    default_width = 400
    default_height = 400
    if not avatar:
        return {
            "x": output_width - default_width - 80,
            "y": output_height - default_height - 60,
            "width": default_width,
            "height": default_height,
        }
    scale_x = output_width / max(canvas_width, 1.0)
    scale_y = output_height / max(canvas_height, 1.0)
    width = max(1, int(float(avatar.get("width") or default_width) * scale_x))
    height = max(1, int(float(avatar.get("height") or default_height) * scale_y))
    x = int(float(avatar.get("x") or 0) * scale_x)
    y = int(float(avatar.get("y") or 0) * scale_y)
    return {
        "x": max(0, min(x, output_width - width)),
        "y": max(0, min(y, output_height - height)),
        "width": min(width, output_width),
        "height": min(height, output_height),
    }


def _resolution_size_for_generation(resolution: str) -> tuple[int, int]:
    if resolution == "720p":
        return 1280, 720
    if resolution == "1080p":
        return 1920, 1080
    if "x" in resolution:
        left, right = resolution.lower().split("x", 1)
        return int(left), int(right)
    return 1920, 1080


def _ffmpeg_error_message(exc: subprocess.CalledProcessError) -> str:
    stderr = exc.stderr
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", errors="ignore")[-1000:] or "FFmpeg failed"
    return str(stderr or "FFmpeg failed")[-1000:]
