#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["beautifulsoup4>=4.12", "requests>=2.31"]
# ///
"""
Generate an .ics calendar from the Authenticate conference agenda page.

    uv run authenticate_ics.py                          # full agenda -> calendars/<year>/authenticate-<year>.ics
    uv run authenticate_ics.py --year 2027              # a future year (URL pattern assumed unchanged)
    uv run authenticate_ics.py --url https://...        # explicit agenda URL if the pattern changes
    uv run authenticate_ics.py --no-breaks              # skip meals/breaks/registration
    uv run authenticate_ics.py --day 2026-10-20 --day 2026-10-21
    uv run authenticate_ics.py --track "Agentic"        # substring match on track
    uv run authenticate_ics.py --search passkey         # substring match on title/description/speaker
    uv run authenticate_ics.py --list                   # print agenda, write nothing
    uv run authenticate_ics.py --html saved_page.html   # parse a saved copy instead of fetching

Each event's UID is derived from the site's session id, so re-importing an
updated file updates existing events instead of creating duplicates.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DEFAULT_YEAR = 2026
URL_TEMPLATE = "https://authenticatecon.com/event/authenticate-u-s-{year}/"
TZID = "America/Los_Angeles"
DEFAULT_VENUE = "Omni La Costa Resort & Spa, 2100 Costa Del Mar Rd, Carlsbad, CA 92009"

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


@dataclass
class Session:
    id: str
    date: str  # YYYY-MM-DD
    start: str  # "9:00 am"
    end: str
    title: str
    room: str = ""
    track: str = ""
    description: str = ""
    speakers: list[str] = field(default_factory=list)
    is_break: bool = False

    def dt(self, t: str) -> datetime:
        return datetime.strptime(f"{self.date} {t.strip().upper()}", "%Y-%m-%d %I:%M %p")


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (agenda-to-ics)"}, timeout=60)
    r.raise_for_status()
    return r.text


def html_to_text(fragment: str) -> str:
    if not fragment:
        return ""
    soup = BeautifulSoup(fragment, "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for blk in soup.find_all(["p", "div", "ul", "ol"]):
        blk.insert_after("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse(html: str) -> list[Session]:
    soup = BeautifulSoup(html, "html.parser")
    sessions: dict[str, Session] = {}
    # The page renders each day twice (desktop grid + mobile list); the mobile
    # list cards also carry the room, so parse those and dedupe by session id.
    for day in soup.select("div.agenda__day[data-date]"):
        date = day["data-date"]
        for card in day.select(".agenda__mobile-list .item--session[data-session-id]"):
            sid = card["data-session-id"]
            if sid in sessions:
                continue
            title_el = card.select_one(".item__title")
            room_el = card.select_one(".mobile-list__venue")
            speakers = []
            for sp in card.select(".item--speaker"):
                name = sp.select_one(".item__speaker-name")
                co = sp.select_one(".item__speaker-company")
                if name:
                    n = name.get_text(strip=True)
                    c = co.get_text(strip=True) if co else ""
                    speakers.append(f"{n} ({c})" if c else n)
            sessions[sid] = Session(
                id=sid,
                date=date,
                start=card.get("data-start-time", ""),
                end=card.get("data-end-time", ""),
                title=title_el.get_text(" ", strip=True) if title_el else "(untitled)",
                room=room_el.get_text(strip=True) if room_el else "",
                track=card.get("data-tag", "") or "",
                description=html_to_text(card.get("data-description", "")),
                speakers=speakers,
                is_break="item--break" in card.get("class", []),
            )
    return sorted(sessions.values(), key=lambda s: (s.date, s.dt(s.start), s.room, s.title))


# ---------- iCalendar output ----------

def esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def fold(line: str) -> str:
    """Fold to 75 octets per RFC 5545 without splitting UTF-8 sequences."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 75:
            out.append(cur)
            cur, size = " ", 1
        cur += ch
        size += n
    out.append(cur)
    return "\r\n".join(out)


