import logging
import ipaddress
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.composer.service import ComposerService
from app.modules.generation.models import GenerationJob, VideoGenerationSettings
from app.modules.projects.models import Asset, ProjectGenerationConfig
from app.modules.projects.service import MOCK_ORG_ID
from app.modules.tts.adapters import TTSProviderError, get_tts_provider
from app.modules.video.adapters import AvatarVideoProviderError, get_avatar_video_provider
from app.providers.storage import get_storage
from app.services.wavespeed_client import WavespeedClient, WavespeedClientError
from app.utils.crypto import decrypt_secret

logger = logging.getLogger(__name__)

TEST_AUDIO_TEXT = "Hello, this is a test audio from Educa Chile."
TEST_AUDIO_PATH = Path("/tmp/educa_test_audio.mp3")
TEST_AVATAR_PATH = Path("/tmp/educa_test_avatar.mp4")
TEST_SLIDE_PATH = Path("/tmp/test_slide.png")
TEST_COMPOSED_PATH = Path("/tmp/educa_test_composed.mp4")
TEST_TALKING_PHOTO_TEXT = "Hello, this is a test talking photo from Educa Chile."


def run_elevenlabs_debug(project_id: uuid.UUID, db: Session) -> dict:
    _ensure_debug_enabled()
    settings_row = _video_settings(project_id, db)
    api_key = decrypt_secret(settings_row.elevenlabs_api_key_encrypted)
    voice_id = settings_row.elevenlabs_voice_id
    if not api_key or not voice_id:
        raise _debug_error("INVALID_ELEVENLABS_CREDENTIALS", "ElevenLabs key or voice ID missing")

    try:
        audio_bytes, _duration = get_tts_provider("elevenlabs").generate_audio(
            text=TEST_AUDIO_TEXT,
            voice_id=voice_id,
            language=settings.TTS_LANGUAGE,
            speed=settings.TTS_SPEED,
            api_key=api_key,
        )
    except TTSProviderError as exc:
        raise _debug_error(exc.code, str(exc)) from exc

    TEST_AUDIO_PATH.write_bytes(audio_bytes)
    media = _verify_media(TEST_AUDIO_PATH)
    if media["size_bytes"] <= 0 or media["duration_seconds"] <= 0:
        raise _debug_error("INVALID_AUDIO_ASSET", "ElevenLabs test audio is empty or invalid")

    storage_key, download_url = _upload_debug_file(
        project_id=project_id,
        path=TEST_AUDIO_PATH,
        content_type="audio/mpeg",
    )
    logger.info(
        "ElevenLabs debug test succeeded: voice_id=%s output=%s size=%s duration=%.2f",
        voice_id,
        TEST_AUDIO_PATH,
        media["size_bytes"],
        media["duration_seconds"],
    )
    return {
        "ok": True,
        "voice_id": voice_id,
        "output_path": str(TEST_AUDIO_PATH),
        "storage_key": storage_key,
        "download_url": download_url,
        **media,
    }


