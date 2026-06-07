# eMAG Review Collection

## Project Structure

```text
.
├── migrations/             Alembic schema history
├── src/project_dm/
│   ├── commands/           Administrative and exploration commands
│   ├── cli.py              Main command-line entry point
│   ├── db.py               Role-specific database connections
│   └── models.py           SQLAlchemy models
├── tests/
├── alembic.ini
└── pyproject.toml
```

The project is an installable `src`-layout package built with uv's
`uv_build` backend.

```bash
uv sync
uv run project-dm version
uv build
```

## Database

Schema changes are tracked in `migrations/versions/` with Alembic.

```bash
uv run alembic current
uv run alembic upgrade head
uv run db-configure-permissions
uv run db-check-access
```

Application code uses:

- `DATABASE_URL_WRITE` for scraper and processing writes.
- `DATABASE_URL_READ` for dashboards, analysis, and exports.
- `DATABASE_URL_ADMIN` only for migrations and grants.

Copy `.env.example` to `.env` for local configuration. Never commit `.env`.
