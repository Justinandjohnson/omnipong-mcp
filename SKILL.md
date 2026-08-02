---
name: omnipong
description: Navigate omnipong.com (US table tennis tournaments, leagues, camps) to find new signup-able events — URL/parameter map, page structures, and known traps. Use when asked about table tennis tournaments, leagues, camps, USATT events, entry deadlines, tournament results, or when extending/debugging the omnipong MCP server.
---

# Omnipong site navigation

Omnipong is a classic ASP site (`.asp`, no JSON API, no auth for public pages). Every public listing is server-rendered HTML built from `<table class="omnipong">`. Everything below was verified against the live site on 2026-08-01 — don't guess new parameters, probe them the way this file documents.

## Scope rule — read first

**Never scrape player names, rosters, or player detail pages. Not ever.** The player-sort pages (`t=100/101/102/110/111`) and the per-player links inside them are deliberately out of bounds; if someone wants player or rating info, point them at the site to look themselves.

What this system is for: **surfacing new tournaments, leagues, and events people can sign up for.** Build toward that, not toward a general mirror of the site.

## Fastest path: use the MCP server, don't re-scrape

`server.py` in this repo already wraps all of this with caching. Prefer it:

```bash
uv run server.py --refresh   # re-scrape + flag new events (run daily; scheduling is external)
```

Tools: `whats_new(days, open_only, state, event_type)` — **the main one**, events that appeared recently and are accepting entries — plus `list_tournaments(state, keyword, year, event_type)`, `get_results(id)`, `get_tournament_info(id)`. Daily snapshots land in `batches/<date>.json`; raw HTML and the first-seen index live in `cache.db`. Only drop to raw URLs below when adding a page type the server doesn't cover yet.

## URL map (all verified)

Base: `https://www.omnipong.com/`

### Listings — `t-tourney.asp?e=<n>`

| `e=` | Content | Columns | Live count (2026-08-01) |
|------|---------|---------|------|
| 0 | Tournaments | 8 | 148 across 24 sections |
| 1 | Leagues | **6** | 74 across 5 sections |
| 2 | Camps/Classes | **6** | 12 across 4 sections |
| 3 | International | — | currently empty |

Tournament columns: Action, List, Name, City/State, Date, Contact, Ball Info, USATT Level.
Leagues/camps drop Ball Info and USATT Level — **a parser hardcoded to 8 columns silently returns zero rows for them.**

### Search / history — POST to `T-tourney.asp?t=9&e=<n>`

Form fields `Year` and `Keyword`. `Year` accepts `2011`–`2026`, `TWeek` (this week), `LWeek`, `NWeek`, `All`. Empty `Year` = current listings only. This is the only way to reach past seasons.

### Per-event pages — `T-tourney.asp?t=<code>&r=<tournament_id>`

| `t=` | Page | Notes |
|------|------|-------|
| 103 | Event draws and results | placements: Event, 1st, 2nd, 3rd, 3rd/4th |
| 108 | Info page | **prose, not a table** — see below |
| 100/101/102/110/111 | Player sorts (name/rating/event/club/state) | **off limits — do not scrape** |

`Members.asp?ae=<n>&h=<id>` is the entry/registration flow — requires login, don't automate it.

Entry forms are static PDFs at `EntryForms/<n>-<n>.pdf`.

## Traps that have already bitten (each cost a debug cycle)

**1. A partial refresh poisons the new-event baseline.** New-event detection works off a `seen` table of first-seen dates. If a run records only some events (e.g. a limited test run), every unrecorded real event gets flagged "new" on the next full run. `OMNIPONG_REFRESH_LIMIT` therefore skips baseline writes entirely — keep it that way, and if you ever reset the baseline (`DROP TABLE seen`), the next run must be a **full** one, which seeds silently and flags nothing.

**2. Tournaments are cross-listed, so listing rows ≠ distinct tournaments.** The tournaments page returns 148 rows but only **134 unique IDs**: 14 events appear once under a USATT series section (Premier Events, National Team Ranking, State Series) and again under their state, same ID and name both times. Dedupe on `tournament_id` before counting or reporting. The `seen` index is keyed on the ID, so new-event detection is already immune.

**3. `t=108` has no table at all.** It's `<h2>` (official name) plus a series of `<h3>` lines carrying the date, entry status, and PDF link. A table parser returns empty and looks broken. Extract h2/h3 text instead.

**4. `<h4>` section headers aren't `<h4>` after parsing.** The raw HTML is `<h4><p Align="Left">California</p></h4>`; lxml auto-closes the `h4` before the block-level `p`, so the state name lives in `p[align]`, not `h4`. Selecting `h4` returns empty strings. Use `page.css("p[align], table.omnipong")` and walk in document order — a `p` starts a new section, the following table belongs to it.

**5. Empty ≠ broken.** Many events legitimately have no data: unposted results render a header-only table, and pages that are genuinely empty say so in a second `<h3>` (the MCP server preserves it as `note`). 21 finished tournaments in batch #1 had no placements — all confirmed as the site's own state. Only treat empty as a bug when the site gives no such signal.

**6. Escaping differs by extraction method.** Reading `onclick` via an attribute accessor gives raw `&` (`t=103&r=4284`); re-serializing the element to a string gives `&amp;`. Match `[?&](?:amp;)?r=(\d+)` if you can't guarantee which you have.

**7. Encoding is `iso-8859-1`**, declared in a meta tag, not UTF-8. Scrapling handles it; `requests` needs `r.encoding` set explicitly.

## Where the ID comes from

Tournament IDs are not in the listing's links — they're inside the `onclick` of the Action/List buttons:

```html
<input value="Results" onclick="open_window('T-tourney.asp?t=103&r=4284','_self')">
```

So extract from `tds[0]`/`tds[1]` input `onclick` attributes, not from `<a href>`. Status is the button's `value`: `Enter` (open), `Closed`, `Results` (finished), `Info`, `Draws`.

## How new-event detection works

`cache.db` has a `seen` table keyed on `(event_id, event_type)` recording the date each event first showed up. Each full refresh inserts unseen events and prints them; `whats_new()` queries that table. Baseline rows are stamped `seed` and excluded, so the first run never reports a flood.

Scheduling lives outside this repo — an external system calls `--refresh`. The diff is only as good as the last run, so a missed day means new events surface a day late.

## Adding a new page type

1. Confirm it isn't player data (see the scope rule) — if it is, stop.
2. Fetch the raw HTML and look at it before writing a selector — this site's markup is inconsistent between page types.
3. Check whether it's a `table.omnipong` page or a prose page (`t=108` is prose; assume nothing).
4. Verify against **two** events: one with data and one without, so you can tell "no data" from "parser broke."
5. Add it to `server.py`, extend `test_smoke.py`, run `uv run test_smoke.py` — it drives a real MCP client against the live site.

## Politeness

Cache is 15 min; the full refresh sleeps 0.2 s between events and takes ~15 min for ~300 pages. Requests occasionally time out at 30 s and Scrapling retries. Don't parallelize aggressively — this is a small site run by a volunteer organization.
