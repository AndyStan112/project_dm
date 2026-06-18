from __future__ import annotations

from project_dm.scraping.reviews import (
    build_reviews_url,
    parse_review_page,
)


def test_build_reviews_url_uses_item_offset() -> None:
    url = build_reviews_url(
        "https://www.emag.ro/test-phone/pd/ABC123/#reviews",
        offset=20,
        limit=10,
    )

    assert "/product-feedback/test-phone/pd/ABC123/reviews/list?" in url
    assert "page%5Boffset%5D=20" in url
    assert "page%5Blimit%5D=10" in url
    assert "pnk=ABC123" in url


def test_parse_review_page_fields_and_avatar_hints() -> None:
    payload = {
        "response": {"code": 200},
        "reviews": {
            "count": 12,
            "items": [
                {
                    "id": 9001,
                    "title": "Excellent",
                    "content": "Ignored <br> content",
                    "content_no_tags": "Very good phone",
                    "rating": 5,
                    "votes": 7,
                    "is_bought": True,
                    "created": "2026-05-01T10:00:00+03:00",
                    "published": "2026-05-02T10:00:00+03:00",
                    "user": {
                        "name": "Ana Test",
                        "hash": "reviewer-hash",
                        "user_avatar": {
                            "initials": "AT",
                            "background_color": "#123456",
                        },
                    },
                    "product": {
                        "part_number_key": "ABC123",
                        "family_characteristics": {
                            "characteristics": [
                                {
                                    "name": "Memorie interna",
                                    "value": {"value": "256 GB"},
                                },
                                {
                                    "name": "Culoare",
                                    "value": {"value": "Blue"},
                                },
                            ]
                        },
                    },
                },
                {
                    "id": 9002,
                    "title": None,
                    "content": "",
                    "rating": 3,
                    "votes": 0,
                    "is_bought": False,
                    "user": {
                        "name": "Custom User",
                        "hash": "custom-hash",
                        "user_avatar": {
                            "initials": "CU",
                            "image": {
                                "original": "https://example.com/avatar.png"
                            },
                        },
                    },
                    "product": {"part_number_key": "XYZ789"},
                },
            ],
        },
    }

    page = parse_review_page(payload)

    assert page.total_count == 12
    assert len(page.reviews) == 2
    first = page.reviews[0]
    assert first.emag_review_id == 9001
    assert first.content == "Very good phone"
    assert first.verified_purchase is True
    assert first.storage == "256 GB"
    assert first.color == "Blue"
    assert first.avatar_metadata["classification_hint"] == "default_name"
    assert (
        page.reviews[1].avatar_metadata["classification_hint"]
        == "needs_image_classification"
    )
