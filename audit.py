# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=2,<3", "scrapling[fetchers]>=0.4.12,<0.5"]
# ///
"""Parser audit — does the scraper still see everything that's actually on the page?

Run this on a schedule alongside --refresh. It answers two questions the tools
themselves can't:

  1. Is the parser MISSING events that are visibly on the page?
  2. Is the parser returning the WRONG data in the right-looking shape?

(2) is the dangerous one. If omnipong inserts a column, every fixed index shifts:
city becomes the date, contact becomes the ball type. Row counts stay identical
and nothing raises — the tools just quietly lie. So this checks the header row
against a recorded baseline and validates the SHAPE of every field.

Exits non-zero on any finding, so cron/CI notices. Never touches player pages.

    uv run audit.py
"""
import re
import sys

import server
from scrapling.fetchers import Fetcher

# Recorded from the live site 2026-08-01. Tournaments carries both spellings of
# the city column; either is fine, a third is a change worth looking at.
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
DATE = re.compile(r"^\d{2}/\d{2}/\d{2}( - \d{2}/\d{2}/\d{2})?$")
TYPE_PARAM = {"tournaments": 0, "leagues": 1, "camps": 2, "international": 3}

findings = []


def note(kind: str, msg: str) -> None:
    findings.append(kind)
    print(f"  {kind}: {msg}")


def headers_on_page(event_type: str) -> set[tuple]:
    """Header rows as the site actually serves them, read fresh."""
    page = Fetcher.get(f"{server.BASE}t-tourney.asp?e={TYPE_PARAM[event_type]}")
    shapes = set()
    for table in page.css("table.omnipong"):
        rows = table.css("tr")
        if rows:
            shapes.add(tuple(server._text(c) for c in rows[0].css("th, td")))
    return shapes


def audit(event_type: str) -> None:
    print(f"{event_type}:")

    # 1. columns — the silent-corruption detector
    found = headers_on_page(event_type)
    expected = BASELINE_HEADERS[event_type]
    for shape in found - expected:
        note("COLUMNS CHANGED",
             f"unrecognized header {shape} — every fixed column index in "
             f"list_tournaments may now point at the wrong field")

    # 2. coverage — list_tournaments raises on its own if it drops visible ids
    try:
        rows = server.list_tournaments(event_type=event_type)
    except RuntimeError as e:
        note("PARSER BROKEN", str(e))
        return
    print(f"  {len(rows)} events, {len(found)} header shape(s)")
    if not rows:
        return

    # 3. field shape — catches a column shift that kept the row count intact
    for r in rows:
        where = f"id={r['tournament_id']} {r['name'][:30]!r}"
        if not isinstance(r["tournament_id"], int) or r["tournament_id"] <= 0:
            note("BAD ID", where)
        if not r["name"]:
            note("EMPTY NAME", where)
        if not DATE.match(r["date"] or ""):
            note("BAD DATE", f"{where} date={r['date']!r} (expected MM/DD/YY)")
        if r["status"] not in STATUSES:
            note("BAD STATUS", f"{where} status={r['status']!r} not in {sorted(STATUSES)}")
        if DATE.match(r["city"] or ""):
            note("CITY LOOKS LIKE A DATE", f"{where} city={r['city']!r} — columns shifted")
        if not r["state_section"]:
            note("NO SECTION", where)
        if r["entry_form_pdf"] and not r["entry_form_pdf"].lower().endswith(".pdf"):
            note("BAD PDF LINK", f"{where} {r['entry_form_pdf']}")


def audit_detail_pages() -> None:
    """get_results/get_tournament_info run through _parse_tables, which nothing above
    touches — a break there is invisible to the listing checks. 1277 is a finished
    tournament with stable published placements; it is the fixture the smoke test uses.
    """
    print("detail pages (id 1277):")
    res = server.get_results(1277)
    rows = [r for t in res["tables"] for r in t]
    if not rows:
        note("RESULTS EMPTY", "get_results(1277) parsed no rows — results page structure changed")
    elif not any("First Place" in r for r in rows):
        note("RESULTS COLUMNS CHANGED",
             f"no 'First Place' column; got {sorted(rows[0])[:6]}")
    else:
        print(f"  {len(rows)} result rows, columns intact")

    info = server.get_tournament_info(1277)
    if not (info["tournament_name"] and info["details"]):
        note("INFO PAGE EMPTY",
             f"name={info['tournament_name']!r} details={info['details'][:1]} — h2/h3 structure changed")
    else:
        print(f"  info page: {info['tournament_name'][:40]!r}")


if __name__ == "__main__":
    for et in server.ALL_TYPES:
        audit(et)
    audit_detail_pages()
    print()
    if findings:
        print(f"AUDIT FAILED — {len(findings)} finding(s). The parser needs re-deriving; "
              f"see SKILL.md for the page structures.")
        sys.exit(1)
    print("AUDIT OK — selectors still match the live site, every field validates.")
