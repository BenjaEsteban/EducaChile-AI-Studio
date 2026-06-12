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


def _rounded_alpha_expr(width: int, height: int, radius_px: int) -> str:
    """FFmpeg geq alpha expression for a rounded-rectangle mask.

    Pixels inside the rounded rectangle keep their alpha; corner pixels outside
    the corner circles become transparent. radius_px = min(w,h)/2 yields a
    circle/ellipse, matching the editor's CSS border-radius preview.
    """
    half_w = width / 2
    half_h = height / 2
    inner_x = max(half_w - radius_px, 0)
    inner_y = max(half_h - radius_px, 0)
    corner = (
        f"if(lte(hypot(max(abs(X-{half_w})-{inner_x},0),"
        f"max(abs(Y-{half_h})-{inner_y},0)),{radius_px}),255,0)"
    )
    return f"alpha(X,Y)*{corner}/255"


def build_avatar_shape_filters(
    width: int,
    height: int,
    border_radius_pct: float,
    border_color: str | None,
    duration_seconds: float,
    border_width_px: int = 8,
) -> tuple[str, str, int]:
    """Build optional shape/border filter snippets for the avatar overlay.

    Returns (shape_filter_suffix, border_chain, border_margin):
    - shape_filter_suffix is appended to the avatar scale filter to round its
      corners (empty when border_radius_pct <= 0).
    - border_chain is a standalone filter_complex fragment that composes the
      rounded avatar over a slightly larger colored plate, producing
      [avatarframed]; empty when no border color is configured.
    - border_margin is the plate margin (= border thickness) in pixels (0 without
      border), used by the caller to offset the overlay position.

    border_width_px controls the visible border thickness in output pixels.
    """
    radius_pct = max(0.0, min(float(border_radius_pct or 0.0), 50.0))
    shape_suffix = ""
    radius_px = 0
    if radius_pct > 0:
        radius_px = max(1, round(min(width, height) * radius_pct / 100))
        alpha_expr = _rounded_alpha_expr(width, height, radius_px)
        shape_suffix = (
            f",format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha_expr}'"
        )

    border_chain = ""
    border_margin = 0
    if border_color:
        border_margin = max(2, int(border_width_px))
        plate_w = width + 2 * border_margin
        plate_h = height + 2 * border_margin
        plate_filters = "format=rgba"
        if radius_pct > 0:
            plate_radius = max(1, round(min(plate_w, plate_h) * radius_pct / 100))
            plate_alpha = _rounded_alpha_expr(plate_w, plate_h, plate_radius)
            plate_filters += (
                f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{plate_alpha}'"
            )
        border_chain = (
            f"color=c={border_color}:s={plate_w}x{plate_h}:d={duration_seconds}"
            f",{plate_filters}[avatarplate];"
            f"[avatarplate][avatar]overlay={border_margin}:{border_margin}"
            ":shortest=0:eof_action=repeat[avatarframed];"
        )
    return shape_suffix, border_chain, border_margin


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
        subtitle_style: str | None = None,
        avatar_border_radius_pct: float = 0.0,
        avatar_border_color: str | None = None,
        avatar_border_width_px: int = 8,
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
            shape_suffix, border_chain, border_margin = build_avatar_shape_filters(
                avatar_overlay["width"],
                avatar_overlay["height"],
                avatar_border_radius_pct,
                avatar_border_color,
                duration_seconds,
                border_width_px=avatar_border_width_px,
            )
            avatar_filter += shape_suffix
            overlay_label = "[avatarframed]" if border_chain else "[avatar]"
            overlay_x = avatar_overlay["x"] - border_margin
            overlay_y = avatar_overlay["y"] - border_margin
            base_overlay_filter = (
                f"[0:v]scale={width}:{height},setsar=1[bg];"
                f"[1:v]{avatar_filter}[avatar];"
                f"{border_chain}"
                f"[bg]{overlay_label}overlay={overlay_x}:{overlay_y}:"
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
                        # Use the per-project configured style when provided,
                        # else the previous default (preserves prior behavior).
                        effective_style = subtitle_style or (
                            "FontName=Arial,FontSize=24,PrimaryColour=&HFFFFFF,"
                            "OutlineColour=&H000000,Outline=2,Alignment=2,MarginV=40"
                        )
                        subtitle_path = _escape_subtitles_filter_path(subtitle_srt)
                        # Commas separate options in a filtergraph, so commas
                        # inside force_style must be escaped even when quoted.
                        escaped_style = effective_style.replace(",", "\\,")
                        subtitle_filter = (
                            f";[basev]subtitles='{subtitle_path}':force_style='{escaped_style}'[outv]"
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

    def mix_background_music(
        self,
        video_bytes: bytes,
        music_bytes: bytes,
        *,
        volume: float = 0.35,
        loop: bool = True,
        fade_out_enabled: bool = True,
        fade_out_seconds: float = 3.0,
    ) -> bytes:
        """Mix background music under the video's narration audio.

        Narration is kept at full volume; the music is attenuated to ``volume``
        (0–1), optionally looped to cover the whole video, and optionally faded
        out over the final ``fade_out_seconds``. The output length always matches
        the input video (``amix=duration=first``), so narration timing is never
        affected. On any failure the original video is returned unchanged.
        """
        _ensure_ffmpeg_available()
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            video_path = tmpdir / "video.mp4"
            music_path = tmpdir / "music.bin"
            output = tmpdir / "mixed.mp4"
            video_path.write_bytes(video_bytes)
            music_path.write_bytes(music_bytes)

            duration = float(_probe_duration_seconds(video_path) or 0.0)
            vol = max(0.0, min(float(volume), 1.0))

            music_chain = f"[1:a]volume={vol:.3f}"
            if fade_out_enabled and fade_out_seconds > 0 and duration > 0:
                fade = min(float(fade_out_seconds), duration)
                start = max(duration - fade, 0.0)
                music_chain += f",afade=t=out:st={start:.3f}:d={fade:.3f}"
            music_chain += "[bg]"
            # normalize=0 keeps narration at full level instead of averaging.
            filter_complex = (
                f"{music_chain};"
                "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
            )

            command = ["ffmpeg", "-y", "-i", str(video_path)]
            if loop:
                command += ["-stream_loop", "-1"]
            command += [
                "-i",
                str(music_path),
                "-filter_complex",
                filter_complex,
                "-map",
                "0:v:0",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-shortest",
                str(output),
            ]
            try:
                subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    timeout=settings.FFMPEG_TIMEOUT_SECONDS,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                stderr = exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else ""
                logger.error(
                    "Background music mix failed, returning video without music: %s",
                    stderr[-1000:] if stderr else type(exc).__name__,
                )
                return video_bytes
            return output.read_bytes()


def _probe_duration_seconds(path: Path) -> float | None:
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
        return float(result.stdout.strip())
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, OSError):
        return None


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
