from app.modules.video.adapters import AvatarVideoProviderError, WavespeedAvatarVideoProvider
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
