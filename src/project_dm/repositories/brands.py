from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from project_dm.models import Brand
from project_dm.schemas import BrandCreate


def upsert_brand(session: Session, data: BrandCreate) -> Brand:
    values = data.model_dump(mode="json")
    statement = (
        insert(Brand)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[Brand.slug],
            set_={
                "name": values["name"],
                "listing_url": values["listing_url"],
                "enabled": values["enabled"],
            },
        )
        .returning(Brand)
    )
    return session.scalars(
        select(Brand).from_statement(statement)
    ).one()


def list_brands(session: Session) -> list[Brand]:
    return list(session.scalars(select(Brand).order_by(Brand.slug)))
