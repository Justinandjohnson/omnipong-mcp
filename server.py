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


_ID_RE = re.compile(r"[?&][rh]=(\d+)")


def _generic_ids(page) -> set[int]:
    """Every event id on the page, found WITHOUT the parser's own selectors.

    Deliberately independent of `table.omnipong` and `p[align]`: it sweeps every
    <tr> anywhere in the document. If this sees ids the parser didn't return, the
    selectors have drifted and the parser is silently dropping real events.
    """
    ids = set()
    for tr in page.css("tr"):
        m = _ID_RE.search(" ".join(i.attrib.get("onclick", "") for i in tr.css("input")))
        if m:
            ids.add(int(m.group(1)))
    return ids


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
    # Self-check against an independent read of the same page. An empty listing is
    # legitimate (International is empty today); silently DROPPING events that are
    # visibly on the page is not. Only meaningful unfiltered.
    if not (state or keyword or year):
        missed = _generic_ids(page) - {t["tournament_id"] for t in out}
        if missed:
            raise RuntimeError(
                f"parser dropped {len(missed)} of {len(_generic_ids(page))} {event_type} "
                f"visible on the page (ids {sorted(missed)[:5]}...) — omnipong's HTML "
                f"changed; the selectors in list_tournaments need re-deriving")
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


# Recorded from the live site 2026-08-01. Tournaments serves both spellings of the
# city column; either is fine, a third is a change worth looking at.
BASELINE_HEADERS = {
    "tournaments": {
        ("Action", "List", "Name - Click for entry Info", "City", "Date", "Contact",
         "Ball Info", "USATT Level"),
        ("Action", "List", "Name - Click for entry Info", "City, State", "Date", "Contact",
         "Ball Info", "USATT Level"),
    },
    "leagues": {("Action", "List", "Name - Click for entry Info", "City", "Date", "Contact")},
    "camps": {("Action", "List", "Name - Click for entry Info", "City", "Date", "Contact")},
    "international": set(),  # legitimately empty today; any header here is new
}
STATUSES = {"Enter", "Closed", "Results", "Info", "Draws"}
DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2}( - \d{2}/\d{2}/\d{2})?$")
_TYPE_PARAM = {"tournaments": 0, "leagues": 1, "camps": 2, "international": 3}


@mcp.tool()
def check_parser_health() -> dict:
    """Verify this server is still reading omnipong correctly. Run before trusting
    a surprising answer, and on a schedule.

    Scrapers fail quietly: if omnipong inserts a column, every fixed index shifts and
    the tools return confident nonsense with the row count unchanged. This compares the
    live header rows against a recorded baseline and validates the shape of every field.

    Returns {"ok": bool, "findings": [...], "report": [...]}. If ok is false, the data
    from the other tools should not be trusted until the selectors are re-derived.
    """
    findings, report = [], []

    def note(kind, msg):
        findings.append({"kind": kind, "detail": msg})
        report.append(f"  {kind}: {msg}")

    for event_type in ALL_TYPES:
        report.append(f"{event_type}:")
        page = _fetch(f"t-tourney.asp?e={_TYPE_PARAM[event_type]}")
        shapes = set()
        for table in page.css("table.omnipong"):
            rows = table.css("tr")
            if rows:
                shapes.add(tuple(_text(c) for c in rows[0].css("th, td")))
        for shape in shapes - BASELINE_HEADERS[event_type]:
            note("COLUMNS CHANGED",
                 f"unrecognized header {shape} — every fixed column index in "
                 f"list_tournaments may now point at the wrong field")
        try:
            rows = list_tournaments(event_type=event_type)
        except RuntimeError as e:
            note("PARSER BROKEN", str(e))
            continue
        report.append(f"  {len(rows)} events, {len(shapes)} header shape(s)")
        for r in rows:
            where = f"id={r['tournament_id']} {r['name'][:30]!r}"
            if not isinstance(r["tournament_id"], int) or r["tournament_id"] <= 0:
                note("BAD ID", where)
            if not r["name"]:
                note("EMPTY NAME", where)
            if not DATE_RE.match(r["date"] or ""):
                note("BAD DATE", f"{where} date={r['date']!r} (expected MM/DD/YY)")
            if r["status"] not in STATUSES:
                note("BAD STATUS", f"{where} status={r['status']!r} not in {sorted(STATUSES)}")
            if DATE_RE.match(r["city"] or ""):
                note("CITY LOOKS LIKE A DATE", f"{where} city={r['city']!r} — columns shifted")
            if not r["state_section"]:
                note("NO SECTION", where)
            if r["entry_form_pdf"] and not r["entry_form_pdf"].lower().endswith(".pdf"):
                note("BAD PDF LINK", f"{where} {r['entry_form_pdf']}")

    # get_results/get_tournament_info run through _parse_tables, which nothing above
    # touches. 1277 is a finished tournament with stable published placements.
    report.append("detail pages (id 1277):")
    res = get_results(1277)
    detail = [r for t in res["tables"] for r in t]
    if not detail:
        note("RESULTS EMPTY", "get_results(1277) parsed no rows — results page structure changed")
    elif not any("First Place" in r for r in detail):
        note("RESULTS COLUMNS CHANGED", f"no 'First Place' column; got {sorted(detail[0])[:6]}")
    else:
        report.append(f"  {len(detail)} result rows, columns intact")
    info = get_tournament_info(1277)
    if not (info["tournament_name"] and info["details"]):
        note("INFO PAGE EMPTY", f"name={info['tournament_name']!r} — h2/h3 structure changed")
    else:
        report.append(f"  info page: {info['tournament_name'][:40]!r}")

    return {"ok": not findings, "findings": findings, "report": report}


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


