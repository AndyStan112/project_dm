from __future__ import annotations

from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

from project_dm.repositories.jobs import checkpoint_review_page
from project_dm.schemas import JobStatus
from project_dm.workers.reviews import _status_needs_manual_solve
from project_dm.workers import reviews as review_worker


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


def test_review_worker_uses_fixed_page_size_for_pagination(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeJob:
        def __init__(self) -> None:
            self.id = 11
            self.family_id = 22
            self.target_url = "https://www.emag.ro/example-phone/pd/ABC123/"
            self.current_offset = 40
            self.total_expected = 999
            self.status = JobStatus.RUNNING.value

    class FakeFamily:
        review_count = 9999

    class FakeSession:
        def __init__(self) -> None:
            self.job = FakeJob()
            self.family = FakeFamily()

        def get(self, model, identifier, with_for_update=False):  # noqa: ANN001
            if model.__name__ == "ProductFamily" and identifier == 22:
                return self.family
            return None

        def begin(self):
            return nullcontext()

        def flush(self) -> None:
            pass

    class FakeResponse:
        status = 200
        headers = {"content-type": "application/json"}
        ok = True

        def text(self) -> str:
            return (
                '{"response": {"code": 200}, "reviews": '
                '{"count": 9999, "items": []}}'
            )

    class FakeRequestContext:
        def get(self, url, timeout):  # noqa: ANN001
            calls.append(("request", url))
            return FakeResponse()

    class FakePage:
        def goto(self, url, wait_until, timeout):  # noqa: ANN001
            return SimpleNamespace(status=200, headers={"content-type": "text/html"})

        def wait_for_timeout(self, ms):  # noqa: ANN001
            return None

        def locator(self, selector):  # noqa: ANN001
            return SimpleNamespace(inner_text=lambda timeout: "ok")

        def bring_to_front(self) -> None:
            return None

    class FakeContext:
        request = FakeRequestContext()

        def new_page(self):
            return FakePage()

    class FakeBrowser:
        def new_context(self, **kwargs):  # noqa: ANN001
            return FakeContext()

        def close(self) -> None:
            return None

    class FakePlaywright:
        def stop(self) -> None:
            return None

    fake_session = FakeSession()

    @contextmanager
    def fake_write_session():
        yield fake_session

    monkeypatch.setattr(
        review_worker, "open_browser", lambda attended_browser=None: (FakePlaywright(), FakeBrowser())
    )
    monkeypatch.setattr(review_worker, "write_session", fake_write_session)
    monkeypatch.setattr(review_worker, "claim_pending_job", lambda session, job_types: fake_session.job)
    monkeypatch.setattr(review_worker, "update_service_control_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(review_worker, "current_status", lambda job_id: JobStatus.RUNNING)
    monkeypatch.setattr(review_worker, "visible_page_is_blocked", lambda text: False)
    monkeypatch.setattr(review_worker, "save_diagnostic", lambda *args, **kwargs: None)
    monkeypatch.setattr(review_worker, "fail_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(review_worker, "set_job_status", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        review_worker,
        "build_reviews_url",
        lambda product_url, *, offset, limit=100: calls.append(
            ("build", offset, limit)
        )
        or f"https://example.invalid/reviews?offset={offset}&limit={limit}",
    )
    monkeypatch.setattr(
        review_worker,
        "apply_review_payload",
        lambda session, job_id, family_id, payload, page_size=None: (
            0,
            40,
            JobStatus.COMPLETED,
        ),
    )

    result = review_worker.run_one_review_job(attended_browser=False, min_delay=0, max_delay=0)

    assert result.status == JobStatus.COMPLETED
    assert ("build", 40, 100) in calls
    assert any(
        kind == "request" and url.endswith("offset=40&limit=100")
        for kind, url, *_ in calls
    )
