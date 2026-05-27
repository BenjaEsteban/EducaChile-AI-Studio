import logging
import subprocess
import tempfile
import shutil
from pathlib import Path
from typing import TypedDict

from app.config import settings
from app.modules.composer.subtitles import build_srt_content

logger = logging.getLogger(__name__)


class AvatarOverlay(TypedDict):
    x: int
    y: int
    width: int
    height: int


class ComposerService:
    def normalize_audio_to_mp3(self, audio_bytes: bytes) -> bytes:
        _ensure_ffmpeg_available()
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_path = tmpdir / "input.bin"
            output_path = tmpdir / "output.mp3"
            input_path.write_bytes(audio_bytes)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(input_path),
                    "-vn",
                    "-c:a",
                    "libmp3lame",
                    "-q:a",
                    "4",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                timeout=settings.FFMPEG_TIMEOUT_SECONDS,
            )
            return output_path.read_bytes()

    def strip_audio_from_video(self, video_bytes: bytes) -> bytes:
        _ensure_ffmpeg_available()
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_path = tmpdir / "input.mp4"
            output_path = tmpdir / "output.mp4"
            input_path.write_bytes(video_bytes)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(input_path),
                    "-an",
                    "-c:v",
                    "copy",
                    str(output_path),
                ],
                check=True,
                capture_output=True,
                timeout=settings.FFMPEG_TIMEOUT_SECONDS,
            )
            return output_path.read_bytes()

    def compose_slide_video(
        self,
        slide_image_bytes: bytes,
        avatar_clip_bytes: bytes,
        audio_bytes: bytes,
        duration_seconds: float,
        avatar_overlay: AvatarOverlay,
        resolution: str = "1080p",
        audio_pad_seconds: float = 0.0,
        avatar_chromakey: bool = False,
        chromakey_color: str = "0x00FF00",
        chromakey_similarity: float = 0.15,
        chromakey_blend: float = 0.05,
        subtitle_text: str | None = None,
        subtitle_duration_seconds: float | None = None,
    ) -> bytes:
        _ensure_ffmpeg_available()
        width, height = _resolution_size(resolution)
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            slide_image = tmpdir / "slide.png"
            avatar_clip = tmpdir / "avatar.mp4"
            audio = tmpdir / "audio.mp3"
            output = tmpdir / "slide.mp4"
            slide_image.write_bytes(slide_image_bytes)
            avatar_clip.write_bytes(avatar_clip_bytes)
            avatar_filter = f"scale={avatar_overlay['width']}:{avatar_overlay['height']},setsar=1"
            if avatar_chromakey:
                avatar_filter += (
                    f",format=rgba,chromakey={chromakey_color}:{chromakey_similarity}:{chromakey_blend}"
                )
            base_overlay_filter = (
                f"[0:v]scale={width}:{height},setsar=1[bg];"
                f"[1:v]{avatar_filter}[avatar];"
                f"[bg][avatar]overlay={avatar_overlay['x']}:{avatar_overlay['y']}:"
                "shortest=0:eof_action=repeat[basev]"
            )
            command = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(slide_image),
                "-i",
                str(avatar_clip),
            ]
            audio.write_bytes(audio_bytes)
            command.extend(["-i", str(audio)])
            subtitle_filter = ""
            subtitle_srt = tmpdir / "subtitles.srt"
            if subtitle_text:
                try:
                    subtitle_duration = (
                        float(subtitle_duration_seconds)
                        if subtitle_duration_seconds is not None and float(subtitle_duration_seconds) > 0
                        else float(duration_seconds)
                    )
                    subtitle_content = build_srt_content(subtitle_text, subtitle_duration)
                    if subtitle_content.strip():
                        subtitle_srt.write_text(subtitle_content, encoding="utf-8")
                        subtitle_style = (
                            "FontName=Arial,FontSize=24,PrimaryColour=&HFFFFFF,"
                            "OutlineColour=&H000000,Outline=2,Alignment=2,MarginV=40"
                        )
                        subtitle_path = _escape_subtitles_filter_path(subtitle_srt)
                        subtitle_filter = (
                            f";[basev]subtitles='{subtitle_path}':force_style='{subtitle_style}'[outv]"
                        )
                except Exception as exc:
                    logger.warning("Subtitle generation skipped: %s", exc)

            filter_complex = f"{base_overlay_filter}{subtitle_filter}"
            map_output_video = "[outv]" if subtitle_filter else "[basev]"
            command.extend(["-filter_complex", filter_complex, "-map", map_output_video])
            command.extend(["-map", "2:a:0"])
            if audio_pad_seconds > 0:
                command.extend(["-af", f"apad=pad_dur={audio_pad_seconds}"])
            command.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-c:a",
                    "aac",
                    "-pix_fmt",
                    "yuv420p",
                    "-r",
                    "30",
                    "-shortest",
                    "-t",
                    str(duration_seconds),
                    str(output),
                ]
            )
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=settings.FFMPEG_TIMEOUT_SECONDS,
                )
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode("utf-8", errors="ignore") if exc.stderr else ""
                if subtitle_filter:
                    logger.error(
                        "FFmpeg subtitle burn-in failed, retrying without subtitles: %s",
                        stderr[-2000:] if stderr else "unknown error",
                    )
                    fallback_command = list(command)
                    filter_index = fallback_command.index("-filter_complex") + 1
                    fallback_command[filter_index] = base_overlay_filter
                    map_index = fallback_command.index("-map") + 1
                    fallback_command[map_index] = "[basev]"
                    try:
                        subprocess.run(
                            fallback_command,
                            check=True,
                            capture_output=True,
                            timeout=settings.FFMPEG_TIMEOUT_SECONDS,
                        )
                        return output.read_bytes()
                    except subprocess.CalledProcessError as fallback_exc:
                        fallback_stderr = (
                            fallback_exc.stderr.decode("utf-8", errors="ignore")
                            if fallback_exc.stderr
                            else ""
                        )
                        if fallback_stderr:
                            logger.error(
                                "FFmpeg slide composition fallback failed stderr: %s",
                                fallback_stderr[-2000:],
                            )
                        raise RuntimeError(
                            f"FFmpeg slide composition failed after subtitle fallback: {fallback_stderr[-2000:]}"
                        ) from fallback_exc
                if stderr:
                    logger.error("FFmpeg slide composition failed stderr: %s", stderr[-2000:])
                raise RuntimeError(
                    f"FFmpeg slide composition failed: {stderr[-2000:]}"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                stderr = exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
                if stderr:
                    logger.error("FFmpeg slide composition timed out stderr: %s", stderr[-2000:])
                raise RuntimeError(
                    f"FFmpeg slide composition timed out after {settings.FFMPEG_TIMEOUT_SECONDS} seconds"
                ) from exc
            return output.read_bytes()

    def concatenate_audio_tracks(self, audio_tracks: list[bytes]) -> bytes:
        _ensure_ffmpeg_available()
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            list_file = tmpdir / "audio.txt"
            paths = []
            for index, audio in enumerate(audio_tracks, 1):
                path = tmpdir / f"audio-{index}.mp3"
                path.write_bytes(audio)
                paths.append(path)
            list_file.write_text(
                "\n".join(f"file '{path.as_posix()}'" for path in paths),
                encoding="utf-8",
            )
            output = tmpdir / "final.mp3"
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
                    "0:a:0",
                    "-c",
                    "copy",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=settings.FFMPEG_TIMEOUT_SECONDS,
            )
            return output.read_bytes()

    def concatenate_video_tracks(self, video_tracks: list[bytes]) -> bytes:
        _ensure_ffmpeg_available()
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            list_file = tmpdir / "videos.txt"
            paths = []
            for index, video in enumerate(video_tracks, 1):
                path = tmpdir / f"video-{index}.mp4"
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
                    "-an",
                    "-c",
                    "copy",
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=settings.FFMPEG_TIMEOUT_SECONDS,
            )
            return output.read_bytes()

    def concatenate_slide_videos(self, slide_videos: list[bytes]) -> bytes:
        _ensure_ffmpeg_available()
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
                timeout=settings.FFMPEG_TIMEOUT_SECONDS,
            )
            return output.read_bytes()


def _escape_subtitles_filter_path(path: Path) -> str:
    value = path.as_posix()
    value = value.replace("\\", "\\\\")
    value = value.replace(":", "\\:")
    value = value.replace("'", "\\'")
    value = value.replace(",", "\\,")
    return value


def _resolution_size(resolution: str) -> tuple[int, int]:
    if resolution == "720p":
        return 1280, 720
    if resolution == "1080p":
        return 1920, 1080
    if "x" in resolution:
        left, right = resolution.lower().split("x", 1)
        return int(left), int(right)
    return 1920, 1080


def _ensure_ffmpeg_available() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is not available in this environment")
