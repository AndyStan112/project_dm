from __future__ import annotations

from project_dm.recommendations import (
    RecommendationDocument,
    build_recommendation_scores,
)


def test_build_recommendation_scores_ranks_similar_products() -> None:
    scores = build_recommendation_scores(
        [
            RecommendationDocument(
                family_id=1,
                text="battery life fast charging camera",
            ),
            RecommendationDocument(
                family_id=2,
                text="battery life and camera quality",
            ),
            RecommendationDocument(
                family_id=3,
                text="laptop keyboard trackpad office",
            ),
        ],
        top_k=2,
    )

    assert [row.recommended_family_id for row in scores[1]] == [2]
    assert [row.recommended_family_id for row in scores[2]] == [1]
    assert scores[3] == []
