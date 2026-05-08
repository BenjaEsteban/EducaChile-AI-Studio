import subprocess
import tempfile
import time
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class AvatarVideoProviderError(RuntimeError):
    code = "WAVESPEED_AVATAR_FAILED"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class AvatarVideoProvider:
    provider_name: str

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
    ) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "avatar.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=0x1d4ed8:s=512x512:r=25",
                    "-t",
                    str(duration_seconds),
                    "-pix_fmt",
                    "yuv420p",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            return output.read_bytes()


class WavespeedAvatarVideoProvider(AvatarVideoProvider):
    provider_name = "wavespeed"

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
    ) -> bytes:
        if not api_key:
            raise AvatarVideoProviderError(
                "WaveSpeed API key is missing",
                "INVALID_WAVESPEED_CREDENTIALS",
            )
        if not audio_url:
            raise AvatarVideoProviderError(
                "WaveSpeed requires a signed audio URL",
                "WAVESPEED_AVATAR_FAILED",
            )
        source_url = avatar_source_url or avatar_id
        if not source_url:
            raise AvatarVideoProviderError(
                "Avatar source URL is missing",
                "MISSING_AVATAR_SOURCE",
            )

        model = model_name or settings.DEFAULT_LIPSYNC_MODEL
        request_prompt = prompt or settings.LIPSYNC_PROMPT
        request_payload = {
            "audio": audio_url,
            "image": source_url,
            "resolution": "720p",
            "seed": -1,
            "prompt": request_prompt,
        }
        logger.info(
            "Submitting WaveSpeed lipsync request: model=%s audio_host=%s image_host=%s "
            "resolution=%s audio_present=%s image_present=%s prompt_present=%s",
            model,
            _url_host(audio_url),
            _url_host(source_url),
            request_payload["resolution"],
            bool(audio_url),
            bool(source_url),
            bool(request_prompt),
        )
        submit_response = httpx.post(
            f"https://api.wavespeed.ai/api/v3/{model}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=request_payload,
            timeout=90,
        )
        if submit_response.status_code in {401, 403}:
            raise AvatarVideoProviderError(
                "WaveSpeed credentials were rejected",
                "INVALID_WAVESPEED_CREDENTIALS",
            )
        if submit_response.status_code >= 400:
            raise AvatarVideoProviderError(
                f"WaveSpeed submit returned HTTP {submit_response.status_code}",
                "WAVESPEED_AVATAR_FAILED",
            )
        payload = submit_response.json()
        prediction = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(prediction, dict):
            raise AvatarVideoProviderError(
                "WaveSpeed response missing data object",
                "WAVESPEED_AVATAR_FAILED",
            )
        result_url = _extract_result_url(prediction)
        if not result_url:
            raise AvatarVideoProviderError(
                "WaveSpeed response missing result URL",
                "WAVESPEED_AVATAR_FAILED",
            )

        output_url = _poll_wavespeed_result(result_url, api_key)
        clip_response = httpx.get(output_url, timeout=180)
        if clip_response.status_code >= 400:
            raise AvatarVideoProviderError(
                f"WaveSpeed output download returned HTTP {clip_response.status_code}",
                "WAVESPEED_AVATAR_FAILED",
            )
        clip = clip_response.content
        if not clip or _probe_duration(clip, ".mp4") <= 0 or not _has_video_stream(clip):
            raise AvatarVideoProviderError(
                "WaveSpeed returned an invalid avatar clip",
                "WAVESPEED_AVATAR_FAILED",
            )
        return clip


def get_avatar_video_provider(provider_name: str) -> AvatarVideoProvider:
    return WavespeedAvatarVideoProvider()


def _poll_wavespeed_result(result_url: str, api_key: str) -> str:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result_response = httpx.get(
            result_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        if result_response.status_code in {401, 403}:
            raise AvatarVideoProviderError(
                "WaveSpeed credentials were rejected",
                "INVALID_WAVESPEED_CREDENTIALS",
            )
        if result_response.status_code >= 400:
            raise AvatarVideoProviderError(
                f"WaveSpeed result returned HTTP {result_response.status_code}",
                "WAVESPEED_AVATAR_FAILED",
            )
        payload = result_response.json()
        prediction = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(prediction, dict):
            raise AvatarVideoProviderError(
                "WaveSpeed result missing data object",
                "WAVESPEED_AVATAR_FAILED",
            )
        status_value = prediction.get("status")
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
        time.sleep(5)
    raise AvatarVideoProviderError("WaveSpeed prediction timed out", "WAVESPEED_AVATAR_FAILED")


def _extract_result_url(prediction: dict) -> str | None:
    urls = prediction.get("urls")
    if isinstance(urls, dict) and isinstance(urls.get("get"), str):
        return urls["get"]
    prediction_id = prediction.get("id")
    if isinstance(prediction_id, str):
        return f"https://api.wavespeed.ai/api/v3/predictions/{prediction_id}/result"
    return None


def _url_host(url: str | None) -> str | None:
    if not url:
        return None
    return urlparse(url).hostname


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
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
    return "video" in result.stdout.splitlines()
