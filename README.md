# authenticate-ics

Turns the [Authenticate conference](https://authenticatecon.com/) agenda page into an
`.ics` file you can import into Google Calendar, Outlook or Apple Calendar.

Generated calendars live in [`calendars/<year>/`](calendars/).

## Requirements

[uv](https://docs.astral.sh/uv/). The script declares its own dependencies
(`beautifulsoup4`, `requests`), so `uv run` installs them the first time.

## Usage

```bash
# Full agenda for the default year -> calendars/2026/authenticate-2026.ics
uv run authenticate_ics.py

# A future year (assumes the URL pattern authenticate-u-s-<year> stays the same)
uv run authenticate_ics.py --year 2027

# If the URL or venue changes
uv run authenticate_ics.py --year 2027 --url https://authenticatecon.com/event/<slug>/ \
  --venue "Some Hotel, City, ST"

# Preview in the terminal without writing a file
uv run authenticate_ics.py --list --no-breaks

# Personal subset
uv run authenticate_ics.py --no-breaks --search passkey -o calendars/2026/passkeys.ics
uv run authenticate_ics.py --day 2026-10-20 --track "Non-human"
```

Filters can be repeated and combined: `--day`, `--track`, `--room`, `--search`
(title, abstract or speaker) and `--no-breaks`. `--html saved.html` parses a saved
copy of the page instead of fetching it.

## What each event contains

- Start/end in `America/Los_Angeles`, with an embedded `VTIMEZONE`
- `LOCATION`: room plus venue address
- `DESCRIPTION`: track, speakers (with company), full abstract, link to the agenda
- `CATEGORIES`: the track
- Meals and breaks are marked free (`TRANSP:TRANSPARENT`)
- Stable `UID`s derived from the site's session IDs, so re-importing an updated file
  updates existing events rather than duplicating them

## Each new year

1. Run `uv run authenticate_ics.py --year <year> --list` to check the page parses.
2. If it 404s, find the new agenda URL and pass `--url`.
3. If the venue moved, pass `--venue` (and update `DEFAULT_VENUE`/`DEFAULT_YEAR` in the script).
4. If the time zone isn't Pacific, update `TZID`/`VTIMEZONE` in the script.
5. If it reports "No sessions found", the page markup changed: the parser looks for
   `div.agenda__day[data-date]` and `.agenda__mobile-list .item--session[data-session-id]`.
6. Commit the new `calendars/<year>/` files.
