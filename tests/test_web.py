from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from project_dm.db import read_session
from project_dm.models import ProductFamily
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
