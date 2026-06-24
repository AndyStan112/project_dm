from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent


def md(text: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": dedent(text).lstrip("\n").splitlines(True),
    }


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": dedent(text).lstrip("\n").splitlines(True),
    }


def build_notebook() -> dict:
    cells = [
        md(
            """
            # Review Likes EDA, Topics, and Feature Analysis

            This notebook is designed to rerun against the live database whenever new data arrives.

            It covers:
            - dataset loading and brand-level EDA
            - Apple vs Samsung comparisons where both brands exist
            - word-cloud style frequency visualizations
            - topic modeling over review text
            - feature analysis for predicting helpful votes / likes

            The notebook reads from `DATABASE_URL_READ`, so make sure that environment variable is set before running it.
            """
        ),
        code(
            """
            import os
            import random
            import re
            import sys
            from collections import Counter, defaultdict
            from pathlib import Path

            import matplotlib.pyplot as plt
            import numpy as np
            from scipy import sparse
            from sqlalchemy import func, select
            from sklearn.decomposition import NMF
            from sklearn.feature_extraction import DictVectorizer
            from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
            from sklearn.linear_model import ElasticNet, PoissonRegressor, Ridge, SGDRegressor
            from sklearn.metrics import mean_absolute_error, r2_score
            from sklearn.model_selection import train_test_split
            from sklearn.compose import TransformedTargetRegressor

            ROOT = Path.cwd()
            while ROOT != ROOT.parent and not (ROOT / "pyproject.toml").exists():
                ROOT = ROOT.parent

            SRC = ROOT / "src"
            if str(SRC) not in sys.path:
                sys.path.insert(0, str(SRC))

            os.environ.setdefault("MPLCONFIGDIR", str(Path("/tmp") / "matplotlib"))

            from project_dm.db import read_session
            from project_dm.models import Brand, NlpResult, ProductFamily, Review
            """
        ),
        code(
            """
            STOPWORDS = set(ENGLISH_STOP_WORDS) | {
                "a",
                "acest",
                "aceasta",
                "acesta",
                "acele",
                "acei",
                "adica",
                "al",
                "ale",
                "am",
                "are",
                "au",
                "ca",
                "cat",
                "ce",
                "cel",
                "cu",
                "dar",
                "de",
                "din",
                "doar",
                "după",
                "este",
                "eu",
                "fi",
                "fie",
                "fara",
                "foarte",
                "in",
                "la",
                "lipsa",
                "mai",
                "multe",
                "mult",
                "ne",
                "nu",
                "o",
                "pe",
                "pentru",
                "prin",
                "sau",
                "sa",
                "se",
                "si",
                "spre",
                "sub",
                "sunt",
                "tu",
                "un",
                "una",
                "unde",
                "voi",
                "vrei",
                "vă",
                "îmi",
                "îți",
                "și",
                "că",
                "nu",
                "review",
                "comentariu",
                "produs",
                "telefon",
                "telefoane",
                "device",
                "dispozitiv",
                "good",
                "bad",
                "great",
                "excellent",
                "love",
                "like",
                "apple",
                "samsung",
                "iphone",
                "galaxy",
            }

            TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9']+")
            URL_RE = re.compile(r"https?://\\S+|www\\.\\S+")
            SPACE_RE = re.compile(r"\\s+")

            def safe_text(value: str | None) -> str:
                if not value:
                    return ""
                value = URL_RE.sub(" ", value)
                value = value.replace("\\n", " ").replace("\\r", " ")
                value = SPACE_RE.sub(" ", value).strip().lower()
                return value

            def tokenize(value: str | None) -> list[str]:
                text = safe_text(value)
                if not text:
                    return []
                return [
                    token.lower()
                    for token in TOKEN_RE.findall(text)
                    if token.lower() not in STOPWORDS and len(token) > 1
                ]

            def word_count(value: str | None) -> int:
                return len(tokenize(value))

            def text_for_model(row: dict) -> str:
                cleaned = row.get("cleaned_text")
                if cleaned:
                    return safe_text(cleaned)
                pieces = [row.get("title") or "", row.get("content") or ""]
                return safe_text(" ".join(piece for piece in pieces if piece))

            def text_for_cloud(row: dict) -> str:
                pieces = [row.get("title") or "", row.get("content") or ""]
                cleaned = " ".join(piece for piece in pieces if piece)
                return safe_text(cleaned)

            def review_signature(row: dict) -> str:
                title = safe_text(row.get("title"))
                content = safe_text(row.get("content"))
                return f"{title} || {content}".strip()

            def preview_text(row: dict, limit: int = 180) -> str:
                text = safe_text(f"{row.get('title') or ''} {row.get('content') or ''}")
                return text[:limit] + ("..." if len(text) > limit else "")

            def pearson(x: list[float], y: list[float]) -> float:
                if len(x) < 2 or len(y) < 2:
                    return float("nan")
                x_arr = np.asarray(x, dtype=float)
                y_arr = np.asarray(y, dtype=float)
                if np.std(x_arr) == 0 or np.std(y_arr) == 0:
                    return float("nan")
                return float(np.corrcoef(x_arr, y_arr)[0, 1])

            def pct(part: int, whole: int) -> float:
                return round((part / whole) * 100, 1) if whole else 0.0

            def summarize_number(values: list[float]) -> dict[str, float]:
                if not values:
                    return {"count": 0, "mean": 0.0, "median": 0.0, "p90": 0.0}
                arr = np.asarray(values, dtype=float)
                return {
                    "count": int(arr.size),
                    "mean": float(np.mean(arr)),
                    "median": float(np.median(arr)),
                    "p90": float(np.percentile(arr, 90)),
                }
            """
        ),
        code(
            """
            session = read_session()

            statement = (
                select(
                    Review.id.label("review_id"),
                    Review.emag_review_id,
                    Review.family_id,
                    Brand.slug.label("brand_slug"),
                    ProductFamily.name.label("family_name"),
                    Review.title,
                    Review.content,
                    Review.rating,
                    Review.votes,
                    Review.verified_purchase,
                    Review.reviewer_name,
                    Review.reviewer_hash,
                    Review.review_created_at,
                    Review.published_at,
                    Review.storage,
                    Review.color,
                    Review.avatar_metadata,
                    NlpResult.cleaned_text,
                    NlpResult.language,
                    NlpResult.sentiment_label,
                    NlpResult.sentiment_score,
                    NlpResult.rating_mismatch,
                    NlpResult.token_count,
                    NlpResult.unique_token_count,
                    NlpResult.tfidf_terms,
                    NlpResult.model_name,
                )
                .join(ProductFamily, ProductFamily.id == Review.family_id)
                .join(Brand, Brand.id == ProductFamily.brand_id)
                .outerjoin(NlpResult, NlpResult.review_id == Review.id)
                .order_by(Brand.slug.asc(), Review.published_at.asc().nullslast(), Review.id.asc())
            )

            rows = [dict(row._mapping) for row in session.execute(statement)]

            if not rows:
                raise RuntimeError("No review rows were returned from the database.")

            brand_counts = Counter(row["brand_slug"] for row in rows)
            preferred = [slug for slug in ("apple", "samsung") if slug in brand_counts]
            focus_brands = preferred if len(preferred) == 2 else [slug for slug, _ in brand_counts.most_common(2)]
            focus_rows = [row for row in rows if row["brand_slug"] in focus_brands]

            print(f"Loaded {len(rows):,} reviews across {len(brand_counts)} brands.")
            print("Focus brands:", ", ".join(focus_brands))

            for slug in focus_brands:
                brand_rows = [row for row in rows if row["brand_slug"] == slug]
                verified = sum(1 for row in brand_rows if row["verified_purchase"])
                with_votes = sum(1 for row in brand_rows if (row["votes"] or 0) > 0)
                nlp_rows = sum(1 for row in brand_rows if row["cleaned_text"])
                print(
                    f"{slug}: {len(brand_rows):,} reviews | "
                    f"{pct(verified, len(brand_rows)):.1f}% verified | "
                    f"{pct(with_votes, len(brand_rows)):.1f}% with votes | "
                    f"{pct(nlp_rows, len(brand_rows)):.1f}% NLP coverage"
                )
            """
        ),
        code(
            """
            def brand_subset(slug: str) -> list[dict]:
                return [row for row in rows if row["brand_slug"] == slug]

            def brand_summary(slug: str) -> dict[str, float]:
                brand_rows = brand_subset(slug)
                votes = [row["votes"] or 0 for row in brand_rows]
                ratings = [row["rating"] for row in brand_rows]
                title_words = [word_count(row["title"]) for row in brand_rows]
                content_words = [word_count(row["content"]) for row in brand_rows]
                verified = [1 if row["verified_purchase"] else 0 for row in brand_rows]
                nlp_scores = [
                    float(row["sentiment_score"])
                    for row in brand_rows
                    if row["sentiment_score"] is not None
                ]
                return {
                    "reviews": len(brand_rows),
                    "verified_share": pct(sum(verified), len(verified)),
                    "avg_rating": round(float(np.mean(ratings)), 3) if ratings else 0.0,
                    "avg_votes": round(float(np.mean(votes)), 3) if votes else 0.0,
                    "median_votes": float(np.median(votes)) if votes else 0.0,
                    "avg_title_words": round(float(np.mean(title_words)), 2) if title_words else 0.0,
                    "avg_content_words": round(float(np.mean(content_words)), 2) if content_words else 0.0,
                    "nlp_coverage": pct(sum(1 for row in brand_rows if row["cleaned_text"]), len(brand_rows)),
                    "avg_sentiment_score": round(float(np.mean(nlp_scores)), 4) if nlp_scores else None,
                }

            summaries = {slug: brand_summary(slug) for slug in focus_brands}
            summaries
            """
        ),
        code(
            """
            signature_counts = Counter(review_signature(row) for row in rows if review_signature(row))
            signature_brands = defaultdict(set)
            for row in rows:
                signature = review_signature(row)
                if signature:
                    signature_brands[signature].add(row["brand_slug"])

            duplicate_rows_total = sum(
                count for signature, count in signature_counts.items() if count > 1
            )
            duplicate_signatures_total = sum(
                1 for signature, count in signature_counts.items() if count > 1
            )
            cross_brand_duplicates_total = sum(
                1
                for signature, count in signature_counts.items()
                if count > 1 and len(signature_brands[signature]) > 1
            )

            duplicate_rows_by_brand = {}
            duplicate_signatures_by_brand = {}
            top_duplicate_examples = []

            for slug in focus_brands:
                brand_rows = brand_subset(slug)
                brand_signature_counts = Counter(
                    review_signature(row) for row in brand_rows if review_signature(row)
                )
                duplicate_rows_by_brand[slug] = sum(
                    count for count in brand_signature_counts.values() if count > 1
                )
                duplicate_signatures_by_brand[slug] = sum(
                    1 for count in brand_signature_counts.values() if count > 1
                )

            for signature, count in signature_counts.most_common():
                if count < 2:
                    break
                sample = next(row for row in rows if review_signature(row) == signature)
                top_duplicate_examples.append(
                    {
                        "signature": signature,
                        "count": count,
                        "brands": ", ".join(sorted(signature_brands[signature])),
                        "preview": preview_text(sample),
                    }
                )

            print(
                f"Duplicate review rows: {duplicate_rows_total:,} across "
                f"{duplicate_signatures_total:,} repeated signatures"
            )
            print(f"Cross-brand duplicate signatures: {cross_brand_duplicates_total:,}")
            for slug in focus_brands:
                print(
                    f"{slug.title()}: {duplicate_rows_by_brand[slug]:,} duplicate rows, "
                    f"{duplicate_signatures_by_brand[slug]:,} repeated signatures"
                )
            print("\\nTop duplicate examples:")
            for item in top_duplicate_examples[:10]:
                print(f"- x{item['count']} | brands={item['brands']} | {item['preview']}")
            """
        ),
        code(
            """
            def plot_grouped_bars(labels, values_by_brand, title, ylabel):
                x = np.arange(len(labels))
                width = 0.36 if len(values_by_brand) == 2 else 0.8 / max(len(values_by_brand), 1)
                fig, ax = plt.subplots(figsize=(12, 5))
                for idx, (brand, values) in enumerate(values_by_brand.items()):
                    offset = (idx - (len(values_by_brand) - 1) / 2) * width
                    ax.bar(x + offset, values, width=width, label=brand.title())
                ax.set_xticks(x)
                ax.set_xticklabels(labels, rotation=20, ha="right")
                ax.set_title(title)
                ax.set_ylabel(ylabel)
                ax.legend(frameon=False)
                ax.grid(axis="y", alpha=0.2)
                fig.tight_layout()
                plt.show()

            rating_labels = ["1", "2", "3", "4", "5"]
            rating_distributions = {}
            verified_shares = {}
            avg_votes_by_rating = {}
            avg_title_words_by_rating = {}

            for slug in focus_brands:
                brand_rows = brand_subset(slug)
                rating_counts = Counter(row["rating"] for row in brand_rows)
                rating_distributions[slug] = [rating_counts.get(rating, 0) for rating in range(1, 6)]
                verified_shares[slug] = [
                    pct(sum(1 for row in brand_rows if row["verified_purchase"]), len(brand_rows))
                ]
                rating_means = []
                title_word_means = []
                for rating in range(1, 6):
                    subset = [row for row in brand_rows if row["rating"] == rating]
                    rating_means.append(float(np.mean([row["votes"] or 0 for row in subset])) if subset else 0.0)
                    title_word_means.append(float(np.mean([word_count(row["title"]) for row in subset])) if subset else 0.0)
                avg_votes_by_rating[slug] = rating_means
                avg_title_words_by_rating[slug] = title_word_means

            plot_grouped_bars(
                rating_labels,
                {slug: rating_distributions[slug] for slug in focus_brands},
                "Rating distribution by brand",
                "Review count",
            )

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.bar(
                [slug.title() for slug in focus_brands],
                [summaries[slug]["verified_share"] for slug in focus_brands],
                color=["#4C78A8", "#F58518"][: len(focus_brands)],
            )
            ax.set_ylim(0, 100)
            ax.set_ylabel("Verified purchase share (%)")
            ax.set_title("Verified purchases by brand")
            ax.grid(axis="y", alpha=0.2)
            fig.tight_layout()
            plt.show()

            plot_grouped_bars(
                rating_labels,
                {slug: avg_votes_by_rating[slug] for slug in focus_brands},
                "Average helpful votes by rating",
                "Average votes",
            )

            plot_grouped_bars(
                rating_labels,
                {slug: avg_title_words_by_rating[slug] for slug in focus_brands},
                "Average title length by rating",
                "Average title words",
            )
            """
        ),
        code(
            """
            fig, axes = plt.subplots(1, 2, figsize=(14, 5))

            axes[0].bar(
                [slug.title() for slug in focus_brands],
                [duplicate_rows_by_brand[slug] for slug in focus_brands],
                color=["#4C78A8", "#F58518"][: len(focus_brands)],
            )
            axes[0].set_title("Exact duplicate review rows by brand")
            axes[0].set_ylabel("Duplicate rows")
            axes[0].grid(axis="y", alpha=0.2)

            axes[1].bar(
                [slug.title() for slug in focus_brands],
                [duplicate_signatures_by_brand[slug] for slug in focus_brands],
                color=["#72B7B2", "#E45756"][: len(focus_brands)],
            )
            axes[1].set_title("Repeated review signatures by brand")
            axes[1].set_ylabel("Repeated signatures")
            axes[1].grid(axis="y", alpha=0.2)
            fig.tight_layout()
            plt.show()

            if top_duplicate_examples:
                top_n = min(12, len(top_duplicate_examples))
                fig, ax = plt.subplots(figsize=(12, max(5, top_n * 0.45)))
                labels = [
                    f"x{item['count']} | {item['brands']} | {item['preview'][:70]}"
                    for item in top_duplicate_examples[:top_n]
                ]
                counts = [item["count"] for item in top_duplicate_examples[:top_n]]
                ax.barh(range(top_n), counts, color="#4C78A8")
                ax.set_yticks(range(top_n))
                ax.set_yticklabels(labels)
                ax.invert_yaxis()
                ax.set_title("Top exact duplicate review signatures")
                ax.set_xlabel("Occurrences")
                ax.grid(axis="x", alpha=0.2)
                fig.tight_layout()
                plt.show()
            """
        ),
        code(
            """
            def make_frequency_counter(records: list[dict]) -> Counter[str]:
                counter: Counter[str] = Counter()
                for row in records:
                    counter.update(tokenize(text_for_cloud(row)))
                return counter

            def plot_frequency_cloud(counter: Counter[str], title: str, ax=None, seed: int = 13, max_words: int = 90):
                if ax is None:
                    fig, ax = plt.subplots(figsize=(11, 7))
                else:
                    fig = ax.figure
                ax.set_title(title)
                ax.set_xlim(0, 1)
                ax.set_ylim(0, 1)
                ax.axis("off")

                items = counter.most_common(max_words)
                if not items:
                    ax.text(0.5, 0.5, "No text available", ha="center", va="center")
                    return fig, ax

                max_freq = items[0][1]
                rng = random.Random(seed)
                used = []
                for idx, (word, freq) in enumerate(items):
                    size = 10 + 34 * ((freq / max_freq) ** 0.65)
                    color = plt.cm.tab20((idx % 20) / 20)
                    for _ in range(120):
                        x = rng.uniform(0.08, 0.92)
                        y = rng.uniform(0.08, 0.92)
                        if all((x - px) ** 2 + (y - py) ** 2 > 0.008 for px, py in used):
                            used.append((x, y))
                            ax.text(
                                x,
                                y,
                                word,
                                fontsize=size,
                                color=color,
                                rotation=rng.choice([0, 0, 0, 20, -20]),
                                ha="center",
                                va="center",
                                alpha=0.9,
                                transform=ax.transAxes,
                            )
                            break
                return fig, ax

            fig, axes = plt.subplots(1, len(focus_brands), figsize=(8 * len(focus_brands), 7))
            if len(focus_brands) == 1:
                axes = [axes]
            for ax, slug in zip(axes, focus_brands):
                brand_rows = brand_subset(slug)
                counter = make_frequency_counter(brand_rows)
                seed = sum(ord(char) for char in slug)
                plot_frequency_cloud(counter, f"{slug.title()} review word cloud", ax=ax, seed=seed)
            fig.tight_layout()
            plt.show()
            """
        ),
        code(
            """
            topic_rows = [row for row in focus_rows if text_for_model(row)]
            topic_texts = [text_for_model(row) for row in topic_rows]

            if len(topic_texts) < 10:
                print("Not enough text rows for topic modeling.")
            else:
                n_topics = min(6, max(2, len(topic_texts) // 50 or 2))
                vectorizer = TfidfVectorizer(
                    max_df=0.9,
                    min_df=2,
                    ngram_range=(1, 2),
                    max_features=5000,
                    stop_words=sorted(STOPWORDS),
                )
                topic_matrix = vectorizer.fit_transform(topic_texts)
                max_topics = min(n_topics, topic_matrix.shape[0] - 1, topic_matrix.shape[1])
                if max_topics < 2:
                    print("Topic modeling skipped because the corpus is too small after vectorization.")
                else:
                    n_topics = max_topics

                    nmf = NMF(
                        n_components=n_topics,
                        init="nndsvda",
                        random_state=42,
                        max_iter=500,
                    )
                    weights = nmf.fit_transform(topic_matrix)
                    terms = np.asarray(vectorizer.get_feature_names_out())
                    dominant_topic = np.argmax(weights, axis=1)

                    print(f"Built {n_topics} topics from {len(topic_texts):,} reviews.")
                    for idx, topic in enumerate(nmf.components_):
                        top_terms = terms[np.argsort(topic)[-10:][::-1]]
                        print(f"Topic {idx}: {', '.join(top_terms)}")

                    topic_counts = Counter(dominant_topic)
                    fig, ax = plt.subplots(figsize=(10, 4))
                    ax.bar(
                        [f"Topic {i}" for i in range(n_topics)],
                        [topic_counts.get(i, 0) for i in range(n_topics)],
                    )
                    ax.set_title("Dominant topic counts")
                    ax.set_ylabel("Reviews")
                    ax.grid(axis="y", alpha=0.2)
                    fig.tight_layout()
                    plt.show()

                    by_brand_topic = defaultdict(Counter)
                    for row, topic_id in zip(topic_rows, dominant_topic):
                        by_brand_topic[row["brand_slug"]][topic_id] += 1

                    fig, ax = plt.subplots(figsize=(12, 5))
                    x = np.arange(n_topics)
                    width = 0.36 if len(focus_brands) == 2 else 0.8 / max(len(focus_brands), 1)
                    for idx, slug in enumerate(focus_brands):
                        counts = np.array([by_brand_topic[slug].get(i, 0) for i in range(n_topics)], dtype=float)
                        shares = counts / counts.sum() if counts.sum() else counts
                        ax.bar(x + (idx - (len(focus_brands) - 1) / 2) * width, shares, width=width, label=slug.title())
                    ax.set_xticks(x)
                    ax.set_xticklabels([f"Topic {i}" for i in range(n_topics)])
                    ax.set_ylabel("Share of brand reviews")
                    ax.set_title("Topic mix by brand")
                    ax.legend(frameon=False)
                    ax.grid(axis="y", alpha=0.2)
                    fig.tight_layout()
                    plt.show()

                    for topic_id in range(n_topics):
                        best_idx = np.argsort(weights[:, topic_id])[-3:][::-1]
                        print(f"\\nTopic {topic_id} example reviews:")
                        for idx in best_idx:
                            row = topic_rows[idx]
                            preview = safe_text((row.get("title") or "") + " " + (row.get("content") or ""))[:220]
                            print(f"- {row['brand_slug']} | rating={row['rating']} | votes={row['votes']} | {preview}")
            """
        ),
        code(
            """
            feature_rows = []
            targets = []
            brands = []
            texts = []
            vote_labels = []

            for row in focus_rows:
                title = row.get("title") or ""
                content = row.get("content") or ""
                published_at = row.get("published_at")
                sentiment_score = row.get("sentiment_score")
                feature_rows.append(
                    {
                        "brand": row["brand_slug"],
                        "verified_purchase": bool(row["verified_purchase"]),
                        "rating": int(row["rating"]),
                        "language": row["language"] or "missing",
                        "sentiment_label": row["sentiment_label"] or "missing",
                        "rating_mismatch": (
                            "yes"
                            if row["rating_mismatch"]
                            else ("no" if row["rating_mismatch"] is not None else "missing")
                        ),
                        "published_weekday": published_at.strftime("%a") if published_at else "missing",
                        "published_month": published_at.strftime("%b") if published_at else "missing",
                        "has_title": bool(title.strip()),
                        "title_exclaim": title.count("!"),
                        "title_question": title.count("?"),
                        "content_exclaim": content.count("!"),
                        "content_question": content.count("?"),
                        "title_words": word_count(title),
                        "title_chars": len(title),
                        "content_words": word_count(content),
                        "content_chars": len(content),
                        "combined_words": word_count(f"{title} {content}"),
                        "token_count": int(row["token_count"] or 0),
                        "unique_token_count": int(row["unique_token_count"] or 0),
                        "vote_bucket": "zero"
                        if (row["votes"] or 0) == 0
                        else ("few" if (row["votes"] or 0) <= 3 else ("some" if (row["votes"] or 0) <= 10 else "many")),
                        "sentiment_score": float(sentiment_score) if sentiment_score is not None else None,
                    }
                )
                targets.append(float(row["votes"] or 0))
                brands.append(row["brand_slug"])
                texts.append(text_for_model(row))
                vote_labels.append(int(row["votes"] or 0))

            train_idx, test_idx = train_test_split(
                np.arange(len(feature_rows)),
                test_size=0.25,
                random_state=42,
            )

            numeric_keys = [
                "title_exclaim",
                "title_question",
                "content_exclaim",
                "content_question",
                "title_words",
                "title_chars",
                "content_words",
                "content_chars",
                "combined_words",
                "token_count",
                "unique_token_count",
                "sentiment_score",
            ]

            impute_values = {}
            for key in numeric_keys:
                values = [
                    row[key]
                    for row in (feature_rows[i] for i in train_idx)
                    if row[key] is not None and not np.isnan(row[key])
                ]
                impute_values[key] = float(np.median(values)) if values else 0.0

            def impute_row(row):
                updated = dict(row)
                for key in numeric_keys:
                    value = updated.get(key)
                    if value is None or (isinstance(value, float) and np.isnan(value)):
                        updated[key] = impute_values[key]
                return updated

            meta_vectorizer = DictVectorizer(sparse=True)
            X_train_meta = [impute_row(feature_rows[i]) for i in train_idx]
            X_test_meta = [impute_row(feature_rows[i]) for i in test_idx]
            X_train_text = [texts[i] for i in train_idx]
            X_test_text = [texts[i] for i in test_idx]
            y_train = np.asarray([targets[i] for i in train_idx], dtype=float)
            y_test = np.asarray([targets[i] for i in test_idx], dtype=float)

            text_vectorizer = TfidfVectorizer(
                max_df=0.95,
                min_df=2,
                ngram_range=(1, 2),
                max_features=8000,
                stop_words=sorted(STOPWORDS),
            )

            X_train = sparse.hstack(
                [
                    meta_vectorizer.fit_transform(X_train_meta),
                    text_vectorizer.fit_transform(X_train_text),
                ]
            ).tocsr()
            X_test = sparse.hstack(
                [
                    meta_vectorizer.transform(X_test_meta),
                    text_vectorizer.transform(X_test_text),
                ]
            ).tocsr()

            model_specs = [
                (
                    "Ridge",
                    TransformedTargetRegressor(
                        regressor=Ridge(alpha=2.0, random_state=42),
                        func=np.log1p,
                        inverse_func=np.expm1,
                    ),
                ),
                (
                    "ElasticNet",
                    TransformedTargetRegressor(
                        regressor=ElasticNet(alpha=0.001, l1_ratio=0.25, max_iter=8000, random_state=42),
                        func=np.log1p,
                        inverse_func=np.expm1,
                    ),
                ),
                (
                    "SGDRegressor",
                    TransformedTargetRegressor(
                        regressor=SGDRegressor(
                            loss="squared_error",
                            penalty="l2",
                            alpha=0.0005,
                            max_iter=4000,
                            tol=1e-4,
                            random_state=42,
                        ),
                        func=np.log1p,
                        inverse_func=np.expm1,
                    ),
                ),
                (
                    "PoissonRegressor",
                    PoissonRegressor(alpha=0.001, max_iter=500),
                ),
            ]

            model_rows = []
            fitted_models = {}
            for name, model_obj in model_specs:
                model_obj.fit(X_train, y_train if name == "PoissonRegressor" else y_train)
                preds = model_obj.predict(X_test)
                preds = np.clip(np.asarray(preds, dtype=float), 0, None)
                row = {
                    "model": name,
                    "r2": round(float(r2_score(y_test, preds)), 4),
                    "mae": round(float(mean_absolute_error(y_test, preds)), 4),
                    "median_abs_error": round(float(np.median(np.abs(y_test - preds))), 4),
                }
                model_rows.append(row)
                fitted_models[name] = model_obj

            best_model_name = min(model_rows, key=lambda item: item["mae"])["model"]
            best_model = fitted_models[best_model_name]
            best_preds = np.clip(np.asarray(best_model.predict(X_test), dtype=float), 0, None)

            print("Model comparison on raw votes:")
            for row in model_rows:
                print(
                    f"  {row['model']}: R^2={row['r2']} MAE={row['mae']} "
                    f"MedianAE={row['median_abs_error']}"
                )
            print(f"Best MAE model: {best_model_name}")

            test_brands = [brands[i] for i in test_idx]
            for slug in focus_brands:
                mask = np.array([brand == slug for brand in test_brands], dtype=bool)
                if mask.any():
                    print(
                        f"  {slug.title()} MAE ({best_model_name}):",
                        round(float(mean_absolute_error(y_test[mask], best_preds[mask])), 3),
                    )
            """
        ),
        code(
            """
            if best_model_name == "PoissonRegressor":
                model_for_inspection = best_model
                coefficients = model_for_inspection.coef_
            else:
                model_for_inspection = best_model.regressor_
                coefficients = model_for_inspection.coef_

            feature_names = list(meta_vectorizer.get_feature_names_out()) + list(text_vectorizer.get_feature_names_out())
            top_pos = np.argsort(coefficients)[-20:][::-1]
            top_neg = np.argsort(coefficients)[:20]

            print("Top positive features:")
            for idx in top_pos[:15]:
                print(f"  {feature_names[idx]}: {coefficients[idx]:.4f}")

            print("\\nTop negative features:")
            for idx in top_neg[:15]:
                print(f"  {feature_names[idx]}: {coefficients[idx]:.4f}")

            brand_coeffs = [(name, coef) for name, coef in zip(feature_names, coefficients) if name.startswith("brand=")]
            if brand_coeffs:
                print("\\nBrand coefficients:")
                for name, coef in brand_coeffs:
                    print(f"  {name}: {coef:.4f}")

            corr_features = [
                "verified_purchase",
                "rating",
                "title_words",
                "content_words",
                "combined_words",
                "token_count",
                "unique_token_count",
                "sentiment_score",
                "title_exclaim",
                "content_exclaim",
                "has_title",
            ]

            def numeric_feature_value(row, key):
                if key == "verified_purchase":
                    return 1.0 if row["verified_purchase"] else 0.0
                if key == "rating":
                    return float(row["rating"])
                if key == "sentiment_score":
                    return float(row["sentiment_score"]) if row["sentiment_score"] is not None else np.nan
                if key == "has_title":
                    return 1.0 if row["has_title"] else 0.0
                return float(row[key])

            fig, axes = plt.subplots(1, len(focus_brands), figsize=(7 * len(focus_brands), 5), sharey=True)
            if len(focus_brands) == 1:
                axes = [axes]
            for ax, slug in zip(axes, focus_brands):
                brand_rows = [feature_rows[i] for i in range(len(feature_rows)) if brands[i] == slug]
                brand_targets = [targets[i] for i in range(len(feature_rows)) if brands[i] == slug]
                correlations = []
                for key in corr_features:
                    values = [numeric_feature_value(row, key) for row in brand_rows]
                    mask = [not np.isnan(v) for v in values]
                    filtered_values = [v for v, keep in zip(values, mask) if keep]
                    filtered_targets = [t for t, keep in zip(brand_targets, mask) if keep]
                    correlations.append(pearson(filtered_values, filtered_targets))
                ax.barh(corr_features, correlations, color="#4C78A8" if slug == focus_brands[0] else "#F58518")
                ax.axvline(0, color="black", linewidth=1)
                ax.set_title(f"{slug.title()} correlations with log votes")
                ax.grid(axis="x", alpha=0.2)
            fig.tight_layout()
            plt.show()

            test_rows = [feature_rows[i] for i in test_idx]
            test_targets = [targets[i] for i in test_idx]
            for slug in focus_brands:
                indices = [i for i, brand in enumerate(test_brands) if brand == slug]
                if not indices:
                    continue
                group_targets = [test_targets[i] for i in indices]
                group_preds = [best_preds[i] for i in indices]
                print(
                    f"{slug.title()} test-set median true/predicted votes: "
                    f"{float(np.median(group_targets)):.2f} / {float(np.median(group_preds)):.2f}"
                )

            fig, ax = plt.subplots(figsize=(10, 4))
            ax.bar([row["model"] for row in model_rows], [row["mae"] for row in model_rows], color="#4C78A8")
            ax.set_title("Model comparison by test MAE")
            ax.set_ylabel("MAE on votes")
            ax.grid(axis="y", alpha=0.2)
            fig.tight_layout()
            plt.show()
            """
        ),
        md(
            """
            ## Notes for reruns

            - The notebook always queries the live database, so new reviews are picked up automatically.
            - Apple and Samsung are chosen first when both are present; otherwise the two largest brands are used.
            - The likes model is intentionally lightweight and interpretable. It is best treated as a feature-analysis baseline, not a production predictor.
            - If the database connection is unavailable, set `DATABASE_URL_READ` and rerun the notebook.
            """
        ),
    ]

    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "name": "python",
                "version": "3.14",
                "mimetype": "text/x-python",
                "codemirror_mode": {
                    "name": "ipython",
                    "version": 3,
                },
                "pygments_lexer": "ipython3",
                "nbconvert_exporter": "python",
                "file_extension": ".py",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    notebook_path = Path("notebooks/review_likes_eda.ipynb")
    notebook_path.parent.mkdir(parents=True, exist_ok=True)
    notebook_path.write_text(json.dumps(build_notebook(), indent=1), encoding="utf-8")
    print(f"Wrote {notebook_path}")


if __name__ == "__main__":
    main()
