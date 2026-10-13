"""Add replayable BSC raw logs and durable stream checkpoints.

Revision ID: 20260911_0007
Revises: 20260904_0006
"""

import sqlalchemy as sa
from alembic import op

revision = "20260911_0007"
down_revision = "20260904_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_chain_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", sa.String(length=128)),
        sa.Column("transaction_hash", sa.String(length=128), nullable=False),
        sa.Column("transaction_index", sa.Integer()),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("contract_address", sa.String(length=128), nullable=False),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("data", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True)),
        sa.Column("ingestion_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("confirmation_status", sa.String(length=16), nullable=False),
        sa.Column("removed", sa.Boolean(), nullable=False),
        sa.Column("raw_payload_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "chain_id", "transaction_hash", "log_index", name="uq_raw_chain_events_identity"
        ),
    )
    op.create_index("ix_raw_chain_events_block", "raw_chain_events", ["chain_id", "block_number"])
    op.create_index("ix_raw_chain_events_status", "raw_chain_events", ["confirmation_status"])

    op.create_table(
        "stream_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("stream_name", sa.String(length=64), nullable=False),
        sa.Column("last_processed_block", sa.BigInteger()),
        sa.Column("last_processed_log", sa.Integer()),
        sa.Column("last_processed_transaction_hash", sa.String(length=128)),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "chain_id", "provider", "stream_name", name="uq_stream_checkpoints_identity"
        ),
    )


def downgrade() -> None:
    op.drop_table("stream_checkpoints")
    op.drop_index("ix_raw_chain_events_status", table_name="raw_chain_events")
    op.drop_index("ix_raw_chain_events_block", table_name="raw_chain_events")
    op.drop_table("raw_chain_events")
