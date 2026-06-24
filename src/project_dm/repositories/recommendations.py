from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from project_dm.models import (
    Brand,
    ProductFamily,
    ProductRecommendation,
    Review,
    Variant,
)
from project_dm.recommendations import (
    MODEL_NAME,
    build_recommendation_documents,
    build_recommendation_scores,
)


def _family_rows(
    session: Session,
) -> list[tuple[int, str, str, str | None]]:
    return list(
        session.execute(
            select(
                ProductFamily.id,
                Brand.slug,
                ProductFamily.name,
                ProductFamily.description,
            )
            .join(Brand, Brand.id == ProductFamily.brand_id)
            .order_by(ProductFamily.id.asc())
        )
    )


def _review_texts(session: Session) -> dict[int, list[str]]:
    texts: dict[int, list[str]] = defaultdict(list)
    for family_id, title, content in session.execute(
        select(Review.family_id, Review.title, Review.content)
    ):
        parts = [part.strip() for part in (title, content) if part and part.strip()]
        if parts:
            texts[family_id].append(" ".join(parts))
    return texts


def _variant_texts(session: Session) -> dict[int, list[str]]:
    texts: dict[int, list[str]] = defaultdict(list)
    for family_id, title, storage, color in session.execute(
        select(Variant.family_id, Variant.title, Variant.storage, Variant.color)
    ):
        parts = [part.strip() for part in (title, storage, color) if part and part.strip()]
        if parts:
            texts[family_id].append(" ".join(parts))
    return texts


def regenerate_product_recommendations(
    session: Session,
    *,
    top_k: int = 6,
) -> int:
    families = _family_rows(session)
    review_texts = _review_texts(session)
    variant_texts = _variant_texts(session)
    documents = build_recommendation_documents(
        families,
        review_texts,
        variant_texts,
    )
    scores_by_family = build_recommendation_scores(documents, top_k=top_k)

    session.execute(delete(ProductRecommendation))
    rows_inserted = 0
    for source_family_id, recommendations in scores_by_family.items():
        for recommendation in recommendations:
            session.add(
                ProductRecommendation(
                    source_family_id=source_family_id,
                    recommended_family_id=recommendation.recommended_family_id,
                    rank=recommendation.rank,
                    score=recommendation.score,
                    model_name=MODEL_NAME,
                )
            )
            rows_inserted += 1
    session.flush()
    return rows_inserted


def recommendations_for_family(
    session: Session,
    *,
    family_id: int,
    limit: int = 6,
) -> list[dict[str, object]]:
    rows = session.execute(
        select(ProductRecommendation, ProductFamily, Brand.slug)
        .join(
            ProductFamily,
            ProductFamily.id == ProductRecommendation.recommended_family_id,
        )
        .join(Brand, Brand.id == ProductFamily.brand_id)
        .where(ProductRecommendation.source_family_id == family_id)
        .order_by(ProductRecommendation.rank.asc())
        .limit(limit)
    )
    return [
        {
            "recommendation": recommendation,
            "family": family,
            "brand_slug": brand_slug,
        }
        for recommendation, family, brand_slug in rows
    ]


def latest_recommendation_model(session: Session) -> str | None:
    return session.scalar(
        select(ProductRecommendation.model_name)
        .order_by(ProductRecommendation.created_at.desc())
        .limit(1)
    )
