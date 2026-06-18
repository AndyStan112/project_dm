from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from project_dm.db import write_session
from project_dm.repositories.jobs import (
    checkpoint_review_page,
    claim_pending_job,
    fail_job,
    set_job_status,
)
from project_dm.repositories.reviews import find_variant_id, upsert_review
from project_dm.repositories.service_controls import update_service_control_state
from project_dm.schemas import JobStatus, JobType, ReviewCreate
from project_dm.scraping.guards import visible_page_is_blocked
from project_dm.scraping.reviews import build_reviews_url, parse_review_page
from project_dm.workers.listing import current_status, open_browser, save_diagnostic


@dataclass(frozen=True)
class ReviewRunResult:
    job_id: int | None
    pages_processed: int
    reviews_upserted: int
    status: JobStatus | None
    message: str


def _save_json_diagnostic(
    payload: object, *, job_id: int, label: str
) -> None:
    directory = Path("data") / "diagnostics" / f"job_{job_id}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{label}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_one_review_job(
    *,
    max_pages: int | None = 1,
    page_size: int = 10,
    min_delay: float = 5.0,
    max_delay: float = 10.0,
) -> ReviewRunResult:
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive")
    if page_size < 1 or page_size > 100:
        raise ValueError("page_size must be between 1 and 100")
    if min_delay < 0 or max_delay < min_delay:
        raise ValueError("Invalid delay range")

    with write_session() as session, session.begin():
        job = claim_pending_job(session, job_types=(JobType.REVIEWS,))
        if job is None:
            with write_session() as heartbeat_session, heartbeat_session.begin():
                update_service_control_state(
                    heartbeat_session,
                    "scraper",
                    current_state=JobStatus.RUNNING.value,
                    current_job_id=None,
                    message="No pending review job.",
                )
            return ReviewRunResult(
                job_id=None,
                pages_processed=0,
                reviews_upserted=0,
                status=None,
                message="No pending review job.",
            )
        job_id = job.id
        family_id = job.family_id
        target_url = job.target_url
        offset = job.current_offset
        update_service_control_state(
            session,
            "scraper",
            current_state=JobStatus.RUNNING.value,
            current_job_id=job_id,
            message=f"Processing review job #{job_id}.",
        )

    if family_id is None or not target_url:
        message = "Review job is missing family_id or target_url."
        with write_session() as session, session.begin():
            fail_job(
                session,
                job_id=job_id,
                status=JobStatus.FAILED,
                message=message,
            )
        return ReviewRunResult(
            job_id=job_id,
            pages_processed=0,
            reviews_upserted=0,
            status=JobStatus.FAILED,
            message=message,
        )

    pages_processed = 0
    reviews_upserted = 0
    playwright = None
    browser = None
    try:
        playwright, browser = open_browser()
        context = browser.new_context(
            locale="ro-RO",
            timezone_id="Europe/Bucharest",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        product_response = page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=60_000,
        )
        page.wait_for_timeout(3_000)
        product_status = (
            product_response.status if product_response is not None else None
        )
        visible_text = page.locator("body").inner_text(timeout=5_000)
        if product_status in {403, 429} or visible_page_is_blocked(
            visible_text
        ):
            save_diagnostic(page, job_id, "blocked_review_product")
            message = (
                f"eMAG blocked review session setup (HTTP {product_status})."
            )
            with write_session() as session, session.begin():
                fail_job(
                    session,
                    job_id=job_id,
                    status=JobStatus.BLOCKED,
                    message=message,
                )
            return ReviewRunResult(
                job_id=job_id,
                pages_processed=0,
                reviews_upserted=0,
                status=JobStatus.BLOCKED,
                message=message,
            )

        while True:
            status = current_status(job_id)
            if status is not JobStatus.RUNNING:
                return ReviewRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    reviews_upserted=reviews_upserted,
                    status=status,
                    message=f"Stopped because job status is {status}.",
                )

            reviews_url = build_reviews_url(
                target_url, offset=offset, limit=page_size
            )
            response = context.request.get(reviews_url, timeout=60_000)
            if response.status in {403, 429}:
                message = (
                    f"eMAG blocked review request (HTTP {response.status})."
                )
                with write_session() as session, session.begin():
                    fail_job(
                        session,
                        job_id=job_id,
                        status=JobStatus.BLOCKED,
                        message=message,
                    )
                return ReviewRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    reviews_upserted=reviews_upserted,
                    status=JobStatus.BLOCKED,
                    message=message,
                )
            if not response.ok:
                raise RuntimeError(
                    f"Review endpoint returned HTTP {response.status}"
                )

            payload = response.json()
            review_page = parse_review_page(payload)
            with write_session() as session, session.begin():
                for review in review_page.reviews:
                    variant_id = find_variant_id(
                        session,
                        family_id=family_id,
                        pnk=review.pnk,
                    )
                    values = review.model_dump(exclude={"pnk"})
                    upsert_review(
                        session,
                        ReviewCreate(
                            family_id=family_id,
                            variant_id=variant_id,
                            **values,
                        ),
                    )
                checkpoint = checkpoint_review_page(
                    session,
                    job_id=job_id,
                    reviews_seen=len(review_page.reviews),
                    total_expected=review_page.total_count,
                )
                checkpoint_status = JobStatus(checkpoint.status)
                offset = checkpoint.current_offset

            pages_processed += 1
            reviews_upserted += len(review_page.reviews)
            if checkpoint_status is JobStatus.COMPLETED:
                return ReviewRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    reviews_upserted=reviews_upserted,
                    status=checkpoint_status,
                    message="Review collection completed.",
                )

            if max_pages is not None and pages_processed >= max_pages:
                with write_session() as session, session.begin():
                    set_job_status(session, job_id, JobStatus.PAUSED)
                return ReviewRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    reviews_upserted=reviews_upserted,
                    status=JobStatus.PAUSED,
                    message="Paused after reaching the page limit.",
                )

            time.sleep(random.uniform(min_delay, max_delay))
    except PlaywrightTimeoutError as exc:
        message = f"Playwright timeout: {exc}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if "payload" in locals():
            _save_json_diagnostic(
                payload, job_id=job_id, label="invalid_review_response"
            )
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()

    with write_session() as session, session.begin():
        fail_job(
            session,
            job_id=job_id,
            status=JobStatus.FAILED,
            message=message,
        )
    return ReviewRunResult(
        job_id=job_id,
        pages_processed=pages_processed,
        reviews_upserted=reviews_upserted,
        status=JobStatus.FAILED,
        message=message,
    )