def run_wavespeed_debug(
    project_id: uuid.UUID,
    db: Session,
    avatar_source_url: str | None = None,
) -> dict:
    _ensure_debug_enabled()
    settings_row = _video_settings(project_id, db)
    wavespeed_key = decrypt_secret(settings_row.wavespeed_api_key_encrypted) or settings.WAVESPEED_API_KEY
    if not wavespeed_key:
        raise _debug_error("INVALID_WAVESPEED_CREDENTIALS", "WaveSpeed API key missing")

    source_bytes, source_meta = _configured_avatar_source_bytes(
        project_id,
        db,
        avatar_source_url=avatar_source_url,
    )
    if not source_bytes:
        raise _debug_error(
            "MISSING_AVATAR_SOURCE",
            "Set DEBUG_AVATAR_SOURCE_URL, pass avatar_source_url, "
            "or save an avatar image in the project avatar settings",
        )
    try:
        client = WavespeedClient(api_key=wavespeed_key)
        uploaded_image_url = client.upload_image(
            source_bytes,
            filename=source_meta.get("filename") or "avatar.png",
            content_type=source_meta.get("mime_type") or "image/png",
        )
        provider = get_avatar_video_provider("wavespeed")
        if settings.AVATAR_GENERATION_MODE.strip().lower() == "audio_lipsync" and (
            settings.AVATAR_LIPSYNC_PROVIDER or "wavespeed_infinitetalk"
        ).strip().lower() == "wavespeed_infinitetalk":
            audio_bytes: bytes | None = None
            audio_download_url: str | None = None
            audio_duration_seconds: float | None = None
            if settings_row.elevenlabs_api_key_encrypted and settings_row.elevenlabs_voice_id:
                audio_key = decrypt_secret(settings_row.elevenlabs_api_key_encrypted)
                if audio_key and settings_row.elevenlabs_voice_id:
                    try:
                        audio_bytes, audio_duration_seconds = get_tts_provider("elevenlabs").generate_audio(
                            text=TEST_TALKING_PHOTO_TEXT,
                            voice_id=settings_row.elevenlabs_voice_id,
                            language=settings.TTS_LANGUAGE,
                            speed=settings.TTS_SPEED,
                            api_key=audio_key,
                        )
                    except TTSProviderError:
                        audio_bytes = None
            if audio_bytes:
                TEST_AUDIO_PATH.write_bytes(audio_bytes)
                audio_storage_key, audio_download_url = _upload_debug_file(
                    project_id=project_id,
                    path=TEST_AUDIO_PATH,
                    content_type="audio/mpeg",
                )
                logger.info(
                    "WaveSpeed debug audio prepared: storage_key=%s",
                    audio_storage_key,
                )
            if audio_download_url:
                video_url = provider.generate_avatar_video_from_audio(
                    image_url=uploaded_image_url,
                    audio_url=audio_download_url,
                    image_bytes=source_bytes,
                    audio_bytes=audio_bytes,
                    image_filename=source_meta.get("filename") or "avatar.png",
                    image_content_type=source_meta.get("mime_type") or "image/png",
                    audio_filename=Path(audio_storage_key).name if audio_storage_key else "educa_test_audio.mp3",
                    audio_content_type="audio/mpeg",
                    prompt=None,
                    api_key=wavespeed_key,
                    audio_duration_seconds=audio_duration_seconds,
                )
            else:
                video_url = provider.generate_avatar_video(
                    image_url=uploaded_image_url,
                    text=TEST_TALKING_PHOTO_TEXT,
                    duration=5,
                    api_key=wavespeed_key,
                )
        else:
            video_url = provider.generate_avatar_video(
                image_url=uploaded_image_url,
                text=TEST_TALKING_PHOTO_TEXT,
                duration=5,
                api_key=wavespeed_key,
            )
        clip_response = httpx.get(video_url, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
        if clip_response.status_code >= 400:
            raise AvatarVideoProviderError(
                f"WaveSpeed output download returned HTTP {clip_response.status_code}",
                "WAVESPEED_AVATAR_FAILED",
            )
        clip_bytes = clip_response.content
    except AvatarVideoProviderError as exc:
        raise _debug_error(exc.code, str(exc)) from exc
    except WavespeedClientError as exc:
        raise _debug_error(exc.code, str(exc)) from exc

    TEST_AVATAR_PATH.write_bytes(clip_bytes)
    _verify_media(TEST_AVATAR_PATH)
    raw_streams = _probe_streams(TEST_AVATAR_PATH)
    provider_audio_present = raw_streams["audio"]
    if provider_audio_present:
        stripped_clip = ComposerService().strip_audio_from_video(clip_bytes)
        TEST_AVATAR_PATH.write_bytes(stripped_clip)
    media = _verify_media(TEST_AVATAR_PATH)
    streams = _probe_streams(TEST_AVATAR_PATH)
    if (
        media["size_bytes"] <= 0
        or media["duration_seconds"] <= 0
        or not streams["video"]
        or streams["audio"]
    ):
        raise _debug_error("INVALID_AVATAR_CLIP", "WaveSpeed avatar clip is empty or invalid")

    storage_key, download_url = _upload_debug_file(
        project_id=project_id,
        path=TEST_AVATAR_PATH,
        content_type="video/mp4",
    )
    logger.info(
        "WaveSpeed debug test succeeded: avatar_source_host=%s output=%s size=%s duration=%.2f",
        source_meta.get("host"),
        TEST_AVATAR_PATH,
        media["size_bytes"],
        media["duration_seconds"],
    )
    return {
        "ok": True,
        "avatar_source_host": source_meta.get("host"),
        "avatar_source_type": source_meta.get("source"),
        "talking_photo_text": TEST_TALKING_PHOTO_TEXT,
        "output_path": str(TEST_AVATAR_PATH),
        "storage_key": storage_key,
        "download_url": download_url,
        "provider_audio_present": provider_audio_present,
        "provider_streams": raw_streams,
        "streams": streams,
        **media,
    }


def run_ffmpeg_debug(project_id: uuid.UUID) -> dict:
    _ensure_debug_enabled()
    if not _ffmpeg_available():
        raise _debug_error(
            "FFMPEG_UNAVAILABLE",
            "FFmpeg is not available in this environment.",
        )
    _ensure_test_slide()
    if not TEST_AVATAR_PATH.exists() or TEST_AVATAR_PATH.stat().st_size <= 0:
        raise _debug_error("MISSING_AVATAR_CLIP", f"Missing test avatar at {TEST_AVATAR_PATH}")
    if not TEST_AUDIO_PATH.exists() or TEST_AUDIO_PATH.stat().st_size <= 0:
        raise _debug_error("MISSING_AUDIO_ASSET", f"Missing test audio at {TEST_AUDIO_PATH}")

    command = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(TEST_SLIDE_PATH),
        "-i",
        str(TEST_AVATAR_PATH),
        "-i",
        str(TEST_AUDIO_PATH),
        "-filter_complex",
        "[0:v]scale=1920:1080,setsar=1[bg];"
        "[1:v]scale=420:-1[av];"
        "[bg][av]overlay=W-w-80:H-h-60:shortest=0[outv]",
        "-map",
        "[outv]",
        "-map",
        "2:a:0",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-shortest",
        str(TEST_COMPOSED_PATH),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=settings.FFMPEG_TIMEOUT_SECONDS)
    except subprocess.CalledProcessError as exc:
        raise _debug_error(
            "VIDEO_COMPOSITION_FAILED",
            exc.stderr.decode("utf-8", errors="ignore")[-1000:],
        ) from exc

    media = _verify_media(TEST_COMPOSED_PATH)
    streams = _probe_streams(TEST_COMPOSED_PATH)
    if media["duration_seconds"] <= 0 or not streams["video"] or not streams["audio"]:
        raise _debug_error("VIDEO_COMPOSITION_FAILED", "Composed debug MP4 is invalid")

    storage_key, download_url = _upload_debug_file(
        project_id=project_id,
        path=TEST_COMPOSED_PATH,
        content_type="video/mp4",
    )
    logger.info(
        "FFmpeg debug composition succeeded: output=%s size=%s duration=%.2f",
        TEST_COMPOSED_PATH,
        media["size_bytes"],
        media["duration_seconds"],
    )
    return {
        "ok": True,
        "command": command,
        "output_path": str(TEST_COMPOSED_PATH),
        "storage_key": storage_key,
        "download_url": download_url,
        "streams": streams,
        **media,
    }


