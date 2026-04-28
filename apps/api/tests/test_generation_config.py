import uuid

from fastapi.testclient import TestClient

BASE = "/api/v1/projects"


def _create_project(client: TestClient) -> str:
    res = client.post(BASE + "/", json={"name": "Generation Project"})
    assert res.status_code == 201
    return res.json()["id"]


def test_get_generation_config_returns_defaults(client: TestClient):
    project_id = _create_project(client)

    res = client.get(f"{BASE}/{project_id}/generation-config")

    assert res.status_code == 200
    body = res.json()
    assert body["project_id"] == project_id
    assert body["id"] is None
    assert body["tts_provider"] == "gemini"
    assert body["video_provider"] == "wavespeed"
    assert body["resolution"] == "1920x1080"
    assert body["aspect_ratio"] == "16:9"
    assert body["gemini_api_key"] is None


def test_put_generation_config_masks_api_keys(client: TestClient):
    project_id = _create_project(client)

    res = client.put(
        f"{BASE}/{project_id}/generation-config",
        json={
            "tts_provider": "elevenlabs",
            "video_provider": "wavespeed",
            "voice_id": "voice-1",
            "voice_name": "Narrator",
            "gemini_api_key": "gemini-secret-1234",
            "elevenlabs_api_key": "eleven-secret-5678",
            "wavespeed_api_key": "wavespeed-secret-9999",
            "resolution": "1280x720",
            "aspect_ratio": "16:9",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["tts_provider"] == "elevenlabs"
    assert body["voice_id"] == "voice-1"
    assert body["voice_name"] == "Narrator"
    assert body["gemini_api_key"] == "********1234"
    assert body["elevenlabs_api_key"] == "********5678"
    assert body["wavespeed_api_key"] == "********9999"
    assert "secret" not in res.text


def test_put_generation_config_preserves_keys_when_omitted(client: TestClient):
    project_id = _create_project(client)
    client.put(
        f"{BASE}/{project_id}/generation-config",
        json={
            "gemini_api_key": "gemini-secret-1234",
            "elevenlabs_api_key": "eleven-secret-5678",
            "wavespeed_api_key": "wavespeed-secret-9999",
        },
    )

    res = client.put(
        f"{BASE}/{project_id}/generation-config",
        json={
            "tts_provider": "gemini",
            "video_provider": "wavespeed",
            "voice_id": "new-voice",
            "resolution": "1920x1080",
            "aspect_ratio": "9:16",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["voice_id"] == "new-voice"
    assert body["gemini_api_key"] == "********1234"
    assert body["elevenlabs_api_key"] == "********5678"
    assert body["wavespeed_api_key"] == "********9999"


def test_generation_config_project_not_found_returns_404(client: TestClient):
    res = client.get(f"{BASE}/{uuid.uuid4()}/generation-config")

    assert res.status_code == 404
    assert res.json()["detail"] == "Project not found"
