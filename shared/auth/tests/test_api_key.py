from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from shared.auth.api_key import require_memory_write_api_key


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected")
    def protected(_api_key=Depends(require_memory_write_api_key)) -> dict[str, object]:
        return {"ok": True}

    return app


def test_dev_mode_allows_request_without_token(monkeypatch):
    monkeypatch.delenv("MEMORY_WRITE_API_KEY", raising=False)
    client = TestClient(_build_app())
    response = client.get("/protected")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_missing_token_returns_401(monkeypatch):
    monkeypatch.setenv("MEMORY_WRITE_API_KEY", "secret-token")
    client = TestClient(_build_app())
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing_api_key"


def test_wrong_token_returns_403(monkeypatch):
    monkeypatch.setenv("MEMORY_WRITE_API_KEY", "secret-token")
    client = TestClient(_build_app())
    response = client.get("/protected", headers={"X-API-Key": "wrong-token"})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "invalid_api_key"


def test_correct_x_api_key_header_returns_200(monkeypatch):
    monkeypatch.setenv("MEMORY_WRITE_API_KEY", "secret-token")
    client = TestClient(_build_app())
    response = client.get("/protected", headers={"X-API-Key": "secret-token"})
    assert response.status_code == 200


def test_correct_bearer_token_returns_200(monkeypatch):
    monkeypatch.setenv("MEMORY_WRITE_API_KEY", "secret-token")
    client = TestClient(_build_app())
    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert response.status_code == 200


def test_empty_api_key_env_falls_back_to_dev_mode(monkeypatch):
    monkeypatch.setenv("MEMORY_WRITE_API_KEY", "   ")
    client = TestClient(_build_app())
    response = client.get("/protected")
    assert response.status_code == 200
