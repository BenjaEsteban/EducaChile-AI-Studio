from datetime import UTC, datetime, timedelta
import uuid

from fastapi.testclient import TestClient

from app.modules.generation.models import GenerationJob
from app.modules.generation.repository import GenerationRepository
from app.modules.projects.models import Asset, Presentation, PresentationStatus, Slide
from app.modules.projects.service import MOCK_ORG_ID
from tests.fakes import InMemoryStorageProvider

PROJECTS_BASE = "/api/v1/projects/"


def test_start_generation_requires_parsed_presentation(client: TestClient):
    project = client.post(PROJECTS_BASE, json={"name": "Generation Project"}).json()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "PRESENTATION_NOT_PARSED"


def test_start_generation_accepts_slides_with_rendered_previews(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Preview Generation"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.DEBUG_AVATAR_SOURCE_URL",
        "https://public.example.test/avatar.png",
    )
    monkeypatch.setattr("app.modules.generation.service.app_settings.ALLOW_DUMMY_TTS", True)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.EXTERNAL_PROVIDER_ASSET_BASE_URL",
        "https://public.example.test",
    )
    monkeypatch.setattr(
        "app.modules.generation.service.enqueue_generate_video",
        lambda **_kwargs: "celery-task-1",
    )

    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "wavespeed_api_key": "wavespeed-secret-9876",
        },
    )
    client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")

    project_id = uuid.UUID(project["id"])
    presentation = Presentation(
        project_id=project_id,
        organization_id=MOCK_ORG_ID,
        title="Preview deck",
        original_filename="preview.pptx",
        storage_key="projects/preview.pptx",
        status=PresentationStatus.parsed,
        slide_count=2,
    )
    db_session.add(presentation)
    db_session.flush()
    for position in (1, 2):
        storage_key = f"presentations/{presentation.id}/previews/slide-{position}.png"
        storage.upload_file(storage_key, b"preview-bytes", "image/png")
        db_session.add(
            Slide(
                presentation_id=presentation.id,
                position=position,
                title=f"Slide {position}",
                notes=f"Notes {position}",
                thumbnail_key=storage_key,
                metadata_={
                    "dialogue": f"Dialogue {position}",
                    "visible_text": f"Visible {position}",
                    "rendered_image_key": storage_key,
                    "slide_preview": {
                        "asset_type": "slide_preview",
                        "storage_key": storage_key,
                        "render_source": "ppt_render",
                        "includes_text": True,
                    },
                },
            )
        )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 200
    body = res.json()
    assert body["generation_job"]["status"] == "queued"
    assert body["generation_job"]["progress_percentage"] == 0.0


def test_start_generation_uses_project_generation_config_for_elevenlabs_without_env_key(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Project Config TTS"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_API_KEY", None)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_VOICE_ID", None)
    monkeypatch.setattr(
        "app.modules.generation.service.enqueue_generate_video",
        lambda **_kwargs: "celery-task-1",
    )
    client.put(
        f"/api/v1/projects/{project['id']}/generation-config",
        json={
            "tts_provider": "elevenlabs",
            "video_provider": "wavespeed",
            "voice_id": "voice-project-1",
            "elevenlabs_api_key": "project-elevenlabs-secret",
            "wavespeed_api_key": "wavespeed-secret-1234",
        },
    )
    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "avatar_source_url": "https://public.example.test/avatar.png",
        },
    )

    project_id = uuid.UUID(project["id"])
    presentation = Presentation(
        project_id=project_id,
        organization_id=MOCK_ORG_ID,
        title="Project Config deck",
        original_filename="project-config.pptx",
        storage_key="projects/project-config.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    storage_key = f"presentations/{presentation.id}/previews/slide-1.png"
    storage.upload_file(storage_key, b"preview-bytes", "image/png")
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notas",
            thumbnail_key=storage_key,
            metadata_={
                "dialogue": "Diálogo",
                "rendered_image_key": storage_key,
                "slide_preview": {
                    "asset_type": "slide_preview",
                    "storage_key": storage_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                },
            },
        )
    )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 200
    assert res.json()["generation_job"]["status"] == "queued"


