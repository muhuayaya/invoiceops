# Database migration

The canonical PostgreSQL DDL is `schema.sql`. In a configured environment use:

```powershell
alembic upgrade head
```

The application also exposes `create_schema(engine)` for isolated SQLite contract tests. Production code must use PostgreSQL and the migration path; SQLite is only a local test adapter.
