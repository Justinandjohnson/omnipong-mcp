# omnipong-mcp

MCP server (MCP SDK 2.0, Scrapling scraper) that lets any agent query omnipong.com tournament data without visiting the site. On-demand fetches cache for 15 min in `cache.db` (SQLite); a daily launchd job re-scrapes everything so the full snapshot is never more than a day old.

## Tools

| Tool | What it returns |
|------|-----------------|
| `whats_new(days, open_only, state, event_type)` | **The point of this server** — events that first appeared in the last N days and are accepting entries. |
| `list_tournaments(state, keyword, year, event_type)` | All listed events: id, name, city, date, status (Enter/Closed/Results/Info/Draws), contact, USATT level, entry-form PDF. `event_type` is `tournaments` (148), `leagues` (74), `camps` (12), or `international`. `year` accepts `2025`, `TWeek`, `All`, etc. |
| `get_results(tournament_id)` | Event placements for finished tournaments |
| `get_tournament_info(tournament_id)` | Official name, date, entry status, entry-form PDF (this page is prose, not a table) |

**Scope: no player data, ever.** Player rosters and per-player pages are deliberately not scraped — for player or rating questions, point people at the site. This server exists to catch new signup-able events.

Site navigation notes — URL/parameter map and the traps that cost debug cycles — live in the `omnipong` skill at `~/.claude/skills/omnipong/SKILL.md`.

## Use from Claude Code (stdio)

```bash
claude mcp add omnipong -- uv run ./server.py
```

## Run as a shared API (for other people's agents)

```bash
uv run server.py --http
```

Serves streamable-HTTP MCP at `http://<your-host>:8722/mcp`. Anyone's agent connects with:

```bash
claude mcp add --transport http omnipong http://<your-host>:8722/mcp
```

## Refresh

Scheduling is external — call this once a day from your own scheduler:

```bash
cd /path/to/omnipong-mcp && uv run server.py --refresh
```

It re-scrapes every listing plus results/info into `cache.db`, prints any `NEW [...]` events, and writes that day's batch. Takes ~13 min for 238 pages. Exits non-zero on failure.

Why daily and not every 2–3 days — measured, not guessed (Last-Modified headers of all 292 entry-form PDFs, Aug 2026):

- New tournaments are posted **every day of the week**: Wed 59, Mon 53, Tue 47, Thu 46, Fri 38, Sat 33, Sun 16. A 2–3 day gap always misses fresh postings and entry-deadline changes.
- 86% of events end Sat/Sun, so results land early week; a daily pull picks them up at most 24 h late.

## Batches

Each refresh run also writes `batches/<YYYY-MM-DD>.json` — one self-contained file with every tournament plus its full player list and results/info. Build one manually from the current snapshot with `uv run server.py --export`.

Batch #1 (`batches/2026-08-01.json`, 2.25 MB): 148 tournaments, 24 state/series sections, 8,129 player entries, 114 info pages with date + entry status.

Reading empties correctly: 36 tournaments have no players and 21 finished ones have no placements — that is the site's own state, not a scrape failure. When omnipong says so explicitly the message is preserved in the `note` field (e.g. "There are no players to list at this time"); results pages just render a header-only table. Zero empties in batch #1 were unexplained.

## Test

```bash
uv run test_smoke.py                                  # real MCP client over stdio, live site
OMNIPONG_REFRESH_LIMIT=3 uv run server.py --refresh   # small real refresh run
```
