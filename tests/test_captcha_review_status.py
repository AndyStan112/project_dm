from __future__ import annotations

from contextlib import contextmanager, nullcontext

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


def test_open_captcha_review_clears_stale_error(monkeypatch) -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.id = 273
            self.job_type = "reviews"
            self.status = "running"
            self.last_error = "Review endpoint returned HTTP 410."

    class FakeSession:
        def __init__(self) -> None:
            self.job = FakeJob()

        def get(self, model, job_id, with_for_update=False):  # noqa: ANN001
            return self.job if job_id == self.job.id else None

        def begin(self):
            return nullcontext()

        def flush(self) -> None:
            pass

    fake_session = FakeSession()

    @contextmanager
    def fake_write_session():
        yield fake_session

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(
        web_app,
        "_captcha_job_row",
        lambda session, job: {"open_url": "https://example.invalid/review"},
    )
    monkeypatch.setattr(
        web_app,
        "set_job_status",
        lambda session, job_id, status: None,
    )

    response = web_app.open_captcha_review(273)

    assert response.status_code == 303
    assert response.headers["location"] == "https://example.invalid/review"
    assert fake_session.job.last_error is None


def test_solve_captcha_review_in_browser_redirects_to_browser(monkeypatch) -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.id = 273
            self.job_type = "reviews"
            self.status = "blocked"
            self.last_error = "Review endpoint returned HTTP 410."

    class FakeSession:
        def __init__(self) -> None:
            self.job = FakeJob()

        def get(self, model, job_id, with_for_update=False):  # noqa: ANN001
            return self.job if job_id == self.job.id else None

        def begin(self):
            return nullcontext()

        def flush(self) -> None:
            pass

    fake_session = FakeSession()

    @contextmanager
    def fake_write_session():
        yield fake_session

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(
        web_app,
        "set_job_status",
        lambda session, job_id, status: None,
    )

    response = web_app.solve_captcha_review_in_browser(273)

    assert response.status_code == 303
    assert response.headers["location"] == "/browser"
    assert fake_session.job.last_error is None
