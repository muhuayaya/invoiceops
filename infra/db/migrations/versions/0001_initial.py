"""create InvoiceOps core tables"""
from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema_path = Path(__file__).resolve().parents[2] / "schema.sql"
    op.execute(schema_path.read_text(encoding="utf-8"))


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS outbox_events, audit_events, taxonomy_versions, model_versions, reviews, prediction_labels, predictions, idempotency_records, tickets CASCADE")