def test_video_settings_validate_and_generate_video_use_project_config_credentials(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Validate Then Generate"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_API_KEY", None)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_VOICE_ID", None)
    enqueue_called = {"called": False}

    def _enqueue_generate_video(**_kwargs):
        enqueue_called["called"] = True
        return "celery-task-validate-generate"

    monkeypatch.setattr("app.modules.generation.service.enqueue_generate_video", _enqueue_generate_video)

    client.put(
        f"/api/v1/projects/{project['id']}/generation-config",
        json={
            "tts_provider": "elevenlabs",
            "video_provider": "wavespeed",
            "voice_id": "voice-project-1",
            "elevenlabs_api_key": "project-elevenlabs-secret",
            "wavespeed_api_key": "wavespeed-secret-1234",
        },
    )
    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "avatar_source_url": "https://public.example.test/avatar.png",
        },
    )

    validate_res = client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")
    assert validate_res.status_code == 200
    validate_body = validate_res.json()
    assert validate_body["validation_status"] == "valid"
    assert validate_body["elevenlabs_valid"] is True

    project_id = uuid.UUID(project["id"])
    presentation = Presentation(
        project_id=project_id,
        organization_id=MOCK_ORG_ID,
        title="Validate Then Generate deck",
        original_filename="validate-generate.pptx",
        storage_key="projects/validate-generate.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    storage_key = f"presentations/{presentation.id}/previews/slide-1.png"
    storage.upload_file(storage_key, b"preview-bytes", "image/png")
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notas",
            thumbnail_key=storage_key,
            metadata_={
                "dialogue": "Diálogo",
                "rendered_image_key": storage_key,
                "slide_preview": {
                    "asset_type": "slide_preview",
                    "storage_key": storage_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                },
            },
        )
    )
    db_session.commit()

    generate_res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert generate_res.status_code == 200
    assert generate_res.json()["generation_job"]["status"] == "queued"
    assert enqueue_called["called"] is True


def test_video_settings_validate_uses_project_config_without_env_or_video_settings_row(
    client: TestClient,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Project Config Validate Only"}).json()
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_API_KEY", None)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_VOICE_ID", None)

    client.put(
        f"/api/v1/projects/{project['id']}/generation-config",
        json={
            "tts_provider": "elevenlabs",
            "video_provider": "wavespeed",
            "voice_id": "voice-project-1",
            "elevenlabs_api_key": "project-elevenlabs-secret",
            "wavespeed_api_key": "wavespeed-secret-1234",
        },
    )

    validate_res = client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")

    assert validate_res.status_code == 200
    validate_body = validate_res.json()
    assert validate_body["validation_status"] == "valid"
    assert validate_body["elevenlabs_valid"] is True


def test_generate_video_uses_saved_video_settings_credentials_without_env_key(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Video Settings Saved TTS"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_API_KEY", None)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_VOICE_ID", None)
    enqueue_called = {"called": False}

    def _enqueue_generate_video(**_kwargs):
        enqueue_called["called"] = True
        return "celery-task-video-settings"

    monkeypatch.setattr("app.modules.generation.service.enqueue_generate_video", _enqueue_generate_video)

    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "elevenlabs_api_key": "saved-video-settings-secret",
            "elevenlabs_voice_id": "voice-saved",
            "wavespeed_api_key": "wavespeed-secret-1234",
        },
    )
    validate_res = client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")
    assert validate_res.status_code == 200
    assert validate_res.json()["elevenlabs_valid"] is True

    project_id = uuid.UUID(project["id"])
    presentation = Presentation(
        project_id=project_id,
        organization_id=MOCK_ORG_ID,
        title="Video Settings Saved TTS deck",
        original_filename="video-settings-saved.pptx",
        storage_key="projects/video-settings-saved.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    storage_key = f"presentations/{presentation.id}/previews/slide-1.png"
    storage.upload_file(storage_key, b"preview-bytes", "image/png")
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notas",
            thumbnail_key=storage_key,
            metadata_={
                "dialogue": "Diálogo",
                "rendered_image_key": storage_key,
                "slide_preview": {
                    "asset_type": "slide_preview",
                    "storage_key": storage_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                },
            },
        )
    )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 200
    assert res.json()["generation_job"]["status"] == "queued"
    assert enqueue_called["called"] is True


