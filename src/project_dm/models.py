from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Brand(TimestampMixin, Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    listing_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, server_default="true", nullable=False
    )


class ProductFamily(TimestampMixin, Base):
    __tablename__ = "product_families"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    brand_id: Mapped[int] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE"), nullable=False
    )
    emag_family_id: Mapped[int | None] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    aggregate_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    review_count: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "brand_id",
            "emag_family_id",
            name="uq_product_families_brand_emag_family",
        ),
        CheckConstraint(
            "aggregate_rating IS NULL OR "
            "(aggregate_rating >= 0 AND aggregate_rating <= 5)",
            name="ck_product_families_rating",
        ),
    )


class Variant(TimestampMixin, Base):
    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    family_id: Mapped[int] = mapped_column(
        ForeignKey("product_families.id", ondelete="CASCADE"),
        nullable=False,
    )
    emag_product_id: Mapped[int | None] = mapped_column(BigInteger)
    pnk: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    storage: Mapped[str | None] = mapped_column(String(40))
    color: Mapped[str | None] = mapped_column(String(120))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    seller: Mapped[str | None] = mapped_column(String(200))
    available: Mapped[bool | None] = mapped_column(Boolean)
    url: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_variants_family_id", "family_id"),
        CheckConstraint(
            "price IS NULL OR price >= 0", name="ck_variants_price"
        ),
    )


class Review(TimestampMixin, Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    emag_review_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, unique=True
    )
    family_id: Mapped[int] = mapped_column(
        ForeignKey("product_families.id", ondelete="CASCADE"),
        nullable=False,
    )
    variant_id: Mapped[int | None] = mapped_column(
        ForeignKey("variants.id", ondelete="SET NULL")
    )
    title: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    votes: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    verified_purchase: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reviewer_name: Mapped[str | None] = mapped_column(String(300))
    reviewer_hash: Mapped[str | None] = mapped_column(String(160))
    review_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    storage: Mapped[str | None] = mapped_column(String(40))
    color: Mapped[str | None] = mapped_column(String(120))
    avatar_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_reviews_family_id", "family_id"),
        Index("ix_reviews_reviewer_hash", "reviewer_hash"),
        CheckConstraint(
            "rating >= 1 AND rating <= 5", name="ck_reviews_rating"
        ),
        CheckConstraint("votes >= 0", name="ck_reviews_votes"),
    )


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), server_default="pending", nullable=False
    )
    brand_id: Mapped[int | None] = mapped_column(
        ForeignKey("brands.id", ondelete="CASCADE")
    )
    family_id: Mapped[int | None] = mapped_column(
        ForeignKey("product_families.id", ondelete="CASCADE")
    )
    target_url: Mapped[str | None] = mapped_column(Text)
    current_offset: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    total_expected: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    priority: Mapped[int] = mapped_column(
        Integer, server_default="100", nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        Index("ix_jobs_claim", "status", "priority", "created_at"),
        CheckConstraint(
            "job_type IN ('brand_listing', 'product', 'reviews', "
            "'avatar', 'nlp')",
            name="ck_jobs_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'paused', 'completed', "
            "'skipped', 'failed', 'blocked')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "current_offset >= 0 AND attempts >= 0", name="ck_jobs_counters"
        ),
    )


class ServiceControl(TimestampMixin, Base):
    __tablename__ = "service_controls"

    service_name: Mapped[str] = mapped_column(String(30), primary_key=True)
    desired_state: Mapped[str] = mapped_column(
        String(20), server_default="paused", nullable=False
    )
    current_state: Mapped[str] = mapped_column(
        String(20), server_default="stopped", nullable=False
    )
    current_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL")
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "service_name IN ('scraper', 'avatar', 'nlp')",
            name="ck_service_controls_name",
        ),
        CheckConstraint(
            "desired_state IN ('running', 'paused', 'stopped')",
            name="ck_service_controls_desired_state",
        ),
        CheckConstraint(
            "current_state IN ('running', 'paused', 'stopped', 'blocked', "
            "'error')",
            name="ck_service_controls_current_state",
        ),
    )


class AvatarResult(TimestampMixin, Base):
    __tablename__ = "avatar_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reviewer_hash: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True
    )
    classification: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    method: Mapped[str | None] = mapped_column(String(100))
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    perceptual_hash: Mapped[str | None] = mapped_column(String(64))
    ocr_text: Mapped[str | None] = mapped_column(String(20))
    error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "classification IN ('default_apple', 'default_name', "
            "'custom', 'unknown')",
            name="ck_avatar_results_classification",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_avatar_results_confidence",
        ),
    )


class NlpResult(TimestampMixin, Base):
    __tablename__ = "nlp_results"

    review_id: Mapped[int] = mapped_column(
        ForeignKey("reviews.id", ondelete="CASCADE"), primary_key=True
    )
    cleaned_text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(20))
    sentiment_label: Mapped[str | None] = mapped_column(String(20))
    sentiment_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 5))
    rating_mismatch: Mapped[bool | None] = mapped_column(Boolean)
    token_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    unique_token_count: Mapped[int] = mapped_column(
        Integer, server_default="0", nullable=False
    )
    tfidf_terms: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    model_name: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        CheckConstraint(
            "sentiment_label IS NULL OR sentiment_label IN "
            "('negative', 'neutral', 'positive')",
            name="ck_nlp_results_sentiment",
        ),
        CheckConstraint(
            "sentiment_score IS NULL OR "
            "(sentiment_score >= 0 AND sentiment_score <= 1)",
            name="ck_nlp_results_score",
        ),
        CheckConstraint(
            "token_count >= 0 AND unique_token_count >= 0",
            name="ck_nlp_results_token_counts",
        ),
    )


class ProductRecommendation(TimestampMixin, Base):
    __tablename__ = "product_recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_family_id: Mapped[int] = mapped_column(
        ForeignKey("product_families.id", ondelete="CASCADE"),
        nullable=False,
    )
    recommended_family_id: Mapped[int] = mapped_column(
        ForeignKey("product_families.id", ondelete="CASCADE"),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        Index(
            "ix_product_recommendations_source_family_id",
            "source_family_id",
            "rank",
        ),
        UniqueConstraint(
            "source_family_id",
            "recommended_family_id",
            name="uq_product_recommendations_pair",
        ),
        CheckConstraint(
            "score >= 0 AND score <= 1",
            name="ck_product_recommendations_score",
        ),
        CheckConstraint("rank >= 1", name="ck_product_recommendations_rank"),
    )
