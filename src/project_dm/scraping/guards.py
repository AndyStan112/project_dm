from __future__ import annotations

import re


BLOCK_MARKERS = (
    "verify that you're not a robot",
    "verifică dacă ești robot",
    "captcha",
    "access denied",
    "too many requests",
)


def visible_page_is_blocked(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text).strip().lower()
    return any(marker in normalized for marker in BLOCK_MARKERS)
