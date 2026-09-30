"""Initial activity, independent history and identity schema.

Revision ID: 0001_initial
Revises:
"""

from alembic import op
from invoiceops.adapters.db import Base, BatchItemHistory


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


_IMMUTABLE_TABLES = ("ticket_versions", "prediction_history", "review_decisions", "audit_events")


def upgrade() -> None:
    bind = op.get_bind()
    tables = [table for table in Base.metadata.sorted_tables if table is not BatchItemHistory.__table__]
    Base.metadata.create_all(bind=bind, tables=tables)
    if bind.dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION invoiceops_reject_history_change() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'history is append-only';
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in _IMMUTABLE_TABLES:
            op.execute(f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION invoiceops_reject_history_change()")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _IMMUTABLE_TABLES:
            op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
        op.execute("DROP FUNCTION invoiceops_reject_history_change()")
    Base.metadata.drop_all(bind=bind)
