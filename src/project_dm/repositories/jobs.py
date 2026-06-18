from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from project_dm.models import Job, ProductFamily, Review
from project_dm.schemas import JobCreate, JobStatus, JobType, ListingProduct


ACTIVE_STATUSES = (
    JobStatus.PENDING.value,
    JobStatus.RUNNING.value,
    JobStatus.PAUSED.value,
    JobStatus.BLOCKED.value,
)


def create_job(session: Session, data: JobCreate) -> Job:
    values = data.model_dump(mode="json")
    job = Job(**values)
    session.add(job)
    session.flush()
    return job


def get_or_create_brand_listing_job(
    session: Session,
    *,
    brand_id: int,
    target_url: str,
) -> tuple[Job, bool]:
    existing = session.scalar(
        select(Job)
        .where(
            Job.job_type == JobType.BRAND_LISTING.value,
            Job.brand_id == brand_id,
            Job.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, False

    job = create_job(
        session,
        JobCreate(
            job_type=JobType.BRAND_LISTING,
            brand_id=brand_id,
            target_url=target_url,
        ),
    )
    return job, True


def get_or_create_product_job(
    session: Session,
    *,
    brand_id: int,
    product: ListingProduct,
) -> tuple[Job, bool]:
    target_url = str(product.url)
    existing = session.scalar(
        select(Job)
        .where(
            Job.job_type == JobType.PRODUCT.value,
            Job.brand_id == brand_id,
            Job.target_url == target_url,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, False

    job = create_job(
        session,
        JobCreate(
            job_type=JobType.PRODUCT,
            brand_id=brand_id,
            target_url=target_url,
            priority=300,
        ),
    )
    return job, True


def get_or_create_review_job(
    session: Session,
    *,
    brand_id: int,
    family_id: int,
    target_url: str,
) -> tuple[Job, bool]:
    existing = session.scalar(
        select(Job)
        .where(
            Job.job_type == JobType.REVIEWS.value,
            Job.family_id == family_id,
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, False

    job = create_job(
        session,
        JobCreate(
            job_type=JobType.REVIEWS,
            brand_id=brand_id,
            family_id=family_id,
            target_url=target_url,
            priority=200,
        ),
    )
    return job, True


def list_jobs(
    session: Session,
    *,
    status: JobStatus | None = None,
    limit: int = 100,
) -> list[Job]:
    statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status is not None:
        statement = statement.where(Job.status == status.value)
    return list(session.scalars(statement))


def claim_pending_job(
    session: Session,
    *,
    job_types: tuple[JobType, ...] | None = None,
    brand_id: int | None = None,
) -> Job | None:
    statement = (
        select(Job)
        .where(Job.status == JobStatus.PENDING.value)
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job_types:
        statement = statement.where(
            Job.job_type.in_(job_type.value for job_type in job_types)
        )
    if brand_id is not None:
        statement = statement.where(Job.brand_id == brand_id)

    job = session.scalar(statement)
    if job is None:
        return None

    job.status = JobStatus.RUNNING.value
    job.attempts += 1
    job.locked_at = datetime.now(UTC)
    session.flush()
    return job


def set_job_status(
    session: Session,
    job_id: int,
    status: JobStatus,
) -> Job | None:
    job = session.get(Job, job_id)
    if job is None:
        return None

    job.status = status.value
    job.locked_at = None
    if status in {JobStatus.COMPLETED, JobStatus.SKIPPED}:
        job.finished_at = datetime.now(UTC)
    else:
        job.finished_at = None
    session.flush()
    return job


def promote_job(
    session: Session,
    job_id: int,
    *,
    priority: int = -1000,
) -> Job | None:
    job = session.get(Job, job_id)
    if job is None:
        return None

    job.priority = priority
    session.flush()
    return job


def queue_missing_review_jobs(
    session: Session,
    *,
    limit: int | None = None,
) -> int:
    collected_reviews = (
        select(
            Review.family_id.label("family_id"),
            func.count().label("collected_reviews"),
        )
        .group_by(Review.family_id)
        .subquery()
    )
    statement = (
        select(
            ProductFamily.id,
            ProductFamily.brand_id,
            ProductFamily.url,
            ProductFamily.review_count,
            func.coalesce(collected_reviews.c.collected_reviews, 0),
        )
        .outerjoin(
            collected_reviews, collected_reviews.c.family_id == ProductFamily.id
        )
        .order_by(ProductFamily.updated_at.desc())
    )
    if limit is not None:
        statement = statement.limit(limit)

    queued = 0
    for family_id, brand_id, url, reported_total, collected_total in session.execute(
        statement
    ):
        if reported_total is None:
            if collected_total == 0:
                continue
        elif collected_total >= reported_total:
            continue

        existing_job = session.scalar(
            select(Job.id)
            .where(
                Job.job_type == JobType.REVIEWS.value,
                Job.family_id == family_id,
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        if existing_job is not None:
            job = session.get(Job, existing_job)
            if job is not None and job.status == JobStatus.PAUSED.value:
                set_job_status(session, job.id, JobStatus.PENDING)
                queued += 1
                continue
            if job is not None and job.status in {
                JobStatus.PENDING.value,
                JobStatus.RUNNING.value,
            }:
                continue

        create_job(
            session,
            JobCreate(
                job_type=JobType.REVIEWS,
                brand_id=brand_id,
                family_id=family_id,
                target_url=url,
                priority=200,
            ),
        )
        queued += 1
    return queued


def checkpoint_listing_page(
    session: Session,
    *,
    job_id: int,
    next_url: str | None,
    products_seen: int,
) -> Job:
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise RuntimeError(f"Job {job_id} no longer exists")
    if job.status not in {
        JobStatus.RUNNING.value,
        JobStatus.PAUSED.value,
    }:
        return job

    job.current_offset += 1
    job.total_expected = (job.total_expected or 0) + products_seen
    job.target_url = next_url or job.target_url
    job.locked_at = None
    if next_url is None:
        job.status = JobStatus.COMPLETED.value
        job.finished_at = datetime.now(UTC)
    session.flush()
    return job


def complete_product_job(
    session: Session, *, job_id: int, family_id: int
) -> Job:
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise RuntimeError(f"Job {job_id} no longer exists")
    job.family_id = family_id
    job.current_offset = 1
    job.total_expected = 1
    job.status = JobStatus.COMPLETED.value
    job.locked_at = None
    job.finished_at = datetime.now(UTC)
    session.flush()
    return job


def checkpoint_review_page(
    session: Session,
    *,
    job_id: int,
    reviews_seen: int,
    total_expected: int,
) -> Job:
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        raise RuntimeError(f"Job {job_id} no longer exists")
    if job.status not in {
        JobStatus.RUNNING.value,
        JobStatus.PAUSED.value,
    }:
        return job

    job.current_offset += reviews_seen
    job.total_expected = total_expected
    job.locked_at = None
    if reviews_seen == 0 or job.current_offset >= total_expected:
        job.status = JobStatus.COMPLETED.value
        job.finished_at = datetime.now(UTC)
    session.flush()
    return job


def fail_job(
    session: Session,
    *,
    job_id: int,
    status: JobStatus,
    message: str,
) -> Job | None:
    if status not in {JobStatus.FAILED, JobStatus.BLOCKED}:
        raise ValueError("Failure status must be failed or blocked")
    job = session.get(Job, job_id, with_for_update=True)
    if job is None:
        return None
    job.status = status.value
    job.last_error = message[:4_000]
    job.locked_at = None
    session.flush()
    return job
