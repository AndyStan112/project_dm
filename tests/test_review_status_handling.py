from __future__ import annotations

from project_dm.repositories.jobs import checkpoint_review_page
from project_dm.schemas import JobStatus
from project_dm.workers.reviews import _status_needs_manual_solve


def test_status_needs_manual_solve_includes_anti_bot_codes() -> None:
    assert _status_needs_manual_solve(403)
    assert _status_needs_manual_solve(405)
    assert _status_needs_manual_solve(410)
    assert _status_needs_manual_solve(429)


def test_status_needs_manual_solve_excludes_ok_codes() -> None:
    assert not _status_needs_manual_solve(200)
    assert not _status_needs_manual_solve(404)


def test_checkpoint_review_page_finishes_short_final_page() -> None:
    class FakeJob:
        def __init__(self) -> None:
            self.status = JobStatus.RUNNING.value
            self.current_offset = 480
            self.total_expected = 530
            self.locked_at = None
            self.finished_at = None

    class FakeSession:
        def __init__(self) -> None:
            self.job = FakeJob()

        def get(self, model, job_id, with_for_update=False):  # noqa: ANN001
            return self.job if job_id == 1 else None

        def flush(self) -> None:
            pass

    job = checkpoint_review_page(
        FakeSession(),
        job_id=1,
        reviews_seen=30,
        total_expected=530,
        page_size=100,
    )

    assert job.status == JobStatus.COMPLETED.value
    assert job.current_offset == 510
    assert job.total_expected == 510
    assert job.finished_at is not None
