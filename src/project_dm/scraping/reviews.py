from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode, urlparse

from project_dm.schemas import ParsedReview, ParsedReviewPage


PRODUCT_PATH_PATTERN = re.compile(
    r"^/(?P<slug>[^/]+)/pd/(?P<pnk>[A-Z0-9]+)/?$",
    re.IGNORECASE,
)


def build_reviews_url(
    product_url: str, *, offset: int, limit: int = 10
) -> str:
    parsed = urlparse(product_url)
    match = PRODUCT_PATH_PATTERN.match(parsed.path)
    if not match:
        raise ValueError(f"Unsupported eMAG product URL: {product_url}")

    slug = match.group("slug")
    pnk = match.group("pnk").upper()
    query = urlencode(
        {
            "source_id": 7,
            "page[offset]": offset,
            "page[limit]": limit,
            "sort[created]": "desc",
            "sefName": slug,
            "pnk": pnk,
        }
    )
    return (
        f"{parsed.scheme}://{parsed.netloc}/product-feedback/{slug}"
        f"/pd/{pnk}/reviews/list?{query}"
    )


def _variant_values(product: dict[str, Any]) -> tuple[str | None, str | None]:
    storage = None
    color = None
    family = product.get("family_characteristics") or {}
    for characteristic in family.get("characteristics") or []:
        name = str(characteristic.get("name") or "").casefold()
        value = (characteristic.get("value") or {}).get("value")
        if "memorie" in name or "storage" in name:
            storage = value
        elif "culoare" in name or "color" in name:
            color = value
    return storage, color


def _avatar_metadata(user: dict[str, Any]) -> dict[str, Any] | None:
    avatar = user.get("user_avatar") or {}
    if not avatar:
        return None
    image = avatar.get("image") or {}
    image_url = image.get("original") or avatar.get("path")
    return {
        "classification_hint": (
            "needs_image_classification" if image_url else "default_name"
        ),
        "initials": avatar.get("initials"),
        "background_color": avatar.get("background_color"),
        "image_url": image_url,
    }


def parse_review_page(payload: dict[str, Any]) -> ParsedReviewPage:
    response = payload.get("response") or {}
    if response.get("code") != 200:
        raise ValueError(f"Review endpoint returned code {response.get('code')}")

    review_data = payload.get("reviews")
    if not isinstance(review_data, dict):
        raise ValueError("Review response is missing reviews data")

    parsed_reviews: list[ParsedReview] = []
    for item in review_data.get("items") or []:
        product = item.get("product") or {}
        user = item.get("user") or {}
        storage, color = _variant_values(product)
        content = item.get("content_no_tags")
        if content is None:
            content = item.get("content") or ""

        parsed_reviews.append(
            ParsedReview(
                emag_review_id=item["id"],
                pnk=product.get("part_number_key"),
                title=item.get("title"),
                content=" ".join(str(content).split()),
                rating=item["rating"],
                votes=item.get("votes") or 0,
                verified_purchase=bool(item.get("is_bought")),
                reviewer_name=user.get("name"),
                reviewer_hash=user.get("hash"),
                review_created_at=item.get("created"),
                published_at=item.get("published"),
                storage=storage,
                color=color,
                avatar_metadata=_avatar_metadata(user),
            )
        )

    return ParsedReviewPage(
        total_count=review_data.get("count") or 0,
        reviews=parsed_reviews,
    )