def test_start_generation_uses_env_fallback_for_elevenlabs_when_project_config_missing(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Env TTS Fallback"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.generation.service.app_settings.TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_API_KEY", "env-elevenlabs-secret")
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_VOICE_ID", "env-voice-id")
    monkeypatch.setattr(
        "app.modules.generation.service.enqueue_generate_video",
        lambda **_kwargs: "celery-task-2",
    )
    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "wavespeed_api_key": "wavespeed-secret-1234",
            "avatar_source_url": "https://public.example.test/avatar.png",
        },
    )

    project_id = uuid.UUID(project["id"])
    presentation = Presentation(
        project_id=project_id,
        organization_id=MOCK_ORG_ID,
        title="Env Fallback deck",
        original_filename="env-fallback.pptx",
        storage_key="projects/env-fallback.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    storage_key = f"presentations/{presentation.id}/previews/slide-1.png"
    storage.upload_file(storage_key, b"preview-bytes", "image/png")
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notas",
            thumbnail_key=storage_key,
            metadata_={
                "dialogue": "Diálogo",
                "rendered_image_key": storage_key,
                "slide_preview": {
                    "asset_type": "slide_preview",
                    "storage_key": storage_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                },
            },
        )
    )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 200
    assert res.json()["generation_job"]["status"] == "queued"


def test_start_generation_logs_decrypt_failed_when_encryption_key_mismatch(
    client: TestClient,
    db_session,
    monkeypatch,
    caplog,
):
    project = client.post(PROJECTS_BASE, json={"name": "Decrypt Failure Project"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_API_KEY", None)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_VOICE_ID", None)
    monkeypatch.setattr(
        "app.modules.generation.service.enqueue_generate_video",
        lambda **_kwargs: "celery-task-4",
    )
    client.put(
        f"/api/v1/projects/{project['id']}/generation-config",
        json={
            "tts_provider": "elevenlabs",
            "video_provider": "wavespeed",
            "voice_id": "voice-project-1",
            "elevenlabs_api_key": "project-elevenlabs-secret",
            "wavespeed_api_key": "wavespeed-secret-1234",
        },
    )
    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "avatar_source_url": "https://public.example.test/avatar.png",
        },
    )

    project_id = uuid.UUID(project["id"])
    presentation = Presentation(
        project_id=project_id,
        organization_id=MOCK_ORG_ID,
        title="Decrypt Failure deck",
        original_filename="decrypt-failure.pptx",
        storage_key="projects/decrypt-failure.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    storage_key = f"presentations/{presentation.id}/previews/slide-1.png"
    storage.upload_file(storage_key, b"preview-bytes", "image/png")
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notas",
            thumbnail_key=storage_key,
            metadata_={
                "dialogue": "Diálogo",
                "rendered_image_key": storage_key,
                "slide_preview": {
                    "asset_type": "slide_preview",
                    "storage_key": storage_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                },
            },
        )
    )
    db_session.commit()

    monkeypatch.setattr("app.utils.crypto.settings.ENCRYPTION_KEY", "wrong-encryption-key")

    with caplog.at_level("INFO"):
        res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    assert "missing_reason=decrypt_failed" in caplog.text


def test_start_generation_rejects_missing_elevenlabs_credentials_with_project_config(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Missing TTS Project Config"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_API_KEY", None)
    monkeypatch.setattr("app.modules.generation.service.app_settings.ELEVENLABS_VOICE_ID", None)
    monkeypatch.setattr(
        "app.modules.generation.service.enqueue_generate_video",
        lambda **_kwargs: "celery-task-3",
    )
    client.put(
        f"/api/v1/projects/{project['id']}/generation-config",
        json={
            "tts_provider": "elevenlabs",
            "video_provider": "wavespeed",
            "wavespeed_api_key": "wavespeed-secret-1234",
        },
    )
    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "avatar_source_url": "https://public.example.test/avatar.png",
        },
    )

    presentation = Presentation(
        project_id=uuid.UUID(project["id"]),
        organization_id=MOCK_ORG_ID,
        title="Missing TTS deck",
        original_filename="missing-tts.pptx",
        storage_key="projects/missing-tts.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    storage_key = f"presentations/{presentation.id}/previews/slide-1.png"
    storage.upload_file(storage_key, b"preview-bytes", "image/png")
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notas",
            thumbnail_key=storage_key,
            metadata_={
                "dialogue": "Diálogo",
                "rendered_image_key": storage_key,
                "slide_preview": {
                    "asset_type": "slide_preview",
                    "storage_key": storage_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                },
            },
        )
    )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "MISSING_ELEVENLABS_API_KEY"
    # Credentials are now managed globally from the dashboard Configuración panel.
    assert "dashboard" in detail["message"]


