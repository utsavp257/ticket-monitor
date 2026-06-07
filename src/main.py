from monitor_amc import check_amc
from monitor_fandango import check_fandango
from monitor_imax import check_imax

from telegram import send_message
from state import already_seen, mark_seen


def process(results):

    for item in results:

        key = (
            f"{item['source']}"
            f"_{item['movie']}"
        )

        if already_seen(key):
            continue

        send_message(
            f"""
🎬 Ticket Alert

Movie:
{item['movie']}

Source:
{item['source']}

URL:
{item['url']}
"""
        )

        mark_seen(key)


def main():

    process(check_amc())

    process(check_fandango())

    process(check_imax())


if __name__ == "__main__":
    main()
