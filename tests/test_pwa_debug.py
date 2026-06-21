from __future__ import annotations

from contextlib import contextmanager

from starlette.requests import Request

from project_dm.web import app as web_app


def test_pwa_debug_page_context(monkeypatch) -> None:
    @contextmanager
    def fake_read_session():
        yield object()

    monkeypatch.setattr(web_app, "read_session", fake_read_session)
    monkeypatch.setattr(web_app, "list_service_controls", lambda session: [])

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/pwa-debug",
            "headers": [],
            "app": web_app.app,
            "router": web_app.app.router,
        }
    )
    response = web_app.pwa_debug(request)

    assert response.status_code == 200
    assert response.template.name == "pwa_debug.html"
    assert response.context["manifest_url"] == "/manifest.webmanifest"
    assert response.context["sw_url"] == "/sw.js"
