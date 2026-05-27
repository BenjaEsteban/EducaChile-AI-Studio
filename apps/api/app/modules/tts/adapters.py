import math
import logging
import subprocess
import tempfile
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class TTSProviderError(RuntimeError):
    code = "TTS_PROVIDER_FAILED"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class TTSProvider:
    provider_name: str

    def generate_audio(
        self,
        text: str,
        voice_id: str | None,
        language: str,
        speed: float = 1.0,
        api_key: str | None = None,
    ) -> tuple[bytes, float]:
        word_count = max(1, len(text.split()))
        duration = max(1.5, math.ceil(word_count / 2.2))
        return _silent_wav(duration), duration


class GeminiTTSProvider(TTSProvider):
    provider_name = "gemini"


class ElevenLabsTTSProvider(TTSProvider):
    provider_name = "elevenlabs"

    def generate_audio(
        self,
        text: str,
        voice_id: str | None,
        language: str,
        speed: float = 1.0,
        api_key: str | None = None,
    ) -> tuple[bytes, float]:
        if not api_key:
            raise TTSProviderError(
                "ElevenLabs API key is missing",
                "INVALID_ELEVENLABS_CREDENTIALS",
            )
        if not voice_id:
            raise TTSProviderError(
                "ElevenLabs voice ID is missing",
                "MISSING_ELEVENLABS_VOICE_ID",
            )

        payload: dict[str, object] = {
            "text": text,
            "model_id": "eleven_multilingual_v2",
        }
        if speed and abs(speed - 1.0) > 0.001:
            payload["speed"] = speed

        response = httpx.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            params={"output_format": "mp3_44100_128"},
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json=payload,
            timeout=90,
        )
        if response.status_code in {401, 403}:
            raise TTSProviderError(
                "ElevenLabs credentials were rejected",
                "INVALID_ELEVENLABS_CREDENTIALS",
            )
        if response.status_code >= 400:
            raise TTSProviderError(
                f"ElevenLabs TTS returned HTTP {response.status_code}",
                "ELEVENLABS_TTS_FAILED",
            )

        audio_bytes = response.content
        duration = _probe_duration(audio_bytes, ".mp3")
        if not audio_bytes or duration <= 0:
            raise TTSProviderError(
                "ElevenLabs returned an invalid audio response",
                "ELEVENLABS_TTS_FAILED",
            )
        return audio_bytes, duration


def get_tts_provider(provider_name: str) -> TTSProvider:
    normalized = (provider_name or "none").strip().lower()
    if normalized == "elevenlabs":
        return ElevenLabsTTSProvider()
    if normalized == "none":
        if not settings.ALLOW_DUMMY_TTS:
            raise TTSProviderError(
                "TTS_PROVIDER=none is disabled. Configure a real TTS provider or set ALLOW_DUMMY_TTS=true for local development.",
                "TTS_PROVIDER_NOT_CONFIGURED",
            )
        logger.warning("Using dummy TTS provider because ALLOW_DUMMY_TTS=true")
    return GeminiTTSProvider()


def _silent_wav(duration: float) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                str(duration),
                str(output),
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return output.read_bytes()


def _probe_duration(media_bytes: bytes, suffix: str) -> float:
    if not media_bytes:
        return 0.0
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"audio{suffix}"
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
