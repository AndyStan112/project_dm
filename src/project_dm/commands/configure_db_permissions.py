from __future__ import annotations

from urllib.parse import unquote, urlparse

from psycopg2 import connect, sql

from project_dm.db import DatabaseRole, database_url


APPLICATION_TABLES = (
    "brands",
    "product_families",
    "variants",
    "reviews",
    "jobs",
    "service_controls",
    "avatar_results",
    "nlp_results",
)


def username(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.username:
        raise RuntimeError("Database URL does not contain a username")
    return unquote(parsed.username)


def main() -> None:
    admin_url = database_url(DatabaseRole.ADMIN)
    write_user = username(database_url(DatabaseRole.WRITE))
    read_user = username(database_url(DatabaseRole.READ))

    with connect(admin_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(write_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                    sql.Identifier(read_user)
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {} TO {}"
                ).format(
                    sql.SQL(", ").join(
                        map(sql.Identifier, APPLICATION_TABLES)
                    ),
                    sql.Identifier(write_user),
                )
            )
            cursor.execute(
                sql.SQL("GRANT SELECT ON TABLE alembic_version TO {}").format(
                    sql.Identifier(write_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                    sql.SQL(", ").join(
                        map(
                            sql.Identifier,
                            ("alembic_version", *APPLICATION_TABLES),
                        )
                    ),
                    sql.Identifier(read_user),
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                    "TO {}"
                ).format(sql.Identifier(write_user))
            )

    print("Configured read and write database permissions.")


if __name__ == "__main__":
    main()
