from __future__ import annotations

from project_dm.workers.reviews import _status_needs_manual_solve


def test_status_needs_manual_solve_includes_anti_bot_codes() -> None:
    assert _status_needs_manual_solve(403)
    assert _status_needs_manual_solve(405)
    assert _status_needs_manual_solve(410)
    assert _status_needs_manual_solve(429)


def test_status_needs_manual_solve_excludes_ok_codes() -> None:
    assert not _status_needs_manual_solve(200)
    assert not _status_needs_manual_solve(404)
