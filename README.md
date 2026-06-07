# eMAG Review Collection

## Database

Schema changes are tracked in `migrations/versions/` with Alembic.

```bash
uv run alembic current
uv run alembic upgrade head
uv run python -m scripts.configure_db_permissions
uv run python -m scripts.check_db_access
```

Application code uses:

- `DATABASE_URL_WRITE` for scraper and processing writes.
- `DATABASE_URL_READ` for dashboards, analysis, and exports.
- `DATABASE_URL_ADMIN` only for migrations and grants.

Copy `.env.example` to `.env` for local configuration. Never commit `.env`.
