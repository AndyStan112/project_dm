from __future__ import annotations

from project_dm.web.app import (
    app_icon,
    app_icon_192,
    app_icon_512,
    apple_touch_icon,
    manifest,
    service_worker,
)


def test_pwa_assets_are_served() -> None:
    manifest_response = manifest()
    assert manifest_response.status_code == 200
    assert manifest_response.media_type == "application/manifest+json"
    assert manifest_response.body.decode("utf-8").startswith("{")
    assert '"start_url": "/"' in manifest_response.body.decode("utf-8")

    sw_response = service_worker()
    assert sw_response.status_code == 200
    assert sw_response.media_type == "application/javascript"
    assert "showNotification" in sw_response.body.decode("utf-8")

    icon_response = app_icon()
    assert icon_response.status_code == 200
    assert icon_response.media_type == "image/svg+xml"

    icon_192_response = app_icon_192()
    assert icon_192_response.status_code == 200
    assert icon_192_response.media_type == "image/png"
    assert icon_192_response.body.startswith(b"\x89PNG")

    icon_512_response = app_icon_512()
    assert icon_512_response.status_code == 200
    assert icon_512_response.media_type == "image/png"
    assert icon_512_response.body.startswith(b"\x89PNG")

    touch_icon_response = apple_touch_icon()
    assert touch_icon_response.status_code == 200
    assert touch_icon_response.media_type == "image/png"
    assert touch_icon_response.body.startswith(b"\x89PNG")