def test_start_generation_rejects_dummy_tts_when_audio_lipsync_requires_real_tts(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Dummy TTS Guard"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.DEBUG_AVATAR_SOURCE_URL",
        "https://public.example.test/avatar.png",
    )
    monkeypatch.setattr("app.modules.generation.service.app_settings.ALLOW_DUMMY_TTS", False)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.EXTERNAL_PROVIDER_ASSET_BASE_URL",
        "https://public.example.test",
    )

    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "wavespeed_api_key": "wavespeed-secret-9876",
        },
    )
    client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")

    presentation = Presentation(
        project_id=uuid.UUID(project["id"]),
        organization_id=MOCK_ORG_ID,
        title="Dummy TTS deck",
        original_filename="dummy-tts.pptx",
        storage_key="projects/dummy-tts.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    storage_key = f"presentations/{presentation.id}/previews/slide-1.png"
    storage.upload_file(storage_key, b"preview-bytes", "image/png")
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notes 1",
            thumbnail_key=storage_key,
            metadata_={
                "dialogue": "Dialogue 1",
                "rendered_image_key": storage_key,
                "slide_preview": {
                    "asset_type": "slide_preview",
                    "storage_key": storage_key,
                    "render_source": "ppt_render",
                    "includes_text": True,
                },
            },
        )
    )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "MISSING_TTS_PROVIDER"
    assert "real TTS provider" in detail["message"]


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


def test_video_settings_validate_updates_status(client: TestClient, monkeypatch):
    project = client.post(PROJECTS_BASE, json={"name": "Validate Video Settings"}).json()
    monkeypatch.setattr("app.modules.generation.service.app_settings.ALLOW_DUMMY_TTS", True)
    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "wavespeed_api_key": "wavespeed-secret-9876",
        },
    )

    res = client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")

    assert res.status_code == 200
    body = res.json()
    assert body["validation_status"] == "valid"
    assert body["wavespeed_valid"] is True


def test_generation_status_is_idle_without_job(client: TestClient):
    project = client.post(PROJECTS_BASE, json={"name": "Idle Generation"}).json()

    res = client.get(f"/api/v1/projects/{project['id']}/generation-status")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "idle"
    assert body["progress"] == 0.0
    assert body["final_video_url"] is None


def test_generation_status_uses_latest_job_final_asset(client: TestClient, db_session, monkeypatch):
    project = client.post(PROJECTS_BASE, json={"name": "Latest Final Video"}).json()
    storage = InMemoryStorageProvider()
    storage.upload_file("projects/output/final_first.mp4", b"first", "video/mp4")
    storage.upload_file("projects/output/final_other.mp4", b"other", "video/mp4")
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)

    final_asset = Asset(
        organization_id=MOCK_ORG_ID,
        project_id=project["id"],
        slide_id=None,
        asset_type="final_video",
        storage_key="projects/output/final_first.mp4",
        filename="final_first.mp4",
        mime_type="video/mp4",
        size_bytes=5,
    )
    unrelated_final_asset = Asset(
        organization_id=MOCK_ORG_ID,
        project_id=project["id"],
        slide_id=None,
        asset_type="final_video",
        storage_key="projects/output/final_other.mp4",
        filename="final_other.mp4",
        mime_type="video/mp4",
        size_bytes=5,
    )
    db_session.add_all([final_asset, unrelated_final_asset])
    db_session.flush()
    db_session.add(
        GenerationJob(
            organization_id=MOCK_ORG_ID,
            project_id=project["id"],
            status="completed",
            progress_percentage=100.0,
            current_step="Completed",
            final_asset_id=final_asset.id,
        )
    )
    db_session.commit()

    res = client.get(f"/api/v1/projects/{project['id']}/generation-status")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "completed"
    assert body["final_video_url"].endswith("projects/output/final_first.mp4")


