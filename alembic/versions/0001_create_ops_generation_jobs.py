"""Create generation jobs and idempotency records.

Revision ID: 0001_ops_generation
Revises:
Create Date: 2026-11-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_ops_generation"
down_revision = None
branch_labels = None
depends_on = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ops_generation_jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=128), nullable=False),
        sa.Column("site_id", sa.String(length=128), nullable=False),
        sa.Column("function_code", sa.String(length=64), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("draft_id", sa.String(length=128), nullable=True),
        sa.Column("lint_issues", json_type, nullable=False, server_default="[]"),
        sa.Column("inputs", json_type, nullable=False),
        sa.Column("generated_content", sa.Text(), nullable=True),
        sa.Column("template_version", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued', 'drafted', 'failed')", name="ck_ops_generation_jobs_status"),
    )
    op.create_index("ix_ops_generation_jobs_status", "ops_generation_jobs", ["status"])
    op.create_index("ix_ops_generation_scope_created", "ops_generation_jobs", ["org_id", "site_id", "created_at"])
    op.create_index("ix_ops_generation_created_by", "ops_generation_jobs", ["created_by", "created_at"])
    op.create_table(
        "ops_generation_idempotency",
        sa.Column("scope_key", sa.String(length=64), primary_key=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ops_generation_idempotency_expires_at", "ops_generation_idempotency", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_ops_generation_idempotency_expires_at", table_name="ops_generation_idempotency")
    op.drop_table("ops_generation_idempotency")
    op.drop_index("ix_ops_generation_created_by", table_name="ops_generation_jobs")
    op.drop_index("ix_ops_generation_scope_created", table_name="ops_generation_jobs")
    op.drop_index("ix_ops_generation_jobs_status", table_name="ops_generation_jobs")
    op.drop_table("ops_generation_jobs")
