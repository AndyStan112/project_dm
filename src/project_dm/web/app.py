from __future__ import annotations

import threading
import sys
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from dataclasses import asdict
from urllib.parse import quote_plus

import uvicorn
from fastapi import Body, FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, func, select

from project_dm.brands import normalize_brand
from project_dm.db import read_session, write_session
from project_dm.models import Brand, Job, ProductFamily
from project_dm.repositories.brands import list_brands, upsert_brand
from project_dm.repositories.dashboard import (
    dashboard_stats,
    family_detail,
    job_status_counts,
    list_families,
    list_jobs_for_dashboard,
    list_reviews,
    rating_counts,
    recent_blocked_jobs,
    recent_failed_jobs,
    recent_jobs,
    review_summary,
)
from project_dm.repositories.recommendations import (
    recommendations_for_family,
    regenerate_product_recommendations,
)
from project_dm.repositories.jobs import (
    get_or_create_brand_listing_job,
    fail_job,
    promote_job,
    queue_missing_review_jobs,
    set_job_status,
)
from project_dm.repositories.service_controls import (
    DESIRED_STATE_ACTIONS,
    SERVICE_NAMES,
    list_service_controls,
    set_service_control_desired_state,
)
from project_dm.schemas import JobStatus, JobType
from project_dm.workers.listing import run_one_listing_job
from project_dm.workers.nlp import run_nlp_batch
from project_dm.workers.product import run_product_jobs
from project_dm.workers.reviews import (
    apply_review_payload,
    import_review_payload,
    run_one_review_job,
)
from project_dm.workers.supervisor import (
    SCRAPER_LANE_COUNT,
    start_worker_supervisors,
    stop_worker_supervisors,
)
from project_dm.scraping.reviews import build_reviews_url


PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
STATIC_DIR = PACKAGE_DIR / "static"
DIAGNOSTICS_DIR = Path("data") / "diagnostics"
DEFAULT_BROWSER_PUBLIC_URL = "https://vnc.windogs.win"
CAPTCHA_JOB_TYPES = {
    "brand": JobType.BRAND_LISTING,
    "product": JobType.PRODUCT,
    "review": JobType.REVIEWS,
}
CAPTCHA_ACTIONABLE_STATUSES = (
    JobStatus.PENDING,
    JobStatus.RUNNING,
    JobStatus.PAUSED,
    JobStatus.BLOCKED,
    JobStatus.FAILED,
)
REVIEW_PAGE_SIZE = 100

app = FastAPI(title="Project DM Dashboard")
app.mount(
    "/static",
    StaticFiles(directory=PACKAGE_DIR / "static"),
    name="static",
)


def _context(request: Request, *, active: str, **values: object) -> dict:
    return {"request": request, "active": active, **values}


def _dashboard_payload(session) -> dict[str, object]:
    return {
        "stats": asdict(dashboard_stats(session)),
        "job_counts": [
            {"status": status, "count": count}
            for status, count in job_status_counts(session)
        ],
        "rating_counts": [
            {"rating": rating, "count": count}
            for rating, count in rating_counts(session)
        ],
        "recent_jobs": [
            {
                "id": row["job"].id,
                "job_type": row["job"].job_type,
                "status": row["job"].status,
                "target_url": row["job"].target_url,
                "current_offset": row["job"].current_offset,
                "total_expected": row["job"].total_expected,
                "attempts": row["job"].attempts,
                "last_error": row["job"].last_error,
                "family_name": row["family_name"],
                "brand_slug": row["brand_slug"],
            }
            for row in recent_jobs(session)
        ],
        "blocked_jobs": [
            {
                "id": row["job"].id,
                "job_type": row["job"].job_type,
                "status": row["job"].status,
                "target_url": row["job"].target_url,
                "current_offset": row["job"].current_offset,
                "total_expected": row["job"].total_expected,
                "attempts": row["job"].attempts,
                "last_error": row["job"].last_error,
                "family_name": row["family_name"],
                "brand_slug": row["brand_slug"],
            }
            for row in recent_blocked_jobs(session)
        ],
        "failed_jobs": [
            {
                "id": row["job"].id,
                "job_type": row["job"].job_type,
                "status": row["job"].status,
                "target_url": row["job"].target_url,
                "current_offset": row["job"].current_offset,
                "total_expected": row["job"].total_expected,
                "attempts": row["job"].attempts,
                "last_error": row["job"].last_error,
                "family_name": row["family_name"],
                "brand_slug": row["brand_slug"],
            }
            for row in recent_failed_jobs(session)
        ],
        "worker_status": [
            {
                "service_name": control.service_name,
                "desired_state": control.desired_state,
                "current_state": control.current_state,
                "current_job_id": control.current_job_id,
                "last_heartbeat_at": (
                    control.last_heartbeat_at.isoformat()
                    if control.last_heartbeat_at is not None
                    else None
                ),
                "message": control.message,
            }
            for control in list_service_controls(session)
        ],
        "scraper_lane_count": SCRAPER_LANE_COUNT,
    }


