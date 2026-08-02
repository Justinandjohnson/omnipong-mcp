# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=2,<3", "scrapling[fetchers]>=0.4.12,<0.5"]
# ///
"""Omnipong MCP server — serves omnipong.com tournament data to agents.

stdio (default):   uv run server.py
shared HTTP API:   uv run server.py --http      -> http://<host>:8722/mcp
daily snapshot:    uv run server.py --refresh   (run from cron; re-scrapes everything)
"""
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from scrapling.fetchers import Fetcher
from scrapling.parser import Selector

BASE = "https://www.omnipong.com/"
TTL = 900  # on-demand freshness; --refresh cron guarantees a full daily snapshot
DB = Path(__file__).with_name("cache.db")
FORCE = "--refresh" in sys.argv

mcp = MCPServer("omnipong")


def _db() -> sqlite3.Connection:
    try:
        con = sqlite3.connect(DB, timeout=30)
        con.execute("CREATE TABLE IF NOT EXISTS pages (key TEXT PRIMARY KEY, ts REAL, body TEXT)")
    except sqlite3.OperationalError as e:
        raise RuntimeError(f"cannot open cache database {DB} ({e}) — "
                           f"the install directory must be writable") from e
    return con


def _fetch(path: str, data: dict | None = None) -> Selector:
    key = path + ("|" + json.dumps(data, sort_keys=True) if data else "")
    con = _db()
    row = con.execute("SELECT ts, body FROM pages WHERE key=?", (key,)).fetchone()
    if row and not FORCE and time.time() - row[0] < TTL:
        con.close()
        return Selector(row[1])
    page = Fetcher.post(BASE + path, data=data) if data else Fetcher.get(BASE + path)
    if page.status != 200:
        raise RuntimeError(f"omnipong returned HTTP {page.status} for {path}")
    con.execute("INSERT OR REPLACE INTO pages VALUES (?,?,?)", (key, time.time(), page.html_content))
    con.commit()
    con.close()
    return page


def _text(el) -> str:
    return " ".join(el.get_all_text(strip=True).split())


def _parse_tables(page) -> dict:
    """Generic: page title + every class="omnipong" table as rows of dicts."""
    h3 = page.css("h3")
    tables = []
    for table in page.css("table.omnipong"):
        rows = table.css("tr")
        if not rows:
            continue
        headers = [_text(c) for c in rows[0].css("th, td")]
        tables.append([
            dict(zip(headers, [_text(td) for td in tr.css("td")]))
            for tr in rows[1:]
        ])
    # the site states emptiness in a second h3 ("There are no players to list at this time")
    note = _text(h3[1]) if len(h3) > 1 else ""
    return {"title": _text(h3[0]) if h3 else "", "note": note, "tables": tables}


def _check_id(tournament_id: int) -> int:
    """Reject junk ids before they become requests to omnipong."""
    if not isinstance(tournament_id, int) or tournament_id <= 0:
        raise ValueError(f"tournament_id must be a positive integer, got {tournament_id!r}")
    return tournament_id


def _tournament_page(t: int, tournament_id: int) -> dict:
    return _parse_tables(_fetch(f"T-tourney.asp?t={t}&r={_check_id(tournament_id)}"))


@mcp.tool()
def list_tournaments(state: str = "", keyword: str = "", year: str = "",
                     event_type: str = "tournaments") -> list[dict]:
    """List events from omnipong.com.

    event_type: tournaments | leagues | camps | international. Leagues and camps
      have no ball/USATT-level columns, so those fields come back empty.
    state: filter by section header, e.g. "California" (substring, case-insensitive).
    keyword: substring match on event name.
    year: "" = current listings; or "2025", "2026", "TWeek" (this week), "LWeek", "NWeek", "All".
    Returned tournament_id works with the other tools. status is one of
    Enter (open entry) / Closed / Results (finished) / Info / Draws.
    """
    types = {"tournaments": 0, "leagues": 1, "camps": 2, "international": 3}
    if event_type not in types:
        raise ValueError(f"event_type must be one of {sorted(types)}, got {event_type!r}")
    e = types[event_type]
    if year:
        page = _fetch(f"T-tourney.asp?t=9&e={e}", data={"Year": year, "Keyword": keyword})
    else:
        page = _fetch(f"t-tourney.asp?e={e}")
    out = []
    section = ""
    # lxml auto-closes <h4> before its block <p>, so section names are the p[align] elements
    for el in page.css("p[align], table.omnipong"):
        if el.tag == "p":
            section = _text(el)
            continue
        for tr in el.css("tr")[1:]:
            tds = tr.css("td")
            if len(tds) < 6:  # leagues/camps are 6 columns, tournaments 8
                continue
            onclicks = " ".join(i.attrib.get("onclick", "") for td in tds[:2] for i in td.css("input"))
            m = re.search(r"[?&][rh]=(\d+)", onclicks)
            status = tds[0].css("input")
            pdf = tds[2].css("a[href*='EntryForms']")
            t = {
                "tournament_id": int(m.group(1)) if m else None,
                "status": status[0].attrib.get("value", "") if status else "",
                "name": _text(tds[2]),
                "city": _text(tds[3]),
                "date": _text(tds[4]),
                "contact": _text(tds[5]),
                "ball": _text(tds[6]) if len(tds) > 7 else "",
                "usatt_level": _text(tds[7]) if len(tds) > 7 else "",
                "state_section": section,
                "entry_form_pdf": BASE + pdf[0].attrib["href"] if pdf else None,
            }
            if state and state.lower() not in section.lower():
                continue
            if keyword and not year and keyword.lower() not in t["name"].lower():
                continue
            out.append(t)
    return out


