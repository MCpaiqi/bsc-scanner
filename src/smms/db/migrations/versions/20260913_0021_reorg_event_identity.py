"""Allow canonical replacement logs with a new block hash.

Revision ID: 20260913_0021
Revises: 20260913_0020
"""

import sqlalchemy as sa
from alembic import op

revision = "20260913_0021"
down_revision = "20260913_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Recreating the table makes PostgreSQL drop its primary key first,
        # which is still referenced by radar_candidate_evidence.raw_event_id.
        op.drop_constraint("uq_raw_chain_events_identity", "raw_chain_events", type_="unique")
        op.create_unique_constraint(
            "uq_raw_chain_events_identity",
            "raw_chain_events",
            ["chain_id", "transaction_hash", "log_index", "block_hash"],
        )
    else:
        with op.batch_alter_table("raw_chain_events", recreate="always") as batch:
            batch.drop_constraint("uq_raw_chain_events_identity", type_="unique")
            batch.create_unique_constraint(
                "uq_raw_chain_events_identity",
                ["chain_id", "transaction_hash", "log_index", "block_hash"],
            )
    op.create_index(
        "ux_raw_chain_events_identity_coalesced",
        "raw_chain_events",
        [
            "chain_id",
            "transaction_hash",
            "log_index",
            sa.text("coalesce(block_hash, '')"),
        ],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_raw_chain_events_identity_coalesced", table_name="raw_chain_events")
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint("uq_raw_chain_events_identity", "raw_chain_events", type_="unique")
        op.create_unique_constraint(
            "uq_raw_chain_events_identity",
            "raw_chain_events",
            ["chain_id", "transaction_hash", "log_index"],
        )
    else:
        with op.batch_alter_table("raw_chain_events", recreate="always") as batch:
            batch.drop_constraint("uq_raw_chain_events_identity", type_="unique")
            batch.create_unique_constraint(
                "uq_raw_chain_events_identity",
                ["chain_id", "transaction_hash", "log_index"],
            )
