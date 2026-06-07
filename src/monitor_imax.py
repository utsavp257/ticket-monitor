from playwright.sync_api import sync_playwright

URL = "https://www.imax.com/movies"


def check_imax():

    results = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        page.goto(
            URL,
            wait_until="networkidle",
            timeout=60000
        )

        html = page.content()

        browser.close()

    targets = [
        "Odyssey",
        "The Odyssey",
        "Dune",
        "Dune Messiah"
    ]

    for movie in targets:
        if movie.lower() in html.lower():
            results.append({
                "source": "IMAX",
                "movie": movie,
                "url": URL
            })

    return results
