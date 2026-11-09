"""Lossless immutable canonical-anchor segments; retain all existing hot fences."""

import sqlalchemy as sa
from alembic import context, op

revision = "20261109_0046"
down_revision = "20261107_0045"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("canonical_anchor_segments",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.BigInteger(), nullable=False), sa.Column("bucket_end", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("format_version", sa.String(32), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False), sa.Column("raw_bytes", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False), sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.UniqueConstraint("chain_id", "bucket_start", "revision", name="uq_canonical_anchor_segment"),
        sa.CheckConstraint("bucket_start >= 0 AND bucket_start % 512 = 0 AND bucket_end = bucket_start + 511",
                           name="ck_canonical_anchor_bucket"),
        sa.CheckConstraint("revision >= 0 AND row_count >= 1 AND row_count <= 512 AND raw_bytes > 0",
                           name="ck_canonical_anchor_segment_size"))


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("canonical anchor archive downgrade requires an online evidence check")
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM canonical_anchor_segments)")):
        raise RuntimeError("canonical anchor archive contains evidence; refusing downgrade without explicit restoration")
    op.drop_table("canonical_anchor_segments")
