import uuid

from fastapi.testclient import TestClient

from app.modules.generation.models import GenerationJob
from app.modules.projects.models import Asset, Folder, Presentation, PresentationStatus, Project, Slide
from app.modules.projects.service import MOCK_ORG_ID
from tests.fakes import InMemoryStorageProvider

BASE = "/api/v1/projects"


# ── helpers ───────────────────────────────────────────────────────────────────

def _create(client: TestClient, name: str = "Test Project", description: str | None = None):
    payload = {"name": name}
    if description:
        payload["description"] = description
    return client.post(BASE + "/", json=payload)


# ── POST /projects ────────────────────────────────────────────────────────────

def test_create_project_returns_201(client):
    res = _create(client, "Mi Proyecto")
    assert res.status_code == 201
    body = res.json()
    assert body["name"] == "Mi Proyecto"
    assert body["status"] == "active"
    assert body["description"] is None
    assert uuid.UUID(body["id"])  # UUID válido


def test_create_project_with_description(client):
    res = _create(client, "Proyecto con desc", "Una descripción")
    assert res.status_code == 201
    assert res.json()["description"] == "Una descripción"


def test_create_project_missing_name_returns_422(client):
    res = client.post(BASE + "/", json={})
    assert res.status_code == 422


# ── GET /projects ─────────────────────────────────────────────────────────────

def test_list_projects_empty(client):
    res = client.get(BASE + "/")
    assert res.status_code == 200
    body = res.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_projects_returns_created(client):
    _create(client, "P1")
    _create(client, "P2")
    res = client.get(BASE + "/")
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2


def test_list_projects_pagination(client):
    for i in range(5):
        _create(client, f"Project {i}")
    res = client.get(BASE + "/", params={"skip": 2, "limit": 2})
    body = res.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["skip"] == 2
    assert body["limit"] == 2


def test_list_projects_invalid_limit_returns_422(client):
    res = client.get(BASE + "/", params={"limit": 200})
    assert res.status_code == 422


# ── GET /projects/{id} ────────────────────────────────────────────────────────

def test_get_project_returns_200(client):
    project_id = _create(client, "Detalle").json()["id"]
    res = client.get(f"{BASE}/{project_id}")
    assert res.status_code == 200
    assert res.json()["id"] == project_id


def test_get_project_not_found_returns_404(client):
    res = client.get(f"{BASE}/{uuid.uuid4()}")
    assert res.status_code == 404
    assert res.json()["detail"] == "Project not found"


def test_get_project_open_state_without_presentation(client):
    project_id = _create(client, "Video sin deck").json()["id"]

    res = client.get(f"{BASE}/{project_id}/open-state")

    assert res.status_code == 200
    body = res.json()
    assert body["project_id"] == project_id
    assert body["has_presentation"] is False
    assert body["presentation_id"] is None
    assert body["slide_count"] == 0
    assert body["has_slides"] is False
    assert body["has_generated_video"] is False
    assert body["generated_video_url"] is None


def test_get_project_open_state_with_presentation_and_final_video(client, db_session):
    project_payload = _create(client, "Video con estado").json()
    project = db_session.get(Project, uuid.UUID(project_payload["id"]))
    assert project is not None

    presentation = Presentation(
        project_id=project.id,
        organization_id=project.organization_id,
        title="Deck existente",
        original_filename="deck.pptx",
        storage_key="projects/deck.pptx",
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
                notes=None,
                thumbnail_key=None,
                duration_seconds=None,
                metadata_=None,
            ),
            Slide(
                presentation_id=presentation.id,
                position=2,
                title="Slide 2",
                notes=None,
                thumbnail_key=None,
                duration_seconds=None,
                metadata_=None,
            ),
        ]
    )

    final_asset = Asset(
        organization_id=project.organization_id,
        project_id=project.id,
        slide_id=None,
        asset_type="final_video",
        storage_key=f"projects/{project.id}/final.mp4",
        filename="final.mp4",
        mime_type="video/mp4",
        size_bytes=1024,
    )
    db_session.add(final_asset)
    db_session.flush()

    generation_job = GenerationJob(
        organization_id=project.organization_id,
        project_id=project.id,
        status="completed",
        progress_percentage=100.0,
        final_asset_id=final_asset.id,
    )
    db_session.add(generation_job)
    db_session.commit()

    res = client.get(f"{BASE}/{project.id}/open-state")

    assert res.status_code == 200
    body = res.json()
    assert body["has_presentation"] is True
    assert body["presentation_id"] == str(presentation.id)
    assert body["presentation_status"] == "parsed"
    assert body["slide_count"] == 2
    assert body["has_slides"] is True
    assert body["has_generated_video"] is True
    assert body["generated_video_asset_id"] == str(final_asset.id)
    assert body["generated_video_url"] is not None
    assert body["latest_generation_job_id"] == str(generation_job.id)
    assert body["latest_generation_status"] == "completed"


