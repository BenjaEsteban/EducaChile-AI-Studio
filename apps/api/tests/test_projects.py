import uuid

from fastapi.testclient import TestClient

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
