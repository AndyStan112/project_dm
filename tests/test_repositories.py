from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from project_dm.brands import normalize_brand
from project_dm.db import DatabaseRole, engine
from project_dm.repositories.brands import upsert_brand
from project_dm.repositories.jobs import (
    create_job,
    checkpoint_listing_page,
    checkpoint_review_page,
    claim_pending_job,
    complete_product_job,
    get_or_create_review_job,
    get_or_create_product_job,
    get_or_create_brand_listing_job,
    queue_missing_review_jobs,
    set_job_status,
)
from project_dm.repositories.products import (
    upsert_product_family,
    upsert_variant,
)
from project_dm.repositories.reviews import (
    backfill_review_variants,
    find_variant_id,
    upsert_review,
)
from project_dm.schemas import (
    JobCreate,
    JobStatus,
    JobType,
    ListingProduct,
    ProductFamilyCreate,
    ReviewCreate,
    VariantCreate,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL_WRITE"),
    reason="DATABASE_URL_WRITE is not configured",
)


def test_brand_and_listing_job_are_idempotent() -> None:
    slug = f"test-{uuid4().hex}"
    data = normalize_brand(slug)

    with engine(DatabaseRole.WRITE).connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            brand = upsert_brand(session, data)
            first_job, first_created = get_or_create_brand_listing_job(
                session,
                brand_id=brand.id,
                target_url=brand.listing_url,
            )
            second_job, second_created = get_or_create_brand_listing_job(
                session,
                brand_id=brand.id,
                target_url=brand.listing_url,
            )

            assert first_created is True
            assert second_created is False
            assert second_job.id == first_job.id

            claimed = claim_pending_job(
                session,
                job_types=(JobType.BRAND_LISTING,),
                brand_id=brand.id,
            )
            assert claimed is not None
            assert claimed.id == first_job.id
            assert claimed.status == JobStatus.RUNNING.value
            assert claimed.attempts == 1
            assert claimed.locked_at is not None

            product = ListingProduct(
                emag_product_id=123,
                offer_id=456,
                family_id=789,
                pnk="TESTPNK",
                title="Test phone",
                url="https://www.emag.ro/test-phone/pd/TESTPNK/",
            )
            first_product_job, product_created = get_or_create_product_job(
                session,
                brand_id=brand.id,
                product=product,
            )
            second_product_job, duplicate_created = get_or_create_product_job(
                session,
                brand_id=brand.id,
                product=product,
            )
            assert product_created is True
            assert duplicate_created is False
            assert second_product_job.id == first_product_job.id

            checkpointed = checkpoint_listing_page(
                session,
                job_id=claimed.id,
                next_url=(
                    f"https://www.emag.ro/telefoane-mobile/brand/"
                    f"{slug}/p2/c"
                ),
                products_seen=1,
            )
            assert checkpointed.status == JobStatus.RUNNING.value
            assert checkpointed.current_offset == 1
            assert checkpointed.total_expected == 1
            assert checkpointed.target_url.endswith("/p2/c")

            family_data = ProductFamilyCreate(
                brand_id=brand.id,
                emag_family_id=789,
                name="Test phone family",
                aggregate_rating="4.50",
                review_count=12,
                url="https://www.emag.ro/test-phone/pd/TESTPNK/",
            )
            family = upsert_product_family(session, family_data)
            updated_family = upsert_product_family(
                session,
                family_data.model_copy(update={"review_count": 13}),
            )
            assert updated_family.id == family.id
            assert updated_family.review_count == 13

            variant_data = VariantCreate(
                family_id=family.id,
                emag_product_id=123,
                pnk="TESTPNK",
                title="Test phone, 128 GB, Black",
                storage="128 GB",
                color="Black",
                price="999.99",
                currency="RON",
                available=True,
                url="https://www.emag.ro/test-phone/pd/TESTPNK/",
            )
            variant = upsert_variant(session, variant_data)
            updated_variant = upsert_variant(
                session,
                variant_data.model_copy(update={"price": Decimal("899.99")}),
            )
            assert updated_variant.id == variant.id
            assert str(updated_variant.price) == "899.99"

            first_review_job, review_created = get_or_create_review_job(
                session,
                brand_id=brand.id,
                family_id=family.id,
                target_url=str(family_data.url),
            )
            second_review_job, duplicate_review_created = (
                get_or_create_review_job(
                    session,
                    brand_id=brand.id,
                    family_id=family.id,
                    target_url=str(family_data.url),
                )
            )
            assert review_created is True
            assert duplicate_review_created is False
            assert second_review_job.id == first_review_job.id

            claimed_review_job = claim_pending_job(
                session,
                job_types=(JobType.REVIEWS,),
                brand_id=brand.id,
            )
            assert claimed_review_job is not None
            assert claimed_review_job.id == first_review_job.id

            assert (
                find_variant_id(
                    session, family_id=family.id, pnk=variant.pnk
                )
                == variant.id
            )
            review_data = ReviewCreate(
                emag_review_id=987654321,
                family_id=family.id,
                variant_id=variant.id,
                title="Excellent",
                content="A useful test review.",
                rating=5,
                votes=2,
                verified_purchase=True,
                reviewer_name="Test User",
                reviewer_hash="test-reviewer-hash",
                storage="128 GB",
                color="Black",
                avatar_metadata={
                    "classification_hint": "default_name",
                    "initials": "TU",
                },
            )
            review = upsert_review(session, review_data)
            updated_review = upsert_review(
                session,
                review_data.model_copy(update={"votes": 3}),
            )
            assert updated_review.id == review.id
            assert updated_review.votes == 3

            updated_review.variant_id = None
            session.flush()
            assert backfill_review_variants(
                session, family_id=family.id
            ) == 1
            session.refresh(updated_review)
            assert updated_review.variant_id == variant.id

            review_checkpoint = checkpoint_review_page(
                session,
                job_id=claimed_review_job.id,
                reviews_seen=10,
                total_expected=12,
            )
            assert review_checkpoint.status == JobStatus.RUNNING.value
            assert review_checkpoint.current_offset == 10
            assert review_checkpoint.total_expected == 12

            completed_review_job = checkpoint_review_page(
                session,
                job_id=claimed_review_job.id,
                reviews_seen=2,
                total_expected=12,
            )
            assert (
                completed_review_job.status == JobStatus.COMPLETED.value
            )
            assert completed_review_job.current_offset == 12

            short_page_job = create_job(
                session,
                JobCreate(
                    job_type=JobType.REVIEWS,
                    family_id=family.id,
                    target_url="https://example.invalid",
                    current_offset=480,
                    total_expected=530,
                ),
            )
            short_page_job.status = JobStatus.RUNNING.value
            session.flush()
            short_page_checkpoint = checkpoint_review_page(
                session,
                job_id=short_page_job.id,
                reviews_seen=30,
                total_expected=530,
                page_size=100,
            )
            assert short_page_checkpoint.status == JobStatus.COMPLETED.value
            assert short_page_checkpoint.current_offset == 510
            assert short_page_checkpoint.total_expected == 510

            completed = complete_product_job(
                session, job_id=first_product_job.id, family_id=family.id
            )
            assert completed.status == JobStatus.COMPLETED.value
            assert completed.current_offset == 1
            assert completed.total_expected == 1
            assert completed.family_id == family.id
        finally:
            session.close()
            transaction.rollback()


def test_queue_missing_review_jobs_resumes_paused_jobs() -> None:
    slug = f"test-{uuid4().hex}"
    data = normalize_brand(slug)

    with engine(DatabaseRole.WRITE).connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            brand = upsert_brand(session, data)
            family = upsert_product_family(
                session,
                ProductFamilyCreate(
                    brand_id=brand.id,
                    emag_family_id=uuid4().int % 1_000_000_000,
                    name="Paused review family",
                    review_count=50,
                    url="https://www.emag.ro/test-phone/pd/PAUSEDREV/",
                ),
            )
            job, created = get_or_create_review_job(
                session,
                brand_id=brand.id,
                family_id=family.id,
                target_url=str(family.url),
            )
            assert created is True
            paused = set_job_status(session, job.id, JobStatus.PAUSED)
            assert paused is not None
            assert paused.status == JobStatus.PAUSED.value

            queued = queue_missing_review_jobs(session)
            assert queued == 1

            refreshed = session.get(type(job), job.id)
            assert refreshed is not None
            assert refreshed.status == JobStatus.PENDING.value
        finally:
            session.close()
            transaction.rollback()
