"""allow idempotency records to survive ticket deletion"""

from alembic import op

revision = "0003_nullable_delete_idempotency"
down_revision = "0002_runtime_persistence_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE idempotency_records ALTER COLUMN ticket_id DROP NOT NULL")
    op.execute("ALTER TABLE idempotency_records DROP CONSTRAINT IF EXISTS idempotency_records_ticket_id_fkey")
    op.execute(
        "ALTER TABLE idempotency_records ADD CONSTRAINT idempotency_records_ticket_id_fkey "
        "FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE SET NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE idempotency_records DROP CONSTRAINT IF EXISTS idempotency_records_ticket_id_fkey")
    op.execute(
        "ALTER TABLE idempotency_records ADD CONSTRAINT idempotency_records_ticket_id_fkey "
        "FOREIGN KEY (ticket_id) REFERENCES tickets(id)"
    )
    op.execute("ALTER TABLE idempotency_records ALTER COLUMN ticket_id SET NOT NULL")