def test_running_generation_job_only_blocks_active_statuses(client: TestClient, db_session):
    project = client.post(PROJECTS_BASE, json={"name": "Running Generation"}).json()
    repo = GenerationRepository(db_session)

    db_session.add_all(
        [
            GenerationJob(
                organization_id=MOCK_ORG_ID,
                project_id=project["id"],
                status="completed",
                progress_percentage=100.0,
            ),
            GenerationJob(
                organization_id=MOCK_ORG_ID,
                project_id=project["id"],
                status="failed",
                progress_percentage=25.0,
            ),
        ]
    )
    db_session.commit()

    assert repo.get_running_generation_job(project["id"], MOCK_ORG_ID) is None

    queued = GenerationJob(
        organization_id=MOCK_ORG_ID,
        project_id=project["id"],
        status="queued",
        progress_percentage=0.0,
    )
    db_session.add(queued)
    db_session.commit()

    assert repo.get_running_generation_job(project["id"], MOCK_ORG_ID).id == queued.id


def test_generation_status_marks_stale_running_job_failed(
    client: TestClient,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.GENERATION_STALLED_AFTER_SECONDS",
        300,
    )
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.PROVIDER_OPERATION_STALLED_AFTER_SECONDS",
        300,
    )
    project = client.post(PROJECTS_BASE, json={"name": "Stale Generation"}).json()
    stale_time = datetime.now(UTC) - timedelta(minutes=6)
    stale_job = GenerationJob(
        organization_id=MOCK_ORG_ID,
        project_id=project["id"],
        status="generating_avatar",
        progress_percentage=35.0,
        current_step="Generating avatar clip for slide 1",
        updated_at=stale_time,
    )
    db_session.add(stale_job)
    db_session.commit()

    res = client.get(f"/api/v1/projects/{project['id']}/generation-status")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "GENERATION_JOB_STALLED"
    assert "Please try again" in body["error_message"]


def test_start_generation_returns_structured_conflict_for_active_job(
    client: TestClient,
    db_session,
):
    project = client.post(PROJECTS_BASE, json={"name": "Active Generation Conflict"}).json()
    active_job = GenerationJob(
        organization_id=MOCK_ORG_ID,
        project_id=project["id"],
        status="generating_avatar",
        progress_percentage=35.0,
        current_step="Generating avatar clip for slide 1",
        updated_at=datetime.now(UTC),
    )
    db_session.add(active_job)
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error_code"] == "GENERATION_ALREADY_RUNNING"
    assert detail["job_id"] == str(active_job.id)
    assert detail["status"] == "generating_avatar"
    assert detail["updated_at"] is not None


def test_start_generation_marks_stale_job_failed_before_conflict(
    client: TestClient,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.GENERATION_STALLED_AFTER_SECONDS",
        300,
    )
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.PROVIDER_OPERATION_STALLED_AFTER_SECONDS",
        300,
    )
    project = client.post(PROJECTS_BASE, json={"name": "Stale Generate Retry"}).json()
    stale_job = GenerationJob(
        organization_id=MOCK_ORG_ID,
        project_id=project["id"],
        status="generating_avatar",
        progress_percentage=35.0,
        current_step="Generating avatar clip for slide 1",
        updated_at=datetime.now(UTC) - timedelta(minutes=6),
    )
    db_session.add(stale_job)
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "PRESENTATION_NOT_PARSED"
    db_session.expire_all()
    refreshed_job = db_session.get(GenerationJob, stale_job.id)
    assert refreshed_job.status == "failed"
    assert refreshed_job.error_code == "GENERATION_JOB_STALLED"


def test_generation_status_keeps_active_job_when_heartbeat_is_fresh(
    client: TestClient,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.GENERATION_STALLED_AFTER_SECONDS",
        300,
    )
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.PROVIDER_OPERATION_STALLED_AFTER_SECONDS",
        300,
    )
    project = client.post(PROJECTS_BASE, json={"name": "Fresh Heartbeat"}).json()
    active_job = GenerationJob(
        organization_id=MOCK_ORG_ID,
        project_id=project["id"],
        status="generating_avatar",
        progress_percentage=25.0,
        current_step="Polling Wavespeed for slide 1 of 5, chunk 2 of 12",
        updated_at=datetime.now(UTC) - timedelta(seconds=30),
    )
    db_session.add(active_job)
    db_session.commit()

    res = client.get(f"/api/v1/projects/{project['id']}/generation-status")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "generating_avatar"
    assert body["error_code"] is None
    assert body["message"] == active_job.current_step


