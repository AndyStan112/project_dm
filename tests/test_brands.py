import pytest

from project_dm.brands import normalize_brand


def test_normalize_brand_slug() -> None:
    brand = normalize_brand("Samsung")

    assert brand.slug == "samsung"
    assert brand.name == "Samsung"
    assert str(brand.listing_url) == (
        "https://www.emag.ro/telefoane-mobile/brand/samsung/c"
    )


def test_normalize_brand_url_removes_query() -> None:
    brand = normalize_brand(
        "https://www.emag.ro/brands/telefoane-mobile/"
        "brand/samsung/c?ref=search_category_1"
    )

    assert brand.slug == "samsung"
    assert str(brand.listing_url) == (
        "https://www.emag.ro/brands/telefoane-mobile/brand/samsung/c"
    )


@pytest.mark.parametrize(
    "value",
    (
        "",
        "not a brand",
        "https://example.com/brand/apple/c",
        "http://www.emag.ro/brand/apple/c",
        "https://www.emag.ro/search/apple",
    ),
)
def test_normalize_brand_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_brand(value)