def _as_bool(value: str | None) -> bool:
    return value in {"1", "true", "yes", "on"}


def _diagnostic_directory(job_id: int) -> Path:
    return DIAGNOSTICS_DIR / f"job_{job_id}"


def _browser_public_url() -> str:
    value = os.getenv("PROJECT_DM_BROWSER_PUBLIC_URL")
    if value:
        return value.rstrip("/")
    return DEFAULT_BROWSER_PUBLIC_URL


def _captcha_kind_label(kind: str) -> str:
    return {
        "brand": "Brand",
        "product": "Product",
        "review": "Review",
    }[kind]


def _captcha_job_type(kind: str) -> JobType:
    job_type = CAPTCHA_JOB_TYPES.get(kind)
    if job_type is None:
        raise HTTPException(status_code=404, detail="Unknown captcha page")
    return job_type


def _job_progress_url(job: Job, session) -> str | None:
    if job.target_url is None:
        return None
    if job.job_type != JobType.REVIEWS.value:
        return job.target_url

    return build_reviews_url(
        job.target_url,
        offset=job.current_offset,
    )


def _job_next_review_url(job: Job) -> str | None:
    if job.target_url is None:
        return None
    if job.job_type != JobType.REVIEWS.value:
        return None
    if (
        job.status == JobStatus.COMPLETED.value
        or (
            job.total_expected is not None
            and job.current_offset >= job.total_expected
        )
    ):
        return None
    return build_reviews_url(
        job.target_url,
        offset=job.current_offset + REVIEW_PAGE_SIZE,
    )


def _captcha_job_row(
    session,
    job: Job,
    *,
    include_next_review_url: bool = False,
) -> dict[str, object]:
    row = session.execute(
        select(Brand.slug, ProductFamily.name)
        .select_from(Job)
        .outerjoin(Brand, Brand.id == Job.brand_id)
        .outerjoin(ProductFamily, ProductFamily.id == Job.family_id)
        .where(Job.id == job.id)
    ).one()
    brand_slug, family_name = row
    return {
        "job": job,
        "brand_slug": brand_slug,
        "family_name": family_name,
        "open_url": _job_progress_url(job, session),
        "next_open_url": (
            _job_next_review_url(job) if include_next_review_url else None
        ),
    }


def _captcha_job_state(session, job_id: int) -> dict[str, object] | None:
    job = session.get(Job, job_id)
    if job is None:
        return None
    row = _captcha_job_row(session, job)
    total_expected = job.total_expected or 0
    current_offset = job.current_offset
    progress_text = (
        f"{current_offset:,} / {total_expected:,}"
        if total_expected
        else f"{current_offset:,}"
    )
    percent = (
        min(100, (current_offset / total_expected) * 100)
        if total_expected
        else 0
    )
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "brand_slug": row["brand_slug"],
        "family_name": row["family_name"],
        "target_url": job.target_url,
        "open_url": row["open_url"],
        "current_offset": current_offset,
        "total_expected": job.total_expected,
        "progress_text": progress_text,
        "progress_percent": percent,
        "attempts": job.attempts,
        "last_error": job.last_error,
        "updated_at": job.updated_at.isoformat(),
    }


def _serialize_job(job: Job) -> dict[str, object]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "brand_id": job.brand_id,
        "family_id": job.family_id,
        "target_url": job.target_url,
        "current_offset": job.current_offset,
        "total_expected": job.total_expected,
        "attempts": job.attempts,
        "priority": job.priority,
        "last_error": job.last_error,
        "locked_at": job.locked_at.isoformat() if job.locked_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }


def _captcha_job_debug_state(session, job_id: int) -> dict[str, object] | None:
    job = session.get(Job, job_id)
    if job is None:
        return None
    row = session.execute(
        select(Brand, ProductFamily)
        .select_from(Job)
        .outerjoin(Brand, Brand.id == Job.brand_id)
        .outerjoin(ProductFamily, ProductFamily.id == Job.family_id)
        .where(Job.id == job.id)
    ).one()
    brand, family = row
    controls = {
        control.service_name: {
            "service_name": control.service_name,
            "desired_state": control.desired_state,
            "current_state": control.current_state,
            "current_job_id": control.current_job_id,
            "last_heartbeat_at": (
                control.last_heartbeat_at.isoformat()
                if control.last_heartbeat_at is not None
                else None
            ),
            "message": control.message,
            "created_at": control.created_at.isoformat(),
            "updated_at": control.updated_at.isoformat(),
        }
        for control in list_service_controls(session)
    }
    return {
        "job": _serialize_job(job),
        "brand": (
            {
                "id": brand.id,
                "name": brand.name,
                "slug": brand.slug,
                "listing_url": brand.listing_url,
                "enabled": brand.enabled,
                "created_at": brand.created_at.isoformat(),
                "updated_at": brand.updated_at.isoformat(),
            }
            if brand is not None
            else None
        ),
        "family": (
            {
                "id": family.id,
                "brand_id": family.brand_id,
                "emag_family_id": family.emag_family_id,
                "name": family.name,
                "description": family.description,
                "aggregate_rating": (
                    str(family.aggregate_rating)
                    if family.aggregate_rating is not None
                    else None
                ),
                "review_count": family.review_count,
                "url": family.url,
                "scraped_at": (
                    family.scraped_at.isoformat()
                    if family.scraped_at is not None
                    else None
                ),
                "created_at": family.created_at.isoformat(),
                "updated_at": family.updated_at.isoformat(),
            }
            if family is not None
            else None
        ),
        "controls": controls,
    }