# ── PATCH /projects/{id} ──────────────────────────────────────────────────────

def test_update_project_name(client):
    project_id = _create(client, "Original").json()["id"]
    res = client.patch(f"{BASE}/{project_id}", json={"name": "Actualizado"})
    assert res.status_code == 200
    assert res.json()["name"] == "Actualizado"


def test_update_project_status_to_archived(client):
    project_id = _create(client).json()["id"]
    res = client.patch(f"{BASE}/{project_id}", json={"status": "archived"})
    assert res.status_code == 200
    assert res.json()["status"] == "archived"


def test_update_project_partial_only_changes_sent_fields(client):
    project_id = _create(client, "Nombre", "Desc original").json()["id"]
    res = client.patch(f"{BASE}/{project_id}", json={"name": "Nuevo nombre"})
    body = res.json()
    assert body["name"] == "Nuevo nombre"
    assert body["description"] == "Desc original"  # no tocado


def test_update_project_empty_name_returns_422(client):
    project_id = _create(client).json()["id"]
    res = client.patch(f"{BASE}/{project_id}", json={"name": "   "})
    assert res.status_code == 422


def test_update_project_not_found_returns_404(client):
    res = client.patch(f"{BASE}/{uuid.uuid4()}", json={"name": "X"})
    assert res.status_code == 404


def test_update_project_invalid_status_returns_422(client):
    project_id = _create(client).json()["id"]
    res = client.patch(f"{BASE}/{project_id}", json={"status": "invalid_status"})
    assert res.status_code == 422


# ── DELETE /projects/{id} ─────────────────────────────────────────────────────

def test_delete_project_returns_204(client):
    project_id = _create(client).json()["id"]
    res = client.delete(f"{BASE}/{project_id}")
    assert res.status_code == 204
    assert res.content == b""  # sin body


def test_delete_project_removes_from_list(client):
    project_id = _create(client).json()["id"]
    client.delete(f"{BASE}/{project_id}")
    res = client.get(BASE + "/")
    assert res.json()["total"] == 0


def test_delete_project_not_found_returns_404(client):
    res = client.delete(f"{BASE}/{uuid.uuid4()}")
    assert res.status_code == 404


def test_delete_project_twice_returns_404(client):
    project_id = _create(client).json()["id"]
    client.delete(f"{BASE}/{project_id}")
    res = client.delete(f"{BASE}/{project_id}")
    assert res.status_code == 404


def test_get_avatar_missing_returns_404(client):
    project_id = _create(client).json()["id"]

    res = client.get(f"{BASE}/{project_id}/avatar")

    assert res.status_code == 404
    assert res.json()["detail"] == "Avatar not configured for this project."


