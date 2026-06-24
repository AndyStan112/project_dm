from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from project_dm.db import write_session
from project_dm.models import Review
from project_dm.nlp import ReviewDocument, build_nlp_records
from project_dm.repositories.nlp import list_reviews_for_nlp, upsert_nlp_result
from project_dm.schemas import NlpResultCreate


@dataclass(frozen=True)
class NlpRunResult:
    reviews_processed: int
    message: str


def run_nlp_batch(*, limit: int | None = None, top_k: int = 12) -> NlpRunResult:
    with write_session() as session, session.begin():
        pending_rows = list_reviews_for_nlp(session, limit=limit)
        if not pending_rows:
            return NlpRunResult(
                reviews_processed=0,
                message="No reviews need NLP processing.",
            )

        all_rows = list(
            session.execute(
                select(Review.id, Review.title, Review.content, Review.rating)
                .order_by(Review.id.asc())
            )
        )
        documents = [
            ReviewDocument(
                review_id=review_id,
                title=title,
                content=content,
                rating=rating,
            )
            for review_id, title, content, rating in all_rows
        ]
        nlp_records = build_nlp_records(documents, top_k=top_k)
        pending_ids = {review_id for review_id, *_ in pending_rows}
        for record in nlp_records:
            if record.review_id not in pending_ids:
                continue
            upsert_nlp_result(
                session,
                NlpResultCreate(
                    review_id=record.review_id,
                    cleaned_text=record.cleaned_text,
                    language=record.language,
                    sentiment_label=record.sentiment_label,
                    sentiment_score=record.sentiment_score,
                    rating_mismatch=record.rating_mismatch,
                    token_count=record.token_count,
                    unique_token_count=record.unique_token_count,
                    tfidf_terms=[],
                    model_name=record.model_name,
                ),
            )
    return NlpRunResult(
        reviews_processed=len(pending_rows),
        message=f"Processed {len(pending_rows)} reviews.",
    )
