"""Use native UUID types for job identifiers.

Revision ID: 0002_native_uuid_ids
Revises: 0001_ops_generation
Create Date: 2026-11-16
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_native_uuid_ids"
down_revision = "0001_ops_generation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "ops_generation_jobs",
        "id",
        existing_type=sa.String(length=36),
        type_=sa.Uuid(as_uuid=False),
        existing_nullable=False,
        postgresql_using="id::uuid",
    )
    op.alter_column(
        "ops_generation_idempotency",
        "job_id",
        existing_type=sa.String(length=36),
        type_=sa.Uuid(as_uuid=False),
        existing_nullable=False,
        postgresql_using="job_id::uuid",
    )


def downgrade() -> None:
    op.alter_column(
        "ops_generation_idempotency",
        "job_id",
        existing_type=sa.Uuid(as_uuid=False),
        type_=sa.String(length=36),
        existing_nullable=False,
        postgresql_using="job_id::text",
    )
    op.alter_column(
        "ops_generation_jobs",
        "id",
        existing_type=sa.Uuid(as_uuid=False),
        type_=sa.String(length=36),
        existing_nullable=False,
        postgresql_using="id::text",
    )