@mcp.tool()
def get_results(tournament_id: int) -> dict:
    """Match results for a completed tournament (status=Results)."""
    return _tournament_page(103, tournament_id)


@mcp.tool()
def get_tournament_info(tournament_id: int) -> dict:
    """Entry/info page for a tournament: official name, date, entry status, entry-form PDF.

    This page is prose (h2/h3), not a table, so details is a list of lines such as
    "Being held on 09/05/2026" and "This tournament is not currently accepting entries".
    """
    page = _fetch(f"T-tourney.asp?t=108&r={tournament_id}")
    h2 = page.css("h2")
    pdf = page.css("a[href*='EntryForms']")
    return {
        "title": _text(page.css("h3")[0]) if page.css("h3") else "",
        "tournament_name": _text(h2[0]) if h2 else "",
        "details": [_text(h) for h in page.css("h3")[1:] if _text(h)],
        "entry_form_pdf": BASE + pdf[0].attrib["href"] if pdf else None,
    }


ALL_TYPES = ("tournaments", "leagues", "camps", "international")


def _record_seen(events: list[dict], event_type: str, seeding: bool) -> list[dict]:
    """Log first-seen date per event. Returns the events that were new this run."""
    from datetime import date
    stamp = "seed" if seeding else date.today().isoformat()
    con = _db()
    con.execute("""CREATE TABLE IF NOT EXISTS seen (
        event_id INT, event_type TEXT, first_seen TEXT, name TEXT, city TEXT,
        date TEXT, status TEXT, state_section TEXT, entry_form_pdf TEXT,
        PRIMARY KEY (event_id, event_type))""")
    new = []
    for e in events:
        if not e["tournament_id"]:
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?,?,?,?,?)",
            (e["tournament_id"], event_type, stamp, e["name"], e["city"],
             e["date"], e["status"], e["state_section"], e["entry_form_pdf"]))
        if cur.rowcount and not seeding:
            new.append(e)
    con.commit()
    con.close()
    return new


def refresh() -> None:
    """Full re-scrape: every listing + results/info per event. Never touches player pages."""
    limit = int(os.environ.get("OMNIPONG_REFRESH_LIMIT", "0"))  # for smoke tests
    con = _db()
    seeding = not con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='seen'").fetchone()
    con.close()
    pages, new_total = 0, []
    for et in ALL_TYPES:
        events = [e for e in list_tournaments(event_type=et) if e["tournament_id"]]
        pages += 1
        if limit:
            # test runs must never write a partial baseline — it would flag every
            # unrecorded real event as "new" on the next full run
            events = events[:limit]
        else:
            new_total += [dict(e, event_type=et) for e in _record_seen(events, et, seeding)]
        for e in events:
            if e["status"] == "Results":
                get_results(e["tournament_id"])
            else:
                get_tournament_info(e["tournament_id"])
            pages += 1
            time.sleep(0.2)  # be polite
    note = "seeded baseline (nothing flagged new)" if seeding else f"{len(new_total)} new events"
    print(f"refreshed {pages} pages -> {DB} | {note}")
    for e in new_total:
        print(f"  NEW [{e['event_type']}] {e['name'][:55]} | {e['city']} | {e['date']} | {e['status']}")


@mcp.tool()
def whats_new(days: int = 7, open_only: bool = True, state: str = "", event_type: str = "") -> list[dict]:
    """Events that first appeared on omnipong within the last N days — the signup radar.

    open_only: only events currently accepting entries (status Enter).
    state: filter by section, e.g. "California". event_type: tournaments|leagues|camps.
    Populated by the daily refresh; the first run seeds a baseline and flags nothing.
    """
    from datetime import date, timedelta
    days = max(0, min(int(days), 36500))  # ponytail: 100y is past every real listing
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    q = ("SELECT event_id, event_type, first_seen, name, city, date, status, "
         "state_section, entry_form_pdf FROM seen WHERE first_seen != 'seed' AND first_seen >= ?")
    args: list = [cutoff]
    if open_only:
        q += " AND status = 'Enter'"
    if state:
        q += " AND LOWER(state_section) LIKE ?"
        args.append(f"%{state.lower()}%")
    if event_type:
        q += " AND event_type = ?"
        args.append(event_type)
    con = _db()
    try:
        rows = con.execute(q + " ORDER BY first_seen DESC, date", args).fetchall()
    except sqlite3.OperationalError:
        return []  # no refresh has run yet
    finally:
        con.close()
    cols = ["tournament_id", "event_type", "first_seen", "name", "city", "date",
            "status", "state_section", "entry_form_pdf"]
    return [dict(zip(cols, r)) for r in rows]


def export_batch() -> Path:
    """Write batches/<date>.json: every event with its results/info. No player data."""
    from datetime import date
    everything = {}
    for et in ALL_TYPES:
        events = [e for e in list_tournaments(event_type=et) if e["tournament_id"]]
        for e in events:
            if e["status"] == "Results":
                e["results"] = get_results(e["tournament_id"])
            else:
                e["info"] = get_tournament_info(e["tournament_id"])
        everything[et] = events
    out = Path(__file__).with_name("batches") / f"{date.today().isoformat()}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(
        {"scraped": date.today().isoformat(),
         "counts": {k: len(v) for k, v in everything.items()}, **everything}, indent=1))
    print(f"wrote {out} ({ {k: len(v) for k, v in everything.items()} })")
    return out


if __name__ == "__main__":
    if FORCE:
        refresh()
        FORCE = False  # export reads the snapshot we just wrote, no refetching
        export_batch()
    elif "--export" in sys.argv:
        export_batch()
    elif "--http" in sys.argv:
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8722)
    else:
        mcp.run()
