from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class WavespeedClientError(RuntimeError):
    code = "WAVESPEED_TALKING_PHOTO_FAILED"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


def _request_timeout_seconds() -> int:
    return max(30, int(settings.WAVESPEED_HTTP_TIMEOUT_SECONDS))


@dataclass(slots=True)
class WavespeedClient:
    api_key: str | None = None
    base_url: str | None = None

    def __post_init__(self) -> None:
        self.api_key = (self.api_key or settings.WAVESPEED_API_KEY or "").strip() or None
        self.base_url = (self.base_url or settings.WAVESPEED_BASE_URL).rstrip("/")
        if not self.api_key:
            raise WavespeedClientError(
                "WaveSpeed API key is missing",
                "MISSING_WAVESPEED_API_KEY",
            )
        logger.info(
            "WaveSpeed client configured: api_key_present=%s base_url=%s",
            True,
            self.base_url,
        )

    def upload_binary(self, file_bytes: bytes, filename: str, content_type: str) -> str:
        if not file_bytes:
            raise WavespeedClientError("Binary payload is empty", "MISSING_AVATAR_SOURCE")

        url = f"{self.base_url}/media/upload/binary"
        headers = self._json_headers(content_type=content_type, filename=filename)
        response = httpx.post(url, content=file_bytes, headers=headers, timeout=_request_timeout_seconds())
        payload: dict[str, Any] | None = None
        if response.status_code < 400:
            try:
                payload = self._json(response)
            except WavespeedClientError:
                payload = None
        if response.status_code >= 400 or not self._extract_download_url(payload or {}):
            response = httpx.post(
                url,
                files={"file": (filename or "avatar.png", file_bytes, content_type)},
                headers=self._auth_headers(),
                timeout=_request_timeout_seconds(),
            )
            if response.status_code in {401, 403}:
                raise WavespeedClientError(
                    "WaveSpeed credentials were rejected",
                    "INVALID_WAVESPEED_CREDENTIALS",
                )
        if response.status_code >= 400:
            raise WavespeedClientError(
                f"WaveSpeed image upload returned HTTP {response.status_code}",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        payload = self._json(response)
        download_url = self._extract_download_url(payload)
        if not download_url:
            raise WavespeedClientError(
                "WaveSpeed upload response missing download_url",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        logger.info(
            "WaveSpeed binary upload succeeded: filename=%s content_type=%s host=%s",
            filename,
            content_type,
            _url_host(download_url),
        )
        return download_url

    def upload_image(self, file_bytes: bytes, filename: str, content_type: str) -> str:
        return self.upload_binary(file_bytes=file_bytes, filename=filename, content_type=content_type)

    def upload_audio(self, file_bytes: bytes, filename: str, content_type: str) -> str:
        return self.upload_binary(file_bytes=file_bytes, filename=filename, content_type=content_type)

    def create_talking_photo(self, image_url: str, text: str, duration: int = 5, seed: int = -1) -> str:
        duration = _validate_duration(duration)
        if not image_url:
            raise WavespeedClientError("Avatar image URL is missing", "MISSING_AVATAR_SOURCE")
        if not text or not text.strip():
            raise WavespeedClientError("Narration text is missing", "MISSING_DIALOGUE")

        url = f"{self.base_url}/wavespeed-ai/ai-talking-photos"
        payload = {
            "image": image_url,
            "text": text.strip(),
            "duration": duration,
            "seed": seed,
        }
        request_shape = _safe_payload_shape(payload)
        logger.info(
            "Submitting WaveSpeed talking photo request: endpoint=%s image_host=%s duration=%s text_present=%s payload_shape=%s",
            url,
            _url_host(image_url),
            duration,
            bool(text.strip()),
            request_shape,
        )
        response = httpx.post(
            url,
            headers=self._json_headers(),
            json=payload,
            timeout=_request_timeout_seconds(),
        )
        if response.status_code in {401, 403}:
            raise WavespeedClientError(
                "WaveSpeed credentials were rejected",
                "INVALID_WAVESPEED_CREDENTIALS",
            )
        if response.status_code >= 400:
            raise WavespeedClientError(
                f"WaveSpeed talking photo returned HTTP {response.status_code}",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        payload = self._json(response)
        request_id = _extract_request_id(payload)
        if not request_id:
            raise WavespeedClientError(
                "WaveSpeed response missing request id",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        return request_id

    def create_infinite_talk(
        self,
        image_url: str,
        audio_url: str,
        *,
        prompt: str | None = None,
        resolution: str = "480p",
        seed: int = -1,
    ) -> str:
        if not image_url:
            raise WavespeedClientError("Avatar image URL is missing", "MISSING_AVATAR_SOURCE")
        if not audio_url:
            raise WavespeedClientError("Audio URL is missing", "MISSING_AUDIO_ASSET")

        url = f"{self.base_url}/wavespeed-ai/infinitetalk"
        payload: dict[str, Any] = {
            "image": image_url,
            "audio": audio_url,
            "resolution": resolution,
            "seed": seed,
        }
        if prompt:
            payload["prompt"] = prompt.strip()
        request_shape = _safe_payload_shape(payload)
        logger.info(
            "Submitting WaveSpeed InfiniteTalk request: endpoint=%s image_host=%s audio_host=%s resolution=%s prompt_present=%s payload_shape=%s",
            url,
            _url_host(image_url),
            _url_host(audio_url),
            resolution,
            bool(prompt and prompt.strip()),
            request_shape,
        )
        response = httpx.post(
            url,
            headers=self._json_headers(),
            json=payload,
            timeout=_request_timeout_seconds(),
        )
        if response.status_code in {401, 403}:
            raise WavespeedClientError(
                "WaveSpeed credentials were rejected",
                "INVALID_WAVESPEED_CREDENTIALS",
            )
        if response.status_code >= 400:
            raise WavespeedClientError(
                f"WaveSpeed InfiniteTalk returned HTTP {response.status_code}",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        payload = self._json(response)
        request_id = _extract_request_id(payload)
        if not request_id:
            raise WavespeedClientError(
                "WaveSpeed response missing request id",
                "WAVESPEED_TALKING_PHOTO_FAILED",
        )
        return request_id

    def create_sync_lipsync(
        self,
        video_url: str,
        audio_url: str,
        *,
        sync_mode: str = "loop",
        seed: int = -1,
        resolution: str | None = None,
        model_path: str | None = None,
    ) -> str:
        if not video_url:
            raise WavespeedClientError("Avatar video URL is missing", "MISSING_AVATAR_SOURCE")
        if not audio_url:
            raise WavespeedClientError("Audio URL is missing", "MISSING_AUDIO_ASSET")

        endpoint = (model_path or settings.AVATAR_LIPSYNC_MODEL_PATH or "wavespeed-ai/sync-lipsync-3").strip("/")
        url = f"{self.base_url}/{endpoint}"
        payload: dict[str, Any] = {
            "video": video_url,
            "audio": audio_url,
            "sync_mode": sync_mode,
            "seed": seed,
        }
        if resolution:
            payload["resolution"] = resolution
        request_shape = _safe_payload_shape(payload)
        logger.info(
            "Submitting WaveSpeed sync lipsync request: endpoint=%s video_host=%s audio_host=%s sync_mode=%s resolution=%s payload_shape=%s",
            url,
            _url_host(video_url),
            _url_host(audio_url),
            sync_mode,
            resolution,
            request_shape,
        )
        response = httpx.post(
            url,
            headers=self._json_headers(),
            json=payload,
            timeout=_request_timeout_seconds(),
        )
        if response.status_code in {401, 403}:
            raise WavespeedClientError(
                "WaveSpeed credentials were rejected",
                "INVALID_WAVESPEED_CREDENTIALS",
            )
        if response.status_code >= 400:
            raise WavespeedClientError(
                f"WaveSpeed sync lipsync returned HTTP {response.status_code}",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        payload = self._json(response)
        request_id = _extract_request_id(payload)
        if not request_id:
            raise WavespeedClientError(
                "WaveSpeed response missing request id",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        return request_id

    def get_prediction_result(self, request_id: str) -> dict[str, Any]:
        if not request_id:
            raise WavespeedClientError("Prediction request id is missing", "WAVESPEED_TALKING_PHOTO_FAILED")
        response = httpx.get(
            f"{self.base_url}/predictions/{request_id}/result",
            headers=self._auth_headers(),
            timeout=_request_timeout_seconds(),
        )
        if response.status_code in {401, 403}:
            raise WavespeedClientError(
                "WaveSpeed credentials were rejected",
                "INVALID_WAVESPEED_CREDENTIALS",
            )
        if response.status_code >= 400:
            raise WavespeedClientError(
                f"WaveSpeed result returned HTTP {response.status_code}",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        payload = self._json(response)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise WavespeedClientError(
                "WaveSpeed response missing data object",
                "WAVESPEED_TALKING_PHOTO_FAILED",
            )
        return data

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def _json_headers(self, *, content_type: str = "application/json", filename: str | None = None) -> dict[str, str]:
        headers = {
            **self._auth_headers(),
            "Content-Type": content_type,
        }
        if filename:
            headers["X-Filename"] = filename
        return headers

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise WavespeedClientError(
            "WaveSpeed response was not a JSON object",
            "WAVESPEED_TALKING_PHOTO_FAILED",
        )

    @staticmethod
    def _extract_download_url(payload: dict[str, Any]) -> str | None:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            download_url = data.get("download_url")
            if isinstance(download_url, str) and download_url:
                return download_url
        return None


def _extract_request_id(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    for key in ("request_id", "prediction_id", "id"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    urls = data.get("urls")
    if isinstance(urls, dict):
        get_url = urls.get("get")
        if isinstance(get_url, str) and get_url:
            return get_url.rsplit("/", 2)[-2] if "/predictions/" in get_url else None
    return None


def _safe_payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    def _host(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            return httpx.URL(value).host
        except Exception:
            return None

    return {
        "keys": sorted(payload.keys()),
        "image_present": bool(payload.get("image")),
        "video_present": bool(payload.get("video")),
        "audio_present": bool(payload.get("audio")),
        "text_present": bool(payload.get("text")),
        "prompt_present": bool(payload.get("prompt")),
        "seed_present": payload.get("seed") is not None,
        "duration_present": payload.get("duration") is not None,
        "resolution": payload.get("resolution"),
        "sync_mode": payload.get("sync_mode"),
        "image_host": _host(payload.get("image")),
        "video_host": _host(payload.get("video")),
        "audio_host": _host(payload.get("audio")),
    }


def _validate_duration(duration: int) -> int:
    if duration < 5 or duration > 15:
        raise WavespeedClientError(
            "Duration must be between 5 and 15 seconds",
            "INVALID_AVATAR_DURATION",
        )
    return duration


def _url_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return httpx.URL(url).host
    except Exception:
        return None
