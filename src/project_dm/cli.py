from __future__ import annotations

import typer

from project_dm import __version__
from project_dm.brands import normalize_brand
from project_dm.db import read_session, write_session
from project_dm.repositories.brands import list_brands, upsert_brand
from project_dm.repositories.jobs import (
    get_or_create_brand_listing_job,
    list_jobs,
    set_job_status,
)
from project_dm.schemas import BrandRead, JobRead, JobStatus
from project_dm.workers.listing import run_one_listing_job
from project_dm.workers.product import run_product_jobs
from project_dm.workers.reviews import run_one_review_job


app = typer.Typer(
    help="Deprecated: use the web UI for day-to-day management.",
    no_args_is_help=True,
)
brand_app = typer.Typer(help="Deprecated: manage brands and listing jobs.")
job_app = typer.Typer(help="Deprecated: inspect and control collection jobs.")
worker_app = typer.Typer(help="Deprecated: run bounded background work.")
app.add_typer(brand_app, name="brand")
app.add_typer(job_app, name="job")
app.add_typer(worker_app, name="worker")


@app.callback()
def root() -> None:
    """Run project administration commands."""


@app.command()
def version() -> None:
    """Print the installed project version."""
    typer.echo(__version__)


@brand_app.command("add")
def add_brand(
    brand: str = typer.Argument(
        help="Brand slug such as 'apple', or a full eMAG brand URL."
    ),
) -> None:
    """Create or update a brand and enqueue its listing job."""
    try:
        data = normalize_brand(brand)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="brand") from exc

    with write_session() as session, session.begin():
        record = upsert_brand(session, data)
        job, created = get_or_create_brand_listing_job(
            session,
            brand_id=record.id,
            target_url=record.listing_url,
        )
        brand_result = BrandRead.model_validate(record)
        job_result = JobRead.model_validate(job)

    action = "created" if created else "already active"
    typer.echo(
        f"brand={brand_result.slug} id={brand_result.id} "
        f"job={job_result.id} ({action})"
    )


@brand_app.command("list")
def show_brands() -> None:
    """List configured brands using the read-only database role."""
    with read_session() as session:
        records = [BrandRead.model_validate(row) for row in list_brands(session)]

    if not records:
        typer.echo("No brands configured.")
        return
    for record in records:
        state = "enabled" if record.enabled else "disabled"
        typer.echo(f"{record.id}\t{record.slug}\t{state}\t{record.listing_url}")


@job_app.command("list")
def show_jobs(
    status: JobStatus | None = typer.Option(
        default=None,
        help="Only display jobs with this status.",
    ),
    limit: int = typer.Option(default=100, min=1, max=1_000),
) -> None:
    """List recent jobs using the read-only database role."""
    with read_session() as session:
        records = [
            JobRead.model_validate(row)
            for row in list_jobs(session, status=status, limit=limit)
        ]

    if not records:
        typer.echo("No jobs found.")
        return
    for record in records:
        progress = str(record.current_offset)
        if record.total_expected is not None:
            progress = f"{progress}/{record.total_expected}"
        typer.echo(
            f"{record.id}\t{record.job_type.value}\t"
            f"{record.status.value}\t{progress}\t"
            f"{record.target_url or ''}"
        )


def change_job_status(job_id: int, status: JobStatus) -> None:
    with write_session() as session, session.begin():
        job = set_job_status(session, job_id, status)
        if job is None:
            raise typer.BadParameter(
                f"Job {job_id} does not exist", param_hint="job_id"
            )
    typer.echo(f"job={job_id} status={status.value}")


@job_app.command()
def pause(job_id: int) -> None:
    """Pause a job after its current unit of work."""
    change_job_status(job_id, JobStatus.PAUSED)


@job_app.command()
def resume(job_id: int) -> None:
    """Return a paused, failed, or blocked job to the pending queue."""
    change_job_status(job_id, JobStatus.PENDING)


@job_app.command()
def skip(job_id: int) -> None:
    """Permanently skip a job."""
    change_job_status(job_id, JobStatus.SKIPPED)


@worker_app.command("listing")
def run_listing_worker(
    max_pages: int | None = typer.Option(
        default=None,
        min=1,
        help="Pause after this many listing pages.",
    ),
    min_delay: float = typer.Option(default=5.0, min=0),
    max_delay: float = typer.Option(default=10.0, min=0),
) -> None:
    """Process one pending brand-listing job."""
    try:
        result = run_one_listing_job(
            max_pages=max_pages,
            min_delay=min_delay,
            max_delay=max_delay,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    status = result.status.value if result.status is not None else "idle"
    typer.echo(
        f"job={result.job_id or '-'} status={status} "
        f"pages={result.pages_processed} "
        f"product_jobs={result.product_jobs_created} "
        f"message={result.message}"
    )


@worker_app.command("product")
def run_product_worker(
    max_jobs: int = typer.Option(
        default=1,
        min=1,
        help="Process at most this many product pages.",
    ),
    min_delay: float = typer.Option(default=5.0, min=0),
    max_delay: float = typer.Option(default=10.0, min=0),
) -> None:
    """Process a bounded batch of pending product jobs."""
    try:
        result = run_product_jobs(
            max_jobs=max_jobs,
            min_delay=min_delay,
            max_delay=max_delay,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    status = result.status.value if result.status is not None else "idle"
    typer.echo(
        f"status={status} products={result.jobs_processed} "
        f"variants={result.variants_upserted} "
        f"review_jobs={result.review_jobs_created} "
        f"message={result.message}"
    )


@worker_app.command("reviews")
def run_review_worker(
    max_pages: int = typer.Option(
        default=1,
        min=1,
        help="Pause after this many review pages.",
    ),
    all_pages: bool = typer.Option(
        default=False,
        help="Continue until the review job is complete.",
    ),
    page_size: int = typer.Option(default=10, min=1, max=100),
    min_delay: float = typer.Option(default=5.0, min=0),
    max_delay: float = typer.Option(default=10.0, min=0),
) -> None:
    """Process one checkpointed review job."""
    try:
        result = run_one_review_job(
            max_pages=None if all_pages else max_pages,
            page_size=page_size,
            min_delay=min_delay,
            max_delay=max_delay,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    status = result.status.value if result.status is not None else "idle"
    typer.echo(
        f"job={result.job_id or '-'} status={status} "
        f"pages={result.pages_processed} "
        f"reviews={result.reviews_upserted} "
        f"message={result.message}"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
