from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session

from project_dm.models import Brand, Job, ProductFamily, Review, Variant


@dataclass(frozen=True)
class DashboardStats:
    brands: int
    families: int
    variants: int
    reviews: int
    verified_reviews: int
    helpful_reviews: int
    pending_jobs: int
    blocked_jobs: int


def dashboard_stats(session: Session) -> DashboardStats:
    return DashboardStats(
        brands=session.scalar(select(func.count()).select_from(Brand)) or 0,
        families=session.scalar(
            select(func.count()).select_from(ProductFamily)
        )
        or 0,
        variants=session.scalar(select(func.count()).select_from(Variant))
        or 0,
        reviews=session.scalar(select(func.count()).select_from(Review)) or 0,
        verified_reviews=session.scalar(
            select(func.count())
            .select_from(Review)
            .where(Review.verified_purchase.is_(True))
        )
        or 0,
        helpful_reviews=session.scalar(
            select(func.count())
            .select_from(Review)
            .where(Review.votes > 0)
        )
        or 0,
        pending_jobs=session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_(("pending", "running", "paused")))
        )
        or 0,
        blocked_jobs=session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_(("blocked", "failed")))
        )
        or 0,
    )


def job_status_counts(session: Session) -> list[tuple[str, int]]:
    return list(
        session.execute(
            select(Job.status, func.count())
            .group_by(Job.status)
            .order_by(Job.status)
        )
    )


def rating_counts(session: Session) -> list[tuple[int, int]]:
    rows = {
        rating: count
        for rating, count in session.execute(
            select(Review.rating, func.count()).group_by(Review.rating)
        )
    }
    return [(rating, rows.get(rating, 0)) for rating in range(5, 0, -1)]


def recent_jobs(session: Session, *, limit: int = 12) -> list[dict]:
    rows = session.execute(
        select(Job, Brand.slug, ProductFamily.name)
        .outerjoin(Brand, Brand.id == Job.brand_id)
        .outerjoin(ProductFamily, ProductFamily.id == Job.family_id)
        .order_by(Job.updated_at.desc())
        .limit(limit)
    )
    return [
        {"job": job, "brand_slug": brand_slug, "family_name": family_name}
        for job, brand_slug, family_name in rows
    ]


def recent_blocked_jobs(session: Session, *, limit: int = 5) -> list[dict]:
    rows = session.execute(
        select(Job, Brand.slug, ProductFamily.name)
        .outerjoin(Brand, Brand.id == Job.brand_id)
        .outerjoin(ProductFamily, ProductFamily.id == Job.family_id)
        .where(Job.status == "blocked")
        .order_by(Job.updated_at.desc())
        .limit(limit)
    )
    return [
        {"job": job, "brand_slug": brand_slug, "family_name": family_name}
        for job, brand_slug, family_name in rows
    ]


def recent_failed_jobs(session: Session, *, limit: int = 5) -> list[dict]:
    rows = session.execute(
        select(Job, Brand.slug, ProductFamily.name)
        .outerjoin(Brand, Brand.id == Job.brand_id)
        .outerjoin(ProductFamily, ProductFamily.id == Job.family_id)
        .where(Job.status == "failed")
        .order_by(Job.updated_at.desc())
        .limit(limit)
    )
    return [
        {"job": job, "brand_slug": brand_slug, "family_name": family_name}
        for job, brand_slug, family_name in rows
    ]


def list_jobs_for_dashboard(
    session: Session,
    *,
    status: str | None = None,
    job_type: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[dict]:
    statement = (
        select(Job, Brand.slug, ProductFamily.name)
        .outerjoin(Brand, Brand.id == Job.brand_id)
        .outerjoin(ProductFamily, ProductFamily.id == Job.family_id)
        .order_by(Job.updated_at.desc())
        .limit(limit)
    )
    if status:
        statement = statement.where(Job.status == status)
    if job_type:
        statement = statement.where(Job.job_type == job_type)
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            or_(
                Job.target_url.ilike(pattern),
                Brand.slug.ilike(pattern),
                ProductFamily.name.ilike(pattern),
            )
        )
    return [
        {"job": job, "brand_slug": brand_slug, "family_name": family_name}
        for job, brand_slug, family_name in session.execute(statement)
    ]


