"""Add product recommendation snapshot cache.

Revision ID: 20260625_01
Revises: 20260624_01
Create Date: 2026-06-25
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260625_01"
down_revision: str | None = "20260624_01"
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
        "product_recommendations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "source_family_id",
            sa.BigInteger(),
            sa.ForeignKey("product_families.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recommended_family_id",
            sa.BigInteger(),
            sa.ForeignKey("product_families.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(8, 6), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        *timestamps(),
        sa.CheckConstraint("score >= 0 AND score <= 1", name="ck_product_recommendations_score"),
        sa.CheckConstraint("rank >= 1", name="ck_product_recommendations_rank"),
        sa.UniqueConstraint(
            "source_family_id",
            "recommended_family_id",
            name="uq_product_recommendations_pair",
        ),
    )
    op.create_index(
        "ix_product_recommendations_source_family_id",
        "product_recommendations",
        ["source_family_id", "rank"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_recommendations_source_family_id",
        table_name="product_recommendations",
    )
    op.drop_table("product_recommendations")
