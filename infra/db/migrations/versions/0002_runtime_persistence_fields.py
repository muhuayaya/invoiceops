"""add durable repository fields introduced by the worker recovery path"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0002_runtime_persistence_fields"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    ticket_columns = {column["name"] for column in inspector.get_columns("tickets")}
    prediction_columns = {column["name"] for column in inspector.get_columns("predictions")}
    idempotency_column = next((column for column in inspector.get_columns("tickets") if column["name"] == "idempotency_key"), None)
    if idempotency_column and not idempotency_column["nullable"] and bind.dialect.name != "sqlite":
        op.alter_column("tickets", "idempotency_key", nullable=True)
    if "risk" not in ticket_columns:
        op.add_column("tickets", sa.Column("risk", sa.String(length=16), nullable=False, server_default="medium"))
    if "language" not in prediction_columns:
        op.add_column("predictions", sa.Column("language", sa.String(length=16), nullable=False, server_default="unknown"))
    idempotency = """
        CREATE TABLE IF NOT EXISTS idempotency_records (
          key VARCHAR(255) PRIMARY KEY,
          fingerprint VARCHAR(64) NOT NULL,
          response JSONB NOT NULL,
          ticket_id UUID NOT NULL REFERENCES tickets(id)
        )
    """
    if bind.dialect.name == "sqlite":
        idempotency = idempotency.replace("JSONB", "TEXT").replace("UUID", "VARCHAR(36)")
    op.execute(sa.text(idempotency))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS idempotency_records"))
    bind = op.get_bind()
    inspector = inspect(bind)
    if "language" in {column["name"] for column in inspector.get_columns("predictions")}:
        op.drop_column("predictions", "language")
    if "risk" in {column["name"] for column in inspector.get_columns("tickets")}:
        op.drop_column("tickets", "risk")
