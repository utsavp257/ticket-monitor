from playwright.sync_api import sync_playwright

AMC_URL = (
    "https://www.amctheatres.com/"
    "movie-theatres/new-york-city/"
    "amc-lincoln-square-13"
)


def check_amc():

    results = []

    with sync_playwright() as p:

        browser = p.chromium.launch(
            headless=True
        )

        page = browser.new_page()

        page.goto(
            AMC_URL,
            wait_until="networkidle",
            timeout=60000
        )

        html = page.content()

        browser.close()

    movies = [
        "Odyssey",
        "The Odyssey",
        "Dune",
        "Dune: Messiah",
        "Dune Messiah"
    ]

    for movie in movies:
        if movie.lower() in html.lower():
            results.append({
                "source": "AMC",
                "movie": movie,
                "url": AMC_URL
            })

    return results
