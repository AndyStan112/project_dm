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

## Legacy CLI

The command-line interface still exists for automation and testing, but the web
UI is now the primary management surface.

Add a brand by slug or full eMAG brand URL:

```bash
uv run project-dm brand add apple
uv run project-dm brand add \
  "https://www.emag.ro/brands/telefoane-mobile/brand/samsung/c"
```

Inspect configured brands and jobs:

```bash
uv run project-dm brand list
uv run project-dm job list
uv run project-dm job list --status pending
```

Control an individual job:

```bash
uv run project-dm job pause JOB_ID
uv run project-dm job resume JOB_ID
uv run project-dm job skip JOB_ID
```

Run one pending brand-listing job:

```bash
# Process every remaining listing page.
uv run project-dm worker listing

# Process one page, checkpoint the next URL, and pause.
uv run project-dm worker listing --max-pages 1
```

The listing worker commits after every page. It creates deduplicated product
jobs, stores the next listing URL, waits 5–10 seconds between pages, and stops
on HTTP 403/429, visible CAPTCHA text, or an invalid empty listing.
Diagnostics for blocked or invalid pages are written under
`data/diagnostics/`.

Run a bounded batch of product jobs:

```bash
# The default processes exactly one product page.
uv run project-dm worker product

# Process up to five pages, waiting 5-10 seconds between requests.
uv run project-dm worker product --max-jobs 5
```

Each product is committed in one transaction. The worker upserts the shared
family metadata and PNK-specific variants, marks the product job complete,
and creates one deduplicated review job for the family. HTTP 403/429,
visible CAPTCHA text, timeouts, and invalid product data stop the batch and
record the error on the current job.

Collect reviews with item-offset checkpoints:

```bash
# Collect 10 reviews, checkpoint, and pause.
uv run project-dm worker reviews

# Continue a paused job with another page.
uv run project-dm job resume JOB_ID
uv run project-dm worker reviews

# Process every remaining page with a 5-10 second delay.
uv run project-dm worker reviews --all-pages
```

Review rows are upserted by their stable eMAG review IDs. The collector stores
title, content, rating, helpful votes, verified-purchase status, created and
published dates, reviewer name/hash, storage, color, and compact avatar
metadata. Initials-only avatars are marked as `default_name`; image-backed
avatars remain queued conceptually for the separate classification service.

Read-only commands use `DATABASE_URL_READ`. Commands that create or change
brands and jobs use `DATABASE_URL_WRITE`.

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

Development milestones are tracked in [TODO.md](TODO.md).

## Web Dashboard

Start the local UI:

```bash
uv run project-dm-web
```

Open `http://127.0.0.1:8000`. The dashboard provides:

- Dataset totals, rating distribution, and job health summaries.
- Searchable and filterable jobs with progress and error details.
- Pause, resume/retry, and skip controls for individual jobs.
- Worker management for scraper, avatar, and NLP services.
- Bounded buttons for one listing, product, or review work unit.
- Brand creation from a slug or full eMAG URL.
- Product-family, variant, price, and review-coverage views.
- Review text queries and rating, verified-purchase, and vote filters.

Dashboard pages use `DATABASE_URL_READ`. Brand creation, job controls, and
worker desired-state updates use `DATABASE_URL_WRITE`. Worker buttons launch one
bounded background task; the service controls manage the long-running worker
daemons.

## Docker Deployment

The repository includes a `Dockerfile`, `compose.yml`, and GitHub Actions
workflow for server deployment.

The container exposes port `8000` and joins an external Docker network named
`web`, so it can sit behind an existing reverse proxy without publishing host
ports.

On the server, create the external network once if it does not exist:

```bash
docker network create web
```

Then clone the repo into `~/apps/project_dm`, place your production `.env`
there, and run:

```bash
docker compose up -d --build
```
