import math
import subprocess
import tempfile
from pathlib import Path


class TTSProvider:
    provider_name: str

    def generate_audio(self, text: str, voice_id: str | None, language: str) -> tuple[bytes, float]:
        word_count = max(1, len(text.split()))
        duration = max(1.5, min(20.0, math.ceil(word_count / 2.5)))
        return _silent_wav(duration), duration


class GeminiTTSProvider(TTSProvider):
    provider_name = "gemini"


class ElevenLabsTTSProvider(TTSProvider):
    provider_name = "elevenlabs"


def get_tts_provider(provider_name: str) -> TTSProvider:
    if provider_name == "elevenlabs":
        return ElevenLabsTTSProvider()
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
