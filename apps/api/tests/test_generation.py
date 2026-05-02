from fastapi.testclient import TestClient

PROJECTS_BASE = "/api/v1/projects/"


def test_start_generation_requires_parsed_presentation(client: TestClient):
    project = client.post(PROJECTS_BASE, json={"name": "Generation Project"}).json()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "PRESENTATION_NOT_PARSED"


def test_video_settings_save_masks_keys_and_preserves_existing_keys(client: TestClient):
    project = client.post(PROJECTS_BASE, json={"name": "Video Settings Project"}).json()

    res = client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "elevenlabs_api_key": "elevenlabs-secret-1234",
            "elevenlabs_voice_id": "voice_abc",
            "wavespeed_api_key": "wavespeed-secret-9876",
        },
    )

    assert res.status_code == 200
    body = res.json()
    assert body["elevenlabs_api_key_masked"] == "************1234"
    assert body["wavespeed_api_key_masked"] == "************9876"
    assert body["elevenlabs_voice_id"] == "voice_abc"
    assert body["validation_status"] == "saved"
    assert "elevenlabs-secret" not in str(body)
    assert "wavespeed-secret" not in str(body)

    res = client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={"elevenlabs_voice_id": "voice_updated"},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["elevenlabs_api_key_masked"] == "************1234"
    assert body["wavespeed_api_key_masked"] == "************9876"
    assert body["elevenlabs_voice_id"] == "voice_updated"


def test_video_settings_validate_updates_status(client: TestClient):
    project = client.post(PROJECTS_BASE, json={"name": "Validate Video Settings"}).json()
    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "elevenlabs_api_key": "elevenlabs-secret-1234",
            "elevenlabs_voice_id": "voice_abc",
            "wavespeed_api_key": "wavespeed-secret-9876",
        },
    )

    res = client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")

    assert res.status_code == 200
    body = res.json()
    assert body["validation_status"] == "valid"
    assert body["elevenlabs_valid"] is True
    assert body["wavespeed_valid"] is True


def test_generation_status_is_idle_without_job(client: TestClient):
    project = client.post(PROJECTS_BASE, json={"name": "Idle Generation"}).json()

    res = client.get(f"/api/v1/projects/{project['id']}/generation-status")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "idle"
    assert body["progress"] == 0.0
    assert body["final_video_url"] is None
