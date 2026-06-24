from __future__ import annotations

import sys
import threading
from dataclasses import dataclass

from project_dm.db import read_session, write_session
from project_dm.models import ServiceControl
from project_dm.repositories.service_controls import (
    SERVICE_NAMES,
    ensure_service_controls,
    update_service_control_state,
)
from project_dm.schemas import JobStatus
from project_dm.workers.nlp import run_nlp_batch
from project_dm.workers.listing import run_one_listing_job
from project_dm.workers.product import run_product_jobs
from project_dm.workers.reviews import run_one_review_job


_STOP_EVENT = threading.Event()
_THREADS: list[threading.Thread] = []
_START_LOCK = threading.Lock()
SCRAPER_LANE_COUNT = 3


@dataclass(frozen=True)
class CycleResult:
    current_state: str
    current_job_id: int | None
    message: str


def _job_id(result: object) -> int | None:
    return getattr(result, "job_id", None)


def _result_status(result: object) -> JobStatus | None:
    status = getattr(result, "status", None)
    return status if isinstance(status, JobStatus) else None


def _result_message(result: object) -> str:
    message = getattr(result, "message", "")
    return str(message)


def _scraper_cycle() -> CycleResult:
    print("[supervisor] scraper cycle start", file=sys.stderr, flush=True)
    runners = (
        (
            "listing",
            lambda: run_one_listing_job(max_pages=1, min_delay=0, max_delay=0),
        ),
        (
            "reviews",
            lambda: run_one_review_job(min_delay=0, max_delay=0),
        ),
        (
            "product",
            lambda: run_product_jobs(max_jobs=1, min_delay=0, max_delay=0),
        ),
    )
    for label, runner in runners:
        print(
            f"[supervisor] trying {label}",
            file=sys.stderr,
            flush=True,
        )
        result = runner()
        job_id = _job_id(result)
        status = _result_status(result)
        message = _result_message(result)
        print(
            f"[supervisor] {label} result job_id={job_id} status={status} message={message}",
            file=sys.stderr,
            flush=True,
        )
        if job_id is None and status is None:
            continue
        if status in {JobStatus.BLOCKED, JobStatus.FAILED}:
            current_state = (
                JobStatus.BLOCKED.value
                if status is JobStatus.BLOCKED
                else "error"
            )
            return CycleResult(
                current_state=current_state,
                current_job_id=job_id,
                message=f"{label}: {message}",
            )
        return CycleResult(
            current_state=JobStatus.RUNNING.value,
            current_job_id=job_id,
            message=f"{label}: {message}",
        )
    return CycleResult(
        current_state=JobStatus.RUNNING.value,
        current_job_id=None,
        message="Idle; no pending scraper jobs.",
    )


def _idle_cycle(service_name: str, desired_state: str) -> CycleResult:
    if desired_state == JobStatus.RUNNING.value:
        return CycleResult(
            current_state=JobStatus.BLOCKED.value,
            current_job_id=None,
            message=f"{service_name}: no worker implementation yet.",
        )
    return CycleResult(
        current_state=desired_state,
        current_job_id=None,
        message=f"{service_name}: {desired_state} by UI.",
    )


def _nlp_cycle() -> CycleResult:
    result = run_nlp_batch(limit=500)
    if result.reviews_processed == 0:
        return CycleResult(
            current_state=JobStatus.RUNNING.value,
            current_job_id=None,
            message=result.message,
        )
    return CycleResult(
        current_state=JobStatus.RUNNING.value,
        current_job_id=None,
        message=result.message,
    )


def _service_cycle(service_name: str, desired_state: str) -> CycleResult:
    if desired_state == JobStatus.RUNNING.value:
        if service_name == "scraper":
            return _scraper_cycle()
        if service_name == "nlp":
            return _nlp_cycle()
        return _idle_cycle(service_name, desired_state)
    return CycleResult(
        current_state=desired_state,
        current_job_id=None,
        message=f"{service_name}: {desired_state} by UI.",
    )


def _current_desired_state(service_name: str) -> str:
    with read_session() as session:
        control = session.get(ServiceControl, service_name)
        if control is None:
            return JobStatus.PAUSED.value
        return control.desired_state


def _service_loop(service_name: str, *, lane: int = 1) -> None:
    thread_label = (
        f"{service_name}-lane-{lane}"
        if service_name == "scraper"
        else service_name
    )
    while not _STOP_EVENT.is_set():
        desired_state = _current_desired_state(service_name)
        try:
            cycle = _service_cycle(service_name, desired_state)
        except Exception as exc:  # pragma: no cover - supervisor safety net
            cycle = CycleResult(
                current_state="error",
                current_job_id=None,
                message=f"{thread_label}: {type(exc).__name__}: {exc}",
            )
        with write_session() as session, session.begin():
            ensure_service_controls(session)
            update_service_control_state(
                session,
                service_name,
                current_state=cycle.current_state,
                current_job_id=cycle.current_job_id,
                message=(
                    f"{thread_label}: {cycle.message}"
                    if service_name == "scraper"
                    else cycle.message
                ),
            )
        _STOP_EVENT.wait(1.0 if desired_state == JobStatus.RUNNING.value else 2.0)


def start_worker_supervisors() -> None:
    with _START_LOCK:
        if _THREADS:
            return
        with write_session() as session, session.begin():
            ensure_service_controls(session)
        for service_name in SERVICE_NAMES:
            lane_count = SCRAPER_LANE_COUNT if service_name == "scraper" else 1
            for lane in range(1, lane_count + 1):
                thread = threading.Thread(
                    target=_service_loop,
                    args=(service_name,),
                    kwargs={"lane": lane},
                    name=(
                        f"project-dm-{service_name}-supervisor"
                        if lane_count == 1
                        else f"project-dm-{service_name}-supervisor-{lane}"
                    ),
                    daemon=True,
                )
                thread.start()
                _THREADS.append(thread)


def stop_worker_supervisors() -> None:
    _STOP_EVENT.set()
    for thread in list(_THREADS):
        thread.join(timeout=5)