def list_generation_debug_assets(project_id: uuid.UUID, db: Session) -> dict:
    _ensure_debug_enabled()
    has_video_settings = (
        db.query(VideoGenerationSettings)
        .filter(VideoGenerationSettings.project_id == project_id)
        .first()
        is not None
    )
    latest_generation_job = (
        db.query(GenerationJob)
        .filter(
            GenerationJob.project_id == project_id,
            GenerationJob.organization_id == MOCK_ORG_ID,
        )
        .order_by(GenerationJob.created_at.desc())
        .first()
    )
    generation_id = str(latest_generation_job.id) if latest_generation_job else None
    assets_query = db.query(Asset).filter(
        Asset.project_id == project_id,
        Asset.organization_id == MOCK_ORG_ID,
    )
    if generation_id:
        assets_query = assets_query.filter(
            or_(
                Asset.storage_key.contains(generation_id),
                Asset.id == latest_generation_job.final_asset_id,
                Asset.asset_type == "avatar_source",
            )
        )
    assets = assets_query.order_by(Asset.created_at.asc()).all()
    storage = get_storage()
    asset_reads = [_debug_asset_read(asset, storage) for asset in assets]
    final_video_asset = next(
        (
            item
            for item in asset_reads
            if latest_generation_job
            and item["id"] == str(latest_generation_job.final_asset_id)
        ),
        next((item for item in asset_reads if item["asset_type"] == "final_video"), None),
    )
    slides = _debug_assets_by_slide(asset_reads)
    return {
        "ok": True,
        "has_video_settings": has_video_settings,
        "latest_generation_job_id": generation_id,
        "job": _debug_job_read(latest_generation_job),
        "final_video_asset": final_video_asset,
        "slides": slides,
        "diagnostics": _debug_generation_diagnostics(slides, final_video_asset),
        "assets": asset_reads,
    }