def test_start_generation_rejects_only_when_preview_images_are_missing(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Missing Preview Generation"}).json()
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.DEBUG_AVATAR_SOURCE_URL",
        "https://public.example.test/avatar.png",
    )
    monkeypatch.setattr("app.modules.generation.service.app_settings.ALLOW_DUMMY_TTS", True)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.EXTERNAL_PROVIDER_ASSET_BASE_URL",
        "https://public.example.test",
    )

    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "wavespeed_api_key": "wavespeed-secret-9876",
        },
    )
    client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")

    presentation = Presentation(
        project_id=uuid.UUID(project["id"]),
        organization_id=MOCK_ORG_ID,
        title="Missing preview deck",
        original_filename="missing-preview.pptx",
        storage_key="projects/missing-preview.pptx",
        status=PresentationStatus.parsed,
        slide_count=2,
    )
    db_session.add(presentation)
    db_session.flush()
    db_session.add_all(
        [
            Slide(
                presentation_id=presentation.id,
                position=1,
                title="Slide 1",
                notes="Notes 1",
                metadata_={"dialogue": "Dialogue 1"},
            ),
            Slide(
                presentation_id=presentation.id,
                position=2,
                title="Slide 2",
                notes="Notes 2",
                metadata_={"dialogue": "Dialogue 2"},
            ),
        ]
    )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "MISSING_RENDERED_PREVIEW"
    assert "Slides missing rendered preview image" in detail["message"]


def test_start_generation_can_regenerate_missing_previews_from_pptx(
    client: TestClient,
    db_session,
    monkeypatch,
):
    project = client.post(PROJECTS_BASE, json={"name": "Regenerate Previews"}).json()
    storage = InMemoryStorageProvider()
    storage.upload_file("projects/regenerate.pptx", b"pptx-bytes", "application/vnd.ms-powerpoint")
    monkeypatch.setattr("app.modules.generation.service.get_storage", lambda: storage)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.DEBUG_AVATAR_SOURCE_URL",
        "https://public.example.test/avatar.png",
    )
    monkeypatch.setattr("app.modules.generation.service.app_settings.ALLOW_DUMMY_TTS", True)
    monkeypatch.setattr(
        "app.modules.generation.service.app_settings.EXTERNAL_PROVIDER_ASSET_BASE_URL",
        "https://public.example.test",
    )
    monkeypatch.setattr(
        "app.modules.generation.service.enqueue_generate_video",
        lambda **_kwargs: "celery-task-1",
    )

    def fake_render_slide_previews(*, pptx_bytes, presentation_id, original_filename, storage):
        key = f"presentations/{presentation_id}/previews/slide-1.png"
        storage.upload_file(key, b"preview-bytes", "image/png")
        return {1: key}

    monkeypatch.setattr(
        "app.modules.generation.service.render_slide_previews",
        fake_render_slide_previews,
    )

    client.put(
        f"/api/v1/projects/{project['id']}/video-settings",
        json={
            "wavespeed_api_key": "wavespeed-secret-9876",
        },
    )
    client.post(f"/api/v1/projects/{project['id']}/video-settings/validate")

    presentation = Presentation(
        project_id=uuid.UUID(project["id"]),
        organization_id=MOCK_ORG_ID,
        title="Regenerate preview deck",
        original_filename="regenerate.pptx",
        storage_key="projects/regenerate.pptx",
        status=PresentationStatus.parsed,
        slide_count=1,
    )
    db_session.add(presentation)
    db_session.flush()
    db_session.add(
        Slide(
            presentation_id=presentation.id,
            position=1,
            title="Slide 1",
            notes="Notes 1",
            metadata_={"dialogue": "Dialogue 1"},
        )
    )
    db_session.commit()

    res = client.post(f"/api/v1/projects/{project['id']}/generate-video")

    assert res.status_code == 200
    assert res.json()["generation_job"]["status"] == "queued"
