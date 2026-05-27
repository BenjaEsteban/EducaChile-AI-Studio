from app.modules.video.adapters import (
    AvatarVideoProviderError,
    WavespeedAvatarVideoProvider,
    _poll_wavespeed_prediction,
)
from app.services.wavespeed_client import WavespeedClient, WavespeedClientError


def test_wavespeed_client_requires_api_key(monkeypatch):
    monkeypatch.setattr("app.services.wavespeed_client.settings.WAVESPEED_API_KEY", None)

    try:
        WavespeedClient(api_key=None)
    except WavespeedClientError as exc:
        assert exc.code == "MISSING_WAVESPEED_API_KEY"
    else:
        raise AssertionError("WavespeedClient should require an API key")


def test_wavespeed_client_rejects_invalid_duration(monkeypatch):
    monkeypatch.setattr("app.services.wavespeed_client.settings.WAVESPEED_API_KEY", "secret")
    client = WavespeedClient()

    try:
        client.create_talking_photo(
            image_url="https://example.test/avatar.png",
            text="hello",
            duration=4,
        )
    except WavespeedClientError as exc:
        assert exc.code == "INVALID_AVATAR_DURATION"
    else:
        raise AssertionError("Duration outside 5-15 seconds should be rejected")


def test_wavespeed_provider_raises_on_failed_prediction(monkeypatch):
    class FakeResponse:
        def __init__(self, payload=None, status_code=200):
            self._payload = payload or {}
            self.status_code = status_code

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, content=None, files=None, timeout=None):
        if url.endswith("/wavespeed-ai/ai-talking-photos"):
            return FakeResponse({"data": {"id": "request-123"}})
        return FakeResponse({"data": {"download_url": "https://wavespeed.test/uploaded.png"}})

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/predictions/request-123/result"):
            return FakeResponse({"data": {"status": "failed", "error": "boom"}})
        return FakeResponse()

    monkeypatch.setattr("app.services.wavespeed_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.wavespeed_client.httpx.get", fake_get)

    provider = WavespeedAvatarVideoProvider()
    try:
        provider.generate_avatar_video(
            image_url="https://wavespeed.test/uploaded.png",
            text="hello",
            duration=5,
            api_key="secret",
        )
    except AvatarVideoProviderError as exc:
        assert exc.code == "WAVESPEED_AVATAR_FAILED"
    else:
        raise AssertionError("Failed WaveSpeed predictions should raise an error")


def test_wavespeed_audio_lipsync_flow_uses_image_and_audio(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload=None, status_code=200, content=b"video-bytes"):
            self._payload = payload or {}
            self.status_code = status_code
            self.content = content

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, content=None, files=None, timeout=None):
        requests.append({"url": url, "headers": headers or {}, "json": json})
        if url.endswith("/wavespeed-ai/infinitetalk"):
            return FakeResponse({"data": {"id": "request-456"}})
        return FakeResponse({"data": {"download_url": "https://wavespeed.test/uploaded.png"}})

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/predictions/request-456/result"):
            return FakeResponse({"data": {"status": "completed", "outputs": ["https://cdn.test/out.mp4"]}})
        if url == "https://cdn.test/out.mp4":
            return FakeResponse(content=b"video-bytes")
        return FakeResponse()

    monkeypatch.setattr("app.services.wavespeed_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.wavespeed_client.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters.httpx.head", lambda *_args, **_kwargs: FakeResponse(status_code=200))
    monkeypatch.setattr("app.modules.video.adapters.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters._probe_duration", lambda *_args: 1.0)
    monkeypatch.setattr("app.modules.video.adapters._has_video_stream", lambda *_args: True)
    monkeypatch.setattr("app.modules.video.adapters._has_audio_stream", lambda *_args: True)
    monkeypatch.setattr("app.modules.video.adapters.settings.AVATAR_GENERATION_MODE", "infinitetalk_image")

    provider = WavespeedAvatarVideoProvider()
    clip = provider.generate_avatar_clip(
        audio_bytes=b"audio-bytes",
        duration_seconds=5,
        avatar_id="https://example.test/avatar.png",
        audio_url="https://example.test/audio.mp3",
        avatar_source_url="https://example.test/avatar.png",
        text="hola mundo",
        api_key="secret",
    )

    assert clip == b"video-bytes"
    assert requests[0]["url"].endswith("/wavespeed-ai/infinitetalk")
    assert requests[0]["json"]["image"] == "https://example.test/avatar.png"
    assert requests[0]["json"]["audio"] == "https://example.test/audio.mp3"
    assert "prompt" not in requests[0]["json"]


