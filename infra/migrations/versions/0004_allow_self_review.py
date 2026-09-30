"""Allow ticket submitters to review their own classifications.

Revision ID: 0004_allow_self_review
Revises: 0003_ticket_external_id
"""

from alembic import op
from sqlalchemy import inspect


revision = "0004_allow_self_review"
down_revision = "0003_ticket_external_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    constraints = inspect(bind).get_check_constraints("review_decisions")
    if any(item["name"] == "review_no_self" for item in constraints):
        with op.batch_alter_table("review_decisions") as batch_op:
            batch_op.drop_constraint("review_no_self", type_="check")


def downgrade() -> None:
    with op.batch_alter_table("review_decisions") as batch_op:
        batch_op.create_check_constraint("review_no_self", "submitted_by <> reviewer_id")