def test_upload_avatar_success(client, monkeypatch):
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.projects.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.projects.router.get_storage", lambda: storage)
    project_id = _create(client).json()["id"]

    res = client.post(
        f"{BASE}/{project_id}/avatar",
        files={"file": ("avatar.png", b"png-bytes", "image/png")},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["object_key"].startswith(f"projects/{project_id}/avatar/")
    assert body["filename"] == "avatar.png"
    assert body["content_type"] == "image/png"
    assert body["url"].startswith("http://minio-test/download/")
    assert body["width"] == 160.0
    assert storage.download_file(body["object_key"]) == b"png-bytes"


def test_upload_avatar_rejects_invalid_content_type(client):
    project_id = _create(client).json()["id"]

    res = client.post(
        f"{BASE}/{project_id}/avatar",
        files={"file": ("avatar.txt", b"text", "text/plain")},
    )

    assert res.status_code == 400
    assert res.json()["detail"]["code"] == "INVALID_AVATAR_MIME_TYPE"


def test_get_avatar_returns_metadata_and_url(client, monkeypatch):
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.projects.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.projects.router.get_storage", lambda: storage)
    project_id = _create(client).json()["id"]
    upload = client.post(
        f"{BASE}/{project_id}/avatar",
        files={"file": ("avatar.webp", b"webp-bytes", "image/webp")},
    ).json()

    res = client.get(f"{BASE}/{project_id}/avatar")

    assert res.status_code == 200
    body = res.json()
    assert body["object_key"] == upload["object_key"]
    assert body["url"].startswith("http://minio-test/download/")


def test_patch_avatar_layout_saves_values(client, monkeypatch):
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.projects.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.projects.router.get_storage", lambda: storage)
    project_id = _create(client).json()["id"]
    client.post(
        f"{BASE}/{project_id}/avatar",
        files={"file": ("avatar.jpg", b"jpg-bytes", "image/jpeg")},
    )

    res = client.patch(
        f"{BASE}/{project_id}/avatar/layout",
        json={"x": 12, "y": 34, "width": 200, "height": 180},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["x"] == 12
    assert body["y"] == 34
    assert body["width"] == 200
    assert body["height"] == 180


def test_create_folder_and_subfolder_and_list_tree(client):
    root = client.post(f"{BASE}/folders", json={"name": "Cursos"}).json()
    child = client.post(
        f"{BASE}/folders",
        json={"name": "Matemáticas", "parent_folder_id": root["id"]},
    ).json()

    res = client.get(f"{BASE}/folders/tree")

    assert res.status_code == 200
    body = res.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == root["id"]
    assert len(body["items"][0]["children"]) == 1
    assert body["items"][0]["children"][0]["id"] == child["id"]


def test_create_project_inside_folder_and_move_between_folders(client):
    folder_a = client.post(f"{BASE}/folders", json={"name": "Semestre 1"}).json()
    folder_b = client.post(f"{BASE}/folders", json={"name": "Semestre 2"}).json()
    project = client.post(
        f"{BASE}/",
        json={"name": "Álgebra", "folder_id": folder_a["id"]},
    ).json()
    assert project["folder_id"] == folder_a["id"]

    move = client.post(
        f"{BASE}/{project['id']}/move",
        json={"folder_id": folder_b["id"]},
    )
    assert move.status_code == 200
    assert move.json()["folder_id"] == folder_b["id"]


def test_create_project_without_folder_assigns_default_sin_nombre_folder(client):
    project_res = client.post(f"{BASE}/", json={"name": "Video sin carpeta"})
    assert project_res.status_code == 201
    project = project_res.json()
    assert project["folder_id"] is not None

    tree_res = client.get(f"{BASE}/folders/tree")
    assert tree_res.status_code == 200
    root_names = {item["name"] for item in tree_res.json()["items"]}
    assert "Sin Nombre" in root_names


def test_move_project_to_null_assigns_default_sin_nombre_folder(client):
    folder = client.post(f"{BASE}/folders", json={"name": "Temporal"}).json()
    project = client.post(
        f"{BASE}/",
        json={"name": "Video para mover", "folder_id": folder["id"]},
    ).json()

    move = client.post(f"{BASE}/{project['id']}/move", json={"folder_id": None})
    assert move.status_code == 200
    moved = move.json()
    assert moved["folder_id"] is not None

    tree_res = client.get(f"{BASE}/folders/tree")
    assert tree_res.status_code == 200
    default_folder = next(
        item for item in tree_res.json()["items"] if item["name"] == "Sin Nombre"
    )
    assert moved["folder_id"] == default_folder["id"]


def test_default_sin_nombre_folder_is_reused_without_duplicates(client):
    first = client.post(f"{BASE}/", json={"name": "Video 1"}).json()
    second = client.post(f"{BASE}/", json={"name": "Video 2"}).json()
    assert first["folder_id"] == second["folder_id"]

    tree_res = client.get(f"{BASE}/folders/tree")
    assert tree_res.status_code == 200
    sin_nombre = [item for item in tree_res.json()["items"] if item["name"] == "Sin Nombre"]
    assert len(sin_nombre) == 1


def test_default_sin_nombre_folder_consolidates_existing_duplicates(client, db_session):
    duplicate_a = Folder(
        organization_id=MOCK_ORG_ID,
        parent_folder_id=None,
        name="Sin Nombre",
    )
    duplicate_b = Folder(
        organization_id=MOCK_ORG_ID,
        parent_folder_id=None,
        name="sin nombre",
    )
    db_session.add_all([duplicate_a, duplicate_b])
    db_session.commit()
    db_session.refresh(duplicate_a)
    db_session.refresh(duplicate_b)

    project = client.post(
        f"{BASE}/",
        json={"name": "Video en duplicado", "folder_id": str(duplicate_b.id)},
    ).json()
    assert project["folder_id"] == str(duplicate_b.id)

    tree_res = client.get(f"{BASE}/folders/tree")
    assert tree_res.status_code == 200
    default_folders = [item for item in tree_res.json()["items"] if item["name"] == "Sin Nombre"]
    assert len(default_folders) == 1
    canonical_id = default_folders[0]["id"]

    moved = client.get(f"{BASE}/{project['id']}")
    assert moved.status_code == 200
    assert moved.json()["folder_id"] == canonical_id


def test_rename_folder(client):
    folder = client.post(f"{BASE}/folders", json={"name": "Viejo nombre"}).json()

    res = client.patch(f"{BASE}/folders/{folder['id']}", json={"name": "Nuevo nombre"})

    assert res.status_code == 200
    assert res.json()["name"] == "Nuevo nombre"


def test_delete_folder_requires_cascade_when_not_empty(client):
    folder = client.post(f"{BASE}/folders", json={"name": "Curso"}).json()
    client.post(
        f"{BASE}/",
        json={"name": "Proyecto en carpeta", "folder_id": folder["id"]},
    )

    res = client.delete(f"{BASE}/folders/{folder['id']}")

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "FOLDER_NOT_EMPTY"


def test_delete_folder_cascade_preserves_projects(client):
    root = client.post(f"{BASE}/folders", json={"name": "Programa"}).json()
    child = client.post(
        f"{BASE}/folders",
        json={"name": "Unidad 1", "parent_folder_id": root["id"]},
    ).json()
    project = client.post(
        f"{BASE}/",
        json={"name": "Video unidad", "folder_id": child["id"]},
    ).json()

    delete_res = client.delete(f"{BASE}/folders/{root['id']}", params={"cascade": "true"})
    assert delete_res.status_code == 204

    project_res = client.get(f"{BASE}/{project['id']}")
    assert project_res.status_code == 200
    reassigned_folder_id = project_res.json()["folder_id"]
    assert reassigned_folder_id is not None

    tree_res = client.get(f"{BASE}/folders/tree")
    assert tree_res.status_code == 200
    default_folder = next(
        item for item in tree_res.json()["items"] if item["name"] == "Sin Nombre"
    )
    assert reassigned_folder_id == default_folder["id"]

    child_res = client.patch(f"{BASE}/folders/{child['id']}", json={"name": "No existe"})
    assert child_res.status_code == 404


def test_delete_avatar_clears_metadata(client, monkeypatch):
    storage = InMemoryStorageProvider()
    monkeypatch.setattr("app.modules.projects.service.get_storage", lambda: storage)
    monkeypatch.setattr("app.modules.projects.router.get_storage", lambda: storage)
    project_id = _create(client).json()["id"]
    uploaded = client.post(
        f"{BASE}/{project_id}/avatar",
        files={"file": ("avatar.png", b"png-bytes", "image/png")},
    ).json()

    res = client.delete(f"{BASE}/{project_id}/avatar")

    assert res.status_code == 204
    assert client.get(f"{BASE}/{project_id}/avatar").status_code == 404
    try:
        storage.download_file(uploaded["object_key"])
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("avatar object was not deleted")
