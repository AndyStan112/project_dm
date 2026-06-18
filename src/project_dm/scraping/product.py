from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any
from urllib.parse import urldefrag, urljoin

from selectolax.parser import HTMLParser

from project_dm.schemas import ParsedProductPage, ParsedVariant


def _required_int(html: str, name: str) -> int:
    match = re.search(rf"EM\.{re.escape(name)}\s*=\s*(\d+)\s*;", html)
    if not match:
        raise ValueError(f"Missing EM.{name}")
    return int(match.group(1))


def _json_value_after(text: str, marker: str) -> Any:
    start = text.find(marker)
    if start < 0:
        raise ValueError(f"Missing product data marker: {marker}")
    start += len(marker)
    while start < len(text) and text[start].isspace():
        start += 1

    decoder = json.JSONDecoder()
    value, _ = decoder.raw_decode(text[start:])
    return value


def _feedback(html: str) -> tuple[Decimal | None, int | None]:
    block_match = re.search(
        r"EM\.feedback\s*=\s*\{(?P<body>.*?)\}\s*;"
        r"\s*EM\.product\s*=",
        html,
        flags=re.DOTALL,
    )
    if not block_match:
        return None, None
    body = block_match.group("body")
    rating_match = re.search(r"\brating:\s*(\d+(?:\.\d+)?)", body)
    reviews_match = re.search(
        r"\breviews:\s*\{\s*count:\s*(\d+)", body, flags=re.DOTALL
    )
    rating = Decimal(rating_match.group(1)) if rating_match else None
    count = int(reviews_match.group(1)) if reviews_match else None
    return rating, count


def _description(html: str) -> str | None:
    tree = HTMLParser(html)
    node = tree.css_first("#description-body")
    if node is None:
        return None
    text = " ".join(node.text(separator=" ", strip=True).split())
    return text or None


def _absolute_url(page_url: str, raw_url: dict[str, Any]) -> str:
    path = str(raw_url.get("path") or "")
    base = str(raw_url.get("desktop_base") or page_url)
    return urldefrag(urljoin(base, path)).url


def _selected_values(
    characteristics: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    storage = None
    color = None
    for characteristic in characteristics:
        selected = next(
            (
                product
                for product in characteristic.get("products", [])
                if product.get("is_selected")
            ),
            None,
        )
        if selected is None:
            continue
        name = str(characteristic.get("name", "")).casefold()
        if "memorie" in name or "storage" in name:
            storage = selected.get("label")
        elif "culoare" in name or "color" in name:
            color = selected.get("label")
    return storage, color


def _variants(
    family: dict[str, Any], page_url: str
) -> list[ParsedVariant]:
    characteristics = family.get("characteristics", [])
    selected_storage, selected_color = _selected_values(characteristics)
    variants: dict[str, ParsedVariant] = {}

    for characteristic in characteristics:
        name = str(characteristic.get("name", "")).casefold()
        for product in characteristic.get("products", []):
            pnk = product.get("part_number_key")
            title = product.get("name")
            raw_url = product.get("url")
            if not pnk or not title or not isinstance(raw_url, dict):
                continue

            storage = selected_storage
            color = selected_color
            if "memorie" in name or "storage" in name:
                storage = product.get("label")
            elif "culoare" in name or "color" in name:
                color = product.get("label")

            price = product.get("price") or {}
            currency = price.get("currency") or {}
            currency_name = currency.get("name") or {}
            variants[str(pnk)] = ParsedVariant(
                emag_product_id=product.get("product_id"),
                pnk=str(pnk),
                title=str(title),
                storage=storage,
                color=color,
                price=price.get("current"),
                currency=currency_name.get("default"),
                available=product.get("is_available"),
                url=_absolute_url(page_url, raw_url),
            )

    return list(variants.values())


def parse_product_page(html: str, page_url: str) -> ParsedProductPage:
    product_start = html.find("EM.product =")
    if product_start < 0:
        raise ValueError("Missing EM.product data")
    product_data = html[product_start:]

    family = _json_value_after(product_data, "family:")
    if not isinstance(family, dict):
        raise ValueError("Invalid product family data")

    family_id = int(family.get("id") or _required_int(html, "family_id"))
    family_name = str(family.get("name") or "").strip()
    if not family_name:
        raise ValueError("Product family has no name")

    variants = _variants(family, page_url)
    if not variants:
        raise ValueError("Product family has no parseable variants")

    rating, review_count = _feedback(html)
    return ParsedProductPage(
        emag_family_id=family_id,
        family_name=family_name,
        description=_description(html),
        aggregate_rating=rating,
        review_count=review_count,
        url=urldefrag(page_url).url,
        variants=variants,
    )