def test_wavespeed_image_audio_infinitetalk_mode_uses_image_and_audio(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload=None, status_code=200, content=b"video-bytes"):
            self._payload = payload or {}
            self.status_code = status_code
            self.content = content

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, content=None, files=None, timeout=None):
        requests.append({"url": url, "headers": headers or {}, "json": json})
        if url.endswith("/wavespeed-ai/infinitetalk"):
            return FakeResponse({"data": {"id": "request-456"}})
        return FakeResponse({"data": {"download_url": "https://wavespeed.test/uploaded.png"}})

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/predictions/request-456/result"):
            return FakeResponse({"data": {"status": "completed", "outputs": ["https://cdn.test/out.mp4"]}})
        if url == "https://cdn.test/out.mp4":
            return FakeResponse(content=b"video-bytes")
        return FakeResponse()

    monkeypatch.setattr("app.services.wavespeed_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.wavespeed_client.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters.httpx.head", lambda *_args, **_kwargs: FakeResponse(status_code=200))
    monkeypatch.setattr("app.modules.video.adapters.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters._probe_duration", lambda *_args: 1.0)
    monkeypatch.setattr("app.modules.video.adapters._has_video_stream", lambda *_args: True)
    monkeypatch.setattr("app.modules.video.adapters._has_audio_stream", lambda *_args: True)
    monkeypatch.setattr("app.modules.video.adapters.settings.AVATAR_GENERATION_MODE", "image_audio_infinitetalk")
    monkeypatch.setattr("app.modules.video.adapters.settings.AVATAR_IMAGE_AUDIO_PROVIDER", "wavespeed_infinitetalk_fast")
    monkeypatch.setattr("app.modules.video.adapters.settings.AVATAR_IMAGE_AUDIO_RESOLUTION", "480p")

    provider = WavespeedAvatarVideoProvider()
    clip = provider.generate_avatar_clip(
        audio_bytes=b"audio-bytes",
        duration_seconds=5,
        avatar_id="https://example.test/avatar.png",
        audio_url="https://example.test/audio.mp3",
        avatar_source_url="https://example.test/avatar.png",
        text="hola mundo",
        api_key="secret",
    )

    assert clip == b"video-bytes"
    assert requests[0]["url"].endswith("/wavespeed-ai/infinitetalk")
    assert requests[0]["json"]["image"] == "https://example.test/avatar.png"
    assert requests[0]["json"]["audio"] == "https://example.test/audio.mp3"
    assert requests[0]["json"]["resolution"] == "480p"
    assert "prompt" not in requests[0]["json"]


def test_wavespeed_sync_lipsync_flow_uses_video_and_audio(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload=None, status_code=200, content=b"video-bytes"):
            self._payload = payload or {}
            self.status_code = status_code
            self.content = content

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, content=None, files=None, timeout=None):
        requests.append({"url": url, "headers": headers or {}, "json": json, "content": content, "files": files})
        if url.endswith("/wavespeed-ai/sync-lipsync-3"):
            return FakeResponse({"data": {"id": "request-sync-123"}})
        if url.endswith("/media/upload/binary") and content == b"base-video-bytes":
            return FakeResponse({"data": {"download_url": "https://wavespeed.test/base-uploaded.mp4"}})
        if url.endswith("/media/upload/binary") and content == b"audio-bytes":
            return FakeResponse({"data": {"download_url": "https://wavespeed.test/audio-uploaded.mp3"}})
        return FakeResponse({"data": {"download_url": "https://wavespeed.test/uploaded.bin"}})

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/predictions/request-sync-123/result"):
            return FakeResponse({"data": {"status": "completed", "outputs": ["https://cdn.test/sync.mp4"]}})
        if url == "https://cdn.test/sync.mp4":
            return FakeResponse(content=b"video-bytes")
        return FakeResponse()

    monkeypatch.setattr("app.services.wavespeed_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.wavespeed_client.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters.httpx.head", lambda *_args, **_kwargs: FakeResponse(status_code=200))
    monkeypatch.setattr("app.modules.video.adapters.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters._probe_duration", lambda *_args: 10.0)
    monkeypatch.setattr("app.modules.video.adapters._has_video_stream", lambda *_args: True)
    monkeypatch.setattr("app.modules.video.adapters._has_audio_stream", lambda *_args: True)
    monkeypatch.setattr("app.modules.video.adapters.settings.AVATAR_GENERATION_MODE", "fast_lipsync")
    monkeypatch.setattr("app.modules.video.adapters.settings.AVATAR_LIPSYNC_PROVIDER", "wavespeed_sync_lipsync_3")

    provider = WavespeedAvatarVideoProvider()
    clip = provider.generate_avatar_video_from_base_video(
        base_video_url="https://example.test/base.mp4",
        audio_url="https://example.test/audio.mp3",
        duration=10,
        api_key="secret",
        base_video_bytes=b"base-video-bytes",
        audio_bytes=b"audio-bytes",
        base_video_filename="base.mp4",
        audio_filename="audio.mp3",
        base_video_content_type="video/mp4",
        audio_content_type="audio/mpeg",
        audio_duration_seconds=10.0,
    )

    assert clip == b"video-bytes"
    sync_request = next(item for item in requests if item["url"].endswith("/wavespeed-ai/sync-lipsync-3"))
    assert sync_request["json"]["video"] == "https://wavespeed.test/base-uploaded.mp4"
    assert sync_request["json"]["audio"] == "https://wavespeed.test/audio-uploaded.mp3"
    assert sync_request["json"]["sync_mode"] == "loop"
    assert "prompt" not in sync_request["json"]


