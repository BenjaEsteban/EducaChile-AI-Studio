import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict


class AvatarOverlay(TypedDict):
    x: int
    y: int
    width: int
    height: int


class ComposerService:
    def compose_slide_video(
        self,
        slide_image_bytes: bytes,
        avatar_clip_bytes: bytes,
        audio_bytes: bytes,
        duration_seconds: float,
        avatar_overlay: AvatarOverlay,
        resolution: str = "1080p",
    ) -> bytes:
        width, height = _resolution_size(resolution)
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            slide_image = tmpdir / "slide.png"
            avatar_clip = tmpdir / "avatar.mp4"
            audio = tmpdir / "audio.mp3"
            output = tmpdir / "slide.mp4"
            slide_image.write_bytes(slide_image_bytes)
            avatar_clip.write_bytes(avatar_clip_bytes)
            audio.write_bytes(audio_bytes)
            overlay_filter = (
                f"[0:v]scale={width}:{height},setsar=1[bg];"
                f"[1:v]scale={avatar_overlay['width']}:{avatar_overlay['height']},setsar=1[avatar];"
                f"[bg][avatar]overlay={avatar_overlay['x']}:{avatar_overlay['y']}:"
                "shortest=0:eof_action=repeat[outv]"
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(slide_image),
                    "-i",
                    str(avatar_clip),
                    "-i",
                    str(audio),
                    "-filter_complex",
                    overlay_filter,
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
                    "-shortest",
                    "-t",
                    str(duration_seconds),
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            return output.read_bytes()

    def concatenate_slide_videos(self, slide_videos: list[bytes]) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            list_file = tmpdir / "videos.txt"
            paths = []
            for index, video in enumerate(slide_videos, 1):
                path = tmpdir / f"slide-{index}.mp4"
                path.write_bytes(video)
                paths.append(path)
            list_file.write_text(
                "\n".join(f"file '{path.as_posix()}'" for path in paths),
                encoding="utf-8",
            )
            output = tmpdir / "final.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_file),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-c",
                    "copy",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=180,
            )
            return output.read_bytes()


def _resolution_size(resolution: str) -> tuple[int, int]:
    if resolution == "720p":
        return 1280, 720
    if resolution == "1080p":
        return 1920, 1080
    if "x" in resolution:
        left, right = resolution.lower().split("x", 1)
        return int(left), int(right)
    return 1920, 1080
