"""Add TF-IDF storage fields to NLP results.

Revision ID: 20260624_01
Revises: 20260606_01
Create Date: 2026-06-24
"""

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260624_01"
down_revision: str | None = "20260606_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "nlp_results",
        sa.Column(
            "token_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "nlp_results",
        sa.Column(
            "unique_token_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "nlp_results",
        sa.Column("tfidf_terms", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.create_check_constraint(
        "ck_nlp_results_token_counts",
        "nlp_results",
        "token_count >= 0 AND unique_token_count >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_nlp_results_token_counts",
        "nlp_results",
        type_="check",
    )
    op.drop_column("nlp_results", "tfidf_terms")
    op.drop_column("nlp_results", "unique_token_count")
    op.drop_column("nlp_results", "token_count")
