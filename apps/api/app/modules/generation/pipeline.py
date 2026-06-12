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
from app.modules.generation.media_settings import (
    build_subtitle_force_style,
    normalize_media_settings,
)
from app.modules.generation.models import GenerationJob, VideoGenerationSettings
from app.modules.projects.models import Asset, Presentation, ProjectGenerationConfig, Slide
from app.modules.provider_credentials.models import ProviderCredential
from app.modules.tts.adapters import TTSProviderError, get_tts_provider
from app.modules.video.adapters import AvatarVideoProviderError, get_avatar_video_provider
from app.services.wavespeed_client import WavespeedClient, WavespeedClientError
from app.services.wavespeed_official_client import (
    INFINITETALK_FAST_MODEL,
    SPANISH_LIPSYNC_PROMPT,
    WaveSpeedOfficialClient,
    WaveSpeedOfficialError,
    download_video,
    public_url_accessible,
)
from app.utils.crypto import decrypt_secret

logger = logging.getLogger(__name__)

# Backward-compatible symbol used by some tests/monkeypatch paths.
settings = app_settings


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


@dataclass
class TTSCredentialsResolution:
    provider: str
    api_key: str | None
    voice_id: str | None
    credentials_source: str


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


def resolve_global_tts_credentials(
    db: Session,
    organization_id: uuid.UUID,
) -> tuple[str | None, str | None]:
    """Read the global ElevenLabs credential (dashboard-managed) for an org.

    Returns (api_key, voice_id). These are the *primary* source for generation;
    environment variables are only an optional fallback.
    """
    credential = (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.organization_id == organization_id,
            ProviderCredential.provider_name == "elevenlabs",
            ProviderCredential.provider_type == "tts",
        )
        .first()
    )
    if credential is None:
        return None, None
    api_key = decrypt_secret(credential.encrypted_api_key) or None
    voice_id = (credential.voice_id or "").strip() or None
    return api_key, voice_id


def resolve_global_wavespeed_key(
    db: Session,
    organization_id: uuid.UUID,
) -> str | None:
    """Read the global WaveSpeed API key (dashboard-managed) for an org."""
    credential = (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.organization_id == organization_id,
            ProviderCredential.provider_name == "wavespeed",
            ProviderCredential.provider_type == "avatar_video",
        )
        .first()
    )
    if credential is None:
        return None
    return decrypt_secret(credential.encrypted_api_key) or None


def resolve_tts_credentials(
    project_config: ProjectGenerationConfig | None,
    settings_module=app_settings,
) -> TTSCredentialsResolution:
    config_provider = ""
    config_api_key = None
    config_voice_id = None
    if project_config is not None:
        config_provider = (project_config.tts_provider or "").strip().lower()
        config_api_key = decrypt_secret(project_config.elevenlabs_api_key_encrypted)
        config_voice_id = (project_config.voice_id or "").strip() or None

    env_provider = (settings_module.TTS_PROVIDER or "none").strip().lower()
    provider = config_provider or env_provider

    if provider != "elevenlabs":
        return TTSCredentialsResolution(
            provider=provider,
            api_key=None,
            voice_id=None,
            credentials_source="project_config" if config_provider else "env_fallback",
        )

    if config_api_key and config_voice_id:
        return TTSCredentialsResolution(
            provider=provider,
            api_key=config_api_key,
            voice_id=config_voice_id,
            credentials_source="project_config",
        )

    env_api_key = (settings_module.ELEVENLABS_API_KEY or "").strip() or None
    env_voice_id = (settings_module.ELEVENLABS_VOICE_ID or "").strip() or None
    if env_api_key and env_voice_id:
        return TTSCredentialsResolution(
            provider=provider,
            api_key=env_api_key,
            voice_id=env_voice_id,
            credentials_source="env_fallback",
        )

    return TTSCredentialsResolution(
        provider=provider,
        api_key=None,
        voice_id=None,
        credentials_source="missing",
    )


