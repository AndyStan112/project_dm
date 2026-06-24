from __future__ import annotations

from contextlib import contextmanager, nullcontext

from fastapi.testclient import TestClient
from starlette.requests import Request

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


def test_captcha_review_job_exposes_next_page_url(monkeypatch) -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.id = 313
            self.job_type = "reviews"
            self.status = "running"
            self.current_offset = 100
            self.target_url = "https://www.emag.ro/example/pd/ABC123/"

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
    monkeypatch.setattr(web_app, "_activate_captcha_job", lambda session, job: job)
    monkeypatch.setattr(
        web_app,
        "_captcha_job_row",
        lambda session, job: {
            "job": job,
            "brand_slug": "example",
            "family_name": "Example",
            "open_url": "https://www.emag.ro/example/pd/ABC123/reviews/list?page[offset]=100&page[limit]=100",
            "next_open_url": web_app.build_reviews_url(
                "https://www.emag.ro/example/pd/ABC123/",
                offset=200,
            ),
        },
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/captcha/review/313",
            "headers": [],
            "app": web_app.app,
            "router": web_app.app.router,
        }
    )
    response = web_app.captcha_review_job(request, 313)

    assert response.status_code == 200
    assert response.context["review_page_size"] == 100
    assert "page%5Boffset%5D=200" in response.context["next_open_url"]


def test_brand_jobs_do_not_expose_review_next_page_url() -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.id = 414
            self.job_type = "brand_listing"
            self.status = "running"
            self.current_offset = 0
            self.total_expected = None
            self.target_url = "https://www.emag.ro/telefoane-mobile/brand/samsung/c"

    assert web_app._job_next_review_url(FakeJob()) is None


def test_solve_captcha_brand_in_browser_redirects_to_browser(monkeypatch) -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.id = 415
            self.job_type = "brand_listing"
            self.status = "blocked"
            self.last_error = "Brand listing blocked."

    class FakeControl:
        service_name = "scraper"
        current_job_id = None

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
    calls: list[tuple[int, str]] = []

    @contextmanager
    def fake_write_session():
        yield fake_session

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(
        web_app,
        "list_service_controls",
        lambda session: [FakeControl()],
    )
    monkeypatch.setattr(
        web_app,
        "set_job_status",
        lambda session, job_id, status: calls.append((job_id, status.value)),
    )

    response = web_app.solve_captcha_brand_in_browser(415)

    assert response.status_code == 303
    assert response.headers["location"] == "/browser"
    assert fake_session.job.last_error is None
    assert calls == [(415, "pending")]


def test_solve_captcha_product_in_browser_redirects_to_browser(monkeypatch) -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.id = 416
            self.job_type = "product"
            self.status = "blocked"
            self.last_error = "Product page blocked."

    class FakeControl:
        service_name = "scraper"
        current_job_id = None

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
    calls: list[tuple[int, str]] = []

    @contextmanager
    def fake_write_session():
        yield fake_session

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(
        web_app,
        "list_service_controls",
        lambda session: [FakeControl()],
    )
    monkeypatch.setattr(
        web_app,
        "set_job_status",
        lambda session, job_id, status: calls.append((job_id, status.value)),
    )

    response = web_app.solve_captcha_product_in_browser(416)

    assert response.status_code == 303
    assert response.headers["location"] == "/browser"
    assert fake_session.job.last_error is None
    assert calls == [(416, "pending")]


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
    monkeypatch.setattr(web_app, "list_service_controls", lambda session: [])
    monkeypatch.setattr(
        web_app,
        "set_job_status",
        lambda session, job_id, status: None,
    )

    response = web_app.solve_captcha_review_in_browser(273)

    assert response.status_code == 303
    assert response.headers["location"] == "/browser"
    assert fake_session.job.last_error is None


def test_solve_captcha_review_in_browser_requeues_stale_running_job(
    monkeypatch,
) -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.id = 277
            self.job_type = "reviews"
            self.status = "running"
            self.last_error = "Review URL returned HTTP 410; marked unrecoverable."

    class FakeControl:
        service_name = "scraper"
        current_job_id = None

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
    calls: list[tuple[int, str]] = []

    @contextmanager
    def fake_write_session():
        yield fake_session

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(
        web_app,
        "list_service_controls",
        lambda session: [FakeControl()],
    )
    monkeypatch.setattr(
        web_app,
        "set_job_status",
        lambda session, job_id, status: calls.append((job_id, status.value)),
    )

    response = web_app.solve_captcha_review_in_browser(277)

    assert response.status_code == 303
    assert response.headers["location"] == "/browser"
    assert fake_session.job.last_error is None
    assert calls == [(277, "pending")]


def test_captcha_review_debug_returns_job_state(monkeypatch) -> None:
    @contextmanager
    def fake_write_session():
        yield object()

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(
        web_app,
        "_captcha_job_debug_state",
        lambda session, job_id: {
            "job": {"id": job_id, "status": "running"},
            "brand": None,
            "family": None,
            "controls": {"scraper": {"current_state": "running"}},
        },
    )

    client = TestClient(web_app.app)
    response = client.get("/api/captcha/review/42/debug")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["id"] == 42
    assert payload["controls"]["scraper"]["current_state"] == "running"
