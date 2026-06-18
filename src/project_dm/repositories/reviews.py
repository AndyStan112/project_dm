from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from project_dm.models import Review, Variant
from project_dm.schemas import ReviewCreate


def find_variant_id(
    session: Session, *, family_id: int, pnk: str | None
) -> int | None:
    if not pnk:
        return None
    return session.scalar(
        select(Variant.id).where(
            Variant.family_id == family_id,
            Variant.pnk == pnk,
        )
    )


def upsert_review(session: Session, data: ReviewCreate) -> Review:
    values = data.model_dump(mode="json")
    statement = (
        insert(Review)
        .values(**values)
        .on_conflict_do_update(
            index_elements=(Review.emag_review_id,),
            set_={
                key: value
                for key, value in values.items()
                if key != "emag_review_id"
            },
        )
        .returning(Review)
    )
    return session.scalars(
        statement.execution_options(populate_existing=True)
    ).one()


def backfill_review_variants(session: Session, *, family_id: int) -> int:
    matching_variant = (
        select(Variant.id)
        .where(
            Variant.family_id == family_id,
            Variant.storage == Review.storage,
            Variant.color == Review.color,
        )
        .limit(1)
        .scalar_subquery()
    )
    result = session.execute(
        update(Review)
        .where(
            Review.family_id == family_id,
            Review.variant_id.is_(None),
            matching_variant.is_not(None),
        )
        .values(variant_id=matching_variant)
    )
    return result.rowcount
