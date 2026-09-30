"""Keep batch item snapshots after active batch deletion.

Revision ID: 0002_batch_item_history
Revises: 0001_initial
"""

from alembic import op
from sqlalchemy import inspect, text

from invoiceops.adapters.db import BatchItemHistory


revision = "0002_batch_item_history"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not inspect(bind).has_table(BatchItemHistory.__tablename__):
        BatchItemHistory.__table__.create(bind=bind)
    if bind.dialect.name == "postgresql":
        exists = bind.scalar(text("SELECT 1 FROM pg_trigger WHERE tgname = 'batch_item_history_append_only'"))
        if not exists:
            op.execute("CREATE TRIGGER batch_item_history_append_only BEFORE UPDATE OR DELETE ON batch_item_history FOR EACH ROW EXECUTE FUNCTION invoiceops_reject_history_change()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS batch_item_history_append_only ON batch_item_history")
    BatchItemHistory.__table__.drop(bind=bind, checkfirst=True)
