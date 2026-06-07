from __future__ import annotations

import typer

from project_dm import __version__


app = typer.Typer(
    help="eMAG review collection and analysis tools.",
    no_args_is_help=True,
)


@app.callback()
def root() -> None:
    """Run project administration commands."""


@app.command()
def version() -> None:
    """Print the installed project version."""
    typer.echo(__version__)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
