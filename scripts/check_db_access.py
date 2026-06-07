from __future__ import annotations

from uuid import uuid4

from sqlalchemy import exc, text

from project_dm.db import DatabaseRole, engine


def main() -> None:
    for role in (DatabaseRole.WRITE, DatabaseRole.READ):
        with engine(role).connect() as connection:
            result = connection.execute(
                text(
                    "SELECT current_user, "
                    "(SELECT count(*) FROM service_controls) AS services"
                )
            ).one()
            print(
                f"{role.value.lower()}: user={result.current_user}, "
                f"service_controls={result.services}"
            )

    slug = f"permission-check-{uuid4().hex}"
    with engine(DatabaseRole.WRITE).connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text(
                "INSERT INTO brands (name, slug, listing_url) "
                "VALUES (:name, :slug, :url)"
            ),
            {
                "name": "Permission check",
                "slug": slug,
                "url": "https://example.invalid",
            },
        )
        transaction.rollback()
    print("write: insert allowed (rolled back)")

    with engine(DatabaseRole.READ).connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "INSERT INTO brands (name, slug, listing_url) "
                    "VALUES (:name, :slug, :url)"
                ),
                {
                    "name": "Permission check",
                    "slug": slug,
                    "url": "https://example.invalid",
                },
            )
        except exc.DBAPIError:
            print("read: insert correctly denied")
        else:
            raise RuntimeError(
                "DATABASE_URL_READ unexpectedly allows INSERT"
            )
        finally:
            transaction.rollback()


if __name__ == "__main__":
    main()
