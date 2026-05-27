from __future__ import annotations

import logging
import math
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse
from typing import Callable

import httpx

from app.config import settings
from app.services.wavespeed_client import WavespeedClient, WavespeedClientError

logger = logging.getLogger(__name__)


def _url_host(url: str | None) -> str:
    if not url:
        return "unknown"
    try:
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


class AvatarVideoProviderError(RuntimeError):
    code = "WAVESPEED_AVATAR_FAILED"
    details: dict | None = None

    def __init__(self, message: str, code: str | None = None, details: dict | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code
        self.details = details


class AvatarVideoProvider:
    provider_name: str

    def generate_avatar_video(
        self,
        image_url: str,
        text: str,
        duration: int = 5,
        seed: int = -1,
        api_key: str | None = None,
    ) -> str:
        raise NotImplementedError

    def generate_avatar_clip(
        self,
        audio_bytes: bytes,
        duration_seconds: float,
        avatar_id: str | None,
        api_key: str | None = None,
        audio_url: str | None = None,
        avatar_source_url: str | None = None,
        avatar_base_video_url: str | None = None,
        avatar_base_video_bytes: bytes | None = None,
        avatar_base_video_filename: str | None = None,
        avatar_base_video_content_type: str | None = None,
        model_name: str | None = None,
        sync_mode: str | None = None,
        prompt: str | None = None,
        text: str | None = None,
    ) -> bytes:
        image_url = avatar_source_url or avatar_id
        narration = (text or prompt or "").strip()
        if not image_url:
            raise AvatarVideoProviderError(
                "Avatar source URL is missing",
                "MISSING_AVATAR_SOURCE",
            )
        mode = (settings.AVATAR_GENERATION_MODE or "fast_lipsync").strip().lower()
        image_audio_provider = (
            settings.AVATAR_IMAGE_AUDIO_PROVIDER
            or settings.AVATAR_LIPSYNC_PROVIDER
            or "wavespeed_infinitetalk_fast"
        ).strip().lower()
        raw_clip: bytes | bytearray | None = None
        if mode == "fast_lipsync":
            if not (avatar_base_video_url or avatar_base_video_bytes):
                raise AvatarVideoProviderError(
                    "Avatar base video URL is missing",
                    "MISSING_AVATAR_SOURCE",
                )
            if not audio_url:
                raise AvatarVideoProviderError(
                    "Audio URL is missing",
                    "MISSING_AUDIO_ASSET",
                )
            base_video_output = self.generate_avatar_video_from_base_video(
                base_video_url=avatar_base_video_url or image_url,
                audio_url=audio_url,
                duration=_validate_duration(duration_seconds),
                seed=-1,
                api_key=api_key,
                audio_duration_seconds=duration_seconds,
                base_video_bytes=avatar_base_video_bytes,
                base_video_filename=avatar_base_video_filename,
                base_video_content_type=avatar_base_video_content_type,
                sync_mode=sync_mode or settings.AVATAR_SYNC_MODE,
                model_name=model_name,
            )
            raw_clip = base_video_output if isinstance(base_video_output, (bytes, bytearray)) else None
            video_url = None if raw_clip is not None else str(base_video_output)
        elif mode in {"wavespeed_text", "ai_talking_photos"}:
            if not narration:
                raise AvatarVideoProviderError(
                    "Narration text is missing",
                    "MISSING_DIALOGUE",
                )
            video_url = self.generate_avatar_video(
                image_url=image_url,
                text=narration,
                duration=_validate_duration(duration_seconds),
                seed=-1,
                api_key=api_key,
            )
        elif mode in {"audio_lipsync", "image_audio_infinitetalk", "infinitetalk_image"}:
            if not audio_url:
                raise AvatarVideoProviderError(
                    "Audio URL is missing",
                    "MISSING_AUDIO_ASSET",
                )
            if image_audio_provider in {
                "wavespeed_infinitetalk",
                "wavespeed_infinitetalk_fast",
                "wavespeed-ai/infinitetalk",
                "wavespeed-ai/infinitetalk-fast",
            }:
                video_url = self.generate_avatar_video_from_audio(
                    image_url=image_url,
                    audio_url=audio_url,
                    duration=_validate_duration(duration_seconds),
                    seed=-1,
                    prompt=None,
                    resolution=settings.AVATAR_IMAGE_AUDIO_RESOLUTION,
                    api_key=api_key,
                )
            elif image_audio_provider in {"wavespeed_text", "ai_talking_photos"}:
                if not narration:
                    raise AvatarVideoProviderError(
                        "Narration text is missing",
                        "MISSING_DIALOGUE",
                    )
                video_url = self.generate_avatar_video(
                    image_url=image_url,
                    text=narration,
                    duration=_validate_duration(duration_seconds),
                    seed=-1,
                    api_key=api_key,
                )
            else:
                raise AvatarVideoProviderError(
                    f"Unsupported lip sync provider: {image_audio_provider}",
                    "WAVESPEED_AVATAR_FAILED",
                )
        else:
            raise AvatarVideoProviderError(
                f"Unsupported avatar generation mode: {mode}",
                "WAVESPEED_AVATAR_FAILED",
            )
        if raw_clip is None:
            clip_response = httpx.get(video_url, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
            if clip_response.status_code >= 400:
                raise AvatarVideoProviderError(
                    f"WaveSpeed output download returned HTTP {clip_response.status_code}",
                    "WAVESPEED_AVATAR_FAILED",
                )
            clip = clip_response.content
        else:
            clip = bytes(raw_clip)
        if not clip or _probe_duration(clip, ".mp4") <= 0 or not _has_video_stream(clip):
            raise AvatarVideoProviderError(
                "WaveSpeed returned an invalid talking photo clip",
                "WAVESPEED_AVATAR_FAILED",
            )
        if not _has_audio_stream(clip):
            raise AvatarVideoProviderError(
                "WaveSpeed talking photo clip is missing audio",
                "WAVESPEED_AVATAR_FAILED",
            )
        return clip


class WavespeedTalkingPhotoProvider(AvatarVideoProvider):
    provider_name = "wavespeed"
    last_request_id: str | None = None

    def generate_avatar_video(
        self,
        image_url: str,
        text: str,
        duration: int = 5,
        seed: int = -1,
        api_key: str | None = None,
    ) -> str:
        client = WavespeedClient(api_key=api_key)
        request_id = client.create_talking_photo(
            image_url=image_url,
            text=text,
            duration=duration,
            seed=seed,
        )
        self.last_request_id = request_id
        self.last_request_context = {
            "avatar_generation_mode": settings.AVATAR_GENERATION_MODE,
            "avatar_provider_name": self.provider_name,
            "provider_endpoint": f"{client.base_url}/wavespeed-ai/ai-talking-photos",
            "provider_request_type": "image_plus_text",
            "input_image_url_present": bool(image_url),
            "input_video_url_present": False,
            "input_audio_url_present": False,
            "input_video_duration": None,
            "input_audio_duration": None,
            "sync_mode": None,
            "resolution": None,
        }
        status_history: list[dict[str, object]] = []

        def _collect_heartbeat(payload: dict[str, object]) -> None:
            status_history.append(
                {
                    "status": payload.get("status"),
                    "elapsed_seconds": payload.get("elapsed_seconds"),
                    "outputs_present": payload.get("outputs_present"),
                }
            )

        self.last_status_history = status_history
        return _poll_wavespeed_prediction(
            client=client,
            request_id=request_id,
            timeout_seconds=settings.WAVESPEED_PREDICTION_TIMEOUT_SECONDS,
            poll_interval_seconds=settings.WAVESPEED_POLL_INTERVAL_SECONDS,
            audio_duration_seconds=None,
            debug_context=self.last_request_context,
            heartbeat_callback=_collect_heartbeat,
        )

    def generate_avatar_video_from_base_video(
        self,
        *,
        base_video_url: str,
        audio_url: str,
        duration: int = 5,
        seed: int = -1,
        api_key: str | None = None,
        audio_duration_seconds: float | None = None,
        base_video_bytes: bytes | None = None,
        base_video_filename: str | None = None,
        base_video_content_type: str | None = None,
        audio_bytes: bytes | None = None,
        audio_filename: str | None = None,
        audio_content_type: str | None = None,
        sync_mode: str | None = None,
        model_name: str | None = None,
        retry_on_mismatch: bool = True,
        minimum_duration_ratio: float = 0.8,
        heartbeat_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> bytes:
        client = WavespeedClient(api_key=api_key)
        effective_audio_duration = float(audio_duration_seconds or duration or 0.0)
        if effective_audio_duration <= 0 and base_video_bytes:
            effective_audio_duration = _probe_duration(base_video_bytes, ".mp4")
        timeout_seconds = max(
            int(settings.AVATAR_PROVIDER_SLIDE_TIMEOUT_SECONDS),
            int(math.ceil(max(effective_audio_duration or 0.0, 0.0) * 20)),
        )
        attempts = 2 if retry_on_mismatch else 1
        last_details: dict | None = None
        for attempt in range(1, attempts + 1):
            if not base_video_url and not base_video_bytes:
                raise AvatarVideoProviderError(
                    "Avatar base video URL is missing",
                    "MISSING_AVATAR_SOURCE",
                )
            if not audio_url and not audio_bytes:
                raise AvatarVideoProviderError(
                    "Audio URL is missing",
                    "MISSING_AUDIO_ASSET",
                )
            prepared_video_url, video_check = _resolve_external_media_url(
                client=client,
                media_url=base_video_url,
                media_bytes=base_video_bytes,
                filename=base_video_filename,
                content_type=base_video_content_type,
                label="avatar base video",
            )
            prepared_audio_url, audio_check = _resolve_external_media_url(
                client=client,
                media_url=audio_url,
                media_bytes=audio_bytes,
                filename=audio_filename,
                content_type=audio_content_type or "audio/mpeg",
                label="narration audio",
            )
            self.last_image_url = prepared_video_url
            self.last_audio_url = prepared_audio_url
            self.last_external_checks = {
                "image": video_check,
                "video": video_check,
                "audio": audio_check,
            }
            self.last_request_context = {
                "avatar_generation_mode": settings.AVATAR_GENERATION_MODE,
                "avatar_provider_name": self.provider_name,
                "provider_endpoint": f"{getattr(client, 'base_url', settings.WAVESPEED_BASE_URL)}/{(model_name or settings.AVATAR_LIPSYNC_MODEL_PATH).strip('/')}",
                "provider_request_type": "video_plus_audio",
                "input_image_url_present": False,
                "input_video_url_present": bool(prepared_video_url),
                "input_audio_url_present": bool(prepared_audio_url),
                "input_video_duration": _probe_duration(base_video_bytes, ".mp4") if base_video_bytes else None,
                "input_audio_duration": effective_audio_duration,
                "sync_mode": sync_mode or settings.AVATAR_SYNC_MODE,
                "resolution": settings.AVATAR_LIPSYNC_RESOLUTION,
            }
            status_history: list[dict[str, object]] = []

            def _collect_heartbeat(payload: dict[str, object]) -> None:
                status_history.append(
                    {
                        "status": payload.get("status"),
                        "elapsed_seconds": payload.get("elapsed_seconds"),
                        "outputs_present": payload.get("outputs_present"),
                    }
                )
                if heartbeat_callback is not None:
                    heartbeat_callback(payload)

            self.last_status_history = status_history
            logger.info(
                "WaveSpeed sync lipsync media prepared: attempt=%s video_host=%s audio_host=%s video_access=%s audio_access=%s sync_mode=%s model_name=%s",
                attempt,
                _url_host(prepared_video_url),
                _url_host(prepared_audio_url),
                video_check.get("status_code"),
                audio_check.get("status_code"),
                sync_mode or settings.AVATAR_SYNC_MODE,
                model_name or settings.AVATAR_LIPSYNC_MODEL_PATH,
            )
            request_id = client.create_sync_lipsync(
                video_url=prepared_video_url,
                audio_url=prepared_audio_url,
                sync_mode=sync_mode or settings.AVATAR_SYNC_MODE,
                seed=seed,
                resolution=settings.AVATAR_LIPSYNC_RESOLUTION,
                model_path=model_name or settings.AVATAR_LIPSYNC_MODEL_PATH,
            )
            self.last_request_id = request_id
            self.last_request_context["prediction_id"] = request_id
            poll_kwargs = {
                "client": client,
                "request_id": request_id,
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": settings.WAVESPEED_POLL_INTERVAL_SECONDS,
                "audio_duration_seconds": effective_audio_duration,
            }
            optional_poll_kwargs = {
                "heartbeat_callback": _collect_heartbeat,
                "debug_context": self.last_request_context,
            }
            try:
                video_url = _poll_wavespeed_prediction(**poll_kwargs, **optional_poll_kwargs)
            except TypeError:
                video_url = _poll_wavespeed_prediction(**poll_kwargs)
            clip_response = httpx.get(video_url, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
            if clip_response.status_code >= 400:
                raise AvatarVideoProviderError(
                    f"WaveSpeed output download returned HTTP {clip_response.status_code}",
                    "WAVESPEED_AVATAR_FAILED",
                )
            clip = clip_response.content
            clip_duration = _probe_duration(clip, ".mp4")
            if not clip or clip_duration <= 0 or not _has_video_stream(clip):
                raise AvatarVideoProviderError(
                    "WaveSpeed returned an invalid sync lipsync clip",
                    "WAVESPEED_AVATAR_FAILED",
                )
            if not _has_audio_stream(clip):
                raise AvatarVideoProviderError(
                    "WaveSpeed sync lipsync clip is missing audio",
                    "WAVESPEED_AVATAR_FAILED",
                )
            if effective_audio_duration > 0 and clip_duration < effective_audio_duration * max(0.8, float(minimum_duration_ratio)):
                last_details = {
                    "prediction_id": request_id,
                    "audio_duration_seconds": effective_audio_duration,
                    "avatar_duration_seconds": clip_duration,
                    "duration_ratio": clip_duration / effective_audio_duration if effective_audio_duration else None,
                    "video_access": video_check,
                    "audio_access": audio_check,
                    "attempt": attempt,
                }
                logger.warning(
                    "WaveSpeed sync lipsync output too short: prediction_id=%s attempt=%s audio_duration_seconds=%.2f avatar_duration_seconds=%.2f ratio=%.2f retry=%s",
                    request_id,
                    attempt,
                    effective_audio_duration,
                    clip_duration,
                    clip_duration / effective_audio_duration if effective_audio_duration else 0.0,
                    attempt < attempts,
                )
                if attempt < attempts:
                    continue
                raise AvatarVideoProviderError(
                    (
                        "WaveSpeed sync lipsync output duration is too short "
                        f"(avatar={clip_duration:.2f}s audio={effective_audio_duration:.2f}s ratio={clip_duration / effective_audio_duration:.2f})"
                    ),
                    "WAVESPEED_AVATAR_FAILED",
                    details=last_details,
                )
            self.last_duration_ratio = clip_duration / effective_audio_duration if effective_audio_duration else None
            self.last_avatar_duration_seconds = clip_duration
            self.last_audio_duration_seconds = effective_audio_duration
            self.last_generated_video_url = video_url
            return clip
        raise AvatarVideoProviderError(
            "WaveSpeed sync lipsync output is too short",
            "WAVESPEED_AVATAR_FAILED",
            details=last_details,
        )

    def generate_avatar_video_from_audio(
        self,
        image_url: str,
        audio_url: str,
        duration: int = 5,
        seed: int = -1,
        prompt: str | None = None,
        resolution: str | None = None,
        api_key: str | None = None,
        audio_duration_seconds: float | None = None,
        image_bytes: bytes | None = None,
        audio_bytes: bytes | None = None,
        image_filename: str | None = None,
        image_content_type: str | None = None,
        audio_filename: str | None = None,
        audio_content_type: str | None = None,
        retry_on_mismatch: bool = True,
        minimum_duration_ratio: float = 0.8,
        heartbeat_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> str:
        client = WavespeedClient(api_key=api_key)
        effective_resolution = resolution or settings.AVATAR_LIPSYNC_RESOLUTION
        effective_prompt = None
        measured_audio_duration = float(audio_duration_seconds or duration or 0.0)
        if measured_audio_duration <= 0 and audio_bytes:
            measured_audio_duration = _probe_duration(audio_bytes, ".mp3")
        timeout_seconds = max(
            int(settings.WAVESPEED_PREDICTION_TIMEOUT_SECONDS),
            int(math.ceil(max(measured_audio_duration or 0.0, 0.0) * 20)),
        )
        attempts = 2 if retry_on_mismatch else 1
        last_details: dict | None = None
        for attempt in range(1, attempts + 1):
            if not image_url and not image_bytes:
                raise AvatarVideoProviderError(
                    "Avatar image URL is missing",
                    "MISSING_AVATAR_SOURCE",
                )
            if not audio_url and not audio_bytes:
                raise AvatarVideoProviderError(
                    "Audio URL is missing",
                    "MISSING_AUDIO_ASSET",
                )
            prepared_image_url, image_check = _resolve_external_media_url(
                client=client,
                media_url=image_url,
                media_bytes=image_bytes,
                filename=image_filename,
                content_type=image_content_type,
                label="avatar image",
            )
            prepared_audio_url, audio_check = _resolve_external_media_url(
                client=client,
                media_url=audio_url,
                media_bytes=audio_bytes,
                filename=audio_filename,
                content_type=audio_content_type,
                label="narration audio",
            )
            self.last_image_url = prepared_image_url
            self.last_audio_url = prepared_audio_url
            self.last_external_checks = {
                "image": image_check,
                "audio": audio_check,
            }
            self.last_request_context = {
                "avatar_generation_mode": settings.AVATAR_GENERATION_MODE,
                "avatar_provider_name": self.provider_name,
                "provider_endpoint": f"{getattr(client, 'base_url', settings.WAVESPEED_BASE_URL)}/wavespeed-ai/infinitetalk",
                "provider_request_type": "image_plus_audio",
                "input_image_url_present": bool(prepared_image_url),
                "input_video_url_present": False,
                "input_audio_url_present": bool(prepared_audio_url),
                "input_video_duration": None,
                "input_audio_duration": measured_audio_duration,
                "sync_mode": None,
                "resolution": effective_resolution,
            }
            status_history: list[dict[str, object]] = []

            def _collect_heartbeat(payload: dict[str, object]) -> None:
                status_history.append(
                    {
                        "status": payload.get("status"),
                        "elapsed_seconds": payload.get("elapsed_seconds"),
                        "outputs_present": payload.get("outputs_present"),
                    }
                )
                if heartbeat_callback is not None:
                    heartbeat_callback(payload)

            self.last_status_history = status_history
            logger.info(
                "WaveSpeed InfiniteTalk media prepared: attempt=%s image_host=%s audio_host=%s image_access=%s audio_access=%s resolution=%s audio_duration_seconds=%s",
                attempt,
                _url_host(prepared_image_url),
                _url_host(prepared_audio_url),
                image_check.get("status_code"),
                audio_check.get("status_code"),
                effective_resolution,
                measured_audio_duration,
            )
            request_id = client.create_infinite_talk(
                image_url=prepared_image_url,
                audio_url=prepared_audio_url,
                prompt=effective_prompt,
                resolution=effective_resolution,
                seed=seed,
            )
            self.last_request_id = request_id
            self.last_request_context["prediction_id"] = request_id
            poll_kwargs = {
                "client": client,
                "request_id": request_id,
                "timeout_seconds": timeout_seconds,
                "poll_interval_seconds": settings.WAVESPEED_POLL_INTERVAL_SECONDS,
                "audio_duration_seconds": measured_audio_duration,
            }
            optional_poll_kwargs = {
                "heartbeat_callback": _collect_heartbeat,
                "debug_context": self.last_request_context,
            }
            try:
                video_url = _poll_wavespeed_prediction(**poll_kwargs, **optional_poll_kwargs)
            except TypeError:
                video_url = _poll_wavespeed_prediction(**poll_kwargs)
            if (
                image_check.get("reason") == "validation_error_assumed_public"
                or audio_check.get("reason") == "validation_error_assumed_public"
            ):
                self.last_duration_ratio = None
                self.last_avatar_duration_seconds = None
                self.last_audio_duration_seconds = measured_audio_duration
                return video_url
            clip_response = httpx.get(video_url, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS)
            if clip_response.status_code >= 400:
                raise AvatarVideoProviderError(
                    f"WaveSpeed output download returned HTTP {clip_response.status_code}",
                    "WAVESPEED_AVATAR_FAILED",
                )
            clip = clip_response.content
            clip_duration = _probe_duration(clip, ".mp4")
            self.last_avatar_duration_seconds = clip_duration
            self.last_audio_duration_seconds = measured_audio_duration
            if not clip or clip_duration <= 0 or not _has_video_stream(clip):
                raise AvatarVideoProviderError(
                    "WaveSpeed returned an invalid talking photo clip",
                    "WAVESPEED_AVATAR_FAILED",
                )
            if not _has_audio_stream(clip):
                raise AvatarVideoProviderError(
                    "WaveSpeed talking photo clip is missing audio",
                    "WAVESPEED_AVATAR_FAILED",
                )
            if (
                audio_duration_seconds is not None
                and measured_audio_duration > 0
                and clip_duration < measured_audio_duration * max(0.8, float(minimum_duration_ratio))
            ):
                last_details = {
                    "prediction_id": request_id,
                    "audio_duration_seconds": measured_audio_duration,
                    "avatar_duration_seconds": clip_duration,
                    "duration_ratio": clip_duration / measured_audio_duration if measured_audio_duration else None,
                    "image_access": image_check,
                    "audio_access": audio_check,
                    "attempt": attempt,
                }
                self.last_duration_ratio = last_details["duration_ratio"]
                logger.warning(
                    "WaveSpeed InfiniteTalk output too short: prediction_id=%s attempt=%s audio_duration_seconds=%.2f avatar_duration_seconds=%.2f ratio=%.2f retry=%s",
                    request_id,
                    attempt,
                    measured_audio_duration,
                    clip_duration,
                    clip_duration / measured_audio_duration if measured_audio_duration else 0.0,
                    attempt < attempts,
                )
                if attempt < attempts:
                    continue
                raise AvatarVideoProviderError(
                    (
                        "WaveSpeed InfiniteTalk output duration is too short "
                        f"(avatar={clip_duration:.2f}s audio={measured_audio_duration:.2f}s ratio={clip_duration / measured_audio_duration:.2f})"
                    ),
                    "WAVESPEED_AVATAR_FAILED",
                    details=last_details,
                )
            self.last_duration_ratio = clip_duration / measured_audio_duration if measured_audio_duration else None
            return video_url
        raise AvatarVideoProviderError(
            "WaveSpeed InfiniteTalk output duration is too short",
            "WAVESPEED_AVATAR_FAILED",
            details=last_details,
        )


class WavespeedAvatarVideoProvider(WavespeedTalkingPhotoProvider):
    pass


def get_avatar_video_provider(provider_name: str) -> AvatarVideoProvider:
    return WavespeedTalkingPhotoProvider()


def _poll_wavespeed_prediction(
    *,
    client: WavespeedClient,
    request_id: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
    audio_duration_seconds: float | None,
    heartbeat_callback: Callable[[dict[str, object]], None] | None = None,
    debug_context: dict[str, object] | None = None,
) -> str:
    start = time.monotonic()
    last_prediction: dict | None = None
    status_history: list[dict[str, object]] = []
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout_seconds:
            safe_last_prediction = _safe_prediction_snapshot(last_prediction)
            logger.error(
                "WaveSpeed prediction timed out: prediction_id=%s elapsed_seconds=%.2f timeout_seconds=%s audio_duration_seconds=%s last_status=%s last_response=%s debug_context=%s status_history=%s",
                request_id,
                elapsed,
                timeout_seconds,
                audio_duration_seconds,
                (last_prediction or {}).get("status"),
                safe_last_prediction,
                debug_context,
                status_history,
            )
            raise AvatarVideoProviderError(
                f"WaveSpeed prediction timed out after {elapsed:.2f}s (prediction_id={request_id}, last_status={(last_prediction or {}).get('status')})",
                "WAVESPEED_AVATAR_FAILED",
                details={"prediction_id": request_id, "last_prediction": safe_last_prediction},
            )
        prediction = client.get_prediction_result(request_id)
        last_prediction = prediction
        status_value = _prediction_status(prediction)
        outputs = _prediction_outputs(prediction)
        logger.info(
            "WaveSpeed poll: prediction_id=%s status=%s elapsed_seconds=%.2f timeout_seconds=%s audio_duration_seconds=%s outputs_present=%s debug_context=%s",
            request_id,
            status_value,
            elapsed,
            timeout_seconds,
            audio_duration_seconds,
            bool(outputs),
            debug_context,
        )
        status_history.append(
            {
                "status": status_value,
                "elapsed_seconds": elapsed,
                "outputs_present": bool(outputs),
            }
        )
        if status_value == "processing" and elapsed >= min(300, timeout_seconds / 2):
            logger.warning(
                "WaveSpeed prediction still processing: prediction_id=%s elapsed_seconds=%.2f timeout_seconds=%s audio_duration_seconds=%s debug_context=%s",
                request_id,
                elapsed,
                timeout_seconds,
                audio_duration_seconds,
                debug_context,
            )
        if heartbeat_callback is not None:
            try:
                heartbeat_callback(
                    {
                        "prediction_id": request_id,
                        "status": status_value,
                        "elapsed_seconds": elapsed,
                        "timeout_seconds": timeout_seconds,
                        "audio_duration_seconds": audio_duration_seconds,
                        "outputs_present": bool(outputs),
                        "last_prediction": _safe_prediction_snapshot(prediction),
                        "debug_context": debug_context,
                    }
                )
            except Exception:
                logger.exception(
                    "WaveSpeed heartbeat callback failed: prediction_id=%s",
                    request_id,
                )
        if status_value in {"completed", "succeeded", "success"}:
            if outputs:
                return outputs[0]
            raise AvatarVideoProviderError(
                "WaveSpeed result has no output URL",
                "WAVESPEED_AVATAR_FAILED",
                details={
                    "prediction_id": request_id,
                    "last_prediction": _safe_prediction_snapshot(prediction),
                    "debug_context": debug_context,
                    "status_history": status_history,
                },
            )
        if status_value in {"failed", "error", "canceled", "cancelled"}:
            raise AvatarVideoProviderError(
                str(prediction.get("error") or prediction.get("message") or "WaveSpeed prediction failed"),
                "WAVESPEED_AVATAR_FAILED",
                details={
                    "prediction_id": request_id,
                    "last_prediction": _safe_prediction_snapshot(prediction),
                    "debug_context": debug_context,
                    "status_history": status_history,
                },
            )
        if status_value not in {"created", "processing", "queued", "running", ""}:
            raise AvatarVideoProviderError(
                f"Unexpected WaveSpeed prediction status: {status_value}",
                "WAVESPEED_AVATAR_FAILED",
                details={
                    "prediction_id": request_id,
                    "last_prediction": _safe_prediction_snapshot(prediction),
                    "debug_context": debug_context,
                    "status_history": status_history,
                },
            )
        sleep_for = min(max(1, poll_interval_seconds), max(1, timeout_seconds - int(elapsed)))
        time.sleep(sleep_for)


def _prediction_status(prediction: dict) -> str:
    for key in ("status", "state", "phase"):
        value = prediction.get(key)
        if value:
            return str(value).strip().lower()
    return ""


def _prediction_outputs(prediction: dict) -> list[str]:
    for key in ("outputs", "output", "result"):
        value = prediction.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        if isinstance(value, str):
            return [value]
    data = prediction.get("data")
    if isinstance(data, dict):
        for key in ("outputs", "output", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, str)]
            if isinstance(value, str):
                return [value]
    return []


def _safe_prediction_snapshot(prediction: dict | None) -> dict | None:
    if not isinstance(prediction, dict):
        return prediction
    snapshot: dict[str, object] = {}
    for key in ("id", "request_id", "status", "state", "phase", "error", "message"):
        if key in prediction and prediction[key] is not None:
            snapshot[key] = prediction[key]
    outputs = _prediction_outputs(prediction)
    if outputs:
        snapshot["outputs"] = [_redact_url(url) for url in outputs]
    return snapshot


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return parsed._replace(query="", fragment="").geturl()


def _validate_duration(duration_seconds: float) -> int:
    duration = max(5, min(15, int(round(duration_seconds or 0)) or 5))
    return duration


def _probe_duration(media_bytes: bytes, suffix: str) -> float:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"avatar{suffix}"
        path.write_bytes(media_bytes)
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
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return 0.0
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _has_video_stream(media_bytes: bytes) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "avatar.mp4"
        path.write_bytes(media_bytes)
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
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
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
    return "video" in result.stdout.splitlines()


def _has_audio_stream(media_bytes: bytes) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "avatar.mp4"
        path.write_bytes(media_bytes)
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
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
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return False
    return "audio" in result.stdout.splitlines()


def _resolve_external_media_url(
    *,
    client: WavespeedClient,
    media_url: str | None,
    media_bytes: bytes | None,
    filename: str | None,
    content_type: str | None,
    label: str,
) -> tuple[str, dict[str, object]]:
    if media_bytes is not None:
        upload_url = client.upload_binary(
            media_bytes,
            filename=filename or f"{label.replace(' ', '-')}.bin",
            content_type=content_type or "application/octet-stream",
        )
        access_check = _validate_external_provider_url_access(
            upload_url,
            label=label,
        )
        return upload_url, access_check

    if not media_url:
        raise AvatarVideoProviderError(
            f"{label.title()} URL is missing",
            "MISSING_AVATAR_SOURCE" if "image" in label else "MISSING_AUDIO_ASSET",
        )

    access_check = _validate_external_provider_url_access(media_url, label=label)
    return media_url, access_check


def _validate_external_provider_url_access(url: str, *, label: str) -> dict[str, object]:
    if not settings.REQUIRE_EXTERNAL_PROVIDER_URL_VALIDATION:
        parsed = urlparse(url)
        return {
            "validated": False,
            "status_code": None,
            "method": None,
            "host": parsed.hostname,
            "path": parsed.path,
            "reason": "validation_disabled",
        }

    parsed = urlparse(url)
    safe_url = parsed._replace(query="", fragment="").geturl()
    headers = {"Range": "bytes=0-0"}
    try:
        head_response = httpx.head(safe_url, timeout=10, follow_redirects=True)
        if 200 <= head_response.status_code < 400:
            return {
                "validated": True,
                "method": "HEAD",
                "status_code": head_response.status_code,
                "host": parsed.hostname,
                "path": parsed.path,
                "reason": None,
            }
        get_response = httpx.get(safe_url, headers=headers, timeout=10, follow_redirects=True)
        if 200 <= get_response.status_code < 400:
            return {
                "validated": True,
                "method": "GET",
                "status_code": get_response.status_code,
                "host": parsed.hostname,
                "path": parsed.path,
                "reason": None,
            }
        logger.warning(
            "WaveSpeed external URL validation failed: label=%s host=%s path=%s head_status=%s get_status=%s",
            label,
            parsed.hostname,
            parsed.path,
            head_response.status_code,
            get_response.status_code,
        )
    except Exception as exc:  # pragma: no cover - network failures are environment specific
        logger.warning(
            "WaveSpeed external URL validation error: label=%s host=%s path=%s error=%s",
            label,
            parsed.hostname,
            parsed.path,
            exc,
        )
        hostname = (parsed.hostname or "").lower()
        blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "minio"}
        if hostname and hostname not in blocked_hosts and not hostname.endswith(".local") and not hostname.startswith("internal."):
            return {
                "validated": False,
                "method": "ERROR",
                "status_code": None,
                "host": parsed.hostname,
                "path": parsed.path,
                "reason": "validation_error_assumed_public",
            }
    raise AvatarVideoProviderError(
        f"WaveSpeed requires a public URL for {label}. Configure a public tunnel or external storage.",
        "EXTERNAL_ASSET_URL_NOT_PUBLIC",
        details={
            "label": label,
            "host": parsed.hostname,
            "path": parsed.path,
        },
    )
