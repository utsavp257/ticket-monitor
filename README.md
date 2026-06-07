# Lincoln Square IMAX ticket monitor

Watches AMC Lincoln Square 13 (the "Lincoln Square IMAX"), Fandango, and
imax.com for **Dune 3** and **Odyssey**, and pings you on Telegram when the
**first show on the upcoming Tuesday and Wednesday** is listed.

## What it actually does

For each upcoming Tuesday and Wednesday it loads that theater's *date-specific*
showtimes page, looks for real showtimes (a movie title tied to a clock time on
that date), and reports the **earliest** one. It only alerts when an actual
showtime is found — not merely when the movie's name appears on a page.

> **Note on sources:** AMC Lincoln Square 13 *is* the Lincoln Square IMAX and is
> what actually sells the tickets. Fandango resells the same AMC showtimes
> (cross-check / fallback). imax.com doesn't sell tickets and isn't reliably
> date-filterable, so treat its alerts as "the film is coming", not "tickets are
> live". AMC is the one to trust.

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium   # one-time browser download
```

### Telegram credentials

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts, and
   copy the **bot token** it gives you.
2. Send your new bot any message (it can't message you until you start it).
3. Get your chat ID: open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and copy
   the `chat.id` value.
4. Export them:

   ```bash
   export TELEGRAM_TOKEN="123456:ABC..."
   export TELEGRAM_CHAT_ID="987654321"
   ```

For the GitHub Action, add the same two as repo secrets
(Settings → Secrets and variables → Actions): `TELEGRAM_TOKEN`,
`TELEGRAM_CHAT_ID`.

## How to check it's working (do these in order)

```bash
# 1. Which dates will it check? Should print the next Tue and Wed.
python src/main.py --dates

# 2. Does Telegram work? Should land a message in your chat.
python src/main.py --test-telegram

# 3. Does scraping/detection work, without sending anything?
#    Prints any matches it would alert on.
python src/main.py --dry-run

# 4. Inspect what the sites actually returned (writes debug_*.html):
python src/main.py --dry-run --debug
```

If `--dry-run` finds nothing, that's expected until showtimes are posted — the
useful check is step 4: open the `debug_*.html` files and confirm you're seeing
the real showtimes page (and not a "Verify you are human" / Cloudflare
challenge). A real run with alerts:

```bash
python src/main.py
```

## Real-world caveats (please read)

- **Bot protection.** AMC and Fandango sit behind Cloudflare. Headless Chromium
  is sometimes served a challenge page instead of showtimes; the scraper detects
  this and prints a warning. If it happens consistently, see *Tuning* below.
- **Selectors/URLs can drift.** The site URLs and the showtime extraction logic
  live in `src/config.py` and `src/scrape.py`. If a site changes its layout,
  the `--debug` HTML dump is how you (or I) refine them.
- **Titles aren't confirmed yet.** "Dune 3" / "Odyssey" will be listed under
  official titles ("Dune: Part Three", "The Odyssey", etc.). Edit the alias
  lists in `src/config.py` once the real titles are known.
- **This monitors; it does not buy.** It tells you the moment the first show is
  live so you can book it yourself. Auto-purchasing requires logging into your
  account and handling payment/CAPTCHA, which is a different (and riskier)
  project — say the word if you want to explore it.

## Tuning

In `src/config.py`: movie aliases, target weekdays, how many weeks ahead, the
per-source URLs, page timeout, and retries. If you hit persistent bot blocks,
options are increasing `SETTLE_MS`, running non-headless locally, or routing
through a residential proxy / a paid showtimes API.

## Files

| File | Purpose |
|------|---------|
| `src/config.py` | Movies, dates, URLs, scraper knobs |
| `src/dates.py` | Computes upcoming Tue/Wed |
| `src/scrape.py` | Fetch + showtime extraction (the core) |
| `src/monitor_*.py` | Per-source wrappers (AMC / Fandango / IMAX) |
| `src/telegram.py` | Notifications |
| `src/state.py` | Dedup so you aren't spammed |
| `src/main.py` | Entry point + CLI flags |
| `.github/workflows/monitor.yml` | Runs it every 30 min |
