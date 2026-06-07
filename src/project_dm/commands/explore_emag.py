from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

from playwright.sync_api import Page, Response, TimeoutError, sync_playwright
from selectolax.parser import HTMLParser


LISTING_URL = (
    "https://www.emag.ro/telefoane-mobile/brand/apple/"
    "c?ref=banner-widget_1_2"
)
BLOCK_MARKERS = (
    "verify that you're not a robot",
    "verifică dacă ești robot",
    "captcha",
    "access denied",
    "too many requests",
)
INTERESTING_RESPONSE_MARKERS = (
    "review",
    "rating",
    "product",
    "offer",
    "variant",
    "recommend",
)


def slug(value: str) -> str:
    value = re.sub(r"^https?://", "", value)
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("_")[:120]


def is_product_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.endswith("emag.ro") and bool(
        re.search(r"/[^/]+/pd/[A-Z0-9]+/?", parsed.path, re.IGNORECASE)
    )


def page_summary(page: Page) -> dict:
    html = page.content()
    tree = HTMLParser(html)
    try:
        visible_text = page.locator("body").inner_text(timeout=5_000)
    except TimeoutError:
        visible_text = ""
    text = re.sub(r"\s+", " ", visible_text).strip()
    links: list[dict[str, str]] = []
    seen: set[str] = set()

    for node in tree.css("a[href]"):
        href = urljoin(page.url, node.attributes["href"])
        if is_product_url(href):
            canonical = href.split("?")[0]
            if canonical not in seen:
                seen.add(canonical)
                links.append(
                    {
                        "url": canonical,
                        "text": node.text(separator=" ", strip=True)[:300],
                    }
                )

    json_ld: list[object] = []
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            json_ld.append(json.loads(node.text()))
        except (json.JSONDecodeError, TypeError):
            json_ld.append({"unparsed": node.text()[:2_000]})

    lowered = text.lower()
    return {
        "url": page.url,
        "title": page.title(),
        "body_text_preview": text[:4_000],
        "blocked": any(marker in lowered for marker in BLOCK_MARKERS),
        "product_links": links,
        "json_ld": json_ld,
        "selector_counts": {
            "product_cards": len(tree.css(".card-item, .js-product-data")),
            "reviews": len(
                tree.css(
                    ".review, .product-review, [data-review-id], "
                    ".js-review-item"
                )
            ),
            "verified_icons": len(tree.css(".em-verified.text-success")),
            "rating_elements": len(
                tree.css(
                    "[class*=rating], [itemprop=ratingValue], "
                    "[data-rating]"
                )
            ),
            "price_elements": len(
                tree.css(
                    ".product-new-price, [itemprop=price], "
                    "[data-price]"
                )
            ),
        },
    }


def save_page(page: Page, output_dir: Path, name: str) -> dict:
    summary = page_summary(page)
    (output_dir / f"{name}.html").write_text(
        page.content(), encoding="utf-8"
    )
    (output_dir / f"{name}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    page.screenshot(
        path=output_dir / f"{name}.png",
        full_page=True,
    )
    return summary


def trigger_review_probes(page: Page) -> None:
    match = re.search(
        r"https://www\.emag\.ro/(?P<sef>[^/]+)/pd/(?P<pnk>[A-Z0-9]+)/?",
        page.url,
        re.IGNORECASE,
    )
    if not match:
        return

    base = (
        f"https://www.emag.ro/product-feedback/{match['sef']}/pd/"
        f"{match['pnk']}/reviews/list"
    )
    for offset in (0, 10):
        query = urlencode(
            {
                "source_id": 7,
                "page[offset]": offset,
                "page[limit]": 10,
                "sort[created]": "desc",
                "sefName": match["sef"],
                "pnk": match["pnk"],
            }
        )
        page.evaluate(
            """async url => {
                const response = await fetch(url, {
                    credentials: "same-origin",
                    headers: {"Accept": "application/json"}
                });
                await response.text();
                return response.status;
            }""",
            f"{base}?{query}",
        )
        page.wait_for_timeout(1_000)


def explore(url: str, output_dir: Path, headed: bool) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    responses: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        context = browser.new_context(
            locale="ro-RO",
            timezone_id="Europe/Bucharest",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()

        def observe(response: Response) -> None:
            lowered = response.url.lower()
            content_type = response.headers.get("content-type", "")
            if not any(
                marker in lowered for marker in INTERESTING_RESPONSE_MARKERS
            ):
                return

            record: dict[str, object] = {
                "url": response.url,
                "status": response.status,
                "method": response.request.method,
                "resource_type": response.request.resource_type,
                "content_type": content_type,
            }
            if "json" in content_type and response.status == 200:
                try:
                    body = response.body()
                    if len(body) <= 2_000_000:
                        filename = (
                            f"response_{len(responses):04d}_"
                            f"{slug(response.url)}.json"
                        )
                        (output_dir / filename).write_bytes(body)
                        record["saved_body"] = filename
                    else:
                        record["body_bytes"] = len(body)
                except Exception as exc:
                    record["body_error"] = type(exc).__name__
            responses.append(record)

        page.on("response", observe)

        try:
            navigation = page.goto(
                url, wait_until="domcontentloaded", timeout=60_000
            )
            page.wait_for_timeout(5_000)
        except TimeoutError:
            navigation = None

        listing = save_page(page, output_dir, "listing")
        status = navigation.status if navigation else None

        if listing["blocked"] or status in {403, 429}:
            result = {
                "stopped": "anti-bot or rate-limit response",
                "initial_status": status,
                "listing": listing,
                "responses": responses,
            }
            (output_dir / "report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            browser.close()
            return 2

        product_links = listing["product_links"]
        if product_links:
            product_url = product_links[0]["url"]
            try:
                navigation = page.goto(
                    product_url,
                    wait_until="domcontentloaded",
                    timeout=60_000,
                )
                page.wait_for_timeout(4_000)
                page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
                page.wait_for_timeout(3_000)
                trigger_review_probes(page)
            except TimeoutError:
                navigation = None

            product = save_page(page, output_dir, "product")
            product_status = navigation.status if navigation else None
        else:
            product = None
            product_status = None

        result = {
            "initial_status": status,
            "product_status": product_status,
            "listing": listing,
            "product": product,
            "responses": responses,
        }
        (output_dir / "report.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        browser.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explore eMAG pages without collecting a dataset."
    )
    parser.add_argument("--url", default=LISTING_URL)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data")
        / "exploration"
        / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Open a visible browser window.",
    )
    args = parser.parse_args()
    raise SystemExit(explore(args.url, args.output, args.headed))


if __name__ == "__main__":
    main()
