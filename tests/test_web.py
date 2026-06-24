from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request

from project_dm.db import read_session
from project_dm.models import ProductFamily
from project_dm.web import app as web_app
from project_dm.web.app import app


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL_READ"),
    reason="DATABASE_URL_READ is not configured",
)


def test_dashboard_pages_render() -> None:
    client = TestClient(app)

    for path in ("/", "/jobs", "/workers", "/products", "/reviews", "/captcha"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Project DM" in response.text


def test_dashboard_filters_and_product_detail_render() -> None:
    client = TestClient(app)

    assert client.get("/jobs?status=paused&job_type=reviews").status_code == 200
    assert client.get("/reviews?rating=5&verified=yes&helpful=yes").status_code == 200
    assert client.get("/products?q=iPhone").status_code == 200

    with read_session() as session:
        family_id = session.scalar(select(ProductFamily.id).limit(1))
    if family_id is not None:
        response = client.get(f"/products/{family_id}")
        assert response.status_code == 200
        assert "Known variants" in response.text


def test_product_detail_exposes_scrape_reviews_button(monkeypatch) -> None:
    @contextmanager
    def fake_read_session():
        yield object()

    family = SimpleNamespace(
        id=58,
        name="Example Phone",
        url="https://www.emag.ro/example-phone/pd/ABC123/",
        aggregate_rating=None,
        review_count=123,
        description=None,
        emag_family_id=88,
    )
    product = {"family": family, "brand_slug": "example", "variants": []}

    monkeypatch.setattr(web_app, "read_session", fake_read_session)
    monkeypatch.setattr(web_app, "family_detail", lambda session, family_id: product)
    monkeypatch.setattr(web_app, "list_reviews", lambda session, **kwargs: [])
    monkeypatch.setattr(
        web_app,
        "recommendations_for_family",
        lambda session, **kwargs: [],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/products/58",
            "headers": [],
            "app": web_app.app,
            "router": web_app.app.router,
        }
    )
    response = web_app.product_detail(request, 58)

    assert response.status_code == 200
    assert "product-feedback/example-phone/pd/ABC123/reviews/list?" in response.context[
        "reviews_url"
    ]
    assert response.context["scrape_reviews_endpoint"] == "/products/58/scrape-reviews"
    assert response.context["recommendations"] == []


def test_scrape_product_reviews_imports_payload(monkeypatch) -> None:
    class FakeFamily:
        def __init__(self) -> None:
            self.review_count = 12
            self.scraped_at = None
            self.url = "https://www.emag.ro/example-phone/pd/ABC123/"

    class FakeSession:
        def __init__(self) -> None:
            self.family = FakeFamily()

        def get(self, model, family_id, with_for_update=False):  # noqa: ANN001
            return self.family if family_id == 58 else None

        def begin(self):
            return nullcontext()

        def flush(self) -> None:
            pass

    fake_session = FakeSession()
    calls: list[tuple[int, dict[str, object]]] = []

    @contextmanager
    def fake_write_session():
        yield fake_session

    monkeypatch.setattr(web_app, "write_session", fake_write_session)
    monkeypatch.setattr(
        web_app,
        "import_review_payload",
        lambda session, family_id, payload: calls.append((family_id, payload))
        or (7, 222),
    )

    response = web_app.scrape_product_reviews(
        58,
        payload={
            "response": {"code": 200},
            "reviews": {"count": 222, "items": []},
        },
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "family_id": 58,
        "reviews_seen": 7,
        "review_count": 222,
        "message": "Reviews imported from pasted JSON.",
    }
    assert calls == [
        (
            58,
            {"response": {"code": 200}, "reviews": {"count": 222, "items": []}},
        )
    ]
    assert fake_session.family.review_count == 222
    assert fake_session.family.scraped_at is not None


def test_worker_controls_render_and_update() -> None:
    if not os.getenv("DATABASE_URL_WRITE"):
        pytest.skip("DATABASE_URL_WRITE is not configured")

    client = TestClient(app)

    assert client.get("/workers").status_code == 200
    assert (
        client.post(
            "/service-controls/scraper/start", follow_redirects=False
        ).status_code
        == 303
    )


def test_workers_route_separates_blocked_and_failed_jobs(monkeypatch) -> None:
    @contextmanager
    def fake_read_session():
        yield object()

    def make_job(job_id: int, status: str, message: str) -> dict[str, object]:
        return {
            "job": SimpleNamespace(
                id=job_id,
                job_type="reviews",
                status=status,
                target_url="https://example.invalid",
                current_offset=0,
                total_expected=None,
                attempts=1,
                last_error=message,
            ),
            "brand_slug": None,
            "family_name": "Example Family",
        }

    monkeypatch.setattr(web_app, "read_session", fake_read_session)
    monkeypatch.setattr(web_app, "list_service_controls", lambda session: [])
    monkeypatch.setattr(
        web_app,
        "recent_blocked_jobs",
        lambda session: [make_job(101, "blocked", "Needs captcha")],
    )
    monkeypatch.setattr(
        web_app,
        "recent_failed_jobs",
        lambda session: [
            make_job(202, "failed", "Marked unrecoverable by user.")
        ],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/workers",
            "headers": [],
            "app": web_app.app,
            "router": web_app.app.router,
        }
    )
    response = web_app.workers(request)

    assert response.status_code == 200
    assert [row["job"].id for row in response.context["blocked_jobs"]] == [101]
    assert [row["job"].id for row in response.context["failed_jobs"]] == [202]
    assert response.context["failed_jobs"][0]["job"].last_error == (
        "Marked unrecoverable by user."
    )


def test_pending_job_can_be_promoted() -> None:
    if not os.getenv("DATABASE_URL_WRITE"):
        pytest.skip("DATABASE_URL_WRITE is not configured")

    client = TestClient(app)
    response = client.post("/jobs/1/promote", follow_redirects=False)
    assert response.status_code in {303, 404}


def test_job_can_be_marked_unrecoverable() -> None:
    if not os.getenv("DATABASE_URL_WRITE"):
        pytest.skip("DATABASE_URL_WRITE is not configured")

    client = TestClient(app)
    response = client.post("/jobs/1/unrecoverable", follow_redirects=False)
    assert response.status_code in {303, 404}


def test_run_worker_passes_attended_browser_to_listing_and_product(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        web_app,
        "run_one_listing_job",
        lambda **kwargs: calls.append(("listing", kwargs)),
    )
    monkeypatch.setattr(
        web_app,
        "run_product_jobs",
        lambda **kwargs: calls.append(("product", kwargs)),
    )
    monkeypatch.setattr(
        web_app,
        "run_one_review_job",
        lambda **kwargs: calls.append(("reviews", kwargs)),
    )

    web_app._run_worker("listing", max_pages=3, attended_browser=True)
    web_app._run_worker("product", max_pages=3, attended_browser=True)

    assert calls == [
        ("listing", {"max_pages": 3, "attended_browser": True}),
        ("product", {"attended_browser": True}),
    ]
