from fastapi.testclient import TestClient

BASE = "/api/v1/provider-credentials"


def test_provider_credentials_status_defaults_to_not_configured(client: TestClient):
    res = client.get(f"{BASE}/status")

    assert res.status_code == 200
    body = res.json()
    assert {item["status"] for item in body} == {"not_configured"}
    assert all(item["masked_api_key"] is None for item in body)


def test_provider_credentials_save_masks_key_and_validate(client: TestClient):
    save = client.post(
        BASE,
        json={
            "provider_name": "gemini",
            "provider_type": "ai",
            "api_key": "gemini-test-key-1234",
        },
    )

    assert save.status_code == 200
    assert save.json()["masked_api_key"] == "************1234"
    assert save.json()["status"] == "configured"
    assert "gemini-test-key" not in str(save.json())

    validate = client.post(f"{BASE}/gemini/ai/validate")
    assert validate.status_code == 200
    assert validate.json()["valid"] is True
    assert validate.json()["status"] == "valid"


def test_provider_credentials_invalid_key_sets_invalid_status(client: TestClient):
    client.post(
        BASE,
        json={
            "provider_name": "wavespeed",
            "provider_type": "avatar_video",
            "api_key": "invalid-provider-key",
        },
    )

    validate = client.post(f"{BASE}/wavespeed/avatar_video/validate")

    assert validate.status_code == 200
    assert validate.json()["valid"] is False
    assert validate.json()["status"] == "invalid"