def test_wavespeed_audio_lipsync_timeout_uses_audio_duration(monkeypatch):
    captured = {}

    class FakeClient:
        def create_infinite_talk(self, **kwargs):
            captured["create_kwargs"] = kwargs
            return "request-789"

    def fake_poll(*, client, request_id, timeout_seconds, poll_interval_seconds, audio_duration_seconds):
        captured["poll_kwargs"] = {
            "request_id": request_id,
            "timeout_seconds": timeout_seconds,
            "poll_interval_seconds": poll_interval_seconds,
            "audio_duration_seconds": audio_duration_seconds,
        }
        return "https://cdn.test/out.mp4"

    monkeypatch.setattr("app.modules.video.adapters.WavespeedClient", lambda api_key=None: FakeClient())
    monkeypatch.setattr("app.modules.video.adapters._poll_wavespeed_prediction", fake_poll)
    monkeypatch.setattr("app.modules.video.adapters.settings.WAVESPEED_PREDICTION_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr("app.modules.video.adapters.settings.WAVESPEED_POLL_INTERVAL_SECONDS", 8)
    monkeypatch.setattr("app.modules.video.adapters.httpx.head", lambda *_args, **_kwargs: FakeResponse(status_code=200))
    monkeypatch.setattr("app.modules.video.adapters.httpx.get", lambda *_args, **_kwargs: FakeResponse(content=b"video-bytes"))
    monkeypatch.setattr("app.modules.video.adapters._probe_duration", lambda *_args, **_kwargs: 1.0)
    monkeypatch.setattr("app.modules.video.adapters._has_video_stream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.modules.video.adapters._has_audio_stream", lambda *_args, **_kwargs: True)

    provider = WavespeedAvatarVideoProvider()
    provider.generate_avatar_video_from_audio(
        image_url="https://example.test/avatar.png",
        audio_url="https://example.test/audio.mp3",
        prompt="hola",
        api_key="secret",
        audio_duration_seconds=30.0,
    )

    assert captured["create_kwargs"]["image_url"] == "https://example.test/avatar.png"
    assert captured["create_kwargs"]["audio_url"] == "https://example.test/audio.mp3"
    assert captured["create_kwargs"]["resolution"] == "480p"
    assert captured["poll_kwargs"]["timeout_seconds"] == 600
    assert captured["poll_kwargs"]["audio_duration_seconds"] == 30.0


def test_wavespeed_audio_lipsync_retries_on_short_output(monkeypatch):
    post_calls = []

    class FakeResponse:
        def __init__(self, payload=None, status_code=200, content=b"video-bytes"):
            self._payload = payload or {}
            self.status_code = status_code
            self.content = content

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, content=None, files=None, timeout=None):
        post_calls.append({"url": url, "json": json})
        if url.endswith("/media/upload/binary"):
            if json is None and content == b"avatar-bytes":
                return FakeResponse({"data": {"download_url": "https://wavespeed.test/avatar-uploaded.png"}})
            if json is None and content == b"audio-bytes":
                return FakeResponse({"data": {"download_url": "https://wavespeed.test/audio-uploaded.mp3"}})
            if files:
                filename = files["file"][0]
                if filename.endswith(".png"):
                    return FakeResponse({"data": {"download_url": "https://wavespeed.test/avatar-uploaded.png"}})
                return FakeResponse({"data": {"download_url": "https://wavespeed.test/audio-uploaded.mp3"}})
        if url.endswith("/wavespeed-ai/infinitetalk"):
            request_id = f"request-{len([item for item in post_calls if item['url'].endswith('/wavespeed-ai/infinitetalk')])}"
            return FakeResponse({"data": {"id": request_id}})
        return FakeResponse()

    def fake_get(url, headers=None, timeout=None):
        if url.endswith("/predictions/request-1/result"):
            return FakeResponse({"data": {"status": "completed", "outputs": ["https://cdn.test/short.mp4"]}})
        if url.endswith("/predictions/request-2/result"):
            return FakeResponse({"data": {"status": "completed", "outputs": ["https://cdn.test/long.mp4"]}})
        if url.endswith("/short.mp4"):
            return FakeResponse(content=b"short-clip")
        if url.endswith("/long.mp4"):
            return FakeResponse(content=b"long-clip")
        return FakeResponse()

    monkeypatch.setattr("app.services.wavespeed_client.httpx.post", fake_post)
    monkeypatch.setattr("app.services.wavespeed_client.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters.httpx.head", lambda *_args, **_kwargs: FakeResponse(status_code=200))
    monkeypatch.setattr("app.modules.video.adapters.httpx.get", fake_get)
    monkeypatch.setattr("app.modules.video.adapters._probe_duration", lambda media_bytes, *_args, **_kwargs: 4.56 if media_bytes == b"short-clip" else 15.7)
    monkeypatch.setattr("app.modules.video.adapters._has_video_stream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.modules.video.adapters._has_audio_stream", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.modules.video.adapters.settings.WAVESPEED_PREDICTION_TIMEOUT_SECONDS", 60)
    monkeypatch.setattr("app.modules.video.adapters.settings.WAVESPEED_POLL_INTERVAL_SECONDS", 1)

    provider = WavespeedAvatarVideoProvider()
    clip = provider.generate_avatar_video_from_audio(
        image_url="https://example.test/avatar.png",
        audio_url="https://example.test/audio.mp3",
        api_key="secret",
        image_bytes=b"avatar-bytes",
        audio_bytes=b"audio-bytes",
        image_filename="avatar.png",
        image_content_type="image/png",
        audio_filename="chunk.mp3",
        audio_content_type="audio/mpeg",
        audio_duration_seconds=15.0,
    )

    assert clip == "https://cdn.test/long.mp4"
    assert len([item for item in post_calls if item["url"].endswith("/wavespeed-ai/infinitetalk")]) == 2


def test_wavespeed_audio_lipsync_rejects_private_urls(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code=403):
            self.status_code = status_code

    monkeypatch.setattr("app.modules.video.adapters.httpx.head", lambda *_args, **_kwargs: FakeResponse(status_code=403))
    monkeypatch.setattr("app.modules.video.adapters.httpx.get", lambda *_args, **_kwargs: FakeResponse(status_code=403))

    provider = WavespeedAvatarVideoProvider()
    try:
        provider.generate_avatar_video_from_audio(
            image_url="https://internal.example.test/avatar.png",
            audio_url="https://internal.example.test/audio.mp3",
            api_key="secret",
            audio_duration_seconds=15.0,
        )
    except AvatarVideoProviderError as exc:
        assert exc.code == "EXTERNAL_ASSET_URL_NOT_PUBLIC"
    else:
        raise AssertionError("Private URLs should be rejected for external provider access")


def test_wavespeed_polling_accepts_success_status_and_alt_output_field(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = 0

        def get_prediction_result(self, request_id):
            self.calls += 1
            return {"state": "success", "output": "https://cdn.test/out.mp4"}

    fake_client = FakeClient()
    monkeypatch.setattr("app.modules.video.adapters.time.sleep", lambda *_args, **_kwargs: None)
    result = _poll_wavespeed_prediction(
        client=fake_client,
        request_id="request-abc",
        timeout_seconds=10,
        poll_interval_seconds=1,
        audio_duration_seconds=12.0,
    )

    assert result == "https://cdn.test/out.mp4"
    assert fake_client.calls == 1
