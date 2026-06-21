from __future__ import annotations

from project_dm.web.app import service_worker


def test_service_worker_includes_fetch_handler() -> None:
    response = service_worker()
    assert response.status_code == 200
    assert "self.addEventListener(\"fetch\"" in response.body.decode()
