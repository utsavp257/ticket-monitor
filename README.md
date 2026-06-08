# Lincoln Square IMAX ticket monitor

Watches **AMC Lincoln Square 13** (the "Lincoln Square IMAX") for
**Dune: Part Three** and **The Odyssey**, and pings you on Telegram when their
**IMAX** **Tuesday/Wednesday** showtimes change — a **new showtime is added** or
a **sold-out show frees up**. Only IMAX-format shows count (set `IMAX_ONLY` in
`src/config.py` to `False` to include Dolby/Laser/standard too).

## What it actually does

Each movie has a `from_date` in `src/config.py` (around its release). The
monitor checks the first couple of Tuesdays/Wednesdays on/after that date, reads
the real showtimes for each, and records each showtime's **sold-out status**. On
every run it diffs against what it saw last time and alerts only on *changes*:

- 🆕 **New showtime(s)** — a time that wasn't listed before (this also covers
  the very first time a date goes on sale).
- 🎟️ **Seats opened up** — a showtime that was *sold out* now has availability.

It never re-alerts on shows you've already been told about, and it only ever
fires on a real showtime tied to the movie — never on a stray title mention
(e.g. a "The Odyssey Bundle" promo).

It also watches the Instagram accounts in `INSTAGRAM_ACCOUNTS` (e.g.
`@dunemovie`) and pings you on **new posts** — these studios often post when
tickets go live. The first run records existing posts silently (baseline), then
alerts only on new ones. Heads-up: Instagram blocks datacenter IPs hard, so this
check is best-effort from GitHub Actions and may intermittently fail (it's
isolated, so it never affects the AMC check).

> **Why only AMC?** AMC Lincoln Square 13 *is* the Lincoln Square IMAX and is
> where you actually book — validated against live showtimes. Fandango and
> imax.com were tried and dropped: Fandango renders showtimes in an interactive
> widget the scraper can't read (and is redundant with AMC), and imax.com is
> Cloudflare bot-blocked and doesn't sell tickets. AMC fully covers the goal.

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

### Instagram (Apify) — optional but needed for IG alerts on CI

Instagram blocks GitHub's IPs, so IG alerts on the Action go through Apify:

1. Create a free account at <https://console.apify.com>.
2. Settings → Integrations → copy your **API token**.
3. Add it as a repo secret named `APIFY_TOKEN`.

Without `APIFY_TOKEN` the IG check falls back to the free direct endpoint (works
locally, usually blocked on CI). The check runs at most every
`IG_CHECK_EVERY_HOURS` hours to stay within Apify's free credits.

## How to check it's working (do these in order)

```bash
# 1. Which dates does each movie watch? (from its from_date in config.py)
python src/main.py --dates

# 2. Does Telegram work? Should land a message in your chat.
python src/main.py --test-telegram

# 3. What changes would it alert on right now, without sending anything?
python src/main.py --dry-run

# 4. Inspect what AMC actually returned (writes debug_*.html):
python src/main.py --dry-run --debug

# 5. SEE THE WHOLE THING WORK NOW, end to end, including a real Telegram
#    message — using a movie that's currently on sale as a stand-in:
python src/main.py --probe "Masters of the Universe"
#    (add --dry-run to print the alert instead of sending it)
```

You can also run the probe from **Actions → Ticket Monitor → Run workflow** by
filling in the *probe_movie* box — handy since the Telegram secrets live there.

If `--dry-run` finds nothing, that's expected until showtimes are posted — the
useful check is step 4: open the `debug_*.html` files and confirm you're seeing
the real showtimes page (and not a "Verify you are human" / Cloudflare
challenge). A real run with alerts:

```bash
python src/main.py
```

## Real-world caveats (please read)

- **Bot protection / datacenter IPs.** AMC sits behind anti-bot protection. It
  loads reliably from a residential IP, but from GitHub Actions' datacenter IPs
  it *intermittently* times out. The fetch retries with backoff, and since the
  job runs every 30 min and a new showtime listing stays up for days, an
  occasional missed cycle self-heals. For rock-solid timing, run it on your own
  machine (cron/launchd) instead — see *Tuning*.
- **Selectors/URLs can drift.** The AMC URL and the showtime extraction logic
  live in `src/config.py` and `src/scrape.py`. If AMC changes its layout, the
  `--debug` HTML dump is how you (or I) refine them.
- **Each movie's `from_date` matters.** It tells the monitor where to start
  looking (verified against AMC: The Odyssey ~Jul 21 2026, Dune: Part Three
  opens Dec 18 2026 — its Tue/Wed go on sale closer to release). If a release
  shifts, just edit `from_date` in `src/config.py`.
- **This monitors; it does not buy.** It tells you when showtimes change so you
  can book yourself. Auto-purchasing requires logging into your account and
  handling payment/CAPTCHA — a different (and riskier) project; say the word.

## Tuning

In `src/config.py`: per-movie aliases + `from_date`, `WATCH_WEEKS` (how many
Tue/Wed per movie), target weekdays, the AMC URL, page timeout, and retries. If
the runner keeps timing out, the most reliable fix is to run the monitor on your
own machine (residential IP) on a cron/launchd timer instead of GitHub Actions.

## Files

| File | Purpose |
|------|---------|
| `src/config.py` | Movies + from_dates, watch window, AMC URL, scraper knobs |
| `src/dates.py` | Per-movie Tue/Wed watch dates |
| `src/scrape.py` | Fetch + showtime/sold-out extraction (the core) |
| `src/monitor_amc.py` | AMC Lincoln Square source |
| `src/telegram.py` | Notifications |
| `src/state.py` | Remembers seen showtimes so you only get changes |
| `src/main.py` | Entry point, change-diffing, CLI flags |
| `.github/workflows/monitor.yml` | Runs it every 30 min |
