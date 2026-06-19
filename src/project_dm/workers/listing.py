from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import (
    Browser,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from project_dm.db import read_session, write_session
from project_dm.models import Job
from project_dm.repositories.jobs import (
    checkpoint_listing_page,
    claim_pending_job,
    fail_job,
    get_or_create_product_job,
    set_job_status,
)
from project_dm.repositories.service_controls import update_service_control_state
from project_dm.schemas import JobStatus, JobType
from project_dm.scraping.guards import visible_page_is_blocked
from project_dm.scraping.listing import parse_listing_page


@dataclass(frozen=True)
class ListingRunResult:
    job_id: int | None
    pages_processed: int
    product_jobs_created: int
    status: JobStatus | None
    message: str


def current_status(job_id: int) -> JobStatus | None:
    with read_session() as session:
        job = session.get(Job, job_id)
        return JobStatus(job.status) if job is not None else None


def save_diagnostic(page: Page, job_id: int, label: str) -> None:
    directory = Path("data") / "diagnostics" / f"job_{job_id}"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{label}.html").write_text(
        page.content(), encoding="utf-8"
    )
    page.screenshot(path=directory / f"{label}.png", full_page=True)


def open_browser(
    *, attended_browser: bool | None = None
) -> tuple[object, Browser]:
    playwright = sync_playwright().start()
    if attended_browser is None:
        attended = os.getenv("PROJECT_DM_ATTENDED_BROWSER", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    else:
        attended = attended_browser
    browser = playwright.chromium.launch(
        headless=not attended,
        slow_mo=50 if attended else 0,
    )
    return playwright, browser


def run_one_listing_job(
    *,
    max_pages: int | None = None,
    min_delay: float = 5.0,
    max_delay: float = 10.0,
) -> ListingRunResult:
    if min_delay < 0 or max_delay < min_delay:
        raise ValueError("Invalid delay range")
    if max_pages is not None and max_pages < 1:
        raise ValueError("max_pages must be positive")

    with write_session() as session, session.begin():
        job = claim_pending_job(
            session, job_types=(JobType.BRAND_LISTING,)
        )
        if job is None:
            with write_session() as heartbeat_session, heartbeat_session.begin():
                update_service_control_state(
                    heartbeat_session,
                    "scraper",
                    current_state=JobStatus.RUNNING.value,
                    current_job_id=None,
                    message="No pending brand listing job.",
                )
            return ListingRunResult(
                job_id=None,
                pages_processed=0,
                product_jobs_created=0,
                status=None,
                message="No pending brand listing job.",
            )
        job_id = job.id
        brand_id = job.brand_id
        target_url = job.target_url
        update_service_control_state(
            session,
            "scraper",
            current_state=JobStatus.RUNNING.value,
            current_job_id=job_id,
            message=f"Processing brand listing job #{job_id}.",
        )

    if brand_id is None or not target_url:
        with write_session() as session, session.begin():
            fail_job(
                session,
                job_id=job_id,
                status=JobStatus.FAILED,
                message="Listing job is missing brand_id or target_url.",
            )
        return ListingRunResult(
            job_id=job_id,
            pages_processed=0,
            product_jobs_created=0,
            status=JobStatus.FAILED,
            message="Listing job is missing required target data.",
        )

    pages_processed = 0
    product_jobs_created = 0
    playwright = None
    browser = None
    try:
        playwright, browser = open_browser()
        page = browser.new_page(
            locale="ro-RO",
            timezone_id="Europe/Bucharest",
            viewport={"width": 1440, "height": 1000},
        )

        while target_url:
            status = current_status(job_id)
            if status is not JobStatus.RUNNING:
                return ListingRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    product_jobs_created=product_jobs_created,
                    status=status,
                    message=f"Stopped because job status is {status}.",
                )

            response = page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            page.wait_for_timeout(3_000)
            http_status = response.status if response else None
            visible_text = page.locator("body").inner_text(timeout=5_000)

            if http_status in {403, 429} or visible_page_is_blocked(
                visible_text
            ):
                save_diagnostic(page, job_id, f"blocked_page_{pages_processed}")
                message = f"eMAG blocked listing request (HTTP {http_status})."
                with write_session() as session, session.begin():
                    fail_job(
                        session,
                        job_id=job_id,
                        status=JobStatus.BLOCKED,
                        message=message,
                    )
                return ListingRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    product_jobs_created=product_jobs_created,
                    status=JobStatus.BLOCKED,
                    message=message,
                )

            listing = parse_listing_page(page.content(), page.url)
            if not listing.products:
                save_diagnostic(page, job_id, f"empty_page_{pages_processed}")
                message = "Listing page contained no product cards."
                with write_session() as session, session.begin():
                    fail_job(
                        session,
                        job_id=job_id,
                        status=JobStatus.FAILED,
                        message=message,
                    )
                return ListingRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    product_jobs_created=product_jobs_created,
                    status=JobStatus.FAILED,
                    message=message,
                )

            next_url = (
                str(listing.next_url) if listing.next_url is not None else None
            )
            created_on_page = 0
            with write_session() as session, session.begin():
                for product in listing.products:
                    _, created = get_or_create_product_job(
                        session,
                        brand_id=brand_id,
                        product=product,
                    )
                    created_on_page += int(created)
                checkpointed = checkpoint_listing_page(
                    session,
                    job_id=job_id,
                    next_url=next_url,
                    products_seen=len(listing.products),
                )
                checkpoint_status = JobStatus(checkpointed.status)

            pages_processed += 1
            product_jobs_created += created_on_page
            target_url = next_url

            if checkpoint_status is JobStatus.COMPLETED:
                return ListingRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    product_jobs_created=product_jobs_created,
                    status=checkpoint_status,
                    message="Brand listing completed.",
                )

            if max_pages is not None and pages_processed >= max_pages:
                with write_session() as session, session.begin():
                    set_job_status(session, job_id, JobStatus.PAUSED)
                return ListingRunResult(
                    job_id=job_id,
                    pages_processed=pages_processed,
                    product_jobs_created=product_jobs_created,
                    status=JobStatus.PAUSED,
                    message="Paused after reaching the page limit.",
                )

            time.sleep(random.uniform(min_delay, max_delay))

    except PlaywrightTimeoutError as exc:
        message = f"Playwright timeout: {exc}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
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
    return ListingRunResult(
        job_id=job_id,
        pages_processed=pages_processed,
        product_jobs_created=product_jobs_created,
        status=JobStatus.FAILED,
        message=message,
    )
