import subprocess
import tempfile
from pathlib import Path


class AvatarVideoProvider:
    provider_name: str

    def generate_avatar_clip(
        self,
        audio_bytes: bytes,
        duration_seconds: float,
        avatar_id: str | None,
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


def get_avatar_video_provider(provider_name: str) -> AvatarVideoProvider:
    return WavespeedAvatarVideoProvider()
