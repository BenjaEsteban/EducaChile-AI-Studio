from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


INFINITETALK_FAST_MODEL = "wavespeed-ai/infinitetalk-fast"
SPANISH_LIPSYNC_PROMPT = (
    "Make the avatar speak in Spanish. Use natural Spanish lip-sync, realistic facial "
    "expressions, and match the provided audio exactly without translating it."
)


class WaveSpeedOfficialError(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class WaveSpeedOfficialClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = (api_key or settings.WAVESPEED_API_KEY or "").strip()
        if not self.api_key:
            raise WaveSpeedOfficialError("WAVESPEED_API_KEY is missing in worker environment")

    def run_infinitetalk_fast(self, *, image_url: str, audio_url: str) -> str:
        wavespeed = self._sdk()
        payload = {
            "audio": audio_url,
            "image": image_url,
            "prompt": SPANISH_LIPSYNC_PROMPT,
            "seed": -1,
        }
        logger.info(
            "Submitting WaveSpeed official request: model=%s payload_shape=%s",
            INFINITETALK_FAST_MODEL,
            _safe_payload_shape(payload),
        )
        try:
            output = wavespeed.run(INFINITETALK_FAST_MODEL, payload)
        except Exception as exc:
            raise WaveSpeedOfficialError(
                f"WaveSpeed InfiniteTalk Fast request failed: {exc}",
                details={"model": INFINITETALK_FAST_MODEL, "payload_shape": _safe_payload_shape(payload)},
            ) from exc
        video_url = _extract_output_url(output)
        if not video_url:
            raise WaveSpeedOfficialError(
                "WaveSpeed InfiniteTalk Fast response missing output video URL",
                details={"model": INFINITETALK_FAST_MODEL, "response_shape": _safe_response_shape(output)},
            )
        logger.info(
            "WaveSpeed official request completed: model=%s video_host=%s video_path=%s",
            INFINITETALK_FAST_MODEL,
            _url_host(video_url),
            _url_path(video_url),
        )
        return video_url

    def upload_bytes(self, file_bytes: bytes, *, filename: str, content_type: str) -> str:
        if not file_bytes:
            raise WaveSpeedOfficialError("Cannot upload empty media to WaveSpeed")
        wavespeed = self._sdk()
        suffix = Path(filename or "media.bin").suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            try:
                uploaded = wavespeed.upload(tmp.name)
            except Exception as exc:
                raise WaveSpeedOfficialError(
                    f"WaveSpeed media upload failed for {filename}: {exc}",
                    details={"filename": filename, "content_type": content_type},
                ) from exc
        url = _extract_upload_url(uploaded)
        if not url:
            raise WaveSpeedOfficialError(
                "WaveSpeed media upload response missing URL",
                details={"filename": filename, "response_shape": _safe_response_shape(uploaded)},
            )
        logger.info(
            "WaveSpeed media upload succeeded: filename=%s content_type=%s host=%s path=%s",
            filename,
            content_type,
            _url_host(url),
            _url_path(url),
        )
        return url

    def _sdk(self):
        try:
            import wavespeed  # type: ignore
        except ImportError as exc:
            raise WaveSpeedOfficialError(
                "WaveSpeed Python SDK is not installed. Add the wavespeed package to the worker image."
            ) from exc
        os.environ["WAVESPEED_API_KEY"] = self.api_key
        try:
            wavespeed.api_key = self.api_key
        except Exception:
            pass
        return wavespeed


def download_video(url: str) -> bytes:
    response = httpx.get(url, timeout=settings.WAVESPEED_HTTP_TIMEOUT_SECONDS, follow_redirects=True)
    if response.status_code >= 400:
        raise WaveSpeedOfficialError(
            f"WaveSpeed output download returned HTTP {response.status_code}",
            details={"host": _url_host(url), "path": _url_path(url)},
        )
    return response.content


def public_url_accessible(url: str) -> bool:
    if not url:
        return False
    try:
        response = httpx.head(url, timeout=10, follow_redirects=True)
        if 200 <= response.status_code < 400:
            return True
        response = httpx.get(
            url,
            headers={"Range": "bytes=0-0"},
            timeout=10,
            follow_redirects=True,
        )
        return 200 <= response.status_code < 400
    except Exception:
        return False


def _extract_output_url(output: Any) -> str | None:
    if isinstance(output, dict):
        outputs = output.get("outputs")
        if isinstance(outputs, list) and outputs and isinstance(outputs[0], str):
            return outputs[0]
        for key in ("output", "result", "url"):
            value = output.get(key)
            if isinstance(value, str):
                return value
    if isinstance(output, list) and output and isinstance(output[0], str):
        return output[0]
    if isinstance(output, str):
        return output
    return None


def _extract_upload_url(uploaded: Any) -> str | None:
    if isinstance(uploaded, str):
        return uploaded
    if isinstance(uploaded, dict):
        data = uploaded.get("data")
        if isinstance(data, dict):
            for key in ("download_url", "url"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    return value
        for key in ("download_url", "url"):
            value = uploaded.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _safe_payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "keys": sorted(payload.keys()),
        "audio_present": bool(payload.get("audio")),
        "image_present": bool(payload.get("image")),
        "prompt_present": bool(payload.get("prompt")),
        "seed": payload.get("seed"),
        "audio_host": _url_host(payload.get("audio")),
        "audio_path": _url_path(payload.get("audio")),
        "image_host": _url_host(payload.get("image")),
        "image_path": _url_path(payload.get("image")),
    }


def _safe_response_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(value.keys())}
    if isinstance(value, list):
        return {"type": "list", "length": len(value)}
    return {"type": type(value).__name__}


def _url_host(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).hostname
    except Exception:
        return None


def _url_path(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).path
    except Exception:
        return None
