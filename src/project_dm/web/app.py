from __future__ import annotations

import threading
import sys
import json
from pathlib import Path
from typing import Annotated
from dataclasses import asdict

import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from project_dm.brands import normalize_brand
from project_dm.db import read_session, write_session
from project_dm.repositories.brands import list_brands, upsert_brand
from project_dm.repositories.dashboard import (
    dashboard_stats,
    family_detail,
    job_status_counts,
    list_families,
    list_jobs_for_dashboard,
    list_reviews,
    rating_counts,
    recent_jobs,
    review_summary,
)
from project_dm.repositories.jobs import (
    get_or_create_brand_listing_job,
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
from project_dm.schemas import JobStatus
from project_dm.workers.listing import run_one_listing_job
from project_dm.workers.product import run_product_jobs
from project_dm.workers.reviews import run_one_review_job
from project_dm.workers.supervisor import (
    SCRAPER_LANE_COUNT,
    start_worker_supervisors,
    stop_worker_supervisors,
)


PACKAGE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
DIAGNOSTICS_DIR = Path("data") / "diagnostics"

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
    review_page_size: int = 10,
    review_max_pages: int = 1,
) -> HTMLResponse:
    with read_session() as session:
        existing = {
            control.service_name: control
            for control in list_service_controls(session)
        }
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
            worker_daemon_online=any(
                row["last_heartbeat_at"] is not None for row in rows
            ),
            queued_reviews=queued_reviews,
            review_page_size=review_page_size,
            review_max_pages=review_max_pages,
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
    page_size: int = 10,
) -> None:
    if worker == "listing":
        run_one_listing_job(max_pages=max_pages)
    elif worker == "product":
        run_product_jobs(max_jobs=1)
    elif worker == "reviews":
        run_one_review_job(max_pages=max_pages, page_size=page_size)


@app.post("/workers/{worker}/run")
def run_worker(
    worker: str,
    max_pages: Annotated[int | None, Form()] = 1,
    page_size: Annotated[int, Form()] = 10,
) -> RedirectResponse:
    print(
        "[web] run_worker "
        f"module={__file__} worker={worker} max_pages={max_pages} page_size={page_size}",
        file=sys.stderr,
        flush=True,
    )
    if worker not in {"listing", "product", "reviews"}:
        raise HTTPException(status_code=400, detail="Unsupported worker")
    if max_pages is not None and max_pages < 1:
        raise HTTPException(status_code=422, detail="max_pages must be positive")
    if page_size < 1:
        raise HTTPException(status_code=422, detail="page_size must be positive")
    threading.Thread(
        target=_run_worker,
        kwargs={
            "worker": worker,
            "max_pages": max_pages,
            "page_size": page_size,
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
    if product is None:
        raise HTTPException(status_code=404, detail="Product family not found")
    return templates.TemplateResponse(
        request,
        "product_detail.html",
        _context(
            request,
            active="products",
            product=product,
            reviews=reviews,
        ),
    )


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
