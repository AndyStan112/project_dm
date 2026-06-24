from __future__ import annotations

from dataclasses import dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass(frozen=True)
class RecommendationDocument:
    family_id: int
    text: str


@dataclass(frozen=True)
class RecommendationScore:
    recommended_family_id: int
    score: float
    rank: int


MODEL_NAME = "tfidf-ngrams-cosine-v1"


def normalize_text(text: str) -> str:
    return " ".join(text.split())


def build_recommendation_documents(
    families: list[tuple[int, str, str, str | None]],
    review_texts_by_family: dict[int, list[str]],
    variant_texts_by_family: dict[int, list[str]],
) -> list[RecommendationDocument]:
    documents: list[RecommendationDocument] = []
    for family_id, brand_slug, name, description in families:
        parts = [brand_slug, name, description or ""]
        parts.extend(review_texts_by_family.get(family_id, []))
        parts.extend(variant_texts_by_family.get(family_id, []))
        documents.append(
            RecommendationDocument(
                family_id=family_id,
                text=normalize_text(" ".join(part for part in parts if part)),
            )
        )
    return documents


def build_recommendation_scores(
    documents: list[RecommendationDocument],
    *,
    top_k: int = 6,
) -> dict[int, list[RecommendationScore]]:
    if len(documents) < 2 or not any(document.text.strip() for document in documents):
        return {document.family_id: [] for document in documents}

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        lowercase=True,
        strip_accents="unicode",
        token_pattern=r"(?u)\b[a-z0-9]+\b",
    )
    try:
        matrix = vectorizer.fit_transform([document.text for document in documents])
    except ValueError:
        return {document.family_id: [] for document in documents}
    similarity_matrix = cosine_similarity(matrix)

    results: dict[int, list[RecommendationScore]] = {}
    family_ids = [document.family_id for document in documents]
    for row_index, source_family_id in enumerate(family_ids):
        similarities = similarity_matrix[row_index]
        ranked_indices = sorted(
            (
                (candidate_index, float(score))
                for candidate_index, score in enumerate(similarities)
                if candidate_index != row_index and score > 0
            ),
            key=lambda item: (-item[1], family_ids[item[0]]),
        )
        results[source_family_id] = [
            RecommendationScore(
                recommended_family_id=family_ids[candidate_index],
                score=score,
                rank=rank,
            )
            for rank, (candidate_index, score) in enumerate(
                ranked_indices[:top_k],
                start=1,
            )
        ]
    return results
