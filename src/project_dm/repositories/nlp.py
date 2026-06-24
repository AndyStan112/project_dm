from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from project_dm.models import NlpResult, Review
from project_dm.schemas import NlpResultCreate


def list_reviews_for_nlp(
    session: Session,
    *,
    limit: int | None = None,
) -> list[tuple[int, str | None, str, int]]:
    statement = (
        select(Review.id, Review.title, Review.content, Review.rating)
        .outerjoin(NlpResult, NlpResult.review_id == Review.id)
        .where(
            NlpResult.review_id.is_(None)
            | (Review.updated_at > NlpResult.updated_at)
        )
        .order_by(Review.id.asc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(session.execute(statement))


def upsert_nlp_result(session: Session, data: NlpResultCreate) -> NlpResult:
    values = data.model_dump(mode="json")
    statement = (
        insert(NlpResult)
        .values(**values)
        .on_conflict_do_update(
            index_elements=(NlpResult.review_id,),
            set_={
                key: value
                for key, value in values.items()
                if key != "review_id"
            }
            | {"updated_at": func.now()},
        )
        .returning(NlpResult)
    )
    return session.scalars(
        statement.execution_options(populate_existing=True)
    ).one()
