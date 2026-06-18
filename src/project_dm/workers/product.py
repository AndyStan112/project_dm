from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from playwright.sync_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from project_dm.db import write_session
from project_dm.repositories.jobs import (
    claim_pending_job,
    complete_product_job,
    fail_job,
    get_or_create_review_job,
)
from project_dm.repositories.products import (
    upsert_product_family,
    upsert_variant,
)
from project_dm.repositories.service_controls import update_service_control_state
from project_dm.repositories.reviews import backfill_review_variants
from project_dm.schemas import (
    JobStatus,
    JobType,
    ProductFamilyCreate,
    VariantCreate,
)
from project_dm.scraping.guards import visible_page_is_blocked
from project_dm.scraping.product import parse_product_page
from project_dm.workers.listing import open_browser, save_diagnostic


@dataclass(frozen=True)
class ProductRunResult:
    jobs_processed: int
    variants_upserted: int
    review_jobs_created: int
    status: JobStatus | None
    message: str


def run_product_jobs(
    *,
    max_jobs: int = 1,
    min_delay: float = 5.0,
    max_delay: float = 10.0,
) -> ProductRunResult:
    if max_jobs < 1:
        raise ValueError("max_jobs must be positive")
    if min_delay < 0 or max_delay < min_delay:
        raise ValueError("Invalid delay range")

    jobs_processed = 0
    variants_upserted = 0
    review_jobs_created = 0
    playwright = None
    browser = None

    try:
        playwright, browser = open_browser()
        page = browser.new_page(
            locale="ro-RO",
            timezone_id="Europe/Bucharest",
            viewport={"width": 1440, "height": 1000},
        )

        while jobs_processed < max_jobs:
            with write_session() as session, session.begin():
                job = claim_pending_job(
                    session, job_types=(JobType.PRODUCT,)
                )
                if job is None:
                    with write_session() as heartbeat_session, heartbeat_session.begin():
                        update_service_control_state(
                            heartbeat_session,
                            "scraper",
                            current_state=JobStatus.RUNNING.value,
                            current_job_id=None,
                            message="No pending product job.",
                        )
                    return ProductRunResult(
                        jobs_processed=jobs_processed,
                        variants_upserted=variants_upserted,
                        review_jobs_created=review_jobs_created,
                        status=None,
                        message="No pending product job.",
                    )
                job_id = job.id
                brand_id = job.brand_id
                target_url = job.target_url
                update_service_control_state(
                    session,
                    "scraper",
                    current_state=JobStatus.RUNNING.value,
                    current_job_id=job_id,
                    message=f"Processing product job #{job_id}.",
                )

            if brand_id is None or not target_url:
                message = "Product job is missing brand_id or target_url."
                with write_session() as session, session.begin():
                    fail_job(
                        session,
                        job_id=job_id,
                        status=JobStatus.FAILED,
                        message=message,
                    )
                return ProductRunResult(
                    jobs_processed=jobs_processed,
                    variants_upserted=variants_upserted,
                    review_jobs_created=review_jobs_created,
                    status=JobStatus.FAILED,
                    message=message,
                )

            try:
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
                    save_diagnostic(page, job_id, "blocked_product")
                    message = (
                        f"eMAG blocked product request (HTTP {http_status})."
                    )
                    with write_session() as session, session.begin():
                        fail_job(
                            session,
                            job_id=job_id,
                            status=JobStatus.BLOCKED,
                            message=message,
                        )
                    return ProductRunResult(
                        jobs_processed=jobs_processed,
                        variants_upserted=variants_upserted,
                        review_jobs_created=review_jobs_created,
                        status=JobStatus.BLOCKED,
                        message=message,
                    )

                product = parse_product_page(page.content(), page.url)
                with write_session() as session, session.begin():
                    family = upsert_product_family(
                        session,
                        ProductFamilyCreate(
                            brand_id=brand_id,
                            emag_family_id=product.emag_family_id,
                            name=product.family_name,
                            description=product.description,
                            aggregate_rating=product.aggregate_rating,
                            review_count=product.review_count,
                            url=product.url,
                            scraped_at=datetime.now(UTC),
                        ),
                    )
                    for variant in product.variants:
                        upsert_variant(
                            session,
                            VariantCreate(
                                family_id=family.id,
                                **variant.model_dump(),
                            ),
                        )
                    backfill_review_variants(
                        session, family_id=family.id
                    )
                    _, review_created = get_or_create_review_job(
                        session,
                        brand_id=brand_id,
                        family_id=family.id,
                        target_url=str(product.url),
                    )
                    complete_product_job(
                        session, job_id=job_id, family_id=family.id
                    )

                jobs_processed += 1
                variants_upserted += len(product.variants)
                review_jobs_created += int(review_created)
            except PlaywrightTimeoutError as exc:
                message = f"Playwright timeout: {exc}"
                save_diagnostic(page, job_id, "product_timeout")
                with write_session() as session, session.begin():
                    fail_job(
                        session,
                        job_id=job_id,
                        status=JobStatus.FAILED,
                        message=message,
                    )
                return ProductRunResult(
                    jobs_processed=jobs_processed,
                    variants_upserted=variants_upserted,
                    review_jobs_created=review_jobs_created,
                    status=JobStatus.FAILED,
                    message=message,
                )
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                save_diagnostic(page, job_id, "invalid_product")
                with write_session() as session, session.begin():
                    fail_job(
                        session,
                        job_id=job_id,
                        status=JobStatus.FAILED,
                        message=message,
                    )
                return ProductRunResult(
                    jobs_processed=jobs_processed,
                    variants_upserted=variants_upserted,
                    review_jobs_created=review_jobs_created,
                    status=JobStatus.FAILED,
                    message=message,
                )

            if jobs_processed < max_jobs:
                time.sleep(random.uniform(min_delay, max_delay))
    finally:
        if browser is not None:
            browser.close()
        if playwright is not None:
            playwright.stop()

    return ProductRunResult(
        jobs_processed=jobs_processed,
        variants_upserted=variants_upserted,
        review_jobs_created=review_jobs_created,
        status=JobStatus.COMPLETED,
        message="Product batch limit reached.",
    )
