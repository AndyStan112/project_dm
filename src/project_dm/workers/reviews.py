from __future__ import annotations

import json
import random
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from project_dm.db import write_session
from project_dm.models import ProductFamily
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
    path = directory / f"{label}.json"
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"[reviews] wrote diagnostic file {path}",
        file=sys.stderr,
        flush=True,
    )


def _save_response_diagnostic(
    *,
    job_id: int,
    label: str,
    url: str,
    status: int,
    headers: dict[str, str],
    body: str,
) -> None:
    _save_json_diagnostic(
        {
            "url": url,
            "status": status,
            "headers": headers,
            "body_preview": body[:4_000],
        },
        job_id=job_id,
        label=label,
    )


def _response_error_message(
    *,
    url: str,
    status: int,
    content_type: str | None,
    body: str,
) -> str:
    preview = " ".join(body.split())[:600]
    return (
        f"Review endpoint returned HTTP {status} "
        f"for {url} "
        f"(content-type={content_type or 'unknown'}; "
        f"body={preview!r})"
    )


def apply_review_payload(
    session,
    *,
    job_id: int,
    family_id: int,
    payload: dict[str, object],
) -> tuple[int, int, JobStatus]:
    review_page = parse_review_page(payload)
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
    return len(review_page.reviews), checkpoint.current_offset, JobStatus(
        checkpoint.status
    )


def run_one_review_job(
    *,
    max_pages: int | None = 1,
    page_size: int = 10,
    min_delay: float = 5.0,
    max_delay: float = 10.0,
) -> ReviewRunResult:
    print(
        "[reviews] enter run_one_review_job "
        f"module={__file__} max_pages={max_pages} page_size={page_size}",
        file=sys.stderr,
        flush=True,
    )
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive")
    if page_size > 10_000:
        raise ValueError("page_size must be 10000 or less")
    if min_delay < 0 or max_delay < min_delay:
        raise ValueError("Invalid delay range")

    with write_session() as session, session.begin():
        job = claim_pending_job(session, job_types=(JobType.REVIEWS,))
        if job is None:
            print(
                "[reviews] no pending review job",
                file=sys.stderr,
                flush=True,
            )
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
        print(
            "[reviews] claimed job "
            f"id={job_id} family_id={family_id} offset={offset} "
            f"target_url={target_url}",
            file=sys.stderr,
            flush=True,
        )
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

    effective_page_size = page_size
    if effective_page_size <= 0:
        with write_session() as session:
            family = session.get(ProductFamily, family_id)
            if family is not None and family.review_count:
                effective_page_size = int(family.review_count)
            elif job.total_expected:
                effective_page_size = int(job.total_expected)
            else:
                effective_page_size = 1_000
        print(
            "[reviews] auto page_size "
            f"job_id={job_id} effective_page_size={effective_page_size}",
            file=sys.stderr,
            flush=True,
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
        print(
            "[reviews] product page "
            f"job_id={job_id} status={product_status} blocked={visible_page_is_blocked(visible_text)}",
            file=sys.stderr,
            flush=True,
        )
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
                target_url, offset=offset, limit=effective_page_size
            )
            response = context.request.get(reviews_url, timeout=60_000)
            response_body = response.text()
            print(
                "[reviews] response "
                f"job_id={job_id} offset={offset} url={reviews_url} "
                f"status={response.status} content_type={response.headers.get('content-type')}",
                file=sys.stderr,
                flush=True,
            )
            if response.status in {403, 405, 429}:
                message = _response_error_message(
                    url=reviews_url,
                    status=response.status,
                    content_type=response.headers.get("content-type"),
                    body=response_body,
                )
                _save_response_diagnostic(
                    job_id=job_id,
                    label="blocked_review_request",
                    url=reviews_url,
                    status=response.status,
                    headers=dict(response.headers),
                    body=response_body,
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
                print(
                    "[reviews] non-ok response "
                    f"job_id={job_id} offset={offset} "
                    f"status={response.status} "
                    f"url={reviews_url} "
                    f"headers={dict(response.headers)} "
                    f"body={response_body}",
                    file=sys.stderr,
                    flush=True,
                )
                message = _response_error_message(
                    url=reviews_url,
                    status=response.status,
                    content_type=response.headers.get("content-type"),
                    body=response_body,
                )
                _save_response_diagnostic(
                    job_id=job_id,
                    label="invalid_review_response",
                    url=reviews_url,
                    status=response.status,
                    headers=dict(response.headers),
                    body=response_body,
                )
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

            payload = json.loads(response_body)
            with write_session() as session, session.begin():
                reviews_seen, offset, checkpoint_status = (
                    apply_review_payload(
                        session,
                        job_id=job_id,
                        family_id=family_id,
                        payload=payload,
                    )
                )

            pages_processed += 1
            reviews_upserted += reviews_seen
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
        traceback.print_exc()
        if "response_body" in locals():
            _save_response_diagnostic(
                job_id=job_id,
                label="review_response_exception",
                url=reviews_url,
                status=response.status,
                headers=dict(response.headers),
                body=response_body,
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
