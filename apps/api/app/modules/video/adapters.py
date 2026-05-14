from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.services.wavespeed_client import WavespeedClient, WavespeedClientError

logger = logging.getLogger(__name__)


class AvatarVideoProviderError(RuntimeError):
    code = "WAVESPEED_AVATAR_FAILED"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


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
        model_name: str | None = None,
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
        clip_response = httpx.get(video_url, timeout=180)
        if clip_response.status_code >= 400:
            raise AvatarVideoProviderError(
                f"WaveSpeed output download returned HTTP {clip_response.status_code}",
                "WAVESPEED_AVATAR_FAILED",
            )
        clip = clip_response.content
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
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            prediction = client.get_prediction_result(request_id)
            status_value = str(prediction.get("status") or "").lower()
            if status_value == "completed":
                outputs = prediction.get("outputs")
                if isinstance(outputs, list) and outputs and isinstance(outputs[0], str):
                    return outputs[0]
                raise AvatarVideoProviderError(
                    "WaveSpeed result has no output URL",
                    "WAVESPEED_AVATAR_FAILED",
                )
            if status_value == "failed":
                raise AvatarVideoProviderError(
                    str(prediction.get("error") or "WaveSpeed prediction failed"),
                    "WAVESPEED_AVATAR_FAILED",
                )
            if status_value not in {"created", "processing", "queued", "running", ""}:
                raise AvatarVideoProviderError(
                    f"Unexpected WaveSpeed prediction status: {status_value}",
                    "WAVESPEED_AVATAR_FAILED",
                )
            time.sleep(5)
        raise AvatarVideoProviderError("WaveSpeed prediction timed out", "WAVESPEED_AVATAR_FAILED")


class WavespeedAvatarVideoProvider(WavespeedTalkingPhotoProvider):
    pass


def get_avatar_video_provider(provider_name: str) -> AvatarVideoProvider:
    return WavespeedTalkingPhotoProvider()


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
