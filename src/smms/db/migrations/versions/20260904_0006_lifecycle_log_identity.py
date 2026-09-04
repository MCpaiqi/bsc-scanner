"""Use the EVM log identity for lifecycle event idempotency.

Revision ID: 20260904_0006
Revises: 20260904_0005

The previous lifecycle schema cannot recover a receipt log index. This
revision therefore refuses non-empty legacy tables instead of assigning every
row the fabricated default ``event_index=0``; operators must provide an
explicit identity backfill first.
"""

import sqlalchemy as sa
from alembic import context, op

revision = "20260904_0006"
down_revision = "20260904_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        # Offline SQL generation cannot inspect legacy rows. The generated
        # script must be applied only after the same preflight is run online.
        return _upgrade_without_preflight()
    bind = op.get_bind()
    existing_rows = bind.execute(
        sa.text("SELECT COUNT(*) FROM token_lifecycle_events")
    ).scalar()
    if existing_rows:
        raise RuntimeError(
            "20260904_0006 refuses to invent lifecycle log identities: "
            f"token_lifecycle_events contains {existing_rows} existing row(s). "
            "Run an explicit manual backfill that supplies each row's real "
            "transaction hash and log index, or migrate an empty table, then "
            "rerun this revision."
        )
    _upgrade_without_preflight()


def _upgrade_without_preflight() -> None:
    with op.batch_alter_table("token_lifecycle_events") as batch_op:
        batch_op.drop_constraint("uq_token_lifecycle_identity", type_="unique")
        batch_op.add_column(
            sa.Column("event_index", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.alter_column(
            "tx_hash",
            existing_type=sa.String(length=128),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_token_lifecycle_chain_tx_index", ["chain", "tx_hash", "event_index"]
        )


def downgrade() -> None:
    with op.batch_alter_table("token_lifecycle_events") as batch_op:
        batch_op.drop_constraint("uq_token_lifecycle_chain_tx_index", type_="unique")
        batch_op.alter_column(
            "tx_hash",
            existing_type=sa.String(length=128),
            nullable=True,
        )
        batch_op.drop_column("event_index")
        batch_op.create_unique_constraint(
            "uq_token_lifecycle_identity",
            ["chain", "token_address", "event_type", "timestamp", "tx_hash"],
        )
