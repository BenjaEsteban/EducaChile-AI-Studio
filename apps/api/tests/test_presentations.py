import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.storage import get_storage
from tests.fakes import InMemoryStorageProvider

PROJECTS_BASE = "/api/v1/projects"
PRESENTATIONS_BASE = "/api/v1/presentations"

PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
PPT_CONTENT_TYPE = "application/vnd.ms-powerpoint"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def storage_provider():
    return InMemoryStorageProvider()


@pytest.fixture
def client(storage_provider):
    from app.database import get_db
    from tests.conftest import override_get_db
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = lambda: storage_provider
    with patch(
        "app.modules.presentations.service.enqueue_parse_presentation",
        return_value="mock-celery-id",
    ):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


def _create_project(client: TestClient, name: str = "Test Project") -> str:
    res = client.post(PROJECTS_BASE + "/", json={"name": name})
    assert res.status_code == 201
    return res.json()["id"]


# ── POST /projects/{id}/presentations/init-upload ─────────────────────────────

def test_init_upload_returns_201_with_url(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "clase1.pptx", "content_type": PPTX_CONTENT_TYPE},
    )
    assert res.status_code == 201
    body = res.json()
    assert uuid.UUID(body["presentation_id"])
    assert body["upload_url"].startswith("http://minio-test/upload/")
    assert "clase1.pptx" in body["storage_key"]
    assert body["expires_in"] == 3600
    assert body["method"] == "PUT"


def test_init_upload_accepts_ppt(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "old_format.ppt", "content_type": PPT_CONTENT_TYPE},
    )
    assert res.status_code == 201


def test_init_upload_accepts_octet_stream_content_type(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "deck.pptx", "content_type": "application/octet-stream"},
    )
    assert res.status_code == 201


def test_init_upload_storage_key_has_org_project_structure(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "deck.pptx"},
    )
    key = res.json()["storage_key"]
    parts = key.split("/")
    # estructura: {org_id}/{project_id}/{prefix}_{filename}
    assert len(parts) == 3
    assert parts[1] == project_id
    assert parts[2].endswith("deck.pptx")


def test_init_upload_creates_presentation_with_upload_pending_status(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "deck.pptx"},
    )
    assert res.status_code == 201
    # El presentation_id debe ser un UUID válido — la presentación fue creada en DB
    presentation_id = res.json()["presentation_id"]
    assert uuid.UUID(presentation_id)


def test_init_upload_rejects_pdf(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "document.pdf"},
    )
    assert res.status_code == 422
    assert "ppt" in res.json()["detail"][0]["msg"].lower()


def test_init_upload_rejects_mp4(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "video.mp4"},
    )
    assert res.status_code == 422


def test_init_upload_rejects_no_extension(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "nodotfile"},
    )
    assert res.status_code == 422


def test_init_upload_rejects_invalid_content_type(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "deck.pptx", "content_type": "text/html"},
    )
    assert res.status_code == 422


def test_init_upload_rejects_path_traversal(client):
    project_id = _create_project(client)
    res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "../secret.pptx"},
    )
    assert res.status_code == 422


def test_init_upload_project_not_found_returns_404(client):
    res = client.post(
        f"{PROJECTS_BASE}/{uuid.uuid4()}/presentations/init-upload",
        json={"filename": "deck.pptx"},
    )
    assert res.status_code == 404
    assert res.json()["detail"] == "Project not found"


# ── POST /presentations/{id}/confirm-upload ───────────────────────────────────

def test_confirm_upload_returns_200_with_job(client):
    project_id = _create_project(client)
    init_res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "deck.pptx"},
    )
    presentation_id = init_res.json()["presentation_id"]

    res = client.post(f"{PRESENTATIONS_BASE}/{presentation_id}/confirm-upload")
    assert res.status_code == 200
    body = res.json()
    assert body["presentation_id"] == presentation_id
    assert body["status"] == "uploaded"
    assert uuid.UUID(body["job_id"])


def test_confirm_upload_creates_parse_job(client):
    project_id = _create_project(client)
    init_res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "deck.pptx"},
    )
    presentation_id = init_res.json()["presentation_id"]
    confirm_res = client.post(f"{PRESENTATIONS_BASE}/{presentation_id}/confirm-upload")

    job_id = confirm_res.json()["job_id"]
    # Verificar que el job existe consultando el endpoint de jobs
    job_res = client.get(f"/api/v1/jobs/{job_id}")
    assert job_res.status_code == 200
    job = job_res.json()
    assert job["job_type"] == "parse_presentation"
    assert job["status"] == "queued"
    assert job["presentation_id"] == presentation_id


def test_confirm_upload_not_found_returns_404(client):
    res = client.post(f"{PRESENTATIONS_BASE}/{uuid.uuid4()}/confirm-upload")
    assert res.status_code == 404
    assert res.json()["detail"] == "Presentation not found"


def test_confirm_upload_twice_returns_409(client):
    project_id = _create_project(client)
    init_res = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "deck.pptx"},
    )
    presentation_id = init_res.json()["presentation_id"]

    client.post(f"{PRESENTATIONS_BASE}/{presentation_id}/confirm-upload")
    res = client.post(f"{PRESENTATIONS_BASE}/{presentation_id}/confirm-upload")
    assert res.status_code == 409
    assert "upload_pending" in res.json()["detail"]


def test_full_flow_init_then_confirm(client):
    """Flujo completo: crear proyecto → init upload → confirm upload → job creado."""
    project_id = _create_project(client, "Proyecto Biología")

    # 1. Init upload
    init = client.post(
        f"{PROJECTS_BASE}/{project_id}/presentations/init-upload",
        json={"filename": "biologia_clase1.pptx", "content_type": PPTX_CONTENT_TYPE},
    )
    assert init.status_code == 201
    presentation_id = init.json()["presentation_id"]
    assert "biologia_clase1.pptx" in init.json()["storage_key"]

    # 2. Confirm upload
    confirm = client.post(f"{PRESENTATIONS_BASE}/{presentation_id}/confirm-upload")
    assert confirm.status_code == 200
    assert confirm.json()["status"] == "uploaded"

    # 3. Job encolado
    job_id = confirm.json()["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "queued"
    assert job["job_type"] == "parse_presentation"
    assert job["project_id"] == project_id