def to_ics(sessions: list[Session], source_url: str, cal_name: str, venue: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//authenticate-ics//agenda scraper//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(cal_name)}",
        f"X-WR-TIMEZONE:{TZID}",
        *VTIMEZONE.splitlines(),
    ]
    for s in sessions:
        desc_parts = []
        if s.track:
            desc_parts.append(f"Track: {s.track}")
        if s.speakers:
            desc_parts.append("Speakers: " + "; ".join(s.speakers))
        if s.description:
            desc_parts.append(s.description)
        desc_parts.append(f"Agenda: {source_url}")
        location = f"{s.room}, {venue}" if s.room else venue
        uid = hashlib.sha1(s.id.encode()).hexdigest() + "@authenticatecon.com"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;TZID={TZID}:{s.dt(s.start):%Y%m%dT%H%M%S}",
            f"DTEND;TZID={TZID}:{s.dt(s.end):%Y%m%dT%H%M%S}",
            f"SUMMARY:{esc(s.title)}",
            f"LOCATION:{esc(location)}",
            f"DESCRIPTION:{esc(chr(10).join(desc_parts))}",
            f"URL:{source_url}",
            "TRANSP:TRANSPARENT" if s.is_break else "TRANSP:OPAQUE",
        ]
        if s.track:
            lines.append(f"CATEGORIES:{esc(s.track)}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


BREAK_TITLES = re.compile(r"\b(breakfast|lunch|break|registration|reception|happy hour)\b", re.I)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--year", type=int, default=DEFAULT_YEAR, help="conference year (default %(default)s)")
    ap.add_argument("--url", help="agenda page URL (default: derived from --year)")
    ap.add_argument("--venue", default=DEFAULT_VENUE, help="venue appended to each room in LOCATION")
    ap.add_argument("--html", type=Path, help="parse a saved HTML file instead of fetching")
    ap.add_argument("-o", "--output", type=Path, help="default: calendars/<year>/authenticate-<year>.ics")
    ap.add_argument("--day", action="append", help="YYYY-MM-DD; repeatable")
    ap.add_argument("--track", action="append", help="track substring; repeatable (OR)")
    ap.add_argument("--search", action="append", help="title/description/speaker substring; repeatable (OR)")
    ap.add_argument("--room", action="append", help="room substring; repeatable (OR)")
    ap.add_argument("--no-breaks", action="store_true", help="drop meals, breaks and registration")
    ap.add_argument("--name", help="calendar display name (default: Authenticate <year>)")
    ap.add_argument("--list", action="store_true", help="print the matching sessions and exit")
    a = ap.parse_args(argv)
    a.url = a.url or URL_TEMPLATE.format(year=a.year)
    a.name = a.name or f"Authenticate {a.year}"
    a.output = a.output or Path("calendars") / str(a.year) / f"authenticate-{a.year}.ics"

    try:
        html = a.html.read_text(encoding="utf-8") if a.html else fetch(a.url)
    except requests.HTTPError as e:
        print(f"Could not fetch {a.url}: {e}\n"
              "If the agenda URL pattern changed this year, pass it with --url.", file=sys.stderr)
        return 1
    sessions = parse(html)
    if not sessions:
        print("No sessions found - the page layout may have changed.", file=sys.stderr)
        return 1

    def any_in(needles, hay):
        return any(n.lower() in hay.lower() for n in needles)

    if a.day:
        sessions = [s for s in sessions if s.date in a.day]
    if a.track:
        sessions = [s for s in sessions if any_in(a.track, s.track)]
    if a.room:
        sessions = [s for s in sessions if any_in(a.room, s.room)]
    if a.search:
        sessions = [s for s in sessions
                    if any_in(a.search, " ".join([s.title, s.description, *s.speakers]))]
    if a.no_breaks:
        sessions = [s for s in sessions if not (s.is_break or BREAK_TITLES.search(s.title))]

    if a.list:
        last = None
        for s in sessions:
            if s.date != last:
                print(f"\n=== {s.dt(s.start):%A %b %d} ===")
                last = s.date
            print(f"{s.dt(s.start):%I:%M%p}-{s.dt(s.end):%I:%M%p}  {s.room or '-':<14} {s.title}")
        print(f"\n{len(sessions)} sessions")
        return 0

    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(to_ics(sessions, a.url, a.name, a.venue), encoding="utf-8", newline="")
    days = sorted({s.date for s in sessions})
    print(f"Wrote {len(sessions)} events across {len(days)} day(s) to {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
