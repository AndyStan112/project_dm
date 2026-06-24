from __future__ import annotations

from project_dm.nlp import ReviewDocument, build_nlp_records, clean_review_text


def test_clean_review_text_normalizes_whitespace_and_case() -> None:
    assert clean_review_text("  Great Phone  ", "Very   GOOD!") == (
        "great phone very good"
    )


def test_build_nlp_records_adds_sentiment_and_language() -> None:
    records = build_nlp_records(
        [
            ReviewDocument(
                review_id=1,
                title="Excelent",
                content="Acest telefon este foarte bun si il recomand.",
                rating=5,
            ),
            ReviewDocument(
                review_id=2,
                title="Bad buy",
                content="This phone is terrible and broken.",
                rating=1,
            ),
        ]
    )

    first, second = records
    assert first.cleaned_text == "excelent acest telefon este foarte bun si il recomand"
    assert first.language == "ro"
    assert first.sentiment_label == "positive"
    assert first.rating_mismatch is False
    assert second.language == "en"
    assert second.sentiment_label == "negative"
    assert second.rating_mismatch is False


def test_build_nlp_records_computes_distinctive_tfidf_terms() -> None:
    records = build_nlp_records(
        [
            ReviewDocument(
                review_id=1,
                title="Great battery",
                content="Great battery battery life and good camera.",
                rating=5,
            ),
            ReviewDocument(
                review_id=2,
                title="Great screen",
                content="Great screen and good speakers.",
                rating=4,
            ),
        ]
    )

    terms = records[0].tfidf_terms
    assert terms
    assert terms[0]["term"] == "battery"
    assert terms[0]["score"] > 0
