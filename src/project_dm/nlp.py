from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Sequence

from langdetect import DetectorFactory, LangDetectException, detect
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression


DetectorFactory.seed = 0

TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class ReviewDocument:
    review_id: int
    title: str | None
    content: str
    rating: int


@dataclass(frozen=True)
class NlpRecord:
    review_id: int
    cleaned_text: str
    language: str | None
    sentiment_label: str | None
    sentiment_score: float | None
    rating_mismatch: bool | None
    token_count: int
    unique_token_count: int
    tfidf_terms: list[dict[str, float | str]]
    model_name: str


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    )


def _normalize_text(text: str) -> str:
    text = _strip_accents(text.casefold())
    return " ".join(text.split())


def tokenize(text: str) -> list[str]:
    normalized = _normalize_text(text)
    return TOKEN_PATTERN.findall(normalized)


def clean_review_text(title: str | None, content: str) -> str:
    parts = [part.strip() for part in (title or "", content) if part.strip()]
    if not parts:
        return ""
    return " ".join(tokenize(" ".join(parts)))


def detect_language(text: str) -> str | None:
    normalized = text.strip()
    if not normalized:
        return None
    try:
        language = detect(normalized)
    except LangDetectException:
        return None
    return language


def expected_sentiment_label(rating: int) -> str | None:
    if rating >= 4:
        return "positive"
    if rating <= 2:
        return "negative"
    return "neutral"


def rating_mismatch(rating: int, sentiment_label: str | None) -> bool | None:
    expected = expected_sentiment_label(rating)
    if sentiment_label is None or expected is None:
        return None
    return sentiment_label != expected


def _tfidf_documents(texts: Sequence[str]) -> list[list[dict[str, float | str]]]:
    if not texts or not any(text.strip() for text in texts):
        return []

    vectorizer = TfidfVectorizer(
        token_pattern=r"(?u)\b[a-z0-9]+\b",
        lowercase=False,
        norm="l2",
    )
    try:
        matrix = vectorizer.fit_transform(texts)
    except ValueError:
        return [[] for _ in texts]
    features = vectorizer.get_feature_names_out()
    results: list[list[dict[str, float | str]]] = []
    for row_index in range(matrix.shape[0]):
        row = matrix.getrow(row_index)
        scored_terms = [
            (features[column], float(score))
            for column, score in zip(row.indices, row.data, strict=False)
        ]
        scored_terms.sort(key=lambda item: (-item[1], item[0]))
        results.append(
            [
                {"term": term, "score": round(score, 6)}
                for term, score in scored_terms[:12]
            ]
        )
    return results


def _sentiment_scores(
    documents: Sequence[ReviewDocument],
    cleaned_texts: Sequence[str],
) -> list[tuple[str | None, float | None]]:
    labels = [
        1 if document.rating >= 4 else 0 if document.rating <= 2 else None
        for document in documents
    ]
    train_texts = [
        text
        for text, label in zip(cleaned_texts, labels, strict=False)
        if label is not None
    ]
    train_labels = [label for label in labels if label is not None]

    if len(set(train_labels)) < 2:
        return [
            (expected_sentiment_label(document.rating), 0.5)
            for document in documents
        ]

    classifier = LogisticRegression(
        max_iter=1_000,
        class_weight="balanced",
    )
    vectorizer = TfidfVectorizer(
        token_pattern=r"(?u)\b[a-z0-9]+\b",
        lowercase=False,
        ngram_range=(1, 2),
        min_df=1,
    )
    try:
        features = vectorizer.fit_transform(train_texts)
    except ValueError:
        return [(None, None) for _ in documents]
    classifier.fit(features, train_labels)

    probabilities = classifier.predict_proba(vectorizer.transform(cleaned_texts))
    positive_index = list(classifier.classes_).index(1)
    results: list[tuple[str | None, float | None]] = []
    for document, probability in zip(
        documents, probabilities, strict=False
    ):
        positive_probability = float(probability[positive_index])
        if positive_probability >= 0.65:
            results.append(("positive", positive_probability))
        elif positive_probability <= 0.35:
            results.append(("negative", positive_probability))
        else:
            results.append(
                (expected_sentiment_label(document.rating), positive_probability)
            )
    return results


def build_nlp_records(
    documents: Sequence[ReviewDocument],
    *,
    model_name: str = "sklearn-sentiment-v1",
    top_k: int = 12,
) -> list[NlpRecord]:
    cleaned_texts = [clean_review_text(doc.title, doc.content) for doc in documents]
    tokenized_documents = [
        [token for token in tokenize(text)] for text in cleaned_texts
    ]
    tfidf_scores = _tfidf_documents(cleaned_texts)
    sentiment_scores = _sentiment_scores(documents, cleaned_texts)

    records: list[NlpRecord] = []
    for document, cleaned_text, tokens, tfidf_terms, (sentiment_label, sentiment_score) in zip(
        documents,
        cleaned_texts,
        tokenized_documents,
        tfidf_scores,
        sentiment_scores,
        strict=False,
    ):
        record = NlpRecord(
            review_id=document.review_id,
            cleaned_text=cleaned_text,
            language=detect_language(document.content or cleaned_text),
            sentiment_label=sentiment_label,
            sentiment_score=sentiment_score,
            rating_mismatch=rating_mismatch(document.rating, sentiment_label),
            token_count=len(tokens),
            unique_token_count=len(set(tokens)),
            tfidf_terms=tfidf_terms[:top_k],
            model_name=model_name,
        )
        records.append(record)
    return records