def list_families(
    session: Session,
    *,
    query: str | None = None,
    brand_id: int | None = None,
    limit: int = 100,
) -> list[dict]:
    review_rows = (
        select(
            Review.family_id.label("family_id"),
            func.count().label("collected_reviews"),
            func.coalesce(func.avg(Review.rating), 0).label("average_rating"),
            func.coalesce(func.avg(Review.votes), 0).label("average_votes"),
        )
        .group_by(Review.family_id)
        .subquery()
    )
    variant_rows = (
        select(
            Variant.family_id.label("family_id"),
            func.count().label("variant_count"),
            func.min(Variant.price).label("min_price"),
            func.max(Variant.price).label("max_price"),
        )
        .group_by(Variant.family_id)
        .subquery()
    )
    statement = (
        select(
            ProductFamily,
            Brand.slug,
            func.coalesce(variant_rows.c.variant_count, 0),
            variant_rows.c.min_price,
            variant_rows.c.max_price,
            func.coalesce(review_rows.c.collected_reviews, 0),
            review_rows.c.average_rating,
            review_rows.c.average_votes,
        )
        .join(Brand, Brand.id == ProductFamily.brand_id)
        .outerjoin(
            variant_rows, variant_rows.c.family_id == ProductFamily.id
        )
        .outerjoin(review_rows, review_rows.c.family_id == ProductFamily.id)
        .order_by(ProductFamily.updated_at.desc())
        .limit(limit)
    )
    if brand_id:
        statement = statement.where(ProductFamily.brand_id == brand_id)
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            or_(
                ProductFamily.name.ilike(pattern),
                ProductFamily.description.ilike(pattern),
            )
        )
    return [
        {
            "family": family,
            "brand_slug": brand_slug,
            "variant_count": variant_count,
            "min_price": min_price,
            "max_price": max_price,
            "collected_reviews": collected_reviews,
            "average_rating": average_rating,
            "average_votes": average_votes,
        }
        for (
            family,
            brand_slug,
            variant_count,
            min_price,
            max_price,
            collected_reviews,
            average_rating,
            average_votes,
        ) in session.execute(statement)
    ]


def family_detail(session: Session, family_id: int) -> dict | None:
    row = session.execute(
        select(ProductFamily, Brand.slug)
        .join(Brand, Brand.id == ProductFamily.brand_id)
        .where(ProductFamily.id == family_id)
    ).one_or_none()
    if row is None:
        return None
    family, brand_slug = row
    variants = list(
        session.scalars(
            select(Variant)
            .where(Variant.family_id == family_id)
            .order_by(Variant.storage, Variant.color)
        )
    )
    return {
        "family": family,
        "brand_slug": brand_slug,
        "variants": variants,
        "rating_counts": rating_counts_for_family(session, family_id),
    }


def rating_counts_for_family(
    session: Session, family_id: int
) -> list[tuple[int, int]]:
    rows = {
        rating: count
        for rating, count in session.execute(
            select(Review.rating, func.count())
            .where(Review.family_id == family_id)
            .group_by(Review.rating)
        )
    }
    return [(rating, rows.get(rating, 0)) for rating in range(5, 0, -1)]


def _review_statement() -> Select:
    return (
        select(Review, ProductFamily.name, Brand.slug, Variant.pnk)
        .join(ProductFamily, ProductFamily.id == Review.family_id)
        .join(Brand, Brand.id == ProductFamily.brand_id)
        .outerjoin(Variant, Variant.id == Review.variant_id)
    )


def list_reviews(
    session: Session,
    *,
    query: str | None = None,
    family_id: int | None = None,
    rating: int | None = None,
    verified: bool | None = None,
    helpful: bool | None = None,
    limit: int = 100,
) -> list[dict]:
    statement = _review_statement().order_by(
        Review.published_at.desc().nullslast(), Review.id.desc()
    )
    if family_id:
        statement = statement.where(Review.family_id == family_id)
    if rating:
        statement = statement.where(Review.rating == rating)
    if verified is not None:
        statement = statement.where(Review.verified_purchase.is_(verified))
    if helpful is not None:
        statement = statement.where(
            Review.votes > 0 if helpful else Review.votes == 0
        )
    if query:
        pattern = f"%{query.strip()}%"
        statement = statement.where(
            or_(
                Review.title.ilike(pattern),
                Review.content.ilike(pattern),
                Review.reviewer_name.ilike(pattern),
                ProductFamily.name.ilike(pattern),
            )
        )
    statement = statement.limit(limit)
    return [
        {
            "review": review,
            "family_name": family_name,
            "brand_slug": brand_slug,
            "pnk": pnk,
        }
        for review, family_name, brand_slug, pnk in session.execute(statement)
    ]


def review_summary(session: Session) -> dict:
    row = session.execute(
        select(
            func.count(Review.id),
            func.coalesce(func.avg(Review.rating), 0),
            func.coalesce(func.avg(Review.votes), 0),
            func.sum(case((Review.verified_purchase.is_(True), 1), else_=0)),
            func.sum(case((Review.votes > 0, 1), else_=0)),
        )
    ).one()
    return {
        "count": row[0] or 0,
        "average_rating": row[1] or 0,
        "average_votes": row[2] or 0,
        "verified": row[3] or 0,
        "helpful": row[4] or 0,
    }
