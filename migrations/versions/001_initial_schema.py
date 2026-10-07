"""Initial schema for files, features, and measurements.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-10-07 12:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create 'files' table
    op.create_table(
        "files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="UPLOADED"),
        sa.Column("feature_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_crs", sa.String(length=64), nullable=True),
        sa.Column("calculation_crs", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("summary_metrics", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_files_status", "files", ["status"], unique=False)

    # 2. Create 'features' table
    op.create_table(
        "features",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("feature_index", sa.Integer(), nullable=False),
        sa.Column("geometry_type", sa.String(length=64), nullable=False),
        sa.Column("geometry_geojson", sa.JSON(), nullable=True),
        sa.Column("properties", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="SUCCESS"),
        sa.Column("warning_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_features_file_id", "features", ["file_id"], unique=False)
    op.create_index(
        "ix_features_file_id_feature_index",
        "features",
        ["file_id", "feature_index"],
        unique=False,
    )

    # 3. Create 'measurements' table
    op.create_table(
        "measurements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("feature_id", sa.String(length=36), nullable=False),
        sa.Column("measurement_type", sa.String(length=32), nullable=True),
        sa.Column("measurement_value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("calculation_crs", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["feature_id"], ["features.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature_id"),
    )
    op.create_index("ix_measurements_feature_id", "measurements", ["feature_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_measurements_feature_id", table_name="measurements")
    op.drop_table("measurements")

    op.drop_index("ix_features_file_id_feature_index", table_name="features")
    op.drop_index("ix_features_file_id", table_name="features")
    op.drop_table("features")

    op.drop_index("ix_files_status", table_name="files")
    op.drop_table("files")
