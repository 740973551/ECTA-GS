"""Local evidence extraction, review history and versioned building knowledge."""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("document_parse_tasks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("parser", sa.String(160), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.Column("finished_at", sa.String, nullable=True))
    op.create_table("document_extractions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("task_id", sa.Integer, sa.ForeignKey("document_parse_tasks.id"), nullable=False),
        sa.Column("document_id", sa.Integer, sa.ForeignKey("documents.id"), nullable=False),
        sa.Column("locator", sa.Text, nullable=False), sa.Column("text", sa.Text, nullable=False),
        sa.Column("details", sa.Text, nullable=False), sa.Column("parser", sa.String(160), nullable=False),
        sa.Column("created_at", sa.String, nullable=False))
    op.create_table("knowledge_claims",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("building_id", sa.Integer, sa.ForeignKey("buildings.id"), nullable=False),
        sa.Column("extraction_id", sa.Integer, sa.ForeignKey("document_extractions.id"), nullable=False),
        sa.Column("category", sa.String(80), nullable=False), sa.Column("content", sa.Text, nullable=False),
        sa.Column("epistemic", sa.String(40), nullable=False), sa.Column("status", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer, nullable=False), sa.Column("updated_at", sa.String, nullable=False))
    op.create_table("knowledge_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("claim_id", sa.Integer, sa.ForeignKey("knowledge_claims.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False), sa.Column("snapshot", sa.Text, nullable=False),
        sa.Column("created_at", sa.String, nullable=False),
        sa.UniqueConstraint("claim_id", "version", name="uq_claim_version"))
    op.create_table("human_reviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("claim_id", sa.Integer, sa.ForeignKey("knowledge_claims.id"), nullable=False),
        sa.Column("version_id", sa.Integer, sa.ForeignKey("knowledge_versions.id"), nullable=False),
        sa.Column("before", sa.Text, nullable=False), sa.Column("after", sa.Text, nullable=False),
        sa.Column("reviewer", sa.String(160), nullable=False), sa.Column("reason", sa.Text, nullable=False),
        sa.Column("created_at", sa.String, nullable=False))
    for table, cols in {
        "document_parse_tasks": ["document_id"],
        "document_extractions": ["task_id", "document_id"],
        "knowledge_claims": ["building_id", "extraction_id", "status"],
        "knowledge_versions": ["claim_id"], "human_reviews": ["claim_id"],
    }.items():
        for col in cols:
            op.create_index(f"ix_{table}_{col}", table, [col])


def downgrade():
    raise RuntimeError("审核历史不可自动删除；请恢复独立备份。")
