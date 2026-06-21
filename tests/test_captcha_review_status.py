from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from project_dm.web import app as web_app


def test_captcha_review_status_uses_write_session(monkeypatch) -> None:
    calls: list[str] = []

    @contextmanager
    def fake_write_session():
        calls.append("write")
        yield object()

    def fake_read_session():  # pragma: no cover - safety net
        raise AssertionError("read_session should not be used here")

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(web_app, "read_session", fake_read_session)
    monkeypatch.setattr(
        web_app,
        "_captcha_job_state",
        lambda session, job_id: {
            "id": job_id,
            "status": "running",
            "last_error": None,
        },
    )

    client = TestClient(web_app.app)
    response = client.get("/api/captcha/review/42")

    assert response.status_code == 200
    assert response.json()["id"] == 42
    assert calls == ["write"]
