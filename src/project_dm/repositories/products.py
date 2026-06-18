from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from project_dm.models import ProductFamily, Variant
from project_dm.schemas import ProductFamilyCreate, VariantCreate


def upsert_product_family(
    session: Session, data: ProductFamilyCreate
) -> ProductFamily:
    values = data.model_dump(mode="json")
    statement = (
        insert(ProductFamily)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_product_families_brand_emag_family",
            set_={
                "name": values["name"],
                "description": values["description"],
                "aggregate_rating": values["aggregate_rating"],
                "review_count": values["review_count"],
                "url": values["url"],
                "scraped_at": values["scraped_at"],
            },
        )
        .returning(ProductFamily)
    )
    return session.scalars(
        statement.execution_options(populate_existing=True)
    ).one()


def upsert_variant(session: Session, data: VariantCreate) -> Variant:
    values = data.model_dump(mode="json")
    statement = (
        insert(Variant)
        .values(**values)
        .on_conflict_do_update(
            index_elements=(Variant.pnk,),
            set_={
                key: value
                for key, value in values.items()
                if key != "pnk"
            },
        )
        .returning(Variant)
    )
    return session.scalars(
        statement.execution_options(populate_existing=True)
    ).one()
