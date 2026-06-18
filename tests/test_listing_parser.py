from project_dm.scraping.listing import parse_listing_page


HTML = """
<html>
  <head>
    <link rel="next" href="/telefoane-mobile/brand/apple/p2/c">
  </head>
  <body>
    <div
      class="card-item js-product-data"
      data-product-id="102971593"
      data-offer-id="273691815"
      data-name="Telefon mobil Apple iPhone"
      data-url="/telefon-mobil-apple-iphone/pd/DN99FV3BM/?ref=listing"
    >
      <button data-family-id="6219301"></button>
    </div>
    <div
      class="card-item js-product-data"
      data-product-id="102971593"
      data-name="Duplicate link"
      data-url="/telefon-mobil-apple-iphone/pd/DN99FV3BM/"
    ></div>
    <a href="/unrelated/pd/NOTACARD/">Unrelated recommendation</a>
  </body>
</html>
"""


def test_parse_listing_products_and_next_page() -> None:
    result = parse_listing_page(
        HTML,
        "https://www.emag.ro/telefoane-mobile/brand/apple/c",
    )

    assert len(result.products) == 1
    product = result.products[0]
    assert product.pnk == "DN99FV3BM"
    assert product.emag_product_id == 102971593
    assert product.offer_id == 273691815
    assert product.family_id == 6219301
    assert str(product.url) == (
        "https://www.emag.ro/telefon-mobil-apple-iphone/pd/DN99FV3BM/"
    )
    assert str(result.next_url) == (
        "https://www.emag.ro/telefoane-mobile/brand/apple/p2/c"
    )


def test_parse_last_or_empty_listing() -> None:
    result = parse_listing_page(
        "<html><body></body></html>",
        "https://www.emag.ro/telefoane-mobile/brand/apple/p9/c",
    )

    assert result.products == []
    assert result.next_url is None
