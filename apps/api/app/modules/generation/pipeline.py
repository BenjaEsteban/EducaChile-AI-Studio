from __future__ import annotations

import ipaddress
import json
import logging
import math
import hashlib
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.modules.composer.service import ComposerService
from app.modules.generation.models import GenerationJob, VideoGenerationSettings
from app.modules.projects.models import Asset, Presentation, Slide
from app.modules.tts.adapters import TTSProviderError, get_tts_provider
from app.modules.video.adapters import AvatarVideoProviderError, get_avatar_video_provider
from app.services.wavespeed_client import WavespeedClient, WavespeedClientError
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
    elevenlabs_api_key: str | None
    elevenlabs_voice_id: str | None
    wavespeed_api_key: str
    avatar_source_url: str | None
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
        chunk_index: int | None = None,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.stage = stage
        self.slide_index = slide_index
        self.chunk_index = chunk_index
        self.details = details


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
        if not app_settings.WAVESPEED_API_KEY:
            raise PipelineError(
                "validation_failed",
                "Video settings are not configured",
                stage="validating",
            )
        settings_row = VideoGenerationSettings(
            organization_id=organization_id,
            project_id=project_id,
            validation_status="valid",
            wavespeed_valid=True,
            elevenlabs_valid=False,
        )
    wavespeed_api_key = decrypt_secret(settings_row.wavespeed_api_key_encrypted) or app_settings.WAVESPEED_API_KEY
    if not wavespeed_api_key:
        raise PipelineError(
            "validation_failed",
            "WaveSpeed API key is missing",
            stage="validating",
        )
    if _requires_tts_audio() and not _tts_provider_configured(settings_row):
        raise PipelineError(
            "validation_failed",
            "ElevenLabs API key is missing",
            stage="validating",
        )
    avatar_source_url, avatar_source_storage_key = _avatar_source_reference(
        db,
        settings_row,
        storage,
    )
    if not avatar_source_url and not avatar_source_storage_key:
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
        elevenlabs_api_key=decrypt_secret(settings_row.elevenlabs_api_key_encrypted),
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
    if existing and _can_reuse_slide_audio_asset(existing, slide, slide_index):
        logger.info(
            "Generation job %s: reusing existing audio asset for slide %s",
            job.id,
            slide_index,
        )
        _stage_progress(db, job, "generating_audio", slide_index, total_slides, 5, 20)
        return existing
    if existing:
        logger.info(
            "Generation job %s: regenerating audio for slide %s because the existing asset does not satisfy the chunked narration contract",
            job.id,
            slide_index,
        )

    logger.info(
        "Generation job %s: generating audio for slide %s/%s",
        job.id,
        slide_index,
        total_slides,
    )
    _stage_progress(db, job, "generating_audio", slide_index - 1, total_slides, 5, 20, slide_index)
    metadata = slide.metadata_ or {}
    dialogue = _normalize_tts_text(str(metadata.get("dialogue") or slide.notes or ""))
    tts_provider_name = (app_settings.TTS_PROVIDER or "none").strip().lower()
    chunks = _build_narration_chunks(dialogue)
    if not chunks:
        raise PipelineError(
            "audio_generation_failed",
            f"Slide {slide_index} is missing narration text",
            stage="generating_audio",
            slide_index=slide_index,
        )
    logger.info(
        "Generation job %s: generating TTS audio for slide %s/%s provider=%s text_length=%s word_count=%s chunk_count=%s speed=%s",
        job.id,
        slide_index,
        total_slides,
        tts_provider_name,
        len(dialogue),
        _word_count(dialogue),
        len(chunks),
        app_settings.TTS_SPEED,
    )
    try:
        provider = get_tts_provider(tts_provider_name)
        composer = ComposerService()
        chunk_audio_bytes: list[bytes] = []
        chunk_metadata: list[dict] = []
        expected_total_duration = 0.0
        pending_chunks = list(chunks)
        chunk_index = 0
        while chunk_index < len(pending_chunks):
            chunk = pending_chunks[chunk_index]
            chunk_text = chunk["text"]
            chunk_word_count = int(chunk["word_count"])
            chunk_expected_duration = float(chunk["expected_duration_seconds"])
            chunk_number = chunk_index + 1
            logger.info(
                "Generation job %s: slide %s chunk %s/%s text_length=%s word_count=%s estimated_duration=%.2f",
                job.id,
                slide_index,
                chunk_number,
                len(pending_chunks),
                len(chunk_text),
                chunk_word_count,
                chunk_expected_duration,
            )
            chunk_audio_raw, _chunk_reported_duration = provider.generate_audio(
                text=chunk_text,
                voice_id=context.elevenlabs_voice_id,
                language=app_settings.TTS_LANGUAGE,
                speed=app_settings.TTS_SPEED,
                api_key=context.elevenlabs_api_key,
            )
            chunk_audio_mp3 = composer.normalize_audio_to_mp3(chunk_audio_raw)
            chunk_audio_info = _probe_media_info(chunk_audio_mp3, ".mp3")
            chunk_audio_duration = float(chunk_audio_info.get("duration_seconds") or 0)
            if chunk_audio_duration <= 0 or not chunk_audio_info.get("has_audio"):
                raise PipelineError(
                    "audio_generation_failed",
                    f"Audio chunk {chunk_number} for slide {slide_index} is invalid",
                    stage="generating_audio",
                    slide_index=slide_index,
                    chunk_index=chunk_number,
                )
            duration_limit = float(app_settings.MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK)
            duration_tolerance = float(app_settings.MAX_AUDIO_CHUNK_DURATION_TOLERANCE_SECONDS)
            if chunk_audio_duration > duration_limit + duration_tolerance:
                replacement_texts = _split_chunk_text_for_retry(chunk_text)
                if len(replacement_texts) > 1:
                    logger.warning(
                        "Generation job %s: slide %s chunk %s exceeded duration limit (measured=%.2fs limit=%.2fs tolerance=%.2fs); splitting into %s smaller chunks",
                        job.id,
                        slide_index,
                        chunk_number,
                        chunk_audio_duration,
                        duration_limit,
                        duration_tolerance,
                        len(replacement_texts),
                    )
                    replacement_chunks = [
                        _make_chunk_spec(text_piece)
                        for text_piece in replacement_texts
                    ]
                    pending_chunks[chunk_index : chunk_index + 1] = replacement_chunks
                    continue
                raise PipelineError(
                    "audio_generation_failed",
                    (
                        f"Audio chunk {chunk_number} for slide {slide_index} is too long "
                        f"({chunk_audio_duration:.2f}s > {duration_limit + duration_tolerance:.2f}s)"
                    ),
                    stage="generating_audio",
                    slide_index=slide_index,
                    chunk_index=chunk_number,
                )
            expected_ratio = chunk_audio_duration / max(chunk_expected_duration, 0.001)
            if chunk_expected_duration > 0 and expected_ratio < app_settings.MIN_EXPECTED_AUDIO_DURATION_RATIO:
                raise PipelineError(
                    "audio_generation_failed",
                    (
                        f"Audio chunk {chunk_number} for slide {slide_index} looks truncated "
                        f"(measured={chunk_audio_duration:.2f}s expected={chunk_expected_duration:.2f}s ratio={expected_ratio:.2f})"
                    ),
                    stage="generating_audio",
                    slide_index=slide_index,
                    chunk_index=chunk_number,
                )
            expected_total_duration += chunk_expected_duration
            chunk_audio_key = f"{context.output_prefix}/audio/slide-{slide_index}/chunk-{chunk_number}.mp3"
            storage.upload_file(chunk_audio_key, chunk_audio_mp3, "audio/mpeg")
            chunk_audio_url = _asset_public_url(storage, chunk_audio_key)
            chunk_audio_asset = _create_asset(
                db,
                context=context,
                slide=slide,
                asset_type="slide_audio_chunk",
                storage_key=chunk_audio_key,
                filename=f"slide-{slide_index}-chunk-{chunk_number}.mp3",
                mime_type="audio/mpeg",
                size_bytes=len(chunk_audio_mp3),
                duration_seconds=chunk_audio_duration,
                metadata_json={
                    "slide_position": slide_index,
                    "chunk_index": chunk_number,
                    "generation_job_id": str(job.id),
                    "provider": tts_provider_name,
                    "voice_id": context.elevenlabs_voice_id,
                    "language": app_settings.TTS_LANGUAGE,
                    "audio_probe": chunk_audio_info,
                    "text_length": len(chunk_text),
                    "word_count": chunk_word_count,
                    "expected_duration_seconds": chunk_expected_duration,
                    "measured_duration_seconds": chunk_audio_duration,
                    "measured_tts_duration": chunk_audio_duration,
                    "estimated_duration": chunk_expected_duration,
                    "index": chunk_number,
                    "text": chunk_text,
                    "audio_storage_key": chunk_audio_key,
                    "audio_url": chunk_audio_url,
                },
            )
            chunk_audio_bytes.append(chunk_audio_mp3)
            chunk_metadata.append(
                {
                    "index": chunk_number,
                    "chunk_index": chunk_number,
                    "text": chunk_text,
                    "text_length": len(chunk_text),
                    "word_count": chunk_word_count,
                    "estimated_duration": chunk_expected_duration,
                    "expected_duration_seconds": chunk_expected_duration,
                    "measured_tts_duration": chunk_audio_duration,
                    "measured_duration_seconds": chunk_audio_duration,
                    "audio_storage_key": chunk_audio_key,
                    "audio_url": chunk_audio_url,
                    "audio_asset_id": str(chunk_audio_asset.id),
                    "audio_probe": chunk_audio_info,
                }
            )
            logger.info(
                "Generation job %s: slide %s chunk %s audio ready text_length=%s word_count=%s estimated_duration=%.2f measured_duration=%.2f storage_key=%s",
                job.id,
                slide_index,
                chunk_number,
                len(chunk_text),
                chunk_word_count,
                chunk_expected_duration,
                chunk_audio_duration,
                chunk_audio_key,
            )
            chunk_index += 1
        if len(chunk_audio_bytes) == 1:
            audio_bytes = chunk_audio_bytes[0]
        else:
            audio_bytes = composer.concatenate_audio_tracks(chunk_audio_bytes)
        audio_bytes = composer.normalize_audio_to_mp3(audio_bytes)
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            "audio_generation_failed",
            _ffmpeg_error_message(exc),
            stage="generating_audio",
            slide_index=slide_index,
        ) from exc
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
    expected_total_duration = max(expected_total_duration, _estimate_spanish_duration(dialogue))
    if expected_total_duration > 0 and (duration / expected_total_duration) < app_settings.MIN_EXPECTED_AUDIO_DURATION_RATIO:
        raise PipelineError(
            "audio_generation_failed",
            (
                f"Slide {slide_index} narration looks truncated "
                f"(measured={duration:.2f}s expected={expected_total_duration:.2f}s ratio={duration / expected_total_duration:.2f})"
            ),
            stage="generating_audio",
            slide_index=slide_index,
        )
    logger.info(
        "Generation job %s: slide %s audio ready text_length=%s chunk_count=%s audio_duration=%.2f expected_duration=%.2f",
        job.id,
        slide_index,
        len(dialogue),
        len(chunk_metadata),
        duration,
        expected_total_duration,
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
            "provider": tts_provider_name,
            "voice_id": context.elevenlabs_voice_id,
            "language": app_settings.TTS_LANGUAGE,
            "audio_probe": audio_info,
            "chunk_count": len(chunk_metadata),
            "expected_duration_seconds": expected_total_duration,
            "measured_duration_seconds": duration,
            "chunks": chunk_metadata,
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
    audio_asset: Asset | None = None,
    heartbeat_callback=None,
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

    metadata = slide.metadata_ or {}
    dialogue = _normalize_tts_text(str(metadata.get("dialogue") or slide.notes or ""))
    mode = _avatar_generation_mode()
    lipsync_provider = _avatar_lipsync_provider()
    if mode == "wavespeed_text" and not dialogue:
        raise PipelineError(
            "avatar_generation_failed",
            f"Slide {slide_index} is missing dialogue text",
            stage="generating_avatar",
            slide_index=slide_index,
        )

    logger.info(
        "Generation job %s: generating avatar for slide %s/%s mode=%s provider=%s text_length=%s audio_duration=%s",
        job.id,
        slide_index,
        total_slides,
        mode,
        lipsync_provider,
        len(dialogue),
        float(audio_asset.duration_seconds or 0) if audio_asset else 0.0,
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
    try:
        avatar_source_bytes, avatar_source_metadata = _load_avatar_source_bytes(
            db=db,
            storage=storage,
            context=context,
        )
        provider = get_avatar_video_provider("wavespeed")
        composer = ComposerService()
        audio_duration_seconds = float(audio_asset.duration_seconds or 0) if audio_asset else 0.0
        chunk_specs = _slide_audio_chunk_specs(audio_asset, dialogue, storage)
        if not chunk_specs:
            raise PipelineError(
                "avatar_generation_failed",
                "Slide narration chunks are missing",
                stage="generating_avatar",
                slide_index=slide_index,
        )
        avatar_base_video_asset: Asset | None = None
        avatar_base_video_bytes: bytes | None = None
        avatar_base_video_metadata: dict | None = None
        if mode == "fast_lipsync":
            avatar_base_video_asset, avatar_base_video_bytes, avatar_base_video_metadata = _ensure_avatar_base_video_asset(
                db=db,
                storage=storage,
                context=context,
            )
        avatar_base_video_url = None
        if avatar_base_video_asset is not None:
            try:
                avatar_base_video_url = _asset_public_url(storage, avatar_base_video_asset.storage_key)
            except PipelineError as exc:
                logger.warning(
                    "Generation job %s: could not build a public avatar base video URL for slide %s, using uploaded bytes only: %s",
                    job.id,
                    slide_index,
                    exc,
                )
        chunk_avatar_bytes: list[bytes] = []
        chunk_metadata: list[dict] = []
        audio_source_urls: list[str] = []
        request_ids: list[str | None] = []
        total_chunks = len(chunk_specs)
        for chunk_index, chunk in enumerate(chunk_specs, 1):
            logger.info(
                "Generation job %s: slide %s chunk %s keys=%s",
                job.id,
                slide_index,
                chunk_index,
                sorted(chunk.keys()),
            )
            chunk_text = _require_chunk_text(chunk, slide_index, chunk_index)
            chunk_audio_storage_key = _require_chunk_audio_storage_key(chunk, slide_index, chunk_index)
            chunk_audio_url = _require_chunk_audio_url(chunk, slide_index, chunk_index)
            chunk_measured_tts_duration = _require_chunk_duration(chunk, slide_index, chunk_index)
            chunk_word_count = int(chunk.get("word_count") or _word_count(chunk_text))
            chunk_expected_duration = float(
                chunk.get("estimated_duration")
                or chunk.get("expected_duration_seconds")
                or _estimate_spanish_duration(chunk_text)
            )
            logger.info(
                "Generation job %s: slide %s chunk %s avatar mode=%s provider=%s has_text=%s has_audio_url=%s measured_tts_duration=%.2f",
                job.id,
                slide_index,
                chunk_index,
                mode,
                lipsync_provider,
                bool(chunk_text),
                bool(chunk_audio_url),
                chunk_measured_tts_duration,
            )
            chunk_audio_bytes = storage.download_bytes(chunk_audio_storage_key)
            chunk_audio_info = _probe_media_info(chunk_audio_bytes, ".mp3")
            chunk_audio_duration = float(chunk_audio_info.get("duration_seconds") or 0.0)
            if chunk_audio_duration <= 0 or not chunk_audio_info.get("has_audio"):
                raise PipelineError(
                    "avatar_generation_failed",
                    f"Audio chunk {chunk_index} for slide {slide_index} is invalid",
                    stage="generating_avatar",
                    slide_index=slide_index,
                    chunk_index=chunk_index,
                )
            if chunk_audio_duration <= 0:
                raise PipelineError(
                    "avatar_generation_failed",
                    f"Audio chunk {chunk_index} for slide {slide_index} has no measurable duration",
                    stage="generating_avatar",
                    slide_index=slide_index,
                    chunk_index=chunk_index,
                )
            logger.info(
                "Generation job %s: slide %s chunk %s/%s avatar input text_length=%s word_count=%s estimated_duration=%.2f measured_tts_duration=%.2f audio_url_host=%s resolution=%s",
                job.id,
                slide_index,
                chunk_index,
                len(chunk_specs),
                len(chunk_text),
                chunk_word_count,
                chunk_expected_duration,
                chunk_audio_duration,
                urlparse(chunk_audio_url).hostname,
                app_settings.AVATAR_LIPSYNC_RESOLUTION,
            )
            for attr in ("last_request_id", "last_audio_url", "last_image_url", "last_external_checks", "last_duration_ratio"):
                try:
                    setattr(provider, attr, None if attr != "last_external_checks" else {})
                except Exception:
                    pass
            def _avatar_poll_heartbeat(payload: dict[str, object]) -> None:
                if heartbeat_callback is None:
                    return
                elapsed = float(payload.get("elapsed_seconds") or 0.0)
                timeout = max(1.0, float(payload.get("timeout_seconds") or 1.0))
                poll_ratio = min(max(elapsed / timeout, 0.0), 1.0)
                chunk_progress_start = 25.0 + ((chunk_index - 1) / max(total_chunks, 1)) * 10.0
                chunk_progress_span = 10.0 / max(total_chunks, 1)
                heartbeat_callback(
                    {
                        **payload,
                        "progress_percentage": round(chunk_progress_start + (poll_ratio * chunk_progress_span), 2),
                        "current_step": (
                            f"Polling Wavespeed for slide {slide_index} of {total_slides}, "
                            f"chunk {chunk_index} of {total_chunks}"
                        ),
                        "current_slide": slide_index,
                        "total_slides": total_slides,
                        "chunk_index": chunk_index,
                        "chunk_count": total_chunks,
                        "stage": "polling_wavespeed",
                    }
                )
            video_url: str | None = None
            raw_clip: bytes | None = None
            fallback_reason: str | None = None
            fallback_used = False
            provider_timeout = False
            chunk_retry_used = False
            request_id: str | None = None
            if mode == "fast_lipsync":
                try:
                    video_url = provider.generate_avatar_video_from_base_video(
                        base_video_url=avatar_base_video_url or avatar_source_metadata.get("download_url") or avatar_source_metadata.get("source_url") or "",
                        audio_url=chunk_audio_url,
                        duration=max(5, int(math.ceil(chunk_measured_tts_duration or chunk_audio_duration))),
                        seed=-1,
                        api_key=context.wavespeed_api_key,
                        audio_duration_seconds=chunk_audio_duration,
                        base_video_bytes=avatar_base_video_bytes,
                        base_video_filename=avatar_base_video_asset.filename if avatar_base_video_asset else f"slide-{slide_index}-avatar-base.mp4",
                        base_video_content_type=avatar_base_video_asset.mime_type if avatar_base_video_asset else "video/mp4",
                        audio_bytes=chunk_audio_bytes,
                        audio_filename=f"slide-{slide_index}-chunk-{chunk_index}.mp3",
                        audio_content_type="audio/mpeg",
                        sync_mode=app_settings.AVATAR_SYNC_MODE,
                        model_name=app_settings.AVATAR_LIPSYNC_MODEL_PATH,
                        retry_on_mismatch=True,
                        minimum_duration_ratio=0.8,
                        heartbeat_callback=_avatar_poll_heartbeat,
                    )
                    request_id = getattr(provider, "last_request_id", None)
                except (AvatarVideoProviderError, WavespeedClientError) as exc:
                    if not app_settings.ENABLE_STATIC_AVATAR_FALLBACK:
                        raise
                    retry_error = exc
                    if int(app_settings.AVATAR_PROVIDER_MAX_RETRIES) > 0:
                        logger.warning(
                            "Generation job %s: fast_lipsync provider failed for slide %s chunk %s, retrying once with fresh uploads: %s",
                            job.id,
                            slide_index,
                            chunk_index,
                            exc,
                        )
                        chunk_retry_used = True
                        try:
                            video_url = provider.generate_avatar_video_from_base_video(
                                base_video_url=avatar_base_video_url or avatar_source_metadata.get("download_url") or avatar_source_metadata.get("source_url") or "",
                                audio_url=chunk_audio_url,
                                duration=max(5, int(math.ceil(chunk_measured_tts_duration or chunk_audio_duration))),
                                seed=-1,
                                api_key=context.wavespeed_api_key,
                                audio_duration_seconds=chunk_audio_duration,
                                base_video_bytes=avatar_base_video_bytes,
                                base_video_filename=avatar_base_video_asset.filename if avatar_base_video_asset else f"slide-{slide_index}-avatar-base.mp4",
                                base_video_content_type=avatar_base_video_asset.mime_type if avatar_base_video_asset else "video/mp4",
                                audio_bytes=chunk_audio_bytes,
                                audio_filename=f"slide-{slide_index}-chunk-{chunk_index}.mp3",
                                audio_content_type="audio/mpeg",
                                sync_mode=app_settings.AVATAR_SYNC_MODE,
                                model_name=app_settings.AVATAR_LIPSYNC_MODEL_PATH,
                                retry_on_mismatch=False,
                                minimum_duration_ratio=0.8,
                                heartbeat_callback=_avatar_poll_heartbeat,
                            )
                            request_id = getattr(provider, "last_request_id", None)
                        except (AvatarVideoProviderError, WavespeedClientError) as retry_exc:
                            retry_error = retry_exc
                    fallback_used = True
                    fallback_reason = str(retry_error)
                    provider_timeout = "timed out" in fallback_reason.lower()
                    logger.warning(
                        "Generation job %s: fast_lipsync provider failed for slide %s chunk %s, falling back to static avatar: %s",
                        job.id,
                        slide_index,
                        chunk_index,
                        retry_error,
                    )
                    raw_clip, fallback_reason = _static_avatar_fallback_clip(
                        storage,
                        context,
                        chunk_measured_tts_duration,
                        str(retry_error),
                    )
            elif mode in {"infinitetalk_image", "audio_lipsync"} and lipsync_provider == "wavespeed_infinitetalk":
                video_url = provider.generate_avatar_video_from_audio(
                    image_url=avatar_source_metadata.get("download_url") or avatar_source_metadata.get("source_url") or "",
                    audio_url=chunk_audio_url,
                    duration=max(5, int(math.ceil(chunk_measured_tts_duration or chunk_audio_duration))),
                    seed=-1,
                    prompt=None,
                    resolution=app_settings.AVATAR_LIPSYNC_RESOLUTION,
                    api_key=context.wavespeed_api_key,
                    audio_duration_seconds=chunk_audio_duration,
                    image_bytes=avatar_source_bytes,
                    audio_bytes=chunk_audio_bytes,
                    image_filename=avatar_source_metadata.get("filename") or f"slide-{slide_index}-avatar.png",
                    image_content_type=avatar_source_metadata.get("mime_type") or "image/png",
                    audio_filename=f"slide-{slide_index}-chunk-{chunk_index}.mp3",
                    audio_content_type="audio/mpeg",
                    retry_on_mismatch=True,
                    minimum_duration_ratio=0.8,
                    heartbeat_callback=_avatar_poll_heartbeat,
                )
                request_id = getattr(provider, "last_request_id", None)
            elif mode == "static_avatar":
                raw_clip, fallback_reason = _static_avatar_fallback_clip(
                    storage,
                    context,
                    chunk_measured_tts_duration,
                    "static avatar mode",
                )
                fallback_used = True
            elif mode in {"wavespeed_text", "ai_talking_photos"}:
                if not chunk_text:
                    raise PipelineError(
                        "avatar_generation_failed",
                        "Narration text is missing",
                        stage="generating_avatar",
                        slide_index=slide_index,
                        chunk_index=chunk_index,
                    )
                video_url = provider.generate_avatar_video(
                    image_url=avatar_source_metadata.get("storage_key") or context.avatar_source_url or "avatar-source",
                    text=chunk_text,
                    duration=_talking_photo_duration_from_audio(chunk_audio_duration, chunk_text),
                    seed=-1,
                    api_key=context.wavespeed_api_key,
                )
                request_id = getattr(provider, "last_request_id", None)
            else:
                raise PipelineError(
                    "avatar_generation_failed",
                    f"Unsupported avatar generation mode: {mode}",
                    stage="generating_avatar",
                    slide_index=slide_index,
                    chunk_index=chunk_index,
                )
            request_ids.append(request_id)
            if raw_clip is None:
                if not video_url:
                    raise AvatarVideoProviderError(
                        "WaveSpeed did not return an avatar video URL",
                        "WAVESPEED_AVATAR_FAILED",
                    )
                clip_response = httpx.get(video_url, timeout=app_settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
                if clip_response.status_code >= 400:
                    raise AvatarVideoProviderError(
                        f"WaveSpeed output download returned HTTP {clip_response.status_code}",
                        "WAVESPEED_AVATAR_FAILED",
                    )
                raw_clip = clip_response.content
            raw_clip_info = _probe_media_info(raw_clip, ".mp4")
            provider_audio_present = bool(raw_clip_info.get("has_audio"))
            if provider_audio_present:
                logger.info(
                    "Generation job %s: slide %s chunk %s provider clip contains audio; stripping it before composition",
                    job.id,
                    slide_index,
                    chunk_index,
                )
            clip = composer.strip_audio_from_video(raw_clip) if provider_audio_present else raw_clip
            clip = composer.strip_audio_from_video(clip) if _probe_media_info(clip, ".mp4").get("has_audio") else clip
            clip_info = _probe_media_info(clip, ".mp4")
            if not clip or float(clip_info.get("duration_seconds") or 0) <= 0 or not clip_info.get("has_video"):
                raise PipelineError(
                    "avatar_generation_failed",
                    f"Avatar chunk {chunk_index} for slide {slide_index} is invalid",
                    stage="generating_avatar",
                    slide_index=slide_index,
                    chunk_index=chunk_index,
                )
            if clip_info.get("has_audio"):
                raise PipelineError(
                    "avatar_generation_failed",
                    f"Avatar chunk {chunk_index} for slide {slide_index} still contains audio after stripping",
                    stage="generating_avatar",
                    slide_index=slide_index,
                    chunk_index=chunk_index,
                )
            audio_source_url = getattr(provider, "last_audio_url", None) or chunk_audio_url
            image_source_url = (
                getattr(provider, "last_image_url", None)
                or avatar_source_metadata.get("storage_key")
                or context.avatar_source_url
                or ""
            )
            external_checks = getattr(provider, "last_external_checks", {}) or {}
            duration_ratio = getattr(provider, "last_duration_ratio", None)
            if chunk_measured_tts_duration > 0:
                effective_ratio = float(clip_info.get("duration_seconds") or 0.0) / chunk_measured_tts_duration
                if effective_ratio < 0.8:
                    raise PipelineError(
                        "avatar_generation_failed",
                        (
                            f"Avatar chunk {chunk_index} for slide {slide_index} is too short "
                            f"(avatar={float(clip_info.get('duration_seconds') or 0.0):.2f}s "
                            f"audio={chunk_measured_tts_duration:.2f}s ratio={effective_ratio:.2f})"
                        ),
                        stage="generating_avatar",
                        slide_index=slide_index,
                        chunk_index=chunk_index,
                        details={
                            "prediction_id": request_id,
                            "audio_duration_seconds": chunk_measured_tts_duration,
                            "avatar_duration_seconds": float(clip_info.get("duration_seconds") or 0.0),
                            "duration_ratio": effective_ratio,
                    "provider_audio_present": provider_audio_present,
                    "image_url_external_check_result": external_checks.get("image"),
                    "audio_url_external_check_result": external_checks.get("audio"),
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "provider_timeout": provider_timeout,
                    "chunk_retry": chunk_retry_used,
                },
                    )
            audio_source_urls.append(audio_source_url)
            chunk_avatar_bytes.append(clip)
            chunk_metadata.append(
                {
                    "chunk_index": chunk_index,
                    "text": chunk_text,
                    "text_length": len(chunk_text),
                    "word_count": chunk_word_count,
                    "estimated_duration_seconds": chunk_expected_duration,
                    "measured_audio_duration_seconds": chunk_audio_duration,
                    "measured_tts_duration": chunk_measured_tts_duration,
                    "avatar_duration_seconds": clip_info.get("duration_seconds"),
                    "audio_storage_key": chunk_audio_storage_key,
                    "audio_url_host": urlparse(audio_source_url).hostname,
                    "audio_url": audio_source_url,
                    "image_url_host": urlparse(image_source_url).hostname if image_source_url else None,
                    "image_url": image_source_url,
                    "image_url_external_check_result": external_checks.get("image"),
                    "audio_url_external_check_result": external_checks.get("audio"),
                    "avatar_video_url_host": urlparse(video_url).hostname,
                    "request_id": request_id,
                    "resolution": app_settings.AVATAR_LIPSYNC_RESOLUTION,
                    "provider_audio_present": provider_audio_present,
                    "provider_clip_ffprobe": raw_clip_info,
                    "stripped_clip_ffprobe": clip_info,
                    "outputs_present": bool(raw_clip_info.get("has_video")),
                    "duration_ratio": duration_ratio,
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "provider_timeout": provider_timeout,
                    "chunk_retry": chunk_retry_used,
                }
            )
            logger.info(
                "Generation job %s: slide %s chunk %s avatar ready prediction_id=%s resolution=%s provider_audio_present=%s avatar_duration=%.2f outputs_present=%s duration_ratio=%.2f fallback_used=%s provider_timeout=%s chunk_retry=%s",
                job.id,
                slide_index,
                chunk_index,
                request_id,
                app_settings.AVATAR_LIPSYNC_RESOLUTION,
                provider_audio_present,
                float(clip_info.get("duration_seconds") or 0.0),
                bool(raw_clip_info.get("has_video")),
                float(duration_ratio or 0.0),
                fallback_used,
                provider_timeout,
                chunk_retry_used,
            )
        if len(chunk_avatar_bytes) == 1:
            clip = chunk_avatar_bytes[0]
        else:
            clip = composer.concatenate_video_tracks(chunk_avatar_bytes)
        clip = composer.strip_audio_from_video(clip) if _probe_media_info(clip, ".mp4").get("has_audio") else clip
        avatar_metadata = {
            "provider": "wavespeed",
            "mode": mode,
            "source_image_url": getattr(provider, "last_image_url", None)
            or avatar_source_metadata.get("storage_key")
            or context.avatar_source_url,
            "source_audio_url": audio_source_urls[0] if len(audio_source_urls) == 1 else None,
            "source_audio_urls": audio_source_urls,
            "wavespeed_request_id": request_ids[-1] if request_ids else None,
            "wavespeed_request_ids": request_ids,
            "request_text_length": len(dialogue),
            "request_duration": (
                audio_duration_seconds
                if mode in {"fast_lipsync", "infinitetalk_image", "audio_lipsync", "static_avatar"}
                else _talking_photo_duration_from_audio(audio_duration_seconds, dialogue)
            ),
            "selected_provider": lipsync_provider,
            "audio_duration_seconds": audio_duration_seconds,
            "chunk_count": len(chunk_specs),
            "chunks": chunk_metadata,
            "resolution": app_settings.AVATAR_LIPSYNC_RESOLUTION,
            "provider_audio_present": any(
                chunk.get("provider_audio_present") for chunk in chunk_metadata
            ),
            "fallback_used": any(chunk.get("fallback_used") for chunk in chunk_metadata),
            "fallback_reason": next(
                (chunk.get("fallback_reason") for chunk in chunk_metadata if chunk.get("fallback_reason")),
                None,
            ),
        }
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            "avatar_generation_failed",
            _ffmpeg_error_message(exc),
            stage="generating_avatar",
            slide_index=slide_index,
        ) from exc
    except (AvatarVideoProviderError, WavespeedClientError) as exc:
        raise PipelineError(
            "avatar_generation_failed",
            str(exc),
            stage="generating_avatar",
            slide_index=slide_index,
            details=getattr(exc, "details", None),
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
    logger.info(
        "Generation job %s: slide %s avatar ready provider=%s mode=%s text_length=%s chunk_count=%s avatar_duration=%.2f has_audio=%s",
        job.id,
        slide_index,
        lipsync_provider,
        mode,
        len(dialogue),
        len(chunk_specs),
        duration,
        avatar_info.get("has_audio"),
    )
    if avatar_info.get("has_audio"):
        logger.warning(
            "Generation job %s: stripped avatar clip still reports audio after composition: slide=%s",
            job.id,
            slide_index,
        )
    motion = _analyze_video_motion(clip)
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
            "model_used": (
                app_settings.DEFAULT_LIPSYNC_MODEL
                if lipsync_provider == "wavespeed_infinitetalk"
                else "wavespeed-ai-talking-photos"
            ),
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
    audio_asset: Asset | None,
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
    if audio_asset is None:
        raise PipelineError(
            "slide_composition_failed",
            "A controlled TTS narration track is required for every slide.",
            stage="composing_slide",
            slide_index=slide_index,
        )
    composition_used_generated_avatar_clip = True
    composition_fallback_reason = None
    avatar_source_asset_id = str(avatar_clip_asset.id)
    avatar_source_storage_key = avatar_clip_asset.storage_key
    avatar_clip_info: dict = {}
    try:
        audio_duration_seconds = float(audio_asset.duration_seconds or 0) if audio_asset is not None else 0.0
        segment_duration_seconds = _slide_segment_duration_seconds(audio_asset, avatar_clip_asset)
        avatar_clip, fallback_reason, avatar_clip_info = _load_avatar_clip_or_static_fallback(
            storage=storage,
            context=context,
            avatar_clip_asset=avatar_clip_asset,
            duration_seconds=segment_duration_seconds,
        )
        if fallback_reason:
            composition_used_generated_avatar_clip = False
            composition_fallback_reason = fallback_reason
            avatar_source_asset_id = None
            avatar_source_storage_key = None
            if audio_asset is None:
                raise PipelineError(
                    "slide_composition_failed",
                    "Generated avatar clip is missing or invalid, and static fallback cannot be used without a separate audio track.",
                    stage="composing_slide",
                    slide_index=slide_index,
                )
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
        audio_bytes = storage.download_bytes(audio_asset.storage_key)
        audio_info = _probe_media_info(audio_bytes, ".mp3")
        audio_storage_key = audio_asset.storage_key
        audio_asset_id = str(audio_asset.id)
        logger.info(
            "Generation job %s: composing slide %s with slide preview source=%s audio=%s "
            "audio_duration=%s avatar_duration=%s",
            job.id,
            slide_index,
            slide_preview_source.get("storage_key"),
            audio_storage_key,
            audio_info.get("duration_seconds"),
            avatar_clip_info.get("duration_seconds"),
        )
        segment = composer.compose_slide_video(
            slide_image_bytes=slide_image,
            avatar_clip_bytes=avatar_clip,
            audio_bytes=audio_bytes,
            duration_seconds=segment_duration_seconds,
            avatar_overlay=_avatar_overlay_from_metadata(metadata, "1080p"),
            resolution="1080p",
            audio_pad_seconds=float(app_settings.SLIDE_PAUSE_SECONDS),
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
    if (
        duration <= 0
        or not segment_info.get("has_video")
        or not segment_info.get("has_audio")
        or int(segment_info.get("audio_stream_count") or 0) != 1
    ):
        raise PipelineError(
            "slide_composition_failed",
            f"Composed segment for slide {slide_index} is invalid or contains an unexpected audio stream layout",
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
            "audio_asset_id": audio_asset_id,
            "audio_duration_seconds": audio_info.get("duration_seconds"),
            "audio_storage_key": audio_storage_key,
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
    details: dict | None = None,
) -> None:
    detail_chunk_index = None
    if isinstance(details, dict):
        detail_chunk_index = details.get("chunk_index")
    if details is not None:
        result = dict(job.result or {})
        result["debug"] = details
        job.result = result
    resolved_step = current_step or job.current_step or "Generation failed"
    if detail_chunk_index is not None and "chunk" not in resolved_step.lower():
        resolved_step = f"{resolved_step} (chunk {detail_chunk_index})"
    update_generation_job_progress(
        db,
        job,
        status="failed",
        progress_percentage=float(job.progress_percentage or 0),
        current_step=resolved_step,
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


def _avatar_source_reference(
    db: Session,
    settings_row: VideoGenerationSettings,
    storage,
) -> tuple[str | None, str | None]:
    if settings_row.avatar_source_url:
        return settings_row.avatar_source_url, None
    if settings_row.avatar_source_asset_id:
        asset = db.get(Asset, settings_row.avatar_source_asset_id)
        if asset and storage_object_exists(storage, asset.storage_key):
            return None, asset.storage_key
    if app_settings.DEBUG_AVATAR_SOURCE_URL and not app_settings.is_production:
        return app_settings.DEBUG_AVATAR_SOURCE_URL, None
    return None, None


def _avatar_generation_mode() -> str:
    mode = (app_settings.AVATAR_GENERATION_MODE or "fast_lipsync").strip().lower()
    if mode == "audio_lipsync":
        return "fast_lipsync"
    return mode


def _requires_tts_audio() -> bool:
    return True


def _avatar_lipsync_provider() -> str:
    return (app_settings.AVATAR_LIPSYNC_PROVIDER or "wavespeed_infinitetalk").strip().lower()


def _tts_provider_configured(settings_row: VideoGenerationSettings) -> bool:
    if (app_settings.TTS_PROVIDER or "none").strip().lower() != "elevenlabs":
        return True
    elevenlabs_key = decrypt_secret(settings_row.elevenlabs_api_key_encrypted)
    return bool(elevenlabs_key and settings_row.elevenlabs_voice_id)


def _load_avatar_source_bytes(
    db: Session,
    storage,
    context: GenerationContext,
) -> tuple[bytes, dict]:
    if context.avatar_source_storage_key:
        asset = (
            db.query(Asset)
            .filter(
                Asset.project_id == context.project_id,
                Asset.organization_id == context.organization_id,
                Asset.storage_key == context.avatar_source_storage_key,
            )
            .first()
        )
        image_bytes = storage.download_bytes(context.avatar_source_storage_key)
        return image_bytes, {
            "filename": asset.filename if asset else None,
            "mime_type": asset.mime_type if asset else "image/png",
            "storage_key": context.avatar_source_storage_key,
            "source": "storage",
        }
    if context.avatar_source_url:
        response = httpx.get(context.avatar_source_url, timeout=app_settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
        if response.status_code >= 400:
            raise PipelineError(
                "validation_failed",
                "Avatar source URL could not be downloaded",
                stage="validating",
            )
        return response.content, {
            "filename": Path(urlparse(context.avatar_source_url).path).name or "avatar.png",
            "mime_type": response.headers.get("content-type") or "image/png",
            "storage_key": None,
            "source": "url",
        }
    raise PipelineError(
        "validation_failed",
        "Please upload an avatar image before generating the video.",
        stage="validating",
    )


def _ensure_avatar_base_video_asset(
    db: Session,
    storage,
    context: GenerationContext,
) -> tuple[Asset, bytes, dict]:
    avatar_source_bytes, avatar_source_metadata = _load_avatar_source_bytes(
        db=db,
        storage=storage,
        context=context,
    )
    source_signature = hashlib.sha256(avatar_source_bytes).hexdigest()
    existing = (
        db.query(Asset)
        .filter(
            Asset.project_id == context.project_id,
            Asset.organization_id == context.organization_id,
            Asset.asset_type == "avatar_base_video",
        )
        .order_by(Asset.created_at.desc())
        .first()
    )
    existing_metadata = existing.metadata_json if existing and isinstance(existing.metadata_json, dict) else {}
    if existing and existing_metadata:
        expected_storage_key = existing_metadata.get("avatar_source_storage_key")
        expected_signature = existing_metadata.get("avatar_source_signature")
        if (
            (context.avatar_source_storage_key and expected_storage_key == context.avatar_source_storage_key)
            or (not context.avatar_source_storage_key and expected_signature == source_signature)
        ):
            try:
                if storage_object_exists(storage, existing.storage_key):
                    return existing, storage.download_bytes(existing.storage_key), existing_metadata
            except Exception:
                pass

    base_duration = max(10.0, min(20.0, float(app_settings.MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK)))
    logger.info(
        "Generation job %s: creating avatar base video from avatar image source_key=%s signature=%s duration=%.2f",
        context.generation_job_id,
        context.avatar_source_storage_key,
        source_signature[:12],
        base_duration,
    )
    base_video = _image_to_video_clip(avatar_source_bytes, base_duration)
    base_video_info = _probe_media_info(base_video, ".mp4")
    if not base_video_info.get("has_video") or float(base_video_info.get("duration_seconds") or 0.0) <= 0:
        raise PipelineError(
            "avatar_generation_failed",
            "Could not create avatar base video",
            stage="generating_avatar",
        )
    base_key = f"{context.output_prefix}/avatar/base/avatar-base.mp4"
    storage.upload_file(base_key, base_video, "video/mp4")
    asset = _create_asset(
        db,
        context=context,
        slide=None,
        asset_type="avatar_base_video",
        storage_key=base_key,
        filename="avatar-base.mp4",
        mime_type="video/mp4",
        size_bytes=len(base_video),
        duration_seconds=float(base_video_info.get("duration_seconds") or base_duration),
        metadata_json={
            "avatar_source_storage_key": context.avatar_source_storage_key,
            "avatar_source_url": context.avatar_source_url,
            "avatar_source_signature": source_signature,
            "generated_from": "static_avatar",
            "fallback_used": True,
            "duration_seconds": float(base_video_info.get("duration_seconds") or base_duration),
            "source_image": avatar_source_metadata,
            "ffprobe": base_video_info,
        },
    )
    return asset, base_video, asset.metadata_json or {}


def _load_avatar_source_url(
    db: Session,
    storage,
    context: GenerationContext,
) -> tuple[str, dict]:
    if context.avatar_source_url:
        return context.avatar_source_url, {
            "filename": Path(urlparse(context.avatar_source_url).path).name or "avatar.png",
            "mime_type": "image/png",
            "storage_key": None,
            "source": "url",
        }
    if context.avatar_source_storage_key:
        asset = (
            db.query(Asset)
            .filter(
                Asset.project_id == context.project_id,
                Asset.organization_id == context.organization_id,
                Asset.storage_key == context.avatar_source_storage_key,
            )
            .first()
        )
        return _asset_public_url(storage, context.avatar_source_storage_key), {
            "filename": asset.filename if asset else None,
            "mime_type": asset.mime_type if asset else "image/png",
            "storage_key": context.avatar_source_storage_key,
            "source": "storage",
        }
    raise PipelineError(
        "validation_failed",
        "Please upload an avatar image before generating the video.",
        stage="validating",
    )


def _asset_public_url(storage, storage_key: str) -> str:
    if hasattr(storage, "generate_external_download_url"):
        candidate = storage.generate_external_download_url(storage_key)
        url = candidate.url if hasattr(candidate, "url") else str(candidate)
    elif hasattr(storage, "generate_read_url"):
        url = storage.generate_read_url(storage_key)
    elif hasattr(storage, "generate_presigned_download_url"):
        candidate = storage.generate_presigned_download_url(storage_key)
        url = candidate.url if hasattr(candidate, "url") else str(candidate)
    else:
        raise PipelineError(
            "validation_failed",
            "Storage service cannot generate a public asset URL",
            stage="validating",
        )
    _validate_external_provider_url(url)
    return url


def _validate_external_provider_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise PipelineError(
            "validation_failed",
            "WaveSpeed requires a public URL for audio/avatar assets.",
            stage="validating",
        )

    if hostname.endswith(".blob.core.windows.net"):
        return

    public_hosts = set()
    for candidate in (
        app_settings.EXTERNAL_PROVIDER_ASSET_BASE_URL,
        app_settings.MINIO_PUBLIC_ENDPOINT,
        app_settings.MINIO_ENDPOINT,
    ):
        if not candidate:
            continue
        parsed_candidate = urlparse(candidate if "://" in candidate else f"http://{candidate}")
        if parsed_candidate.hostname:
            public_hosts.add(parsed_candidate.hostname.lower())

    if hostname in public_hosts:
        return

    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "minio"}
    if hostname in blocked_hosts or hostname.endswith(".local"):
        raise PipelineError(
            "validation_failed",
            "WaveSpeed requires a public URL for audio/avatar assets. Configure a public tunnel or external storage.",
            stage="validating",
        )


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÿ]+\b", text or "", flags=re.UNICODE))


def _estimate_spanish_duration(text: str) -> float:
    return _word_count(text) / 2.2 if text else 0.0


def _build_narration_chunks(text: str) -> list[dict[str, object]]:
    normalized = _normalize_tts_text(text)
    if not normalized:
        return []

    max_chars = max(1, int(app_settings.MAX_TTS_CHARS_PER_CHUNK))
    max_seconds = max(1.0, float(app_settings.MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK))
    sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?¿¡])\s+", normalized) if part.strip()]
    if not sentence_parts:
        sentence_parts = [normalized]

    chunks: list[str] = []
    current: list[str] = []
    for sentence in sentence_parts:
        sentence_variants = _split_long_chunk(sentence, max_chars=max_chars, max_seconds=max_seconds)
        for variant in sentence_variants:
            candidate = " ".join(current + [variant]).strip()
            if not candidate:
                continue
            if _chunk_fits_limits(candidate, max_chars=max_chars, max_seconds=max_seconds):
                current.append(variant)
                continue
            if current:
                chunks.append(" ".join(current).strip())
                current = []
            if _chunk_fits_limits(variant, max_chars=max_chars, max_seconds=max_seconds):
                current.append(variant)
            else:
                chunks.append(variant.strip())
    if current:
        chunks.append(" ".join(current).strip())
    max_chunks = max(1, int(app_settings.MAX_CHUNKS_PER_SLIDE))
    chunks = _compress_narration_chunks(
        chunks,
        max_chars=max_chars,
        max_seconds=max_seconds,
        max_chunks=max_chunks,
    )
    if len(chunks) > max_chunks:
        logger.warning(
            "Generation narration chunk cap exceeded after compression: max_chunks=%s actual_chunks=%s text_length=%s",
            max_chunks,
            len(chunks),
            len(normalized),
        )
        raise PipelineError(
            "audio_generation_failed",
            "Slide narration is too long for the configured chunk cap. Please shorten the slide dialogue.",
            stage="generating_audio",
        )

    result: list[dict[str, object]] = []
    for index, chunk_text in enumerate(chunks, 1):
        cleaned = _normalize_tts_text(chunk_text)
        if not cleaned:
            continue
        result.append(
            {
                "chunk_index": index,
                "text": cleaned,
                "text_length": len(cleaned),
                "word_count": _word_count(cleaned),
                "expected_duration_seconds": round(_estimate_spanish_duration(cleaned), 2),
            }
        )
    return result


def _compress_narration_chunks(
    chunks: list[str],
    *,
    max_chars: int,
    max_seconds: float,
    max_chunks: int,
) -> list[str]:
    normalized = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    if len(normalized) <= max_chunks:
        return normalized

    while len(normalized) > max_chunks:
        merged = False
        for index in range(len(normalized) - 1):
            candidate = f"{normalized[index]} {normalized[index + 1]}".strip()
            if _chunk_fits_limits(candidate, max_chars=max_chars, max_seconds=max_seconds):
                normalized[index : index + 2] = [candidate]
                merged = True
                break
        if merged:
            continue
        for index in range(len(normalized) - 1):
            candidate = f"{normalized[index]} {normalized[index + 1]}".strip()
            if len(candidate) <= max_chars * 2:
                normalized[index : index + 2] = [candidate]
                merged = True
                break
        if not merged:
            break
    return normalized


def _can_reuse_slide_audio_asset(asset: Asset, slide: Slide, slide_index: int) -> bool:
    dialogue = _normalize_tts_text(str((slide.metadata_ or {}).get("dialogue") or slide.notes or ""))
    planned_chunks = _build_narration_chunks(dialogue)
    if len(planned_chunks) <= 1:
        return True

    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    chunks = metadata.get("chunks")
    if not isinstance(chunks, list) or len(chunks) < len(planned_chunks):
        logger.info(
            "Generation job audio reuse rejected for slide %s: chunk metadata missing or incomplete (planned_chunks=%s stored_chunks=%s)",
            slide_index,
            len(planned_chunks),
            len(chunks) if isinstance(chunks, list) else None,
        )
        return False

    stored_count = metadata.get("chunk_count")
    if isinstance(stored_count, int) and stored_count < len(planned_chunks):
        logger.info(
            "Generation job audio reuse rejected for slide %s: stored chunk count too small (planned_chunks=%s stored_chunk_count=%s)",
            slide_index,
            len(planned_chunks),
            stored_count,
        )
        return False

    expected_duration = sum(float(chunk.get("expected_duration_seconds") or 0.0) for chunk in planned_chunks)
    measured_duration = float(asset.duration_seconds or 0.0)
    if expected_duration > 0 and measured_duration < expected_duration * app_settings.MIN_EXPECTED_AUDIO_DURATION_RATIO:
        logger.info(
            "Generation job audio reuse rejected for slide %s: measured duration too short for planned narration (measured=%.2fs expected=%.2fs)",
            slide_index,
            measured_duration,
            expected_duration,
        )
        return False

    return True


def _make_chunk_spec(text: str) -> dict[str, object]:
    cleaned = _normalize_tts_text(text)
    return {
        "text": cleaned,
        "index": 1,
        "text_length": len(cleaned),
        "word_count": _word_count(cleaned),
        "estimated_duration": round(_estimate_spanish_duration(cleaned), 2),
        "expected_duration_seconds": round(_estimate_spanish_duration(cleaned), 2),
    }


def _require_chunk_text(chunk: dict, slide_index: int, chunk_index: int) -> str:
    text = chunk.get("text")
    if not isinstance(text, str) or not text.strip():
        raise PipelineError(
            "avatar_generation_failed",
            f"Chunk {chunk_index} for slide {slide_index} is missing required text",
            stage="generating_avatar",
            slide_index=slide_index,
            chunk_index=chunk_index,
        )
    return text.strip()


def _require_chunk_audio_storage_key(chunk: dict, slide_index: int, chunk_index: int) -> str:
    storage_key = chunk.get("audio_storage_key")
    if not isinstance(storage_key, str) or not storage_key.strip():
        raise PipelineError(
            "avatar_generation_failed",
            f"Chunk {chunk_index} for slide {slide_index} is missing required audio storage key",
            stage="generating_avatar",
            slide_index=slide_index,
            chunk_index=chunk_index,
        )
    return storage_key.strip()


def _require_chunk_audio_url(chunk: dict, slide_index: int, chunk_index: int) -> str:
    audio_url = chunk.get("audio_url")
    if not isinstance(audio_url, str) or not audio_url.strip():
        raise PipelineError(
            "avatar_generation_failed",
            f"Chunk {chunk_index} for slide {slide_index} is missing required audio URL",
            stage="generating_avatar",
            slide_index=slide_index,
            chunk_index=chunk_index,
        )
    return audio_url.strip()


def _require_chunk_duration(chunk: dict, slide_index: int, chunk_index: int) -> float:
    duration = chunk.get("measured_tts_duration")
    if duration is None:
        duration = chunk.get("measured_duration_seconds")
    try:
        duration_value = float(duration)
    except (TypeError, ValueError):
        duration_value = 0.0
    if duration_value <= 0:
        raise PipelineError(
            "avatar_generation_failed",
            f"Chunk {chunk_index} for slide {slide_index} is missing required measured TTS duration",
            stage="generating_avatar",
            slide_index=slide_index,
            chunk_index=chunk_index,
        )
    return duration_value


def _normalize_audio_chunk_spec(
    chunk: dict,
    *,
    chunk_index: int,
    audio_asset: Asset | None,
    dialogue: str,
    storage=None,
) -> dict[str, object]:
    normalized_text = _normalize_tts_text(
        str(
            chunk.get("text")
            or chunk.get("normalized_text")
            or chunk.get("content")
            or chunk.get("dialogue")
            or dialogue
            or ""
        )
    )
    if not normalized_text:
        raise PipelineError(
            "avatar_generation_failed",
            f"Chunk {chunk_index} is missing required narration text",
            stage="generating_avatar",
            chunk_index=chunk_index,
        )
    audio_storage_key = (
        chunk.get("audio_storage_key")
        or chunk.get("storage_key")
        or (audio_asset.storage_key if audio_asset is not None else None)
    )
    if not isinstance(audio_storage_key, str) or not audio_storage_key:
        raise PipelineError(
            "avatar_generation_failed",
            f"Chunk {chunk_index} is missing required audio storage key",
            stage="generating_avatar",
            chunk_index=chunk_index,
        )
    audio_url = chunk.get("audio_url")
    if not isinstance(audio_url, str) or not audio_url:
        if storage is None:
            raise PipelineError(
                "avatar_generation_failed",
                f"Chunk {chunk_index} is missing required audio URL",
                stage="generating_avatar",
                chunk_index=chunk_index,
            )
        audio_url = _asset_public_url(storage, audio_storage_key)
    estimated_duration = float(
        chunk.get("estimated_duration")
        or chunk.get("expected_duration_seconds")
        or _estimate_spanish_duration(normalized_text)
    )
    measured_tts_duration_value = chunk.get("measured_tts_duration") or chunk.get("measured_duration_seconds")
    if measured_tts_duration_value is None and audio_asset is not None:
        measured_tts_duration_value = float(audio_asset.duration_seconds or 0)
    measured_tts_duration = float(measured_tts_duration_value or 0.0)
    word_count = int(chunk.get("word_count") or _word_count(normalized_text))
    index = int(chunk.get("index") or chunk.get("chunk_index") or chunk_index)
    return {
        "index": index,
        "chunk_index": index,
        "text": normalized_text,
        "text_length": int(chunk.get("text_length") or len(normalized_text)),
        "word_count": word_count,
        "estimated_duration": estimated_duration,
        "expected_duration_seconds": estimated_duration,
        "measured_tts_duration": measured_tts_duration,
        "measured_duration_seconds": measured_tts_duration,
        "audio_storage_key": audio_storage_key,
        "audio_url": audio_url,
        "audio_asset_id": chunk.get("audio_asset_id") or (str(audio_asset.id) if audio_asset is not None else None),
    }


def _split_long_chunk(chunk: str, *, max_chars: int, max_seconds: float) -> list[str]:
    if not chunk:
        return []
    if _chunk_fits_limits(chunk, max_chars=max_chars, max_seconds=max_seconds):
        return [chunk]

    for delimiter in (r"(?<=[,;:])\s+", r"(?<=[-–—])\s+"):
        parts = [part.strip() for part in re.split(delimiter, chunk) if part.strip()]
        if len(parts) > 1:
            if all(_chunk_fits_limits(part, max_chars=max_chars, max_seconds=max_seconds) for part in parts):
                return parts
            flattened: list[str] = []
            for part in parts:
                flattened.extend(_split_long_chunk(part, max_chars=max_chars, max_seconds=max_seconds))
            return flattened

    words = [word for word in re.split(r"\s+", chunk) if word]
    if len(words) <= 1:
        return [chunk]

    split_points = max(1, math.ceil(len(words) / max(1, math.floor(len(words) / 2) or 1)))
    if split_points <= 1:
        split_points = 2
    step = max(1, math.ceil(len(words) / split_points))
    pieces: list[str] = []
    start = 0
    while start < len(words):
        end = min(len(words), start + step)
        piece = " ".join(words[start:end]).strip()
        if piece:
            pieces.append(piece)
        start = end
    if len(pieces) == 1:
        return [chunk]

    flattened: list[str] = []
    for piece in pieces:
        flattened.extend(_split_long_chunk(piece, max_chars=max_chars, max_seconds=max_seconds))
    return flattened


def _split_chunk_text_for_retry(text: str) -> list[str]:
    normalized = _normalize_tts_text(text)
    if not normalized:
        return []

    sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?¿¡])\s+", normalized) if part.strip()]
    if len(sentence_parts) > 1:
        return sentence_parts

    clause_parts = [part.strip() for part in re.split(r"(?<=[,;:])\s+", normalized) if part.strip()]
    if len(clause_parts) > 1:
        return clause_parts

    words = [word for word in re.split(r"\s+", normalized) if word]
    if len(words) <= 1:
        return [normalized]

    midpoint = max(1, len(words) // 2)
    left = " ".join(words[:midpoint]).strip()
    right = " ".join(words[midpoint:]).strip()
    return [part for part in (left, right) if part]


def _chunk_fits_limits(text: str, *, max_chars: int, max_seconds: float) -> bool:
    return len(text) <= max_chars and _estimate_spanish_duration(text) <= max_seconds


def _slide_audio_chunk_specs(
    audio_asset: Asset | None,
    dialogue: str,
    storage=None,
) -> list[dict[str, object]]:
    planned_chunks = _build_narration_chunks(dialogue)
    if audio_asset and isinstance(audio_asset.metadata_json, dict):
        chunks = audio_asset.metadata_json.get("chunks")
        if isinstance(chunks, list) and chunks:
            normalized_chunks: list[dict[str, object]] = []
            for idx, chunk in enumerate(chunks, 1):
                if not isinstance(chunk, dict):
                    continue
                normalized_chunks.append(
                    _normalize_audio_chunk_spec(
                        chunk,
                        chunk_index=idx,
                        audio_asset=audio_asset,
                        dialogue=dialogue,
                        storage=storage,
                    )
                )
            return normalized_chunks
    if not audio_asset or not dialogue:
        return []
    if len(planned_chunks) > 1:
        raise PipelineError(
            "avatar_generation_failed",
            "Slide audio asset is missing chunk metadata for long narration. Regenerate the slide audio.",
            stage="generating_avatar",
        )
    return [
        _normalize_audio_chunk_spec(
            {
                "text": dialogue,
                "text_length": len(dialogue),
                "word_count": _word_count(dialogue),
                "expected_duration_seconds": round(_estimate_spanish_duration(dialogue), 2),
                "measured_duration_seconds": float(audio_asset.duration_seconds or 0),
                "audio_storage_key": audio_asset.storage_key,
                "audio_asset_id": str(audio_asset.id),
                "index": 1,
            },
            chunk_index=1,
            audio_asset=audio_asset,
            dialogue=dialogue,
            storage=storage,
        )
    ]


def _talking_photo_duration(text: str) -> int:
    word_count = max(1, len(text.split()))
    return max(5, min(15, math.ceil(word_count / 2.5)))


def _normalize_tts_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", raw) if part.strip()]
    normalized_parts: list[str] = []
    for paragraph in paragraphs or [raw]:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", paragraph) if part.strip()]
        if not sentences:
            continue
        for sentence in sentences:
            cleaned = re.sub(r"\s+", " ", sentence).strip()
            if cleaned and cleaned[-1] not in ".!?":
                cleaned = f"{cleaned}."
            normalized_parts.append(cleaned)
    return " ".join(normalized_parts)


def _talking_photo_duration_from_audio(audio_duration_seconds: float, text: str) -> int:
    if audio_duration_seconds > 0:
        return max(5, int(math.ceil(audio_duration_seconds)))
    return _talking_photo_duration(text)


def _slide_segment_duration_seconds(audio_asset: Asset | None, avatar_clip_asset: Asset) -> float:
    if audio_asset is None or float(audio_asset.duration_seconds or 0) <= 0:
        raise PipelineError(
            "slide_composition_failed",
            "A controlled TTS narration track is required for every slide.",
            stage="composing_slide",
        )
    return float(audio_asset.duration_seconds or 0) + float(app_settings.SLIDE_PAUSE_SECONDS)


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
        "video_stream_count": 0,
        "audio_stream_count": 0,
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
            info["video_stream_count"] += 1
            info["has_video"] = True
            info["video_codec"] = stream.get("codec_name")
            info["width"] = stream.get("width")
            info["height"] = stream.get("height")
        elif stream.get("codec_type") == "video":
            info["video_stream_count"] += 1
        if stream.get("codec_type") == "audio" and not info["has_audio"]:
            info["audio_stream_count"] += 1
            info["has_audio"] = True
            info["audio_codec"] = stream.get("codec_name")
        elif stream.get("codec_type") == "audio":
            info["audio_stream_count"] += 1
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
