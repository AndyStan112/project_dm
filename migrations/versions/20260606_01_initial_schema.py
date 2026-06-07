"""Create the initial collection and processing schema.

Revision ID: 20260606_01
Revises:
Create Date: 2026-06-06
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260606_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "brands",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("listing_url", sa.Text(), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), server_default="true", nullable=False
        ),
        *timestamps(),
        sa.UniqueConstraint("slug"),
    )

    op.create_table(
        "product_families",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "brand_id",
            sa.BigInteger(),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("emag_family_id", sa.BigInteger()),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("aggregate_rating", sa.Numeric(3, 2)),
        sa.Column("review_count", sa.Integer()),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("scraped_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint(
            "aggregate_rating IS NULL OR "
            "(aggregate_rating >= 0 AND aggregate_rating <= 5)",
            name="ck_product_families_rating",
        ),
        sa.UniqueConstraint(
            "brand_id",
            "emag_family_id",
            name="uq_product_families_brand_emag_family",
        ),
    )

    op.create_table(
        "variants",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "family_id",
            sa.BigInteger(),
            sa.ForeignKey("product_families.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("emag_product_id", sa.BigInteger()),
        sa.Column("pnk", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("storage", sa.String(length=40)),
        sa.Column("color", sa.String(length=120)),
        sa.Column("price", sa.Numeric(12, 2)),
        sa.Column("currency", sa.String(length=8)),
        sa.Column("seller", sa.String(length=200)),
        sa.Column("available", sa.Boolean()),
        sa.Column("url", sa.Text(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "price IS NULL OR price >= 0", name="ck_variants_price"
        ),
        sa.UniqueConstraint("pnk"),
    )
    op.create_index("ix_variants_family_id", "variants", ["family_id"])

    op.create_table(
        "reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("emag_review_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "family_id",
            sa.BigInteger(),
            sa.ForeignKey("product_families.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            sa.BigInteger(),
            sa.ForeignKey("variants.id", ondelete="SET NULL"),
        ),
        sa.Column("title", sa.Text()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column(
            "votes", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("verified_purchase", sa.Boolean(), nullable=False),
        sa.Column("reviewer_name", sa.String(length=300)),
        sa.Column("reviewer_hash", sa.String(length=160)),
        sa.Column("review_created_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("storage", sa.String(length=40)),
        sa.Column("color", sa.String(length=120)),
        sa.Column("avatar_metadata", postgresql.JSONB()),
        *timestamps(),
        sa.CheckConstraint(
            "rating >= 1 AND rating <= 5", name="ck_reviews_rating"
        ),
        sa.CheckConstraint("votes >= 0", name="ck_reviews_votes"),
        sa.UniqueConstraint("emag_review_id"),
    )
    op.create_index("ix_reviews_family_id", "reviews", ["family_id"])
    op.create_index("ix_reviews_reviewer_hash", "reviews", ["reviewer_hash"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("job_type", sa.String(length=30), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "brand_id",
            sa.BigInteger(),
            sa.ForeignKey("brands.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "family_id",
            sa.BigInteger(),
            sa.ForeignKey("product_families.id", ondelete="CASCADE"),
        ),
        sa.Column("target_url", sa.Text()),
        sa.Column(
            "current_offset",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("total_expected", sa.Integer()),
        sa.Column(
            "attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "priority", sa.Integer(), server_default="100", nullable=False
        ),
        sa.Column("last_error", sa.Text()),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.CheckConstraint(
            "job_type IN ('brand_listing', 'product', 'reviews', "
            "'avatar', 'nlp')",
            name="ck_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'paused', 'completed', "
            "'skipped', 'failed', 'blocked')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "current_offset >= 0 AND attempts >= 0",
            name="ck_jobs_counters",
        ),
    )
    op.create_index(
        "ix_jobs_claim",
        "jobs",
        ["status", "priority", "created_at"],
    )

    op.create_table(
        "service_controls",
        sa.Column("service_name", sa.String(length=30), primary_key=True),
        sa.Column(
            "desired_state",
            sa.String(length=20),
            server_default="paused",
            nullable=False,
        ),
        sa.Column(
            "current_state",
            sa.String(length=20),
            server_default="stopped",
            nullable=False,
        ),
        sa.Column(
            "current_job_id",
            sa.BigInteger(),
            sa.ForeignKey("jobs.id", ondelete="SET NULL"),
        ),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("message", sa.Text()),
        *timestamps(),
        sa.CheckConstraint(
            "service_name IN ('scraper', 'avatar', 'nlp')",
            name="ck_service_controls_name",
        ),
        sa.CheckConstraint(
            "desired_state IN ('running', 'paused', 'stopped')",
            name="ck_service_controls_desired_state",
        ),
        sa.CheckConstraint(
            "current_state IN ('running', 'paused', 'stopped', "
            "'blocked', 'error')",
            name="ck_service_controls_current_state",
        ),
    )

    op.create_table(
        "avatar_results",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "reviewer_hash", sa.String(length=160), nullable=False
        ),
        sa.Column(
            "classification", sa.String(length=30), nullable=False
        ),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("method", sa.String(length=100)),
        sa.Column("image_sha256", sa.String(length=64)),
        sa.Column("perceptual_hash", sa.String(length=64)),
        sa.Column("ocr_text", sa.String(length=20)),
        sa.Column("error", sa.Text()),
        *timestamps(),
        sa.CheckConstraint(
            "classification IN ('default_apple', 'default_name', "
            "'custom', 'unknown')",
            name="ck_avatar_results_classification",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_avatar_results_confidence",
        ),
        sa.UniqueConstraint("reviewer_hash"),
    )

    op.create_table(
        "nlp_results",
        sa.Column(
            "review_id",
            sa.BigInteger(),
            sa.ForeignKey("reviews.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cleaned_text", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=20)),
        sa.Column("sentiment_label", sa.String(length=20)),
        sa.Column("sentiment_score", sa.Numeric(6, 5)),
        sa.Column("rating_mismatch", sa.Boolean()),
        sa.Column("model_name", sa.String(length=200)),
        *timestamps(),
        sa.CheckConstraint(
            "sentiment_label IS NULL OR sentiment_label IN "
            "('negative', 'neutral', 'positive')",
            name="ck_nlp_results_sentiment",
        ),
        sa.CheckConstraint(
            "sentiment_score IS NULL OR "
            "(sentiment_score >= 0 AND sentiment_score <= 1)",
            name="ck_nlp_results_score",
        ),
    )

    service_controls = sa.table(
        "service_controls",
        sa.column("service_name", sa.String),
        sa.column("desired_state", sa.String),
        sa.column("current_state", sa.String),
    )
    op.bulk_insert(
        service_controls,
        [
            {
                "service_name": name,
                "desired_state": "paused",
                "current_state": "stopped",
            }
            for name in ("scraper", "avatar", "nlp")
        ],
    )


def downgrade() -> None:
    op.drop_table("nlp_results")
    op.drop_table("avatar_results")
    op.drop_table("service_controls")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_reviews_reviewer_hash", table_name="reviews")
    op.drop_index("ix_reviews_family_id", table_name="reviews")
    op.drop_table("reviews")
    op.drop_index("ix_variants_family_id", table_name="variants")
    op.drop_table("variants")
    op.drop_table("product_families")
    op.drop_table("brands")
