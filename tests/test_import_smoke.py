from __future__ import annotations

import importlib
import os
import pkgutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy import select

import project_dm
from project_dm.db import read_session
from project_dm.models import Job
from project_dm.web.app import app


def test_project_modules_import_cleanly() -> None:
    failures: list[str] = []
    for mod in pkgutil.walk_packages(
        project_dm.__path__, project_dm.__name__ + "."
    ):
        if mod.ispkg:
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # pragma: no cover - failure is the point
            failures.append(f"{mod.name}: {type(exc).__name__}: {exc}")

    assert failures == [], "\n".join(failures)


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL_READ"),
    reason="DATABASE_URL_READ is not configured",
)


def test_captcha_routes_render_with_real_job() -> None:
    client = TestClient(app)

    try:
        with read_session() as session:
            review_job_id = session.scalar(
                select(Job.id).where(Job.job_type == "reviews").limit(1)
            )
    except OperationalError as exc:
        pytest.skip(f"Database is not reachable: {exc}")

    assert review_job_id is not None

    review_page = client.get(f"/captcha/review/{review_job_id}")
    assert review_page.status_code == 200
    assert "review" in review_page.text.lower()

    open_response = client.get(
        f"/captcha/review/{review_job_id}/open",
        follow_redirects=False,
    )
    assert open_response.status_code in {303, 404, 410}
