from project_dm.scraping.guards import visible_page_is_blocked


def test_detects_visible_challenge() -> None:
    assert visible_page_is_blocked("Please verify that you're not a robot")


def test_ignores_normal_page_text() -> None:
    assert not visible_page_is_blocked(
        "Telefon mobil Apple iPhone. Livrare rapidă și recenzii."
    )