# ── REST face ──────────────────────────────────────────────────────────────
# Second face over the SAME core (the @mcp.tool functions above). Agents use the
# MCP face at /mcp; web apps (rubberr) use these plain-JSON GETs. One data source,
# so the two faces can never disagree. See ARCHITECTURE.md.
from starlette.concurrency import run_in_threadpool  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402


async def _serve(fn, *args, **kwargs) -> JSONResponse:
    """Run a blocking core call off the event loop; turn its errors into clean
    JSON instead of a raw traceback (a leaked traceback is a defect)."""
    try:
        data = await run_in_threadpool(fn, *args, **kwargs)
    except ValueError as e:            # bad enum / bad id — caller's fault
        return JSONResponse({"error": str(e)}, status_code=400)
    except RuntimeError as e:          # parser drift / omnipong HTTP error
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse(data)


def _int_param(request: Request, name: str) -> int:
    """Path/query int with a clean 400 instead of a ValueError traceback."""
    try:
        return int(request.path_params.get(name, request.query_params.get(name, "")))
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")


@mcp.custom_route("/events", methods=["GET"])
async def rest_events(request: Request) -> JSONResponse:
    q = request.query_params
    return await _serve(list_tournaments,
                        state=q.get("state", ""), keyword=q.get("keyword", ""),
                        year=q.get("year", ""),
                        event_type=q.get("event_type", "tournaments"))


@mcp.custom_route("/results/{tournament_id:int}", methods=["GET"])
async def rest_results(request: Request) -> JSONResponse:
    try:
        tid = _int_param(request, "tournament_id")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return await _serve(get_results, tid)


@mcp.custom_route("/info/{tournament_id:int}", methods=["GET"])
async def rest_info(request: Request) -> JSONResponse:
    try:
        tid = _int_param(request, "tournament_id")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return await _serve(get_tournament_info, tid)


@mcp.custom_route("/whats-new", methods=["GET"])
async def rest_whats_new(request: Request) -> JSONResponse:
    q = request.query_params
    try:
        days = int(q.get("days", "7"))
    except ValueError:
        return JSONResponse({"error": "days must be an integer"}, status_code=400)
    open_only = q.get("open_only", "true").lower() not in ("false", "0", "no")
    return await _serve(whats_new, days=days, open_only=open_only,
                        state=q.get("state", ""), event_type=q.get("event_type", ""))


@mcp.custom_route("/health", methods=["GET"])
async def rest_health(request: Request) -> JSONResponse:
    result = await run_in_threadpool(check_parser_health)
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


# ── abuse protection ─────────────────────────────────────────────────────────
# The face is public read-only data (no player data, nothing secret), so the real
# risk is load/amplification, not disclosure. One protection, always on, covering
# BOTH faces: a fixed-window per-IP rate limit. This is what makes --http safe to
# expose. Pure-ASGI (not BaseHTTPMiddleware) so it never buffers the /mcp SSE stream.
import threading  # noqa: E402
from collections import defaultdict  # noqa: E402

RATE_LIMIT = int(os.environ.get("OMNIPONG_RATE_LIMIT", "60"))    # requests per window
RATE_WINDOW = int(os.environ.get("OMNIPONG_RATE_WINDOW", "60"))  # window, seconds
_hits: dict[str, list] = defaultdict(lambda: [0.0, 0])  # ip -> [window_start, count]
_hits_lock = threading.Lock()


class RateLimitMiddleware:
    """Fixed-window per-client-IP limiter. Sends its own 429 and otherwise passes
    the request through untouched (no response buffering, so SSE keeps streaming)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        ip = scope["client"][0] if scope.get("client") else "unknown"
        now = time.time()
        with _hits_lock:
            if len(_hits) > 10000:  # bound memory: drop IPs whose window has expired
                for k in [k for k, v in _hits.items() if now - v[0] >= RATE_WINDOW]:
                    del _hits[k]
            win = _hits[ip]
            if now - win[0] >= RATE_WINDOW:
                win[0], win[1] = now, 0
            win[1] += 1
            over = win[1] > RATE_LIMIT
            retry = max(int(RATE_WINDOW - (now - win[0])), 1)
        if over:
            resp = JSONResponse(
                {"error": f"rate limit {RATE_LIMIT} requests per {RATE_WINDOW}s exceeded"},
                status_code=429, headers={"Retry-After": str(retry)})
            return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


if __name__ == "__main__":
    if FORCE:
        refresh()
        FORCE = False  # export reads the snapshot we just wrote, no refetching
        export_batch()
    elif "--export" in sys.argv:
        export_batch()
    elif "--http" in sys.argv:
        import uvicorn
        app = mcp.streamable_http_app(host="0.0.0.0")
        app.add_middleware(RateLimitMiddleware)  # always on — see abuse protection
        uvicorn.run(app, host="0.0.0.0", port=8722)
    else:
        mcp.run()