def resolve_saved_tts_credentials(
    db: Session,
    project_id: uuid.UUID,
    organization_id: uuid.UUID,
    settings_module=app_settings,
) -> TTSCredentialsResolution:
    project_config = (
        db.query(ProjectGenerationConfig)
        .filter(
            ProjectGenerationConfig.project_id == project_id,
            ProjectGenerationConfig.organization_id == organization_id,
        )
        .first()
    )
    video_settings = (
        db.query(VideoGenerationSettings)
        .filter(VideoGenerationSettings.project_id == project_id)
        .first()
    )
    project_resolution = resolve_tts_credentials(project_config, settings_module)

    # PRIMARY SOURCE: global dashboard-managed credentials. When an ElevenLabs
    # credential is configured globally it takes precedence over project config,
    # legacy per-project video settings, and environment variables. The presence
    # of a global ElevenLabs key also implies the ElevenLabs provider.
    global_api_key, global_voice_id = resolve_global_tts_credentials(db, organization_id)
    if global_api_key and global_voice_id:
        return TTSCredentialsResolution(
            provider="elevenlabs",
            api_key=global_api_key,
            voice_id=global_voice_id,
            credentials_source="global_provider_credentials",
        )

    if project_resolution.provider == "elevenlabs":
        if project_resolution.api_key and project_resolution.voice_id:
            return project_resolution
    else:
        if video_settings is not None:
            fallback_api_key = decrypt_secret(video_settings.elevenlabs_api_key_encrypted)
            fallback_voice_id = (video_settings.elevenlabs_voice_id or "").strip() or None
            if fallback_api_key and fallback_voice_id:
                return TTSCredentialsResolution(
                    provider="elevenlabs",
                    api_key=fallback_api_key,
                    voice_id=fallback_voice_id,
                    credentials_source="video_settings",
                )
        return project_resolution

    if video_settings is not None:
        fallback_api_key = decrypt_secret(video_settings.elevenlabs_api_key_encrypted)
        fallback_voice_id = (video_settings.elevenlabs_voice_id or "").strip() or None
        if fallback_api_key and fallback_voice_id:
            return TTSCredentialsResolution(
                provider="elevenlabs",
                api_key=fallback_api_key,
                voice_id=fallback_voice_id,
                credentials_source="video_settings",
            )

    return project_resolution


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
        settings_row = VideoGenerationSettings(
            organization_id=organization_id,
            project_id=project_id,
            validation_status="valid",
            wavespeed_valid=True,
            elevenlabs_valid=False,
        )
    # PRIMARY SOURCE: global dashboard-managed WaveSpeed key; env is fallback.
    wavespeed_api_key = (
        resolve_global_wavespeed_key(db, organization_id)
        or app_settings.WAVESPEED_API_KEY
        or ""
    ).strip()
    if not wavespeed_api_key:
        raise PipelineError(
            "validation_failed",
            "No hay una WaveSpeed API Key configurada. Configúrala en el panel de "
            "Configuración del dashboard.",
            stage="validating",
        )
    tts_resolution = resolve_saved_tts_credentials(
        db,
        project_id,
        organization_id,
        app_settings,
    )
    logger.info(
        "Generation job %s: TTS credentials resolved provider=%s credentials_source=%s elevenlabs_api_key_present=%s elevenlabs_voice_id_present=%s",
        job.id,
        tts_resolution.provider,
        tts_resolution.credentials_source,
        bool(tts_resolution.api_key),
        bool(tts_resolution.voice_id),
    )
    if tts_resolution.provider == "none":
        if not app_settings.ALLOW_DUMMY_TTS:
            raise PipelineError(
                "validation_failed",
                "Please configure a real TTS provider before generating the video.",
                stage="validating",
            )
    elif tts_resolution.provider == "elevenlabs":
        if not tts_resolution.api_key or not tts_resolution.voice_id:
            if not tts_resolution.api_key and not (app_settings.ELEVENLABS_API_KEY or "").strip():
                missing_message = "ELEVENLABS_API_KEY is missing in worker environment"
            elif not tts_resolution.voice_id and not (app_settings.ELEVENLABS_VOICE_ID or "").strip():
                missing_message = "ELEVENLABS_VOICE_ID is missing in worker environment"
            else:
                missing_message = (
                    "Please configure ElevenLabs API key and voice ID in project settings before generating video."
                )
            raise PipelineError(
                "validation_failed",
                missing_message,
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
        elevenlabs_api_key=tts_resolution.api_key,
        elevenlabs_voice_id=tts_resolution.voice_id,
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
    tts_resolution = resolve_saved_tts_credentials(
        db,
        context.project_id,
        context.organization_id,
        app_settings,
    )
    tts_provider_name = tts_resolution.provider
    chunks = _build_narration_chunks(dialogue)
    if not chunks:
        raise PipelineError(
            "audio_generation_failed",
            f"Slide {slide_index} is missing narration text",
            stage="generating_audio",
            slide_index=slide_index,
        )
    logger.info(
        "Generation job %s: generating TTS audio for slide %s/%s provider=%s credentials_source=%s text_length=%s word_count=%s chunk_count=%s speed=%s elevenlabs_api_key_present=%s elevenlabs_voice_id_present=%s",
        job.id,
        slide_index,
        total_slides,
        tts_provider_name,
        tts_resolution.credentials_source,
        len(dialogue),
        _word_count(dialogue),
        len(chunks),
        app_settings.TTS_SPEED,
        bool(tts_resolution.api_key),
        bool(tts_resolution.voice_id),
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
                voice_id=tts_resolution.voice_id,
                language=app_settings.TTS_LANGUAGE,
                speed=app_settings.TTS_SPEED,
                api_key=tts_resolution.api_key,
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
            ratio_tolerance = 0.02
            if (
                chunk_expected_duration > 0
                and (expected_ratio + ratio_tolerance) < app_settings.MIN_EXPECTED_AUDIO_DURATION_RATIO
            ):
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
    final_ratio_tolerance = 0.02
    if (
        expected_total_duration > 0
        and ((duration / expected_total_duration) + final_ratio_tolerance) < app_settings.MIN_EXPECTED_AUDIO_DURATION_RATIO
    ):
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
    avatar_base_video_asset: Asset | None = None,
    avatar_base_video_bytes: bytes | None = None,
    avatar_base_video_metadata: dict | None = None,
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
    image_audio_provider = _avatar_image_audio_provider()
    image_audio_resolution = _avatar_image_audio_resolution()
    image_audio_mode = mode in {"infinitetalk_image", "audio_lipsync", "image_audio_infinitetalk"}
    if mode == "image_audio_infinitetalk":
        effective_provider_name = image_audio_provider
    elif mode in {"infinitetalk_image", "audio_lipsync"}:
        # Backward-compatible behavior: these modes follow AVATAR_LIPSYNC_PROVIDER.
        effective_provider_name = lipsync_provider
    else:
        effective_provider_name = lipsync_provider
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
        avatar_base_video_metadata: dict | None = None
        final_avatar_output_storage_key: str | None = None
        provider_lipsync_output_storage_key: str | None = None
        if mode == "fast_lipsync":
            if avatar_base_video_asset is None or avatar_base_video_bytes is None:
                avatar_base_video_asset, avatar_base_video_bytes, avatar_base_video_metadata = _ensure_avatar_base_video_asset(
                    db=db,
                    storage=storage,
                    context=context,
                )
        avatar_base_video_url = ""
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
                retry_error: Exception | None = None
                try:
                    provider_output = provider.generate_avatar_video_from_base_video(
                        base_video_url=avatar_base_video_url,
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
                    if isinstance(provider_output, (bytes, bytearray)):
                        raw_clip = bytes(provider_output)
                        video_url = getattr(provider, "last_generated_video_url", None)
                    else:
                        video_url = str(provider_output)
                except (AvatarVideoProviderError, WavespeedClientError) as exc:
                    retry_error = exc
                    if not app_settings.ENABLE_STATIC_AVATAR_FALLBACK:
                        raise
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
                            provider_output = provider.generate_avatar_video_from_base_video(
                                base_video_url=avatar_base_video_url,
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
                            if isinstance(provider_output, (bytes, bytearray)):
                                raw_clip = bytes(provider_output)
                                video_url = getattr(provider, "last_generated_video_url", None)
                            else:
                                video_url = str(provider_output)
                            retry_error = None
                        except (AvatarVideoProviderError, WavespeedClientError) as retry_exc:
                            retry_error = retry_exc
                    if retry_error is not None:
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
            elif image_audio_mode and image_audio_provider in {
                "wavespeed_infinitetalk",
                "wavespeed_infinitetalk_fast",
                "wavespeed-ai/infinitetalk",
                "wavespeed-ai/infinitetalk-fast",
            }:
                video_url = provider.generate_avatar_video_from_audio(
                    image_url=avatar_source_metadata.get("download_url") or avatar_source_metadata.get("source_url") or "",
                    audio_url=chunk_audio_url,
                    duration=max(5, int(math.ceil(chunk_measured_tts_duration or chunk_audio_duration))),
                    seed=-1,
                    prompt=None,
                    resolution=image_audio_resolution,
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
            provider_request_context = getattr(provider, "last_request_context", {}) or {}
            provider_status_history = getattr(provider, "last_status_history", []) or []
            logger.info(
                "Generation job %s: slide %s chunk %s provider debug mode=%s provider=%s request_type=%s endpoint=%s "
                "input_image_url_present=%s input_video_url_present=%s input_audio_url_present=%s input_video_duration=%s "
                "input_audio_duration=%s sync_mode=%s resolution=%s prediction_id=%s provider_status_history=%s",
                job.id,
                slide_index,
                chunk_index,
                provider_request_context.get("avatar_generation_mode") or mode,
                provider_request_context.get("avatar_provider_name") or effective_provider_name,
                provider_request_context.get("provider_request_type"),
                provider_request_context.get("provider_endpoint"),
                provider_request_context.get("input_image_url_present"),
                provider_request_context.get("input_video_url_present"),
                provider_request_context.get("input_audio_url_present"),
                provider_request_context.get("input_video_duration"),
                provider_request_context.get("input_audio_duration"),
                provider_request_context.get("sync_mode"),
                provider_request_context.get("resolution"),
                request_id,
                provider_status_history,
            )
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
            provider_green_background = _detect_green_background(raw_clip)
            chunk_motion = _analyze_video_motion(clip)
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
                    "avatar_generation_mode": mode,
                    "effective_avatar_generation_mode": mode,
                    "avatar_provider_name": effective_provider_name,
                    "provider_request_type": provider_request_context.get("provider_request_type"),
                    "provider_endpoint": provider_request_context.get("provider_endpoint"),
                    "input_image_url_present": provider_request_context.get("input_image_url_present"),
                    "input_video_url_present": provider_request_context.get("input_video_url_present"),
                    "input_audio_url_present": provider_request_context.get("input_audio_url_present"),
                    "input_video_duration": provider_request_context.get("input_video_duration"),
                    "input_audio_duration": provider_request_context.get("input_audio_duration"),
                    "sync_mode": provider_request_context.get("sync_mode"),
                    "resolution": provider_request_context.get("resolution") or app_settings.AVATAR_LIPSYNC_RESOLUTION,
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
                    "avatar_video_url_host": urlparse(video_url).hostname if video_url else None,
                    "request_id": request_id,
                    "provider_audio_present": provider_audio_present,
                    "provider_output_has_motion": not bool(chunk_motion.get("almost_static")),
                    "provider_output_has_green_background": provider_green_background.get("detected"),
                    "provider_requires_chromakey": bool(provider_green_background.get("detected")),
                    "provider_output_url_present": bool(video_url),
                    "provider_output_url_host": urlparse(video_url).hostname if video_url else None,
                    "provider_output_duration": float(clip_info.get("duration_seconds") or 0.0),
                    "provider_output_green_ratio": provider_green_background.get("green_ratio"),
                    "provider_clip_ffprobe": raw_clip_info,
                    "stripped_clip_ffprobe": clip_info,
                    "provider_motion_analysis": chunk_motion,
                    "provider_status_history": provider_status_history,
                    "outputs_present": bool(raw_clip_info.get("has_video")),
                    "duration_ratio": duration_ratio,
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "provider_timeout": provider_timeout,
                    "chunk_retry": chunk_retry_used,
                }
            )
            logger.info(
                "Generation job %s: slide %s chunk %s avatar ready prediction_id=%s resolution=%s provider_audio_present=%s avatar_duration=%.2f outputs_present=%s provider_output_url_present=%s provider_output_has_motion=%s provider_output_has_green_background=%s duration_ratio=%.2f fallback_used=%s provider_timeout=%s chunk_retry=%s",
                job.id,
                slide_index,
                chunk_index,
                request_id,
                app_settings.AVATAR_LIPSYNC_RESOLUTION,
                provider_audio_present,
                float(clip_info.get("duration_seconds") or 0.0),
                bool(raw_clip_info.get("has_video")),
                bool(video_url),
                not bool(chunk_motion.get("almost_static")),
                provider_green_background.get("detected"),
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
        avatar_info = _probe_media_info(clip, ".mp4")
        duration = float(avatar_info.get("duration_seconds") or 0)
        fallback_used_any = any(chunk.get("fallback_used") for chunk in chunk_metadata)
        fallback_reason_any = next(
            (chunk.get("fallback_reason") for chunk in chunk_metadata if chunk.get("fallback_reason")),
            None,
        )
        final_avatar_output_storage_key = f"{context.output_prefix}/avatar/slide-{slide_index}.mp4"
        provider_lipsync_output_storage_key = None if fallback_used_any else final_avatar_output_storage_key
        avatar_metadata = {
            "provider": "wavespeed",
            "provider_name": "wavespeed",
            "mode": mode,
            "requested_avatar_generation_mode": app_settings.AVATAR_GENERATION_MODE,
            "effective_avatar_generation_mode": mode,
            "avatar_provider_name": effective_provider_name,
            "provider_request_type": chunk_metadata[-1].get("provider_request_type") if chunk_metadata else None,
            "provider_endpoint": chunk_metadata[-1].get("provider_endpoint") if chunk_metadata else None,
            "avatar_overlay_type": (
                "static_avatar_fallback"
                if fallback_used_any or mode == "static_avatar"
                else "provider_lipsync_video"
                if mode == "fast_lipsync"
                else "image_audio_infinitetalk_video"
                if mode in {"infinitetalk_image", "audio_lipsync", "image_audio_infinitetalk"}
                else "provider_lipsync_video"
            ),
            "source_avatar_image_url": avatar_source_metadata.get("download_url")
            or avatar_source_metadata.get("source_url")
            or context.avatar_source_url,
            "source_image_url": getattr(provider, "last_image_url", None)
            or avatar_source_metadata.get("download_url")
            or avatar_source_metadata.get("source_url")
            or context.avatar_source_url,
            "avatar_base_video_url": getattr(provider, "last_image_url", None)
            if mode == "fast_lipsync"
            else None,
            "avatar_base_video_asset_id": str(avatar_base_video_asset.id) if avatar_base_video_asset else None,
            "avatar_base_video_storage_key": avatar_base_video_asset.storage_key if avatar_base_video_asset else None,
            "avatar_base_video_provider": (avatar_base_video_metadata or {}).get("base_video_provider"),
            "avatar_base_video_fallback_used": bool((avatar_base_video_metadata or {}).get("fallback_used")),
            "avatar_base_video_source": (avatar_base_video_metadata or {}).get("avatar_base_video_source"),
            "avatar_base_video_is_real_motion": (avatar_base_video_metadata or {}).get("avatar_base_video_is_real_motion"),
            "avatar_base_video_metadata": avatar_base_video_metadata,
            "source_audio_url": audio_source_urls[0] if len(audio_source_urls) == 1 else None,
            "source_audio_urls": audio_source_urls,
            "wavespeed_request_id": request_ids[-1] if request_ids else None,
            "provider_prediction_id": request_ids[-1] if request_ids else None,
            "provider_name": "wavespeed",
            "wavespeed_request_ids": request_ids,
            "provider_status_history": getattr(provider, "last_status_history", []) or [],
            "provider_output_duration": duration,
            "provider_output_url_present": bool(video_url),
            "provider_output_url_host": urlparse(video_url).hostname if video_url else None,
            "request_text_length": len(dialogue),
            "request_duration": (
                audio_duration_seconds
                if mode in {"fast_lipsync", "infinitetalk_image", "audio_lipsync", "static_avatar"}
                else _talking_photo_duration_from_audio(audio_duration_seconds, dialogue)
            ),
            "selected_provider": effective_provider_name,
            "audio_duration_seconds": audio_duration_seconds,
            "chunk_count": len(chunk_specs),
            "chunks": chunk_metadata,
            "resolution": app_settings.AVATAR_LIPSYNC_RESOLUTION,
            "input_image_url_present": bool(avatar_source_bytes),
            "input_video_url_present": mode == "fast_lipsync",
            "input_audio_url_present": True,
            "provider_request_type": (
                "video_plus_audio"
                if mode == "fast_lipsync"
                else "image_plus_audio"
                if image_audio_mode
                else "image_plus_text"
                if mode in {"wavespeed_text", "ai_talking_photos"}
                else "static_fallback"
            ),
            "provider_audio_present": any(
                chunk.get("provider_audio_present") for chunk in chunk_metadata
            ),
            "provider_output_has_motion": any(
                chunk.get("provider_output_has_motion") for chunk in chunk_metadata
            ),
            "provider_output_has_green_background": any(
                chunk.get("provider_output_has_green_background") for chunk in chunk_metadata
            ),
            "provider_requires_chromakey": any(
                chunk.get("provider_output_has_green_background") for chunk in chunk_metadata
            ),
            "provider_motion_analysis": chunk_metadata[-1].get("provider_motion_analysis") if chunk_metadata else {},
            "fallback_used": fallback_used_any,
            "fallback_reason": fallback_reason_any,
            "green_screen_background": False,
            "provider_lipsync_output_present": not fallback_used_any,
            "provider_lipsync_output_duration": duration,
            "provider_lipsync_output_storage_key": provider_lipsync_output_storage_key,
            "provider_output_green_ratio": max(
                [float(chunk.get("provider_output_green_ratio") or 0.0) for chunk in chunk_metadata] or [0.0]
            ),
            "final_overlay_source": (
                "provider_lipsync_output" if not fallback_used_any else "static_avatar_fallback"
            ),
            "provider_output_url_present": bool(video_url),
            "provider_output_url_host": urlparse(video_url).hostname if video_url else None,
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
    if audio_duration_seconds > 0 and duration > 0:
        duration_tolerance = float(app_settings.MAX_AUDIO_CHUNK_DURATION_TOLERANCE_SECONDS)
        if duration < max(0.0, audio_duration_seconds - duration_tolerance):
            raise PipelineError(
                "avatar_generation_failed",
                (
                    f"Avatar overlay duration does not match slide audio duration. "
                    f"This would freeze the avatar (avatar={duration:.2f}s audio={audio_duration_seconds:.2f}s tolerance={duration_tolerance:.2f}s)."
                ),
                stage="generating_avatar",
                slide_index=slide_index,
                details={
                    "avatar_video_storage_key": final_avatar_output_storage_key,
                    "provider_lipsync_output_storage_key": provider_lipsync_output_storage_key,
                    "final_avatar_output_storage_key": final_avatar_output_storage_key,
                    "avatar_overlay_type": avatar_metadata.get("avatar_overlay_type"),
                    "fallback_used": fallback_used_any,
                    "fallback_reason": fallback_reason_any,
                    "avatar_duration": duration,
                    "audio_duration": audio_duration_seconds,
                    "duration_tolerance": duration_tolerance,
                    "avatar_base_video_source": (avatar_base_video_metadata or {}).get("avatar_base_video_source"),
                    "avatar_base_video_is_real_motion": (avatar_base_video_metadata or {}).get("avatar_base_video_is_real_motion"),
                },
            )
    if final_avatar_output_storage_key is None or duration <= 0:
        raise PipelineError(
            "avatar_generation_failed",
            f"Avatar output was not produced for slide {slide_index} chunk {last_chunk_index}.",
            stage="generating_avatar",
            slide_index=slide_index,
            chunk_index=last_chunk_index,
        )
    storage.upload_file(final_avatar_output_storage_key, clip, "video/mp4")
    asset = _create_asset(
        db,
        context=context,
        slide=slide,
        asset_type="generated_avatar_clip",
        storage_key=final_avatar_output_storage_key,
        filename=f"avatar-slide-{slide_index}.mp4",
        mime_type="video/mp4",
        size_bytes=len(clip),
        duration_seconds=duration,
        metadata_json={
            **avatar_metadata,
            "slide_position": slide_index,
            "generation_job_id": str(job.id),
            "provider": "wavespeed",
            "provider_name": "wavespeed",
            "provider_prediction_id": request_ids[-1] if request_ids else None,
            "model_used": (
                app_settings.AVATAR_LIPSYNC_MODEL_PATH
                if mode == "fast_lipsync"
                else app_settings.DEFAULT_LIPSYNC_MODEL
                if lipsync_provider == "wavespeed_infinitetalk"
                else "wavespeed-ai-talking-photos"
            ),
            "avatar_overlay_type": avatar_metadata.get("avatar_overlay_type"),
            "avatar_video_storage_key": final_avatar_output_storage_key,
            "provider_lipsync_output_storage_key": provider_lipsync_output_storage_key,
            "final_avatar_output_storage_key": final_avatar_output_storage_key,
            "avatar_video_duration": duration,
            "avatar_video_has_motion_checked": True,
            "avatar_video_has_motion": not motion.get("almost_static"),
            "ffprobe": avatar_info,
            "motion_analysis": motion,
            "avatar_base_video_asset_id": str(avatar_base_video_asset.id) if avatar_base_video_asset else None,
            "avatar_base_video_storage_key": avatar_base_video_asset.storage_key if avatar_base_video_asset else None,
            "avatar_base_video_fallback_used": bool(
                (avatar_base_video_metadata or {}).get("fallback_used")
            ),
            "avatar_base_video_source": (avatar_base_video_metadata or {}).get("avatar_base_video_source"),
            "avatar_base_video_is_real_motion": (avatar_base_video_metadata or {}).get("avatar_base_video_is_real_motion"),
            "avatar_base_video_metadata": avatar_base_video_metadata,
            "provider_lipsync_output_present": not fallback_used_any,
            "provider_lipsync_output_duration": duration,
            "final_overlay_source": "provider_lipsync_output" if not fallback_used_any else "static_avatar_fallback",
        },
    )
    _stage_progress(db, job, "generating_avatar", slide_index, total_slides, 25, 35, slide_index)
    return asset


def generate_wavespeed_slide_video_for_slide(
    db: Session,
    storage,
    job: GenerationJob,
    context: GenerationContext,
    slide: Slide,
    slide_index: int,
    total_slides: int,
    audio_asset: Asset | None,
) -> Asset:
    existing = _find_valid_asset(
        db,
        storage,
        context,
        slide,
        slide_index,
        "wavespeed_slide_video",
    )
    if existing:
        logger.info(
            "Generation job %s: reusing existing WaveSpeed slide video for slide %s",
            job.id,
            slide_index,
        )
        _stage_progress(db, job, "generating_avatar", slide_index, total_slides, 25, 60, slide_index)
        return existing

    if not (app_settings.WAVESPEED_API_KEY or "").strip():
        raise PipelineError(
            "avatar_generation_failed",
            "WAVESPEED_API_KEY is missing in worker environment",
            stage="generating_avatar",
            slide_index=slide_index,
        )
    if not context.avatar_source_url and not context.avatar_source_storage_key:
        raise PipelineError(
            "avatar_generation_failed",
            "Avatar image is required for WaveSpeed generation",
            stage="generating_avatar",
            slide_index=slide_index,
        )
    if audio_asset is None:
        raise PipelineError(
            "avatar_generation_failed",
            f"Slide audio is missing for slide_id={slide.id}",
            stage="generating_avatar",
            slide_index=slide_index,
        )

    logger.info(
        "Generation job %s: generating WaveSpeed InfiniteTalk Fast video for slide %s/%s",
        job.id,
        slide_index,
        total_slides,
    )
    _stage_progress(db, job, "generating_avatar", slide_index - 1, total_slides, 25, 60, slide_index)

    client = WaveSpeedOfficialClient(api_key=app_settings.WAVESPEED_API_KEY)
    audio_bytes = storage.download_bytes(audio_asset.storage_key)
    audio_info = _probe_media_info(audio_bytes, ".mp3")
    audio_duration = float(audio_info.get("duration_seconds") or 0.0)
    if audio_duration <= 0 or not audio_info.get("has_audio"):
        raise PipelineError(
            "avatar_generation_failed",
            f"Slide audio is missing or invalid for slide_id={slide.id}",
            stage="generating_avatar",
            slide_index=slide_index,
        )
    max_audio_seconds = float(app_settings.MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK)
    duration_tolerance = float(app_settings.MAX_AUDIO_CHUNK_DURATION_TOLERANCE_SECONDS)
    if audio_duration > max_audio_seconds + duration_tolerance:
        raise PipelineError(
            "avatar_generation_failed",
            (
                f"Slide audio is too long for WaveSpeed generation "
                f"(slide_id={slide.id}, duration={audio_duration:.2f}s, max={max_audio_seconds:.2f}s). "
                "Shorten the slide dialogue or increase MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK."
            ),
            stage="generating_avatar",
            slide_index=slide_index,
            details={
                "slide_id": str(slide.id),
                "audio_duration_seconds": audio_duration,
                "max_lipsync_audio_seconds_per_chunk": max_audio_seconds,
            },
        )

    avatar_source_bytes, avatar_source_metadata = _load_avatar_source_bytes(
        db=db,
        storage=storage,
        context=context,
    )
    if not avatar_source_bytes:
        raise PipelineError(
            "avatar_generation_failed",
            "Avatar image is required for WaveSpeed generation",
            stage="generating_avatar",
            slide_index=slide_index,
        )

    try:
        audio_url, audio_source = _prepare_wavespeed_input_url(
            storage=storage,
            storage_key=audio_asset.storage_key,
            media_bytes=audio_bytes,
            filename=f"slide-{slide_index}.mp3",
            content_type=audio_asset.mime_type or "audio/mpeg",
            label=f"slide {slide_index} audio",
            client=client,
        )
        avatar_image_url, avatar_source = _prepare_wavespeed_avatar_image_url(
            storage=storage,
            context=context,
            avatar_source_bytes=avatar_source_bytes,
            avatar_source_metadata=avatar_source_metadata,
            client=client,
        )
        logger.info(
            "Generation job %s: WaveSpeed slide %s input ready audio_url=%s audio_source=%s avatar_image_url=%s avatar_source=%s audio_duration=%.2f prompt_present=%s model=%s",
            job.id,
            slide_index,
            _safe_provider_url_for_log(audio_url),
            audio_source,
            _safe_provider_url_for_log(avatar_image_url),
            avatar_source,
            audio_duration,
            bool(SPANISH_LIPSYNC_PROMPT),
            INFINITETALK_FAST_MODEL,
        )
        video_url = client.run_infinitetalk_fast(
            image_url=avatar_image_url,
            audio_url=audio_url,
        )
        logger.info(
            "Generation job %s: WaveSpeed slide %s returned video_url=%s",
            job.id,
            slide_index,
            _safe_provider_url_for_log(video_url),
        )
        video_bytes = download_video(video_url)
    except WaveSpeedOfficialError as exc:
        raise PipelineError(
            "avatar_generation_failed",
            str(exc),
            stage="generating_avatar",
            slide_index=slide_index,
            details=exc.details,
        ) from exc

    video_info = _probe_media_info(video_bytes, ".mp4")
    video_duration = float(video_info.get("duration_seconds") or 0.0)
    if video_duration <= 0 or not video_info.get("has_video"):
        raise PipelineError(
            "avatar_generation_failed",
            f"WaveSpeed generated an invalid video for slide_id={slide.id}",
            stage="generating_avatar",
            slide_index=slide_index,
            details={"ffprobe": video_info},
        )
    key = f"{context.output_prefix}/wavespeed-slides/slide-{slide_index}.mp4"
    storage.upload_file(key, video_bytes, "video/mp4")
    asset = _create_asset(
        db,
        context=context,
        slide=slide,
        asset_type="wavespeed_slide_video",
        storage_key=key,
        filename=f"wavespeed-slide-{slide_index}.mp4",
        mime_type="video/mp4",
        size_bytes=len(video_bytes),
        duration_seconds=video_duration,
        metadata_json={
            "slide_position": slide_index,
            "generation_job_id": str(job.id),
            "provider": "wavespeed",
            "model_used": INFINITETALK_FAST_MODEL,
            "provider_request_type": "image_plus_audio",
            "prompt": SPANISH_LIPSYNC_PROMPT,
            "seed": -1,
            "audio_asset_id": str(audio_asset.id),
            "audio_storage_key": audio_asset.storage_key,
            "audio_duration_seconds": audio_duration,
            "audio_url": _safe_provider_url_for_log(audio_url),
            "audio_url_source": audio_source,
            "avatar_image_url": _safe_provider_url_for_log(avatar_image_url),
            "avatar_image_url_source": avatar_source,
            "video_url": _safe_provider_url_for_log(video_url),
            "ffprobe": video_info,
        },
    )
    logger.info(
        "Generation job %s: WaveSpeed slide %s video asset saved storage_key=%s duration=%.2f",
        job.id,
        slide_index,
        key,
        video_duration,
    )
    _stage_progress(db, job, "generating_avatar", slide_index, total_slides, 25, 60, slide_index)
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
    avatar_clip_meta = avatar_clip_asset.metadata_json if isinstance(avatar_clip_asset.metadata_json, dict) else {}
    avatar_generation_mode = (
        (avatar_clip_meta.get("mode") if isinstance(avatar_clip_meta, dict) else None)
        or app_settings.AVATAR_GENERATION_MODE
        or "fast_lipsync"
    ).strip().lower()
    avatar_overlay_type = _avatar_overlay_type(avatar_clip_meta, fallback_reason=None)
    fallback_reason_metadata = avatar_clip_meta.get("fallback_reason") if isinstance(avatar_clip_meta, dict) else None
    fallback_used_metadata = bool(avatar_clip_meta.get("fallback_used")) if isinstance(avatar_clip_meta, dict) else False
    provider_lipsync_output_present = bool(avatar_clip_meta.get("provider_lipsync_output_present")) if isinstance(avatar_clip_meta, dict) else False
    provider_lipsync_output_duration = avatar_clip_meta.get("provider_lipsync_output_duration") if isinstance(avatar_clip_meta, dict) else None
    final_overlay_source_metadata = avatar_clip_meta.get("final_overlay_source") if isinstance(avatar_clip_meta, dict) else None
    composition_fallback_reason = fallback_reason_metadata
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
        avatar_clip_motion = _analyze_video_motion(avatar_clip)
        if fallback_reason:
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
        avatar_overlay_type = _avatar_overlay_type(
            avatar_clip_meta,
            fallback_reason=fallback_reason or fallback_reason_metadata,
        )
        if (
            not provider_lipsync_output_present
            and not composition_fallback_reason
            and avatar_overlay_type in {"provider_lipsync_video", "image_audio_infinitetalk_video"}
            and bool(avatar_source_storage_key)
        ):
            provider_lipsync_output_present = True

        final_overlay_source = (
            final_overlay_source_metadata
            or ("provider_lipsync_output" if provider_lipsync_output_present and not composition_fallback_reason else "static_avatar_fallback")
        )
        composition_used_generated_avatar_clip = final_overlay_source == "provider_lipsync_output"
        audio_bytes = storage.download_bytes(audio_asset.storage_key)
        audio_info = _probe_media_info(audio_bytes, ".mp3")
        audio_duration = float(audio_info.get("duration_seconds") or 0.0)
        audio_storage_key = audio_asset.storage_key
        audio_asset_id = str(audio_asset.id)
        avatar_base_video_meta = avatar_clip_meta.get("avatar_base_video_metadata") if isinstance(avatar_clip_meta, dict) else None
        avatar_base_video_source = None
        avatar_base_video_is_real_motion = None
        if isinstance(avatar_base_video_meta, dict):
            avatar_base_video_source = avatar_base_video_meta.get("avatar_base_video_source")
            avatar_base_video_is_real_motion = avatar_base_video_meta.get("avatar_base_video_is_real_motion")
        green_background_info = _detect_green_background(avatar_clip)
        avatar_duration = float(avatar_clip_info.get("duration_seconds") or 0.0)
        if composition_used_generated_avatar_clip and audio_duration > 0 and avatar_duration > 0:
            duration_tolerance = float(app_settings.MAX_AUDIO_CHUNK_DURATION_TOLERANCE_SECONDS)
            if avatar_duration < max(0.0, audio_duration - duration_tolerance):
                raise PipelineError(
                    "slide_composition_failed",
                    "Avatar overlay duration does not match slide audio duration. This would freeze the avatar.",
                    stage="composing_slide",
                    slide_index=slide_index,
                    details={
                        "avatar_video_storage_key": avatar_source_storage_key,
                        "avatar_overlay_type": avatar_overlay_type,
                        "fallback_used": fallback_used_metadata,
                        "fallback_reason": composition_fallback_reason,
                        "avatar_base_video_source": avatar_base_video_source,
                        "avatar_base_video_is_real_motion": avatar_base_video_is_real_motion,
                        "avatar_duration": avatar_duration,
                        "audio_duration": audio_duration,
                        "duration_tolerance": duration_tolerance,
                        "final_overlay_source": final_overlay_source,
                        "provider_lipsync_output_present": provider_lipsync_output_present,
                        "provider_lipsync_output_duration": provider_lipsync_output_duration,
                    },
                )
        provider_requires_chromakey = bool(
            green_background_info.get("detected") is True
            and avatar_overlay_type in {"provider_lipsync_video", "image_audio_infinitetalk_video"}
            and final_overlay_source in {None, "", "provider_lipsync_output"}
        )
        explicit_chromakey_requested = bool(
            app_settings.ENABLE_AVATAR_CHROMAKEY
            and (
                green_background_info.get("detected") is True
                or avatar_clip_meta.get("green_screen_background") is True
                or avatar_clip_meta.get("provider_background") == "green"
            )
        )
        apply_avatar_chromakey = bool(explicit_chromakey_requested or provider_requires_chromakey)
        if provider_requires_chromakey and not app_settings.ENABLE_AVATAR_CHROMAKEY:
            logger.warning(
                "Generation job %s slide %s: provider output appears green-screened; applying provider-only chromakey cleanup without enabling global chromakey",
                job.id,
                slide_index,
            )
        elif not apply_avatar_chromakey and green_background_info.get("detected") is True:
            logger.warning(
                "Generation job %s slide %s: provider output has green background but no cleanup path is active",
                job.id,
                slide_index,
            )
        if apply_avatar_chromakey:
            logger.info(
                "Generation job %s: applying avatar chromakey for slide %s due to explicit provider/background metadata",
                job.id,
                slide_index,
            )
        elif app_settings.ENABLE_AVATAR_CHROMAKEY:
            logger.info(
                "Generation job %s: avatar chromakey enabled but no explicit green-screen metadata found for slide %s",
                job.id,
                slide_index,
            )
        logger.info(
            "Generation job %s: composing slide %s with slide preview source=%s audio=%s "
            "audio_duration=%s avatar_duration=%s avatar_overlay_type=%s avatar_video_storage_key=%s "
            "avatar_base_video_source=%s avatar_base_video_is_real_motion=%s fallback_used=%s fallback_reason=%s "
            "apply_avatar_chromakey=%s provider_requires_chromakey=%s allow_base_video_as_final_overlay=%s provider_lipsync_output_present=%s provider_lipsync_output_duration=%s final_overlay_source=%s green_ratio=%s",
            job.id,
            slide_index,
            slide_preview_source.get("storage_key"),
            audio_storage_key,
            audio_info.get("duration_seconds"),
            avatar_clip_info.get("duration_seconds"),
            avatar_overlay_type,
            avatar_source_storage_key,
            avatar_base_video_source,
            avatar_base_video_is_real_motion,
            composition_fallback_reason is not None,
            composition_fallback_reason,
            apply_avatar_chromakey,
            provider_requires_chromakey,
            app_settings.ALLOW_BASE_VIDEO_AS_FINAL_OVERLAY,
            provider_lipsync_output_present,
            provider_lipsync_output_duration,
            final_overlay_source,
            green_background_info.get("green_ratio"),
        )
        avatar_clip_motion = avatar_clip_meta.get("motion_analysis")
        if not isinstance(avatar_clip_motion, dict):
            avatar_clip_motion = {}
        fail_on_static = bool(app_settings.FAIL_ON_STATIC_AVATAR_FALLBACK)
        if avatar_generation_mode == "fast_lipsync":
            if final_overlay_source == "avatar_base_video" and app_settings.ALLOW_BASE_VIDEO_AS_FINAL_OVERLAY:
                logger.warning(
                    "Generation job %s slide %s: allowing avatar_base_video overlay in debug mode avatar_video_storage_key=%s",
                    job.id,
                    slide_index,
                    avatar_source_storage_key,
                )
            elif final_overlay_source != "provider_lipsync_output" or not provider_lipsync_output_present:
                logger.warning(
                    "Generation job %s slide %s: invalid fast_lipsync overlay source avatar_video_storage_key=%s avatar_overlay_type=%s fallback_used=%s fallback_reason=%s avatar_base_video_source=%s avatar_base_video_is_real_motion=%s avatar_duration=%s audio_duration=%s apply_avatar_chromakey=%s FAIL_ON_STATIC_AVATAR_FALLBACK=%s final_overlay_source=%s provider_lipsync_output_present=%s provider_lipsync_output_duration=%s",
                    job.id,
                    slide_index,
                    avatar_source_storage_key,
                    avatar_overlay_type,
                    fallback_used_metadata,
                    composition_fallback_reason,
                    avatar_base_video_source,
                    avatar_base_video_is_real_motion,
                    avatar_clip_info.get("duration_seconds"),
                    audio_info.get("duration_seconds"),
                    apply_avatar_chromakey,
                    fail_on_static,
                    final_overlay_source,
                    provider_lipsync_output_present,
                    provider_lipsync_output_duration,
                )
                if fallback_used_metadata or composition_fallback_reason or avatar_overlay_type in {"static_avatar_fallback", "ffmpeg_static_loop_fallback"}:
                    if fail_on_static and avatar_overlay_type in {"static_avatar_fallback", "ffmpeg_static_loop_fallback"}:
                        raise PipelineError(
                            "slide_composition_failed",
                            "Avatar animation was not generated; static fallback was used.",
                            stage="composing_slide",
                            slide_index=slide_index,
                            details={
                                "avatar_video_storage_key": avatar_source_storage_key,
                                "avatar_overlay_type": avatar_overlay_type,
                                "fallback_used": fallback_used_metadata,
                                "fallback_reason": composition_fallback_reason,
                                "avatar_base_video_source": avatar_base_video_source,
                                "avatar_base_video_is_real_motion": avatar_base_video_is_real_motion,
                                "provider_requires_chromakey": provider_requires_chromakey,
                            },
                        )
                    logger.warning(
                        "Generation job %s slide %s: fast_lipsync accepted explicit static fallback avatar_video_storage_key=%s avatar_overlay_type=%s fallback_used=%s fallback_reason=%s",
                        job.id,
                        slide_index,
                        avatar_source_storage_key,
                        avatar_overlay_type,
                        fallback_used_metadata,
                        composition_fallback_reason,
                    )
                else:
                    raise PipelineError(
                        "slide_composition_failed",
                        "Invalid overlay source: avatar_base_video was used instead of per-slide lip-sync output.",
                        stage="composing_slide",
                        slide_index=slide_index,
                        details={
                            "avatar_video_storage_key": avatar_source_storage_key,
                            "avatar_overlay_type": avatar_overlay_type,
                            "fallback_used": fallback_used_metadata,
                            "fallback_reason": composition_fallback_reason,
                            "avatar_base_video_source": avatar_base_video_source,
                            "avatar_base_video_is_real_motion": avatar_base_video_is_real_motion,
                            "avatar_duration": avatar_clip_info.get("duration_seconds"),
                            "audio_duration": audio_info.get("duration_seconds"),
                            "final_overlay_source": final_overlay_source,
                            "provider_lipsync_output_present": provider_lipsync_output_present,
                            "provider_lipsync_output_duration": provider_lipsync_output_duration,
                            "provider_requires_chromakey": provider_requires_chromakey,
                        },
                    )
            if composition_used_generated_avatar_clip and avatar_clip_motion.get("almost_static"):
                raise PipelineError(
                    "slide_composition_failed",
                    "Per-slide lip-sync output appears static; refusing to compose a frozen avatar.",
                    stage="composing_slide",
                    slide_index=slide_index,
                    details={
                        "avatar_video_storage_key": avatar_source_storage_key,
                        "avatar_overlay_type": avatar_overlay_type,
                        "fallback_used": fallback_used_metadata,
                        "fallback_reason": composition_fallback_reason,
                        "avatar_base_video_source": avatar_base_video_source,
                        "avatar_base_video_is_real_motion": avatar_base_video_is_real_motion,
                        "avatar_duration": avatar_clip_info.get("duration_seconds"),
                        "audio_duration": audio_info.get("duration_seconds"),
                        "final_overlay_source": final_overlay_source,
                        "provider_lipsync_output_present": provider_lipsync_output_present,
                        "provider_lipsync_output_duration": provider_lipsync_output_duration,
                        "provider_requires_chromakey": provider_requires_chromakey,
                        "avatar_motion_analysis": avatar_clip_motion,
                    },
                )
        if fallback_used_metadata or composition_fallback_reason:
            logger.warning(
                "Generation job %s slide %s: avatar fallback metadata detected avatar_video_storage_key=%s avatar_overlay_type=%s fallback_used=%s fallback_reason=%s avatar_base_video_source=%s avatar_base_video_is_real_motion=%s avatar_duration=%s audio_duration=%s apply_avatar_chromakey=%s provider_requires_chromakey=%s FAIL_ON_STATIC_AVATAR_FALLBACK=%s",
                job.id,
                slide_index,
                avatar_source_storage_key,
                avatar_overlay_type,
                fallback_used_metadata,
                composition_fallback_reason,
                avatar_base_video_source,
                avatar_base_video_is_real_motion,
                avatar_clip_info.get("duration_seconds"),
                audio_info.get("duration_seconds"),
                apply_avatar_chromakey,
                provider_requires_chromakey,
                fail_on_static,
            )
            if fail_on_static and avatar_overlay_type in {"static_avatar_fallback", "ffmpeg_static_loop_fallback"}:
                raise PipelineError(
                    "slide_composition_failed",
                    "Avatar animation was not generated; static fallback was used.",
                    stage="composing_slide",
                    slide_index=slide_index,
                    details={
                        "avatar_video_storage_key": avatar_source_storage_key,
                        "avatar_overlay_type": avatar_overlay_type,
                        "fallback_used": fallback_used_metadata,
                        "fallback_reason": composition_fallback_reason,
                        "avatar_base_video_source": avatar_base_video_source,
                        "avatar_base_video_is_real_motion": avatar_base_video_is_real_motion,
                        "provider_requires_chromakey": provider_requires_chromakey,
                    },
                )
        # Per-project subtitle configuration (enable toggle + styling). Defaults
        # preserve the previous always-on subtitle behavior.
        media = normalize_media_settings(
            context.settings.media_settings if context.settings is not None else None
        )
        subtitles_cfg = media["subtitles"]
        subtitle_text = (
            str(metadata.get("dialogue") or slide.notes or "").strip() or None
            if subtitles_cfg["enabled"]
            else None
        )
        subtitle_style = build_subtitle_force_style(subtitles_cfg) if subtitle_text else None

        segment = composer.compose_slide_video(
            slide_image_bytes=slide_image,
            avatar_clip_bytes=avatar_clip,
            audio_bytes=audio_bytes,
            duration_seconds=segment_duration_seconds,
            avatar_overlay=(
                # When avatar_visible is explicitly False, place overlay offscreen
                # so it does not appear in the composed video segment.
                {"x": 99999, "y": 99999, "width": 1, "height": 1}
                if metadata.get("avatar_visible") is False
                else _avatar_overlay_from_metadata(metadata, "1080p")
            ),
            resolution="1080p",
            audio_pad_seconds=float(app_settings.SLIDE_PAUSE_SECONDS),
            avatar_chromakey=apply_avatar_chromakey,
            chromakey_color=app_settings.AVATAR_CHROMAKEY_COLOR,
            chromakey_similarity=float(app_settings.AVATAR_CHROMAKEY_SIMILARITY),
            chromakey_blend=float(app_settings.AVATAR_CHROMAKEY_BLEND),
            subtitle_text=subtitle_text,
            subtitle_duration_seconds=audio_duration if audio_duration > 0 else None,
            subtitle_style=subtitle_style,
            # Match the editor preview: rounded/circular avatars and the
            # configured border color must survive into the exported video.
            avatar_border_radius_pct=(
                0.0
                if metadata.get("avatar_visible") is False
                else _slide_avatar_border_radius(metadata)
            ),
            avatar_border_color=(
                None
                if metadata.get("avatar_visible") is False
                else _slide_avatar_border_color(metadata)
            ),
            avatar_border_width_px=_slide_avatar_border_width_px(metadata, "1080p"),
        )
    except subprocess.CalledProcessError as exc:
        raise PipelineError(
            "slide_composition_failed",
            _ffmpeg_error_message(exc),
            stage="composing_slide",
            slide_index=slide_index,
        ) from exc
    segment_info = _probe_media_info(segment, ".mp4")
    segment_green_background = _detect_green_background(segment)
    if segment_green_background.get("detected") and not apply_avatar_chromakey:
        raise PipelineError(
            "slide_composition_failed",
            (
                "Final slide segment contains a green background after avatar overlay cleanup. "
                "Either switch to image_audio_infinitetalk or enable provider-only chromakey cleanup "
                "for the selected provider/model."
            ),
            stage="composing_slide",
            slide_index=slide_index,
            details={
                "avatar_video_storage_key": avatar_source_storage_key,
                "avatar_overlay_type": avatar_overlay_type,
                "avatar_provider_name": avatar_clip_meta.get("avatar_provider_name"),
                "provider_request_type": avatar_clip_meta.get("provider_request_type"),
                "fallback_used": fallback_used_metadata,
                "fallback_reason": composition_fallback_reason,
                "avatar_base_video_source": avatar_base_video_source,
                "avatar_base_video_is_real_motion": avatar_base_video_is_real_motion,
                "avatar_duration": avatar_clip_info.get("duration_seconds"),
                "audio_duration": audio_info.get("duration_seconds"),
                "final_overlay_source": final_overlay_source,
                "provider_lipsync_output_present": provider_lipsync_output_present,
                "provider_lipsync_output_duration": provider_lipsync_output_duration,
                "provider_output_has_green_background": green_background_info.get("detected"),
                "provider_output_green_ratio": green_background_info.get("green_ratio"),
                "segment_green_background": segment_green_background,
                "provider_requires_chromakey": provider_requires_chromakey,
            },
        )
    if segment_green_background.get("detected") and apply_avatar_chromakey:
        logger.warning(
            "Generation job %s slide %s: green background detected in final segment but chromakey cleanup is enabled; continuing.",
            job.id,
            slide_index,
        )
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
    final_overlay_source = final_overlay_source or (
        "provider_lipsync_output"
        if composition_used_generated_avatar_clip and not composition_fallback_reason
        else "static_avatar_fallback"
    )
    logger.info(
        "Generation job %s: slide %s final segment ready path=%s final_overlay_source=%s fallback_used=%s fallback_reason=%s",
        job.id,
        slide_index,
        key,
        final_overlay_source,
        composition_fallback_reason is not None,
        composition_fallback_reason,
    )
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
            "avatar_overlay_type": avatar_overlay_type,
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
            "avatar_clip_source": final_overlay_source,
            "avatar_video_storage_key": avatar_source_storage_key,
            "avatar_video_duration": avatar_clip_info.get("duration_seconds"),
            "avatar_video_has_motion_checked": bool(avatar_clip_motion),
            "provider_requires_chromakey": provider_requires_chromakey,
            "provider_output_has_green_background": green_background_info.get("detected"),
            "provider_output_green_ratio": green_background_info.get("green_ratio"),
            "green_background_cleanup_applied": apply_avatar_chromakey,
            "audio_asset_id": audio_asset_id,
            "audio_duration_seconds": audio_info.get("duration_seconds"),
            "audio_storage_key": audio_storage_key,
            "ffprobe": segment_info,
            "segment_motion_analysis": segment_motion,
            "segment_appears_static": segment_motion.get("almost_static"),
            "segment_green_background": segment_green_background,
            "composition_motion_warning": composition_motion_warning,
        },
    )
    _stage_progress(db, job, "composing_slide", slide_index, total_slides, 60, 25, slide_index)
    return asset


def _apply_background_music(
    db: Session,
    storage,
    composer: ComposerService,
    context: GenerationContext,
    final_video: bytes,
    job: GenerationJob,
) -> bytes:
    """Mix the project's background music under the final video, if configured.

    Returns the original video unchanged when no music is enabled/available, so
    the default generation path is unaffected.
    """
    settings_row = context.settings
    media = normalize_media_settings(
        settings_row.media_settings if settings_row is not None else None
    )
    music = media["background_music"]
    if not music.get("enabled") or not music.get("asset_id"):
        return final_video
    try:
        asset = db.get(Asset, uuid.UUID(str(music["asset_id"])))
    except (ValueError, TypeError):
        asset = None
    if asset is None or asset.project_id != context.project_id:
        logger.warning(
            "Generation job %s: background music asset missing; skipping music", job.id
        )
        return final_video
    try:
        music_bytes = storage.download_bytes(asset.storage_key)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Generation job %s: could not load background music: %s", job.id, exc)
        return final_video
    logger.info(
        "Generation job %s: mixing background music (loop=%s volume=%s fade_out=%s/%ss)",
        job.id,
        music["loop"],
        music["volume"],
        music["fade_out_enabled"],
        music["fade_out_seconds"],
    )
    return composer.mix_background_music(
        final_video,
        music_bytes,
        volume=float(music["volume"]),
        loop=bool(music["loop"]),
        fade_out_enabled=bool(music["fade_out_enabled"]),
        fade_out_seconds=float(music["fade_out_seconds"]),
    )


def compose_final_video(
    db: Session,
    storage,
    job: GenerationJob,
    context: GenerationContext,
    segment_assets: list[Asset],
) -> Asset:
    invalid_assets = [asset for asset in segment_assets if asset.asset_type != "slide_segment_video"]
    if invalid_assets:
        raise PipelineError(
            "final_composition_failed",
            "Final composition requires composed slide segments, not raw avatar clips.",
            stage="composing_video",
            details={
                "invalid_asset_ids": [str(asset.id) for asset in invalid_assets],
                "invalid_asset_types": [asset.asset_type for asset in invalid_assets],
                "generation_job_id": str(job.id),
            },
        )
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
        current_step="Concatenating final video",
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

    # Optional background music overlay. No-op when no music is configured, so
    # existing videos are byte-for-byte unchanged.
    final_video = _apply_background_music(db, storage, composer, context, final_video, job)
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
        "preparing_slide_previews": "Preparing slide previews",
        "generating_audio": "Generating slide audio",
        "preparing_avatar_base_video": "Preparing avatar base video",
        "generating_avatar": "Generating slide lip-sync",
        "composing_slide": "Composing slide segment",
        "concatenating_final_video": "Concatenating final video",
        "composing_video": "Concatenating final video",
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


def _avatar_image_audio_provider() -> str:
    return (
        app_settings.AVATAR_IMAGE_AUDIO_PROVIDER
        or app_settings.AVATAR_LIPSYNC_PROVIDER
        or "wavespeed_infinitetalk_fast"
    ).strip().lower()


def _avatar_image_audio_resolution() -> str:
    return (app_settings.AVATAR_IMAGE_AUDIO_RESOLUTION or app_settings.AVATAR_LIPSYNC_RESOLUTION or "480p").strip().lower()


def _allows_base_video_as_final_overlay() -> bool:
    return bool(app_settings.ALLOW_BASE_VIDEO_AS_FINAL_OVERLAY)


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
    base_video_provider = (app_settings.AVATAR_BASE_VIDEO_PROVIDER or "wavespeed_infinitetalk_fast").strip().lower()
    base_video_resolution = (app_settings.AVATAR_LIPSYNC_RESOLUTION or "480p").strip().lower()
    base_video_duration_seconds = max(5.0, float(app_settings.AVATAR_BASE_VIDEO_DURATION_SECONDS or 8))
    base_video_model = (app_settings.AVATAR_LIPSYNC_MODEL_PATH or "wavespeed-ai/sync-lipsync-3").strip()
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
    cached_real: tuple[Asset, bytes, dict] | None = None
    cached_fallback: tuple[Asset, bytes, dict] | None = None
    if existing and existing_metadata:
        matching_assets = [existing] + [
            asset
            for asset in (
                db.query(Asset)
                .filter(
                    Asset.project_id == context.project_id,
                    Asset.organization_id == context.organization_id,
                    Asset.asset_type == "avatar_base_video",
                )
                .order_by(Asset.created_at.desc())
                .all()
            )
            if asset.id != existing.id
        ]
        for asset in matching_assets:
            metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
            expected_storage_key = metadata.get("avatar_source_storage_key")
            expected_signature = metadata.get("avatar_source_signature")
            expected_provider = (metadata.get("base_video_provider") or "").strip().lower()
            expected_resolution = (metadata.get("resolution") or "").strip().lower()
            expected_duration = float(metadata.get("base_video_duration_seconds") or 0.0)
            expected_model = str(metadata.get("model_used") or metadata.get("base_video_model") or "").strip()
            if (
                expected_provider != base_video_provider
                or expected_resolution != base_video_resolution
                or expected_model != base_video_model
                or abs(expected_duration - base_video_duration_seconds) > 0.01
            ):
                continue
            if (
                (context.avatar_source_storage_key and expected_storage_key == context.avatar_source_storage_key)
                or (not context.avatar_source_storage_key and expected_signature == source_signature)
            ):
                if not storage_object_exists(storage, asset.storage_key):
                    continue
                try:
                    asset_bytes = storage.download_bytes(asset.storage_key)
                except Exception:
                    continue
                if metadata.get("generated_from") == "real_avatar_base_video" and not metadata.get("fallback_used"):
                    cached_real = (asset, asset_bytes, metadata)
                    break
                if cached_fallback is None:
                    cached_fallback = (asset, asset_bytes, metadata)

    if cached_real is not None:
        logger.info(
            "Generation job %s: reusing cached real avatar base video asset=%s",
            context.generation_job_id,
            cached_real[0].storage_key,
        )
        return cached_real

    logger.info(
        "Generation job %s: preparing avatar base video source_key=%s signature=%s provider=%s resolution=%s duration=%.2f",
        context.generation_job_id,
        context.avatar_source_storage_key,
        source_signature[:12],
        base_video_provider,
        base_video_resolution,
        base_video_duration_seconds,
    )
    if base_video_provider in {"wavespeed_infinitetalk_fast", "wavespeed_infinitetalk", "image_audio_infinitetalk"}:
        try:
            return _generate_real_avatar_base_video(
                db=db,
                storage=storage,
                context=context,
                avatar_source_bytes=avatar_source_bytes,
                avatar_source_metadata=avatar_source_metadata,
                source_signature=source_signature,
                duration_seconds=base_video_duration_seconds,
                resolution=base_video_resolution,
                model_path=base_video_model,
            )
        except (AvatarVideoProviderError, WavespeedClientError, TTSProviderError, PipelineError, subprocess.CalledProcessError) as exc:
            logger.warning(
                "Generation job %s: real avatar base video generation failed, falling back to static loop: %s",
                context.generation_job_id,
                exc,
            )
            if cached_fallback is not None:
                return cached_fallback

    if cached_fallback is not None:
        logger.info(
            "Generation job %s: reusing cached static avatar base video fallback asset=%s",
            context.generation_job_id,
            cached_fallback[0].storage_key,
        )
        return cached_fallback

    return _create_static_avatar_base_video(
        db=db,
        storage=storage,
        context=context,
        avatar_source_bytes=avatar_source_bytes,
        avatar_source_metadata=avatar_source_metadata,
        source_signature=source_signature,
        duration_seconds=base_video_duration_seconds,
        base_video_provider=base_video_provider,
        resolution=base_video_resolution,
        model_path=base_video_model,
    )


def prepare_avatar_base_video(
    db: Session,
    storage,
    job: GenerationJob,
    context: GenerationContext,
) -> tuple[Asset, bytes, dict]:
    logger.info(
        "Generation job %s: preparing avatar base video",
        job.id,
    )
    return _ensure_avatar_base_video_asset(db=db, storage=storage, context=context)


def _generate_real_avatar_base_video(
    *,
    db: Session,
    storage,
    context: GenerationContext,
    avatar_source_bytes: bytes,
    avatar_source_metadata: dict,
    source_signature: str,
    duration_seconds: float,
    resolution: str,
    model_path: str,
) -> tuple[Asset, bytes, dict]:
    tts_resolution = resolve_saved_tts_credentials(
        db,
        context.project_id,
        context.organization_id,
        app_settings,
    )
    tts_provider_name = tts_resolution.provider
    if tts_provider_name == "none" and not app_settings.ALLOW_DUMMY_TTS:
        raise PipelineError(
            "avatar_generation_failed",
            "A real TTS provider is required to build the avatar base video.",
            stage="preparing_avatar_base_video",
        )
    if tts_provider_name == "none":
        raise PipelineError(
            "avatar_generation_failed",
            "Dummy TTS cannot be used to build a real avatar base video.",
            stage="preparing_avatar_base_video",
        )

    calibration_text = _normalize_tts_text(
        "Hola, esta es una breve calibración de sincronización para el avatar."
    )
    tts_provider = get_tts_provider(tts_provider_name)
    calibration_audio_raw, _ = tts_provider.generate_audio(
        text=calibration_text,
        voice_id=tts_resolution.voice_id,
        language=app_settings.TTS_LANGUAGE,
        speed=app_settings.TTS_SPEED,
        api_key=tts_resolution.api_key,
    )
    composer = ComposerService()
    calibration_audio_mp3 = composer.normalize_audio_to_mp3(calibration_audio_raw)
    calibration_audio_info = _probe_media_info(calibration_audio_mp3, ".mp3")
    calibration_audio_duration = float(calibration_audio_info.get("duration_seconds") or 0.0)
    if calibration_audio_duration <= 0 or not calibration_audio_info.get("has_audio"):
        raise PipelineError(
            "avatar_generation_failed",
            "Calibration audio for avatar base video is invalid",
            stage="preparing_avatar_base_video",
        )
    provider = get_avatar_video_provider("wavespeed")
    provider_request_context = {
        "avatar_generation_mode": "fast_lipsync",
        "avatar_provider_name": "wavespeed",
        "provider_endpoint": f"{app_settings.WAVESPEED_BASE_URL.rstrip('/')}/wavespeed-ai/infinitetalk",
        "provider_request_type": "image_plus_audio",
        "input_image_url_present": True,
        "input_video_url_present": False,
        "input_audio_url_present": True,
        "input_video_duration": None,
        "input_audio_duration": calibration_audio_duration,
        "sync_mode": None,
        "resolution": resolution,
    }
    logger.info(
        "Generation job %s: avatar base video provider debug payload=%s",
        context.generation_job_id,
        provider_request_context,
    )
    provider_video_url = provider.generate_avatar_video_from_audio(
        image_url=avatar_source_metadata.get("download_url")
        or avatar_source_metadata.get("source_url")
        or context.avatar_source_url
        or "",
        audio_url="",
        duration=max(5, int(math.ceil(calibration_audio_duration))),
        seed=-1,
        prompt=None,
        resolution=resolution,
        api_key=context.wavespeed_api_key,
        audio_duration_seconds=calibration_audio_duration,
        image_bytes=avatar_source_bytes,
        audio_bytes=calibration_audio_mp3,
        image_filename=avatar_source_metadata.get("filename") or "avatar.png",
        image_content_type=avatar_source_metadata.get("mime_type") or "image/png",
        audio_filename="avatar-base-calibration.mp3",
        audio_content_type="audio/mpeg",
        retry_on_mismatch=True,
        minimum_duration_ratio=0.8,
    )
    clip_response = httpx.get(provider_video_url, timeout=app_settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
    if clip_response.status_code >= 400:
        raise PipelineError(
            "avatar_generation_failed",
            f"WaveSpeed avatar base video download returned HTTP {clip_response.status_code}",
            stage="preparing_avatar_base_video",
        )
    raw_clip = clip_response.content
    clip_info = _probe_media_info(raw_clip, ".mp4")
    if not clip_info.get("has_video") or float(clip_info.get("duration_seconds") or 0.0) <= 0:
        raise PipelineError(
            "avatar_generation_failed",
            "WaveSpeed avatar base video is invalid",
            stage="preparing_avatar_base_video",
        )
    clip = composer.strip_audio_from_video(raw_clip) if clip_info.get("has_audio") else raw_clip
    clip_info = _probe_media_info(clip, ".mp4")
    if clip_info.get("has_audio"):
        raise PipelineError(
            "avatar_generation_failed",
            "Avatar base video still contains audio after stripping",
            stage="preparing_avatar_base_video",
        )
    base_green_background = _detect_green_background(clip)
    base_request_context = getattr(provider, "last_request_context", {}) or {}
    base_key = f"{context.output_prefix}/avatar/base/avatar-base.mp4"
    storage.upload_file(base_key, clip, "video/mp4")
    avatar_info = {
        "provider": "wavespeed",
        "mode": "fast_lipsync",
        "selected_provider": "wavespeed_sync_lipsync_3",
        "provider_request_type": base_request_context.get("provider_request_type") or "image_plus_audio",
        "provider_endpoint": base_request_context.get("provider_endpoint"),
        "provider_status_history": getattr(provider, "last_status_history", []) or [],
        "source_image_url": provider.last_image_url if hasattr(provider, "last_image_url") else None,
        "source_audio_url": provider.last_audio_url if hasattr(provider, "last_audio_url") else None,
        "source_audio_urls": [provider.last_audio_url] if getattr(provider, "last_audio_url", None) else [],
        "wavespeed_request_id": getattr(provider, "last_request_id", None),
        "wavespeed_request_ids": [getattr(provider, "last_request_id", None)] if getattr(provider, "last_request_id", None) else [],
        "request_text_length": len(calibration_text),
        "request_duration": calibration_audio_duration,
        "audio_duration_seconds": calibration_audio_duration,
        "chunk_count": 1,
        "chunks": [
            {
                "index": 1,
                "text": calibration_text,
                "text_length": len(calibration_text),
                "word_count": _word_count(calibration_text),
                "estimated_duration": round(_estimate_spanish_duration(calibration_text), 2),
                "expected_duration_seconds": round(_estimate_spanish_duration(calibration_text), 2),
                "measured_tts_duration": calibration_audio_duration,
                "measured_duration_seconds": calibration_audio_duration,
                "audio_storage_key": None,
                "audio_url": provider.last_audio_url if hasattr(provider, "last_audio_url") else None,
                "audio_asset_id": None,
                "audio_probe": calibration_audio_info,
                "fallback_used": False,
                "fallback_reason": None,
                "provider_timeout": False,
                "chunk_retry": False,
            }
        ],
        "resolution": resolution,
        "provider_audio_present": bool(clip_info.get("has_audio")),
        "fallback_used": False,
        "fallback_reason": None,
        "avatar_base_video_source": "generated_real_avatar_base_video",
        "avatar_base_video_is_real_motion": True,
        "avatar_base_video_has_green_background": base_green_background.get("detected"),
        "base_video_provider": (app_settings.AVATAR_BASE_VIDEO_PROVIDER or "wavespeed_infinitetalk_fast").strip().lower(),
        "base_video_duration_seconds": duration_seconds,
        "base_video_model": model_path,
        "avatar_image_hash": source_signature,
    }
    asset = _create_asset(
        db,
        context=context,
        slide=None,
        asset_type="avatar_base_video",
        storage_key=base_key,
        filename="avatar-base.mp4",
        mime_type="video/mp4",
        size_bytes=len(clip),
        duration_seconds=float(clip_info.get("duration_seconds") or duration_seconds),
        metadata_json={
            **avatar_info,
            "avatar_source_storage_key": context.avatar_source_storage_key,
            "avatar_source_url": context.avatar_source_url,
            "avatar_source_signature": source_signature,
            "generated_from": "real_avatar_base_video",
            "fallback_used": False,
            "duration_seconds": float(clip_info.get("duration_seconds") or duration_seconds),
            "source_image": avatar_source_metadata,
            "ffprobe": clip_info,
            "green_background_analysis": base_green_background,
            "avatar_base_video_metadata": avatar_info,
        },
    )
    logger.info(
        "Generation job %s: prepared real avatar base video asset=%s duration=%.2f provider=%s",
        context.generation_job_id,
        base_key,
        float(clip_info.get("duration_seconds") or duration_seconds),
        avatar_info["base_video_provider"],
    )
    return asset, clip, asset.metadata_json or {}


def _create_static_avatar_base_video(
    *,
    db: Session,
    storage,
    context: GenerationContext,
    avatar_source_bytes: bytes,
    avatar_source_metadata: dict,
    source_signature: str,
    duration_seconds: float,
    base_video_provider: str,
    resolution: str,
    model_path: str,
) -> tuple[Asset, bytes, dict]:
    base_video = _image_to_video_clip(avatar_source_bytes, duration_seconds)
    base_video_info = _probe_media_info(base_video, ".mp4")
    if not base_video_info.get("has_video") or float(base_video_info.get("duration_seconds") or 0.0) <= 0:
        raise PipelineError(
            "avatar_generation_failed",
            "Could not create avatar base video",
            stage="preparing_avatar_base_video",
        )
    base_green_background = _detect_green_background(base_video)
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
        duration_seconds=float(base_video_info.get("duration_seconds") or duration_seconds),
        metadata_json={
            "avatar_source_storage_key": context.avatar_source_storage_key,
            "avatar_source_url": context.avatar_source_url,
            "avatar_source_signature": source_signature,
            "generated_from": "static_avatar",
            "fallback_used": True,
            "avatar_base_video_source": "ffmpeg_static_fallback",
            "avatar_base_video_is_real_motion": False,
            "avatar_base_video_has_green_background": base_green_background.get("detected"),
            "base_video_provider": base_video_provider,
            "base_video_duration_seconds": duration_seconds,
            "base_video_model": model_path,
            "resolution": resolution,
            "duration_seconds": float(base_video_info.get("duration_seconds") or duration_seconds),
            "source_image": avatar_source_metadata,
            "ffprobe": base_video_info,
            "green_background_analysis": base_green_background,
            "avatar_image_hash": source_signature,
        },
    )
    logger.info(
        "Generation job %s: created static avatar base video fallback asset=%s duration=%.2f",
        context.generation_job_id,
        base_key,
        float(base_video_info.get("duration_seconds") or duration_seconds),
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


def _prepare_wavespeed_avatar_image_url(
    *,
    storage,
    context: GenerationContext,
    avatar_source_bytes: bytes,
    avatar_source_metadata: dict,
    client: WaveSpeedOfficialClient,
) -> tuple[str, str]:
    if context.avatar_source_url and public_url_accessible(context.avatar_source_url):
        return context.avatar_source_url, "public_url"
    if context.avatar_source_storage_key:
        return _prepare_wavespeed_input_url(
            storage=storage,
            storage_key=context.avatar_source_storage_key,
            media_bytes=avatar_source_bytes,
            filename=avatar_source_metadata.get("filename") or "avatar.png",
            content_type=avatar_source_metadata.get("mime_type") or "image/png",
            label="avatar image",
            client=client,
        )
    return client.upload_bytes(
        avatar_source_bytes,
        filename=avatar_source_metadata.get("filename") or "avatar.png",
        content_type=avatar_source_metadata.get("mime_type") or "image/png",
    ), "wavespeed_upload"


def _prepare_wavespeed_input_url(
    *,
    storage,
    storage_key: str,
    media_bytes: bytes,
    filename: str,
    content_type: str,
    label: str,
    client: WaveSpeedOfficialClient,
) -> tuple[str, str]:
    try:
        public_url = _asset_public_url(storage, storage_key)
    except PipelineError as exc:
        logger.warning(
            "Could not create public URL for WaveSpeed %s; uploading media to WaveSpeed instead: %s",
            label,
            exc.message,
        )
    else:
        if public_url_accessible(public_url):
            return public_url, "public_url"
        logger.warning(
            "Public URL for WaveSpeed %s is not externally accessible; uploading media to WaveSpeed instead host=%s path=%s",
            label,
            urlparse(public_url).hostname,
            urlparse(public_url).path,
        )
    return client.upload_bytes(media_bytes, filename=filename, content_type=content_type), "wavespeed_upload"


def _safe_provider_url_for_log(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return parsed._replace(query="", fragment="").geturl()


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


def _effective_chunk_seconds_limit() -> float:
    lipsync_limit = float(app_settings.MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK or 0)
    avatar_limit = float(app_settings.MAX_AVATAR_AUDIO_SECONDS_PER_CHUNK or 0)
    positive_limits = [value for value in (lipsync_limit, avatar_limit) if value > 0]
    if not positive_limits:
        return 30.0
    return min(positive_limits)


def _build_narration_chunks(text: str) -> list[dict[str, object]]:
    normalized = _normalize_tts_text(text)
    if not normalized:
        return []

    max_chars = max(1, int(app_settings.MAX_TTS_CHARS_PER_CHUNK))
    max_seconds = max(1.0, _effective_chunk_seconds_limit())
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
        # Keep chunks with valid per-chunk limits; hard cap enforcement happens
        # later in the generation pipeline where we can decide retry/fallback.

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
        if not merged and max_chars >= 300:
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
    max_chunk_seconds = _effective_chunk_seconds_limit()
    estimated_duration = _estimate_spanish_duration(dialogue)
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    stored_chunks = metadata.get("chunks")
    if len(planned_chunks) <= 1:
        if (
            estimated_duration > max_chunk_seconds
            and not (isinstance(stored_chunks, list) and stored_chunks)
        ):
            logger.info(
                "Generation job audio reuse rejected for slide %s: long narration is missing chunk metadata (estimated_duration=%.2fs max_chunk_seconds=%.2fs)",
                slide_index,
                estimated_duration,
                max_chunk_seconds,
            )
            return False
        return True

    chunks = stored_chunks
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
    raw_text = str(
        chunk.get("text")
        or chunk.get("normalized_text")
        or chunk.get("content")
        or chunk.get("dialogue")
        or dialogue
        or ""
    )
    normalized_text = re.sub(r"\s+", " ", raw_text).strip()
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

    # Keep single long sentences intact here; retry splitting is handled once
    # we have measured audio duration and can split adaptively.
    return [chunk]


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
    estimated_duration = _estimate_spanish_duration(dialogue)
    max_chunk_seconds = _effective_chunk_seconds_limit()
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
    if len(planned_chunks) > 1 or estimated_duration > max_chunk_seconds:
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
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
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


def _detect_green_background(media_bytes: bytes) -> dict:
    info = {"detected": False, "green_ratio": 0.0, "sample_count": 0, "threshold": 0.45}
    if not media_bytes:
        return info
    with tempfile.TemporaryDirectory() as tmp:
        media_path = Path(tmp) / "avatar.mp4"
        media_path.write_bytes(media_bytes)
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-ss",
                    "0.2",
                    "-i",
                    str(media_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=64:64,format=rgb24",
                    "-f",
                    "rawvideo",
                    "-",
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return info
    frame = result.stdout or b""
    width = 64
    height = 64
    bytes_per_pixel = 3
    expected = width * height * bytes_per_pixel
    if len(frame) < expected:
        return info
    green_pixels = 0
    total_pixels = 0
    border_limit = 3
    for y in range(height):
        for x in range(width):
            if x > border_limit and x < width - border_limit - 1 and y > border_limit and y < height - border_limit - 1:
                continue
            index = (y * width + x) * bytes_per_pixel
            red = frame[index]
            green = frame[index + 1]
            blue = frame[index + 2]
            total_pixels += 1
            if green > 70 and green > red * 1.25 and green > blue * 1.25:
                green_pixels += 1
    if total_pixels <= 0:
        return info
    green_ratio = green_pixels / total_pixels
    info.update(
        {
            "detected": green_ratio >= info["threshold"],
            "green_ratio": round(green_ratio, 4),
            "sample_count": total_pixels,
            "method": "border_pixel_ratio",
        }
    )
    return info


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
    # Vertical fine-tune offset (canvas units, may be negative). Applied after
    # clamping so full-slide avatars can be shifted up/down to center the face;
    # FFmpeg overlay accepts off-frame coordinates.
    offset_y = 0
    try:
        offset_y = int(float(metadata.get("avatar_offset_y") or 0) * scale_y)
    except (TypeError, ValueError):
        offset_y = 0
    return {
        "x": max(0, min(x, output_width - width)),
        "y": max(0, min(y, output_height - height)) + offset_y,
        "width": min(width, output_width),
        "height": min(height, output_height),
    }


def _slide_avatar_border_radius(metadata: dict) -> float:
    """Border radius percentage (0–50) stored by the editor; 50 = circle."""
    try:
        value = float(metadata.get("avatar_border_radius") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(value, 50.0))


_HEX_COLOR_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")


def _slide_avatar_border_color(metadata: dict) -> str | None:
    """Validated avatar border color as #RRGGBB, or None when unset/invalid."""
    raw = metadata.get("avatar_border_color")
    if not isinstance(raw, str):
        return None
    match = _HEX_COLOR_RE.match(raw.strip())
    if not match:
        return None
    return f"#{match.group(1)}"


def _slide_avatar_border_width_px(metadata: dict, resolution: str) -> int:
    """Avatar border thickness in OUTPUT pixels.

    Stored as canvas-space units (`avatar_border_width`, default 6) and scaled to
    the output resolution the same way the overlay geometry is scaled.
    """
    try:
        width_canvas = float(metadata.get("avatar_border_width"))
    except (TypeError, ValueError):
        width_canvas = 6.0
    if width_canvas <= 0:
        width_canvas = 6.0
    canvas = metadata.get("canvas") if isinstance(metadata, dict) else None
    canvas_height = 540.0
    if isinstance(canvas, dict):
        canvas_height = float(canvas.get("height") or canvas_height)
    _, output_height = _resolution_size_for_generation(resolution)
    scale = output_height / max(canvas_height, 1.0)
    return max(2, round(width_canvas * scale))


def _avatar_overlay_type(metadata: dict | None, *, fallback_reason: str | None = None) -> str:
    if isinstance(metadata, dict):
        overlay_type = metadata.get("avatar_overlay_type")
        if isinstance(overlay_type, str) and overlay_type:
            return overlay_type
        if metadata.get("fallback_used"):
            return "static_avatar_fallback"
    if fallback_reason:
        return "static_avatar_fallback"
    return "provider_lipsync_video"


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