def _next_captcha_job(session, kind: str) -> Job | None:
    job_type = _captcha_job_type(kind)
    status_rank = case(
        (Job.status == JobStatus.PENDING.value, 0),
        (Job.status == JobStatus.RUNNING.value, 1),
        (Job.status == JobStatus.PAUSED.value, 2),
        (Job.status == JobStatus.BLOCKED.value, 3),
        (Job.status == JobStatus.FAILED.value, 4),
        else_=5,
    )
    return session.scalar(
        select(Job)
        .where(
            Job.job_type == job_type.value,
            Job.status.in_(status.value for status in CAPTCHA_ACTIONABLE_STATUSES),
            Job.finished_at.is_(None),
        )
        .order_by(status_rank, Job.priority.asc(), Job.updated_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )


def _activate_captcha_job(session, job: Job) -> Job:
    if job.status != JobStatus.RUNNING.value:
        job.status = JobStatus.RUNNING.value
        job.locked_at = datetime.now(UTC)
        session.flush()
    return job


def _static_text_response(filename: str, media_type: str) -> Response:
    return Response(
        content=(STATIC_DIR / filename).read_text(encoding="utf-8"),
        media_type=media_type,
    )


def _static_binary_response(filename: str, media_type: str) -> Response:
    return Response(
        content=(STATIC_DIR / filename).read_bytes(),
        media_type=media_type,
    )


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> Response:
    return _static_text_response(
        "manifest.webmanifest",
        "application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> Response:
    return _static_text_response("sw.js", "application/javascript")


@app.get("/icon.svg", include_in_schema=False)
def app_icon() -> Response:
    return _static_text_response("icon.svg", "image/svg+xml")


@app.get("/icon-192.png", include_in_schema=False)
def app_icon_192() -> Response:
    return _static_binary_response("icon-192.png", "image/png")


@app.get("/icon-512.png", include_in_schema=False)
def app_icon_512() -> Response:
    return _static_binary_response("icon-512.png", "image/png")


@app.get("/apple-touch-icon.png", include_in_schema=False)
def apple_touch_icon() -> Response:
    return _static_binary_response("apple-touch-icon.png", "image/png")


@app.get("/pwa-debug", response_class=HTMLResponse)
def pwa_debug(request: Request) -> HTMLResponse:
    with read_session() as session:
        worker_status = list_service_controls(session)
    return templates.TemplateResponse(
        request,
        "pwa_debug.html",
            _context(
            request,
            active="pwa_debug",
            worker_status=worker_status,
            manifest_url="/static/manifest.webmanifest",
            sw_url="/sw.js",
            icon_192_url="/static/icon-192.png",
            icon_512_url="/static/icon-512.png",
            touch_icon_url="/static/apple-touch-icon.png",
        ),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, live: str | None = None) -> HTMLResponse:
    with read_session() as session:
        worker_status = list_service_controls(session)
        context = _context(
            request,
            active="dashboard",
            live=_as_bool(live),
            stats=dashboard_stats(session),
            job_counts=job_status_counts(session),
            rating_counts=rating_counts(session),
            recent_jobs=recent_jobs(session),
            blocked_jobs=recent_blocked_jobs(session),
            brands=list_brands(session),
            worker_status=worker_status,
            scraper_lane_count=SCRAPER_LANE_COUNT,
        )
    return templates.TemplateResponse(request, "dashboard.html", context)


@app.get("/api/dashboard")
def dashboard_api() -> JSONResponse:
    with read_session() as session:
        payload = _dashboard_payload(session)
    return JSONResponse(payload)


@app.get("/jobs", response_class=HTMLResponse)
def jobs(
    request: Request,
    status: str = "",
    job_type: str = "",
    q: str = "",
) -> HTMLResponse:
    with read_session() as session:
        rows = list_jobs_for_dashboard(
            session,
            status=status or None,
            job_type=job_type or None,
            query=q or None,
        )
    return templates.TemplateResponse(
        request,
        "jobs.html",
        _context(
            request,
            active="jobs",
            jobs=rows,
            status=status,
            job_type=job_type,
            q=q,
            statuses=[status.value for status in JobStatus],
        ),
    )


@app.get("/workers", response_class=HTMLResponse)
def workers(
    request: Request,
    queued_reviews: int | None = None,
) -> HTMLResponse:
    with read_session() as session:
        existing = {
            control.service_name: control
            for control in list_service_controls(session)
        }
        blocked_jobs = recent_blocked_jobs(session)
        failed_jobs = recent_failed_jobs(session)
    rows: list[dict[str, object]] = []
    for service_name in SERVICE_NAMES:
        control = existing.get(service_name)
        rows.append(
            {
                "service_name": service_name,
                "desired_state": control.desired_state if control else "paused",
                "current_state": control.current_state if control else "stopped",
                "current_job_id": control.current_job_id if control else None,
                "last_heartbeat_at": (
                    control.last_heartbeat_at if control else None
                ),
                "message": control.message if control else None,
                "status_note": (
                    "Worker daemon is connected."
                    if control is not None
                    and control.last_heartbeat_at is not None
                    else "No worker daemon has reported in yet."
                ),
            }
        )
    return templates.TemplateResponse(
        request,
        "workers.html",
        _context(
            request,
            active="workers",
            controls=rows,
            blocked_jobs=blocked_jobs,
            failed_jobs=failed_jobs,
            worker_daemon_online=any(
                row["last_heartbeat_at"] is not None for row in rows
            ),
            queued_reviews=queued_reviews,
        ),
    )


@app.post("/service-controls/{service_name}/{action}")
def control_worker(service_name: str, action: str) -> RedirectResponse:
    if service_name not in SERVICE_NAMES:
        raise HTTPException(status_code=400, detail="Unsupported worker")
    desired_state = DESIRED_STATE_ACTIONS.get(action)
    if desired_state is None:
        raise HTTPException(status_code=400, detail="Unsupported worker action")
    with write_session() as session, session.begin():
        control = set_service_control_desired_state(
            session,
            service_name,
            desired_state,
        )
        if control is None:
            raise HTTPException(status_code=404, detail="Worker not found")
    return RedirectResponse("/workers", status_code=303)


@app.post("/jobs/{job_id}/{action}")
def control_job(job_id: int, action: str) -> RedirectResponse:
    print(
        f"[web] control_job job_id={job_id} action={action}",
        file=sys.stderr,
        flush=True,
    )
    actions = {
        "pause": JobStatus.PAUSED,
        "resume": JobStatus.PENDING,
        "retry": JobStatus.PENDING,
        "skip": JobStatus.SKIPPED,
        "unrecoverable": JobStatus.FAILED,
    }
    status = actions.get(action)
    with write_session() as session, session.begin():
        if action == "promote":
            job = promote_job(session, job_id)
        elif status is not None:
            job = set_job_status(session, job_id, status)
        else:
            raise HTTPException(status_code=400, detail="Unsupported job action")
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if action == "unrecoverable":
            job.last_error = job.last_error or "Marked unrecoverable by user."
            job.finished_at = datetime.now(UTC)
            session.flush()
        print(
            f"[web] control_job updated job_id={job_id} status={job.status}",
            file=sys.stderr,
            flush=True,
        )
    return RedirectResponse("/jobs", status_code=303)


@app.get("/jobs/{job_id}/diagnostics")
def job_diagnostics(job_id: int) -> JSONResponse:
    directory = _diagnostic_directory(job_id)
    if not directory.exists():
        raise HTTPException(status_code=404, detail="No diagnostics found")

    files: list[dict[str, object]] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        entry: dict[str, object] = {
            "name": path.name,
            "size": path.stat().st_size,
        }
        if path.suffix in {".json", ".txt", ".html"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            entry["text"] = text[:20_000]
            if path.suffix == ".json":
                try:
                    entry["json"] = json.loads(text)
                except json.JSONDecodeError:
                    pass
        files.append(entry)
    return JSONResponse({"job_id": job_id, "files": files})


@app.get("/browser")
def browser_session(request: Request) -> RedirectResponse:
    return RedirectResponse(
        f"{_browser_public_url()}/vnc.html?autoconnect=true&resize=remote",
        status_code=303,
    )


@app.get("/captcha", response_class=HTMLResponse)
def captcha_home(request: Request) -> HTMLResponse:
    with read_session() as session:
        counts = {
            kind: session.scalar(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.job_type == job_type.value,
                    Job.status.in_(
                        status.value for status in CAPTCHA_ACTIONABLE_STATUSES
                    ),
                    Job.finished_at.is_(None),
                )
            )
            or 0
            for kind, job_type in CAPTCHA_JOB_TYPES.items()
        }
    return templates.TemplateResponse(
        request,
        "captcha.html",
        _context(
            request,
            active="captcha",
            kind="",
            title="Overview",
            counts=counts,
        ),
    )


def _render_captcha_job(
    request: Request,
    *,
    kind: str,
    job_id: int | None = None,
    brand: str | None = None,
    marked: int | None = None,
    stale: int | None = None,
) -> HTMLResponse:
    job_type = _captcha_job_type(kind)
    with write_session() as session:
        if kind == "brand" and brand:
            from project_dm.brands import normalize_brand

            try:
                data = normalize_brand(brand)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            with session.begin():
                record = upsert_brand(session, data)
                job, _ = get_or_create_brand_listing_job(
                    session,
                    brand_id=record.id,
                    target_url=record.listing_url,
                )
                job = _activate_captcha_job(session, job)
                row = _captcha_job_row(session, job)
            return templates.TemplateResponse(
                request,
                "captcha.html",
                _context(
                    request,
                    active="captcha",
                    kind=kind,
                    title="Brand",
                    job=row["job"],
                    job_row=row,
                    open_url=row["open_url"],
                    next_url="/captcha/brand",
                    brand_value=brand,
                    marked=bool(marked),
                ),
            )

        job: Job | None = None
        with session.begin():
            if job_id is not None:
                job = session.get(Job, job_id)
                if job is None:
                    raise HTTPException(status_code=404, detail="Job not found")
                if job.job_type != job_type.value:
                    raise HTTPException(
                        status_code=400,
                        detail="Job type does not match captcha page",
                    )
                if job.finished_at is not None and job.status != JobStatus.COMPLETED.value:
                    job = None
            else:
                job = _next_captcha_job(session, kind)
            if job is not None:
                job = _activate_captcha_job(session, job)
                row = _captcha_job_row(session, job)
            else:
                row = None
    return templates.TemplateResponse(
        request,
        "captcha.html",
        _context(
            request,
            active="captcha",
            kind=kind,
            title=_captcha_kind_label(kind),
            job=row["job"] if row else None,
            job_row=row,
            open_url=row["open_url"] if row else None,
            next_url=f"/captcha/{kind}",
            brand_value=brand or "",
            marked=bool(marked),
            stale=bool(stale),
        ),
    )


@app.get("/captcha/brand", response_class=HTMLResponse)
def captcha_brand(
    request: Request,
    brand: str = "",
    job_id: int | None = None,
    marked: int | None = None,
) -> HTMLResponse:
    return _render_captcha_job(
        request,
        kind="brand",
        job_id=job_id,
        brand=brand or None,
        marked=marked,
    )


@app.get("/captcha/brand/{job_id}", response_class=HTMLResponse)
def captcha_brand_job(
    request: Request,
    job_id: int,
    marked: int | None = None,
) -> HTMLResponse:
    return _render_captcha_job(request, kind="brand", job_id=job_id, marked=marked)


@app.post("/captcha/brand")
def submit_captcha_brand(
    brand: Annotated[str, Form(min_length=1)],
) -> RedirectResponse:
    return RedirectResponse(
        f"/captcha/brand?brand={quote_plus(brand)}",
        status_code=303,
    )


@app.post("/captcha/brand/{job_id}/unrecoverable")
def captcha_brand_unrecoverable(job_id: int) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.BRAND_LISTING.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        set_job_status(session, job_id, JobStatus.FAILED)
        job = session.get(Job, job_id)
        if job is not None:
            job.last_error = job.last_error or "Marked unrecoverable by user."
            job.finished_at = datetime.now(UTC)
            session.flush()
    return RedirectResponse("/captcha/brand?marked=1", status_code=303)


@app.get("/captcha/product", response_class=HTMLResponse)
def captcha_product(
    request: Request,
    job_id: int | None = None,
    marked: int | None = None,
) -> HTMLResponse:
    return _render_captcha_job(
        request, kind="product", job_id=job_id, marked=marked
    )


@app.get("/captcha/product/{job_id}", response_class=HTMLResponse)
def captcha_product_job(
    request: Request,
    job_id: int,
    marked: int | None = None,
) -> HTMLResponse:
    return _render_captcha_job(
        request, kind="product", job_id=job_id, marked=marked
    )


@app.post("/captcha/product/{job_id}/unrecoverable")
def captcha_product_unrecoverable(job_id: int) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.PRODUCT.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        set_job_status(session, job_id, JobStatus.FAILED)
        job = session.get(Job, job_id)
        if job is not None:
            job.last_error = job.last_error or "Marked unrecoverable by user."
            job.finished_at = datetime.now(UTC)
            session.flush()
    return RedirectResponse("/captcha/product?marked=1", status_code=303)


@app.get("/captcha/product/{job_id}/browser")
def solve_captcha_product_in_browser(job_id: int) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.PRODUCT.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        scraper_control = next(
            (
                control
                for control in list_service_controls(session)
                if control.service_name == "scraper"
            ),
            None,
        )
        should_requeue = (
            job.status
            in {
                JobStatus.FAILED.value,
                JobStatus.BLOCKED.value,
                JobStatus.PAUSED.value,
            }
            or scraper_control is None
            or scraper_control.current_job_id != job.id
        )
        if should_requeue:
            set_job_status(session, job_id, JobStatus.PENDING)
        job = session.get(Job, job_id)
        if job is not None and job.last_error:
            job.last_error = None
            session.flush()
    return RedirectResponse("/browser", status_code=303)


@app.get("/captcha/review", response_class=HTMLResponse)
def captcha_review(
    request: Request,
    job_id: int | None = None,
    marked: int | None = None,
) -> HTMLResponse:
    return _render_captcha_job(
        request, kind="review", job_id=job_id, marked=marked
    )


@app.get("/jobs/{job_id}/solve")
def solve_job(request: Request, job_id: int) -> RedirectResponse:
    return RedirectResponse(f"/captcha/review?job_id={job_id}", status_code=303)


@app.get("/captcha/review/{job_id}", response_class=HTMLResponse)
def captcha_review_job(
    request: Request,
    job_id: int,
    stale: int | None = None,
    marked: int | None = None,
) -> HTMLResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.REVIEWS.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        job = _activate_captcha_job(session, job)
        row = _captcha_job_row(
            session,
            job,
            include_next_review_url=True,
        )
    return templates.TemplateResponse(
        request,
        "captcha.html",
        _context(
            request,
            active="captcha",
            kind="review",
            title="Review",
            job=row["job"],
            job_row=row,
            open_url=row["open_url"],
            next_open_url=row["next_open_url"],
            review_page_size=REVIEW_PAGE_SIZE,
            next_url="/captcha/review",
            stale=bool(stale),
            marked=bool(marked),
        ),
    )


@app.get("/captcha/review/{job_id}/open")
def open_captcha_review(job_id: int) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.REVIEWS.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        row = _captcha_job_row(
            session,
            job,
            include_next_review_url=True,
        )
        review_url = row["open_url"]
        if not review_url:
            raise HTTPException(status_code=400, detail="Job has no review URL")
        if job.status in {
            JobStatus.FAILED.value,
            JobStatus.BLOCKED.value,
            JobStatus.PAUSED.value,
        }:
            set_job_status(session, job_id, JobStatus.PENDING)
        # Manual browser intervention should clear any stale failure text so
        # the mobile status view reflects the current solve attempt instead of
        # an earlier 404/410 response.
        job = session.get(Job, job_id)
        if job is not None and job.last_error:
            job.last_error = None
            session.flush()
    return RedirectResponse(review_url, status_code=303)


@app.get("/captcha/review/{job_id}/browser")
def solve_captcha_review_in_browser(job_id: int) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.REVIEWS.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        scraper_control = next(
            (
                control
                for control in list_service_controls(session)
                if control.service_name == "scraper"
            ),
            None,
        )
        should_requeue = (
            job.status in {
                JobStatus.FAILED.value,
                JobStatus.BLOCKED.value,
                JobStatus.PAUSED.value,
            }
            or scraper_control is None
            or scraper_control.current_job_id != job.id
        )
        if should_requeue:
            set_job_status(session, job_id, JobStatus.PENDING)
            job = session.get(Job, job_id)
        if job is not None and job.last_error:
            job.last_error = None
            session.flush()
    return RedirectResponse("/browser", status_code=303)


@app.get("/captcha/brand/{job_id}/browser")
def solve_captcha_brand_in_browser(job_id: int) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.BRAND_LISTING.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        scraper_control = next(
            (
                control
                for control in list_service_controls(session)
                if control.service_name == "scraper"
            ),
            None,
        )
        should_requeue = (
            job.status
            in {
                JobStatus.FAILED.value,
                JobStatus.BLOCKED.value,
                JobStatus.PAUSED.value,
            }
            or scraper_control is None
            or scraper_control.current_job_id != job.id
        )
        if should_requeue:
            set_job_status(session, job_id, JobStatus.PENDING)
        job = session.get(Job, job_id)
        if job is not None and job.last_error:
            job.last_error = None
            session.flush()
    return RedirectResponse("/browser", status_code=303)


@app.get("/api/captcha/review/{job_id}/debug")
def captcha_review_debug(job_id: int) -> JSONResponse:
    with write_session() as session:
        state = _captcha_job_debug_state(session, job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(state)


@app.get("/api/captcha/review/{job_id}")
def captcha_review_status(job_id: int) -> JSONResponse:
    # Use the write connection here so the mobile progress view reflects the
    # latest state immediately after a manual browser intervention.
    with write_session() as session:
        state = _captcha_job_state(session, job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JSONResponse(state)


@app.post("/captcha/review/{job_id}/unrecoverable")
def captcha_review_unrecoverable(job_id: int) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.job_type != JobType.REVIEWS.value:
            raise HTTPException(
                status_code=400,
                detail="Job type does not match captcha page",
            )
        set_job_status(session, job_id, JobStatus.FAILED)
        job = session.get(Job, job_id)
        if job is not None:
            job.last_error = job.last_error or "Marked unrecoverable by user."
            job.finished_at = datetime.now(UTC)
            session.flush()
    return RedirectResponse("/captcha/review?marked=1", status_code=303)


@app.post("/jobs/{job_id}/solve")
def submit_solved_review(
    job_id: int,
    payload: dict[str, object] = Body(...),
) -> RedirectResponse:
    return _submit_review_payload(job_id, payload)


@app.post("/captcha/review/{job_id}")
def submit_captcha_review(
    job_id: int,
    payload: dict[str, object] = Body(...),
) -> RedirectResponse:
    return _submit_review_payload(job_id, payload)


def _submit_review_payload(
    job_id: int,
    payload: dict[str, object],
) -> RedirectResponse:
    with write_session() as session, session.begin():
        job = session.get(Job, job_id, with_for_update=True)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.family_id is None:
            raise HTTPException(
                status_code=400, detail="Job is missing family metadata"
            )
        reviews_seen, _, checkpoint_status = apply_review_payload(
            session,
            job_id=job.id,
            family_id=job.family_id,
            payload=payload,
            page_size=REVIEW_PAGE_SIZE,
        )
        if checkpoint_status is JobStatus.COMPLETED:
            message = f"Manual solve imported {reviews_seen} reviews and completed the job."
        else:
            message = f"Manual solve imported {reviews_seen} reviews."
        if checkpoint_status is not JobStatus.COMPLETED:
            set_job_status(session, job_id, JobStatus.PENDING)
        print(
            f"[web] submit_solved_review job_id={job_id} reviews_seen={reviews_seen} status={checkpoint_status} message={message}",
            file=sys.stderr,
            flush=True,
        )
    return RedirectResponse("/captcha/review", status_code=303)


@app.post("/brands")
def add_brand(
    brand: Annotated[str, Form(min_length=1)],
) -> RedirectResponse:
    try:
        data = normalize_brand(brand)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    with write_session() as session, session.begin():
        record = upsert_brand(session, data)
        get_or_create_brand_listing_job(
            session,
            brand_id=record.id,
            target_url=record.listing_url,
        )
    return RedirectResponse("/jobs", status_code=303)


def _run_worker(
    worker: str,
    *,
    max_pages: int | None = 1,
    attended_browser: bool = False,
) -> None:
    if worker == "listing":
        run_one_listing_job(
            max_pages=max_pages,
            attended_browser=attended_browser,
        )
    elif worker == "product":
        run_product_jobs(attended_browser=attended_browser)
    elif worker == "reviews":
        run_one_review_job(attended_browser=attended_browser)
    elif worker == "nlp":
        run_nlp_batch()


@app.post("/workers/{worker}/run")
def run_worker(
    worker: str,
    max_pages: Annotated[int | None, Form()] = 1,
    attended_browser: Annotated[bool, Form()] = False,
) -> RedirectResponse:
    print(
        "[web] run_worker "
        f"module={__file__} worker={worker} max_pages={max_pages} attended_browser={attended_browser}",
        file=sys.stderr,
        flush=True,
    )
    if worker not in {"listing", "product", "reviews", "nlp"}:
        raise HTTPException(status_code=400, detail="Unsupported worker")
    if max_pages is not None and max_pages < 1:
        raise HTTPException(status_code=422, detail="max_pages must be positive")
    threading.Thread(
        target=_run_worker,
        kwargs={
            "worker": worker,
            "max_pages": max_pages,
            "attended_browser": attended_browser,
        },
        daemon=True,
        name=f"project-dm-{worker}-run",
    ).start()
    return RedirectResponse("/jobs", status_code=303)


@app.post("/workers/reviews/queue-missing")
def queue_missing_reviews() -> RedirectResponse:
    print("[web] queue_missing_reviews", file=sys.stderr, flush=True)
    with write_session() as session, session.begin():
        queued = queue_missing_review_jobs(session)
    return RedirectResponse(f"/workers?queued_reviews={queued}", status_code=303)


@app.get("/products", response_class=HTMLResponse)
def products(
    request: Request,
    q: str = "",
    brand_id: int | None = None,
) -> HTMLResponse:
    with read_session() as session:
        rows = list_families(
            session, query=q or None, brand_id=brand_id
        )
        brands = list_brands(session)
    return templates.TemplateResponse(
        request,
        "products.html",
        _context(
            request,
            active="products",
            products=rows,
            brands=brands,
            q=q,
            brand_id=brand_id,
        ),
    )


@app.get("/products/{family_id}", response_class=HTMLResponse)
def product_detail(request: Request, family_id: int) -> HTMLResponse:
    with read_session() as session:
        product = family_detail(session, family_id)
        reviews = list_reviews(session, family_id=family_id, limit=50)
        recommendations = recommendations_for_family(
            session,
            family_id=family_id,
            limit=6,
        )
    if product is None:
        raise HTTPException(status_code=404, detail="Product family not found")
    family = product["family"]
    return templates.TemplateResponse(
        request,
        "product_detail.html",
        _context(
            request,
            active="products",
            product=product,
            reviews=reviews,
            recommendations=recommendations,
            reviews_url=build_reviews_url(
                family.url,
                offset=0,
            ),
            scrape_reviews_endpoint=f"/products/{family_id}/scrape-reviews",
        ),
    )


@app.post("/products/{family_id}/recommendations/regenerate")
def regenerate_product_recommendations_view(
    family_id: int,
) -> RedirectResponse:
    with write_session() as session, session.begin():
        regenerate_product_recommendations(session)
    return RedirectResponse(f"/products/{family_id}", status_code=303)


def _optional_bool(value: str) -> bool | None:
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


@app.get("/reviews", response_class=HTMLResponse)
def reviews(
    request: Request,
    q: str = "",
    rating: int | None = None,
    verified: str = "",
    helpful: str = "",
) -> HTMLResponse:
    with read_session() as session:
        rows = list_reviews(
            session,
            query=q or None,
            rating=rating,
            verified=_optional_bool(verified),
            helpful=_optional_bool(helpful),
        )
        summary = review_summary(session)
        ratings = rating_counts(session)
    return templates.TemplateResponse(
        request,
        "reviews.html",
        _context(
            request,
            active="reviews",
            reviews=rows,
            summary=summary,
            rating_counts=ratings,
            q=q,
            rating=rating,
            verified=verified,
            helpful=helpful,
        ),
    )


@app.post("/products/{family_id}/scrape-reviews")
def scrape_product_reviews(
    family_id: int,
    payload: dict[str, object] = Body(...),
) -> JSONResponse:
    with write_session() as session, session.begin():
        family = session.get(ProductFamily, family_id, with_for_update=True)
        if family is None:
            raise HTTPException(status_code=404, detail="Product family not found")
        reviews_seen, review_count = import_review_payload(
            session,
            family_id=family_id,
            payload=payload,
        )
        if review_count:
            family.review_count = review_count
        family.scraped_at = datetime.now(UTC)
    return JSONResponse(
        {
            "family_id": family_id,
            "reviews_seen": reviews_seen,
            "review_count": review_count,
            "message": "Reviews imported from pasted JSON.",
        }
    )


def main() -> None:
    start_worker_supervisors()
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=8000,
            reload=False,
        )
    finally:
        stop_worker_supervisors()


if __name__ == "__main__":
    main()
