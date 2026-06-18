from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from project_dm.schemas import BrandCreate


BRAND_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BRAND_PATH = re.compile(r"/brand/(?P<slug>[a-z0-9-]+)/c/?$", re.IGNORECASE)
DEFAULT_LISTING_URL = (
    "https://www.emag.ro/telefoane-mobile/brand/{slug}/c"
)


def normalize_brand(value: str) -> BrandCreate:
    candidate = value.strip()
    if not candidate:
        raise ValueError("Brand slug or URL cannot be empty")

    if "://" not in candidate:
        slug = candidate.lower()
        if not BRAND_SLUG.fullmatch(slug):
            raise ValueError(
                "Brand slug may contain lowercase letters, digits, and hyphens"
            )
        listing_url = DEFAULT_LISTING_URL.format(slug=slug)
    else:
        parsed = urlsplit(candidate)
        if parsed.scheme != "https" or parsed.hostname not in {
            "emag.ro",
            "www.emag.ro",
        }:
            raise ValueError("Brand URL must be an HTTPS URL on www.emag.ro")

        path = parsed.path.rstrip("/")
        match = BRAND_PATH.search(path)
        if not match:
            raise ValueError(
                "Brand URL must end with /brand/<brand-slug>/c"
            )
        slug = match.group("slug").lower()
        listing_url = urlunsplit(
            ("https", "www.emag.ro", path, "", "")
        )

    return BrandCreate(
        name=slug.replace("-", " ").title(),
        slug=slug,
        listing_url=listing_url,
    )
