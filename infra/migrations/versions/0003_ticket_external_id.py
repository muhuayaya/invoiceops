"""为单条分类保留可选的外部编号。

Revision ID: 0003_ticket_external_id
Revises: 0002_batch_item_history
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0003_ticket_external_id"
down_revision = "0002_batch_item_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "external_id" not in {column["name"] for column in inspect(op.get_bind()).get_columns("tickets")}:
        with op.batch_alter_table("tickets") as batch_op:
            batch_op.add_column(sa.Column("external_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    if "external_id" in {column["name"] for column in inspect(op.get_bind()).get_columns("tickets")}:
        with op.batch_alter_table("tickets") as batch_op:
            batch_op.drop_column("external_id")
