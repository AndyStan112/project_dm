from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from project_dm.schemas import ListingPage, ListingProduct


PNK_PATTERN = re.compile(r"/pd/(?P<pnk>[A-Z0-9]+)/?", re.IGNORECASE)


def optional_int(value: str | None) -> int | None:
    if value is None or not value.isdigit():
        return None
    return int(value)


def parse_listing_page(html: str, page_url: str) -> ListingPage:
    tree = HTMLParser(html)
    products: list[ListingProduct] = []
    seen_pnks: set[str] = set()

    for card in tree.css(".js-product-data"):
        raw_url = card.attributes.get("data-url")
        title = card.attributes.get("data-name")
        if not raw_url or not title:
            continue

        url = urljoin(page_url, raw_url).split("?", 1)[0]
        match = PNK_PATTERN.search(url)
        if not match:
            continue

        pnk = match.group("pnk").upper()
        if pnk in seen_pnks:
            continue

        family_node = card.css_first("[data-family-id]")
        family_id = (
            optional_int(family_node.attributes.get("data-family-id"))
            if family_node
            else None
        )
        products.append(
            ListingProduct(
                emag_product_id=optional_int(
                    card.attributes.get("data-product-id")
                ),
                offer_id=optional_int(card.attributes.get("data-offer-id")),
                family_id=family_id,
                pnk=pnk,
                title=title.strip(),
                url=url,
            )
        )
        seen_pnks.add(pnk)

    next_node = tree.css_first('link[rel="next"]')
    next_url = None
    if next_node and next_node.attributes.get("href"):
        next_url = urljoin(page_url, next_node.attributes["href"])

    return ListingPage(products=products, next_url=next_url)