def _debug_job_read(job: GenerationJob | None) -> dict | None:
    if job is None:
        return None
    return {
        "job_id": str(job.id),
        "status": job.status,
        "progress_percentage": job.progress_percentage,
        "current_step": job.current_step,
        "current_slide": job.current_slide,
        "total_slides": job.total_slides,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


def _debug_asset_read(asset: Asset, storage) -> dict:
    return {
        "id": str(asset.id),
        "slide_id": str(asset.slide_id) if asset.slide_id else None,
        "asset_type": asset.asset_type,
        "storage_key": asset.storage_key,
        "filename": asset.filename,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "duration_seconds": asset.duration_seconds,
        "metadata_json": asset.metadata_json,
        "download_url": storage.generate_presigned_download_url(asset.storage_key).url,
    }


def _debug_assets_by_slide(asset_reads: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for asset in asset_reads:
        slide_id = asset.get("slide_id")
        metadata = asset.get("metadata_json") or {}
        slide_position = metadata.get("slide_position")
        if not slide_id:
            continue
        entry = grouped.setdefault(
            slide_id,
            {
                "slide_id": slide_id,
                "slide_position": slide_position,
                "audio_asset": None,
                "avatar_clip_asset": None,
                "segment_asset": None,
                "slide_preview_asset": None,
                "slide_preview_source": None,
                "selected_slide_preview_asset": None,
                "slide_preview_asset_type": None,
                "slide_preview_storage_key": None,
                "slide_preview_metadata": None,
                "composition_used_full_slide_preview": None,
                "composition_preview_warning": None,
                "composition_asset_used": None,
                "motion_analysis": None,
                "avatar_clip_duration_seconds": None,
                "avatar_clip_ffprobe": None,
                "model_used": None,
                "appears_static": None,
                "segment_motion_analysis": None,
                "segment_appears_static": None,
                "composition_motion_warning": None,
                "composition_used_generated_avatar_clip": None,
                "composition_fallback_reason": None,
            },
        )
        if slide_position is not None:
            entry["slide_position"] = slide_position
        if asset["asset_type"] in {"slide_audio", "tts_audio"}:
            entry["audio_asset"] = asset
        elif asset["asset_type"] in {"generated_avatar_clip", "avatar_clip"}:
            entry["avatar_clip_asset"] = asset
            entry["motion_analysis"] = metadata.get("motion_analysis")
            entry["avatar_clip_duration_seconds"] = asset.get("duration_seconds")
            entry["avatar_clip_ffprobe"] = metadata.get("ffprobe")
            entry["model_used"] = metadata.get("model_used") or metadata.get("model")
            motion = metadata.get("motion_analysis") or {}
            entry["appears_static"] = motion.get("almost_static")
        elif asset["asset_type"] in {"slide_segment_video", "slide_video"}:
            entry["segment_asset"] = asset
            entry["slide_preview_source"] = metadata.get("slide_preview_source")
            entry["selected_slide_preview_asset"] = metadata.get(
                "selected_slide_preview_asset"
            ) or metadata.get("slide_preview_source")
            entry["slide_preview_asset_type"] = metadata.get("slide_preview_asset_type")
            entry["slide_preview_storage_key"] = metadata.get("slide_preview_storage_key")
            entry["slide_preview_metadata"] = metadata.get("slide_preview_metadata")
            entry["composition_used_full_slide_preview"] = metadata.get(
                "composition_used_full_slide_preview"
            )
            entry["composition_preview_warning"] = metadata.get(
                "composition_preview_warning"
            )
            entry["composition_asset_used"] = metadata.get("composition_asset_used")
            entry["composition_used_generated_avatar_clip"] = metadata.get(
                "composition_used_generated_avatar_clip"
            )
            entry["composition_fallback_reason"] = metadata.get(
                "composition_fallback_reason"
            )
            entry["segment_motion_analysis"] = metadata.get("segment_motion_analysis")
            entry["segment_appears_static"] = metadata.get("segment_appears_static")
            entry["composition_motion_warning"] = metadata.get(
                "composition_motion_warning"
            )
    return sorted(
        grouped.values(),
        key=lambda item: item["slide_position"] if item["slide_position"] is not None else 999999,
    )


def _debug_generation_diagnostics(slides: list[dict], final_video_asset: dict | None) -> dict:
    total = len(slides)
    full_preview_count = sum(
        1 for slide in slides if slide.get("composition_used_full_slide_preview") is True
    )
    slides_missing_text_risk = [
        {
            "slide_id": slide.get("slide_id"),
            "slide_position": slide.get("slide_position"),
            "source": (slide.get("slide_preview_source") or {}).get("source"),
            "storage_key": slide.get("slide_preview_storage_key"),
            "warning": slide.get("composition_preview_warning"),
        }
        for slide in slides
        if slide.get("composition_used_full_slide_preview") is False
        or slide.get("composition_preview_warning")
    ]
    generated_overlay_count = sum(
        1 for slide in slides if slide.get("composition_used_generated_avatar_clip") is True
    )
    static_fallback_count = sum(
        1 for slide in slides if slide.get("composition_used_generated_avatar_clip") is False
    )
    low_motion_clip_count = sum(1 for slide in slides if slide.get("appears_static") is True)

    recommended_action = None
    if slides_missing_text_risk:
        recommended_action = (
            "Regenerate slide previews from the PPT render pipeline; composition is using a "
            "background-only image for at least one slide."
        )
    elif static_fallback_count:
        recommended_action = (
            "Inspect generated avatar clip assets; at least one slide used the static avatar fallback."
        )
    elif low_motion_clip_count:
        recommended_action = (
            "Inspect WaveSpeed intermediate clips; at least one generated clip appears almost static."
        )

    return {
        "total_slides": total,
        "all_slides_used_full_preview": total > 0 and full_preview_count == total,
        "total_slide_previews_found": full_preview_count,
        "total_audio_assets": sum(1 for slide in slides if slide.get("audio_asset")),
        "total_avatar_clips": sum(1 for slide in slides if slide.get("avatar_clip_asset")),
        "total_segment_assets": sum(1 for slide in slides if slide.get("segment_asset")),
        "full_slide_preview_count": full_preview_count,
        "slides_missing_text_risk": slides_missing_text_risk,
        "generated_avatar_video_overlay_count": generated_overlay_count,
        "static_avatar_fallback_count": static_fallback_count,
        "low_motion_clip_count": low_motion_clip_count,
        "final_video_storage_key": final_video_asset.get("storage_key") if final_video_asset else None,
        "recommended_action": recommended_action,
    }


def _ensure_debug_enabled() -> None:
    if settings.is_production:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _video_settings(project_id: uuid.UUID, db: Session) -> VideoGenerationSettings:
    settings_row = (
        db.query(VideoGenerationSettings)
        .filter(
            VideoGenerationSettings.project_id == project_id,
            VideoGenerationSettings.organization_id == MOCK_ORG_ID,
        )
        .first()
    )
    if settings_row is None:
        raise _debug_error("VIDEO_SETTINGS_NOT_CONFIGURED", "Video settings are missing")
    return settings_row


def _configured_avatar_source_bytes(
    project_id: uuid.UUID,
    db: Session,
    avatar_source_url: str | None = None,
) -> tuple[bytes | None, dict]:
    settings_row = (
        db.query(VideoGenerationSettings)
        .filter(
            VideoGenerationSettings.project_id == project_id,
            VideoGenerationSettings.organization_id == MOCK_ORG_ID,
        )
        .first()
    )
    if avatar_source_url:
        response = httpx.get(avatar_source_url, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
        if response.status_code < 400:
            return response.content, {
                "filename": Path(urlparse(avatar_source_url).path).name or "avatar.png",
                "mime_type": response.headers.get("content-type") or "image/png",
                "host": urlparse(avatar_source_url).hostname,
                "source": "override_url",
            }
        return None, {"host": urlparse(avatar_source_url).hostname, "source": "override_url"}
    if settings_row and settings_row.avatar_source_asset_id:
        asset = db.get(Asset, settings_row.avatar_source_asset_id)
        if asset:
            storage = get_storage()
            return storage.download_bytes(asset.storage_key), {
                "filename": asset.filename,
                "mime_type": asset.mime_type or "image/png",
                "host": None,
                "source": "project_avatar_asset",
            }
    if settings_row and settings_row.avatar_source_url:
        response = httpx.get(settings_row.avatar_source_url, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
        if response.status_code < 400:
            return response.content, {
                "filename": Path(urlparse(settings_row.avatar_source_url).path).name or "avatar.png",
                "mime_type": response.headers.get("content-type") or "image/png",
                "host": urlparse(settings_row.avatar_source_url).hostname,
                "source": "settings_url",
            }
    if settings.DEBUG_AVATAR_SOURCE_URL:
        response = httpx.get(settings.DEBUG_AVATAR_SOURCE_URL, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
        if response.status_code < 400:
            return response.content, {
                "filename": Path(urlparse(settings.DEBUG_AVATAR_SOURCE_URL).path).name or "avatar.png",
                "mime_type": response.headers.get("content-type") or "image/png",
                "host": urlparse(settings.DEBUG_AVATAR_SOURCE_URL).hostname,
                "source": "debug_url",
            }
    config = (
        db.query(ProjectGenerationConfig)
        .filter(
            ProjectGenerationConfig.project_id == project_id,
            ProjectGenerationConfig.organization_id == MOCK_ORG_ID,
        )
        .first()
    )
    avatar_id = config.avatar_id if config else None
    if avatar_id and urlparse(avatar_id).scheme in {"http", "https"}:
        response = httpx.get(avatar_id, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
        if response.status_code < 400:
            return response.content, {
                "filename": Path(urlparse(avatar_id).path).name or "avatar.png",
                "mime_type": response.headers.get("content-type") or "image/png",
                "host": urlparse(avatar_id).hostname,
                "source": "config_url",
            }
    return None, {"host": None, "source": None}


def _verify_media(path: Path) -> dict:
    if not path.exists():
        raise _debug_error("MISSING_MEDIA_FILE", f"Missing file: {path}")
    size_bytes = path.stat().st_size
    duration = _probe_duration(path)
    return {"size_bytes": size_bytes, "duration_seconds": duration}


def _probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 0.0


def _probe_streams(path: Path) -> dict[str, bool]:
    streams = {"video": False, "audio": False}
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return streams
    for line in result.stdout.splitlines():
        codec_type = line.strip()
        if codec_type in streams:
            streams[codec_type] = True
    return streams


def _ensure_test_slide() -> None:
    if TEST_SLIDE_PATH.exists() and TEST_SLIDE_PATH.stat().st_size > 0:
        return
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=1920x1080",
            "-frames:v",
            "1",
            str(TEST_SLIDE_PATH),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _upload_debug_file(project_id: uuid.UUID, path: Path, content_type: str) -> tuple[str, str]:
    key = f"orgs/{MOCK_ORG_ID}/projects/{project_id}/debug/{path.name}"
    storage = get_storage()
    storage.upload_file(key, path.read_bytes(), content_type)
    return key, storage.generate_presigned_download_url(key).url


def _external_debug_download_url(storage_key: str) -> str:
    try:
        url = get_storage().generate_external_download_url(storage_key).url
    except Exception as exc:
        raise _debug_error(
            "AVATAR_SIGNED_URL_FAILED",
            "Could not create a provider-accessible signed URL.",
        ) from exc
    _validate_external_provider_url(url)
    return url


def _validate_external_provider_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if parsed.scheme not in {"http", "https"} or not hostname:
        raise _debug_error(
            "EXTERNAL_ASSET_URL_NOT_PUBLIC",
            "WaveSpeed requires a public URL for audio/avatar assets. Configure a public tunnel or external storage.",
        )
    lowered_host = hostname.lower()
    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "minio"}
    is_blocked = lowered_host in blocked_hosts or lowered_host.endswith(".local")
    try:
        ip_address = ipaddress.ip_address(lowered_host)
        is_blocked = (
            is_blocked
            or ip_address.is_private
            or ip_address.is_loopback
            or ip_address.is_link_local
        )
    except ValueError:
        pass
    if is_blocked:
        raise _debug_error(
            "EXTERNAL_ASSET_URL_NOT_PUBLIC",
            "WaveSpeed requires a public URL for audio/avatar assets. Configure a public tunnel or external storage.",
        )


def _debug_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, "message": message},
    )
