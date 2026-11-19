"""create request_logs

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("request_id", sa.String(32), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "api_key_id",
            sa.Uuid(),
            sa.ForeignKey("api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model_alias", sa.String(100), nullable=False),
        sa.Column("provider", sa.String(100), nullable=True),
        sa.Column("upstream_model", sa.String(100), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("cache", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("stream", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("ttft_ms", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])
    op.create_index("ix_request_logs_api_key_id", "request_logs", ["api_key_id"])
    op.create_index("ix_request_logs_model_alias", "request_logs", ["model_alias"])
    op.create_index(
        "ix_request_logs_api_key_id_created_at", "request_logs", ["api_key_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_table("request_logs")
