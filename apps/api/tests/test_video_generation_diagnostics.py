import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from app.services.wavespeed_client import WavespeedClient
from app.modules.video.adapters import WavespeedAvatarVideoProvider
from app.modules.generation.pipeline import (
    PipelineError,
    _analyze_video_motion,
    _probe_media_info,
    _load_slide_preview_image,
)
from app.workers.tasks import GenerateVideoTask


def test_wavespeed_talking_photo_flow_uses_image_text_duration_and_seed(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload=None, status_code=200, content=b"not-used", headers=None):
            self.status_code = status_code
            self.content = content
            self._payload = payload or {}
            self.headers = headers or {}

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, content=None, files=None, timeout=None):
        requests.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json,
                "content": content,
                "files": files,
                "timeout": timeout,
            }
        )
        if url.endswith("/media/upload/binary"):
            return FakeResponse({"data": {"download_url": "https://wavespeed.test/uploaded.png"}})
        if url.endswith("/wavespeed-ai/ai-talking-photos"):
            return FakeResponse({"data": {"id": "request-123"}})
        return FakeResponse(status_code=404)

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/predictions/request-123/result"):
            return FakeResponse(
                {"data": {"status": "completed", "outputs": ["https://cdn.test/out.mp4"]}}
            )
        if url == "https://cdn.test/out.mp4":
            return FakeResponse(content=b"video-bytes")
        return FakeResponse()

    monkeypatch.setattr("app.services.wavespeed_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.wavespeed_client.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters._probe_duration", lambda *_args: 1.0)
    monkeypatch.setattr("app.modules.video.adapters._has_video_stream", lambda *_args: True)
    monkeypatch.setattr("app.modules.video.adapters._has_audio_stream", lambda *_args: True)

    client = WavespeedClient(api_key="secret")
    upload_url = client.upload_image(
        b"image-bytes",
        filename="avatar.png",
        content_type="image/png",
    )
    provider = WavespeedAvatarVideoProvider()
    video_url = provider.generate_avatar_video(
        image_url=upload_url,
        text="talking head prompt",
        duration=5,
        api_key="secret",
    )

    assert upload_url == "https://wavespeed.test/uploaded.png"
    assert video_url == "https://cdn.test/out.mp4"
    assert requests[0]["url"].endswith("/media/upload/binary")
    assert requests[0]["content"] == b"image-bytes"
    assert requests[1]["url"].endswith("/wavespeed-ai/ai-talking-photos")
    assert requests[1]["json"]["image"] == "https://wavespeed.test/uploaded.png"
    assert requests[1]["json"]["text"] == "talking head prompt"
    assert requests[1]["json"]["duration"] == 5
    assert requests[1]["json"]["seed"] == -1


def test_almost_static_clip_detection_flags_static_video(tmp_path: Path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not available in this test environment")
    output = tmp_path / "static.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=128x128:r=10",
            "-t",
            "1",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    motion = _analyze_video_motion(output.read_bytes())

    assert motion["almost_static"] is True
    assert motion["motion_score"] < motion["threshold"]


def test_slide_preview_loader_prefers_full_render_over_background():
    class FakeStorage:
        def __init__(self):
            self.objects = {
                "background.png": b"background-only",
                "rendered.png": b"full-render",
                "thumbnail.png": b"thumbnail",
            }

        def download_bytes(self, storage_key):
            return self.objects[storage_key]

    slide = SimpleNamespace(id=uuid.uuid4(), position=1, thumbnail_key="thumbnail.png")

    image, source = _load_slide_preview_image(
        FakeStorage(),
        slide,
        {
            "background_image_key": "background.png",
            "rendered_image_key": "rendered.png",
        },
    )

    assert image == b"full-render"
    assert source["source"] == "rendered_image_key"
    assert source["includes_text"] is True


def test_slide_preview_loader_rejects_background_only_source():
    class FakeStorage:
        def download_bytes(self, storage_key):
            if storage_key == "missing.png":
                raise FileNotFoundError
            return b"background-only"

    slide = SimpleNamespace(id=uuid.uuid4(), position=1, thumbnail_key="missing.png")

    try:
        _load_slide_preview_image(
            FakeStorage(),
            slide,
            {
                "background_image_key": "background.png",
                "rendered_image_key": "missing.png",
            },
        )
    except PipelineError as exc:
        assert exc.code == "slide_composition_failed"
        assert "full rendered PPT preview" in exc.message
    else:
        raise AssertionError("background-only slide source should not be used")


def test_probe_media_info_counts_audio_streams(monkeypatch):
    class FakeResult:
        stdout = """
        {
          "format": {"duration": "7.5"},
          "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
            {"codec_type": "audio", "codec_name": "aac"},
            {"codec_type": "audio", "codec_name": "aac"}
          ]
        }
        """

    def fake_run(*_args, **_kwargs):
        return FakeResult()

    monkeypatch.setattr("app.modules.generation.pipeline.subprocess.run", fake_run)

    info = _probe_media_info(b"video-bytes", ".mp4")

    assert info["has_video"] is True
    assert info["has_audio"] is True
    assert info["audio_stream_count"] == 2
    assert info["video_stream_count"] == 1


def test_generate_video_soft_time_limit_marks_generation_failed(monkeypatch):
    failed = {}
    task = GenerateVideoTask()

    monkeypatch.setattr("app.workers.tasks.get_storage", lambda: object())
    monkeypatch.setattr(
        "app.workers.tasks.validate_generation_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(SoftTimeLimitExceeded()),
    )
    monkeypatch.setattr(
        "app.workers.tasks.mark_generation_failed",
        lambda db, job, error_code, error_message, **_kwargs: failed.update(
            {
                "generation_job_id": job.id,
                "error_code": error_code,
                "error_message": error_message,
            }
        ),
    )
    generation_job_id = uuid.uuid4()

    class FakeDb:
        def get(self, *_args, **_kwargs):
            return type("Job", (), {"id": generation_job_id, "organization_id": uuid.uuid4()})()

    class FakeSession:
        def __enter__(self):
            return FakeDb()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr("app.workers.tasks.worker_db_session", lambda: FakeSession())

    try:
        task.run_job(uuid.uuid4(), str(generation_job_id), str(uuid.uuid4()))
    except SoftTimeLimitExceeded:
        pass

    assert failed["generation_job_id"] == generation_job_id
    assert failed["error_code"] == "soft_time_limit_exceeded"
