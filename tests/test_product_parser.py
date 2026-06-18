from __future__ import annotations

import json

from project_dm.scraping.product import parse_product_page


def test_parse_product_family_and_variant_cross() -> None:
    family = {
        "id": 77,
        "name": "Test Phone",
        "characteristics": [
            {
                "name": "Memorie interna",
                "products": [
                    {
                        "product_id": 101,
                        "label": "128 GB",
                        "part_number_key": "PNK1",
                        "name": "Test Phone, 128 GB, Black",
                        "url": {
                            "path": "/phone/pd/PNK1/#128",
                            "desktop_base": "https://www.emag.ro",
                        },
                        "price": {
                            "current": 1000,
                            "currency": {"name": {"default": "RON"}},
                        },
                        "is_available": True,
                        "is_selected": True,
                    },
                    {
                        "product_id": 102,
                        "label": "256 GB",
                        "part_number_key": "PNK2",
                        "name": "Test Phone, 256 GB, Black",
                        "url": {
                            "path": "/phone/pd/PNK2/",
                            "desktop_base": "https://www.emag.ro",
                        },
                        "price": {
                            "current": 1200,
                            "currency": {"name": {"default": "RON"}},
                        },
                        "is_available": False,
                    },
                ],
            },
            {
                "name": "Culoare",
                "products": [
                    {
                        "product_id": 101,
                        "label": "Black",
                        "part_number_key": "PNK1",
                        "name": "Test Phone, 128 GB, Black",
                        "url": {
                            "path": "/phone/pd/PNK1/",
                            "desktop_base": "https://www.emag.ro",
                        },
                        "price": {
                            "current": 1000,
                            "currency": {"name": {"default": "RON"}},
                        },
                        "is_available": True,
                        "is_selected": True,
                    },
                    {
                        "product_id": 103,
                        "label": "Blue",
                        "part_number_key": "PNK3",
                        "name": "Test Phone, 128 GB, Blue",
                        "url": {
                            "path": "/phone/pd/PNK3/",
                            "desktop_base": "https://www.emag.ro",
                        },
                        "price": {
                            "current": 1000,
                            "currency": {"name": {"default": "RON"}},
                        },
                        "is_available": True,
                    },
                ],
            },
        ],
    }
    html = f"""
    <html><body>
      <div id="description-body"><p> A useful description. </p></div>
      <script>
        EM.product_id = 101;
        EM.family_id = 77;
        EM.feedback = {{
          rating: 4.5,
          reviews: {{ count: 12 }}
        }};
        EM.product = {{
          family: {json.dumps(family)}
        }};
      </script>
    </body></html>
    """

    product = parse_product_page(
        html, "https://www.emag.ro/phone/pd/PNK1/#reviews"
    )

    assert product.emag_family_id == 77
    assert product.family_name == "Test Phone"
    assert product.description == "A useful description."
    assert str(product.aggregate_rating) == "4.5"
    assert product.review_count == 12
    assert str(product.url) == "https://www.emag.ro/phone/pd/PNK1/"
    assert len(product.variants) == 3
    assert {
        variant.pnk: (variant.storage, variant.color)
        for variant in product.variants
    } == {
        "PNK1": ("128 GB", "Black"),
        "PNK2": ("256 GB", "Black"),
        "PNK3": ("128 GB", "Blue"),
    }
