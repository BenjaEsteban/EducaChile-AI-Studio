import json
import subprocess
import tempfile
from pathlib import Path


class ComposerService:
    def render_slide_image(
        self,
        background_bytes: bytes | None,
        slide_metadata: dict,
        resolution: str,
    ) -> bytes:
        width, height = _resolution_size(resolution)
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            background = tmpdir / "background.png"
            output = tmpdir / "slide.png"
            if background_bytes:
                background.write_bytes(background_bytes)
                input_args = ["-i", str(background)]
                base_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease"
                base_filter += f",pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
            else:
                input_args = ["-f", "lavfi", "-i", f"color=c=white:s={width}x{height}"]
                base_filter = "null"

            filters = [base_filter]
            filters.extend(_drawtext_filters(slide_metadata, width, height))
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    *input_args,
                    "-frames:v",
                    "1",
                    "-vf",
                    ",".join(filters),
                    str(output),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            return output.read_bytes()

    def compose_slide_video(
        self,
        slide_image_bytes: bytes,
        audio_bytes: bytes,
        duration_seconds: float,
    ) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            slide_image = tmpdir / "slide.png"
            audio = tmpdir / "audio.wav"
            output = tmpdir / "slide.mp4"
            slide_image.write_bytes(slide_image_bytes)
            audio.write_bytes(audio_bytes)
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(slide_image),
                    "-i",
                    str(audio),
                    "-t",
                    str(duration_seconds),
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    "-pix_fmt",
                    "yuv420p",
                    "-shortest",
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


def _drawtext_filters(slide_metadata: dict, width: int, height: int) -> list[str]:
    canvas = slide_metadata.get("canvas") if isinstance(slide_metadata, dict) else {}
    if not isinstance(canvas, dict):
        return []
    canvas_width = float(canvas.get("width") or 960)
    canvas_height = float(canvas.get("height") or 540)
    elements = canvas.get("elements") or canvas.get("text_blocks") or []
    filters: list[str] = []
    for element in elements:
        if not isinstance(element, dict) or element.get("type") not in {"text", "title", "body"}:
            continue
        text = str(element.get("text") or "")
        if not text:
            continue
        style = element.get("style") if isinstance(element.get("style"), dict) else element
        color = _ffmpeg_color(str(style.get("color") or style.get("originalColor") or "#000000"))
        font_size = int(float(style.get("fontSize") or 28) * (height / canvas_height))
        x = int(float(element.get("x") or 0) * (width / canvas_width))
        y = int(float(element.get("y") or 0) * (height / canvas_height))
        filters.append(
            "drawtext="
            f"text={_escape_drawtext(text)}:"
            f"x={x}:y={y}:fontsize={font_size}:"
            f"fontcolor={color}:box=0"
        )
    return filters


def _escape_drawtext(value: str) -> str:
    return json.dumps(value.replace("\n", "\\n"))[1:-1].replace(":", "\\:")


def _ffmpeg_color(value: str) -> str:
    color = value.strip()
    if color.startswith("#") and len(color) in {4, 7}:
        if len(color) == 4:
            color = "#" + "".join(character * 2 for character in color[1:])
        return f"0x{color[1:]}"
    return color
