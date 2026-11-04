"""M0/M1: buildings, phases, immutable assets and document assignments."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("buildings", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("aliases", sa.Text, nullable=False), sa.Column("location", sa.Text, nullable=False),
        sa.Column("period", sa.String(120), nullable=False), sa.Column("building_type", sa.String(120), nullable=False),
        sa.Column("dating_basis", sa.Text, nullable=False), sa.Column("archived", sa.Boolean, nullable=False),
        sa.Column("updated_at", sa.String, nullable=False))
    op.create_table("building_phases", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("building_id", sa.Integer, sa.ForeignKey("buildings.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False), sa.Column("date_statement", sa.Text, nullable=False),
        sa.Column("evidence", sa.Text, nullable=False))
    op.create_table("source_assets", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("relative_path", sa.Text, nullable=False), sa.Column("size", sa.Integer, nullable=False),
        sa.Column("imported_at", sa.String, nullable=False))
    op.create_table("documents", sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("asset_id", sa.Integer, sa.ForeignKey("source_assets.id"), nullable=False),
        sa.Column("building_id", sa.Integer, sa.ForeignKey("buildings.id")),
        sa.Column("scope", sa.Integer, nullable=False), sa.Column("filename", sa.Text, nullable=False),
        sa.Column("extension", sa.String(40), nullable=False), sa.Column("source_note", sa.Text, nullable=False),
        sa.Column("notes", sa.Text, nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("error", sa.Text, nullable=False), sa.Column("imported_at", sa.String, nullable=False),
        sa.UniqueConstraint("asset_id", "scope", name="uq_asset_scope"))


def downgrade():
    raise RuntimeError("为保护研究资料，不提供自动删除数据表。请从备份恢复。")
