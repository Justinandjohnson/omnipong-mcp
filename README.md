# omnipong-mcp

Ask your agent about US table tennis tournaments instead of browsing omnipong.com.

> "Any new tournaments open for signup in California?"
> "What leagues are accepting entries?"
> "Who won tournament 1277?"

An MCP server that reads [omnipong.com](https://www.omnipong.com/) — tournaments, leagues, and camps you can sign up for. Works with Claude Code, Claude Desktop, or anything else that speaks MCP.

## Install

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/). Nothing else — deps install on first run.

```bash
git clone https://github.com/Justinandjohnson/omnipong-mcp && cd omnipong-mcp
claude mcp add omnipong -- uv run "$PWD/server.py"
```

Claude Desktop instead? Add to `claude_desktop_config.json`:

```json
{"mcpServers": {"omnipong": {"command": "uv", "args": ["run", "/full/path/to/server.py"]}}}
```

That's it. Ask your agent about tournaments.

## Tools

| Tool | What it returns |
|------|-----------------|
| `whats_new(days, open_only, state, event_type)` | **The point of this** — events that showed up in the last N days and are accepting entries |
| `list_tournaments(state, keyword, year, event_type)` | Every listed event: name, city, date, status, contact, USATT level, entry-form PDF. `event_type` is `tournaments` (148), `leagues` (74), `camps` (12), or `international`. `year` accepts `2025`, `TWeek`, `All`, etc. |
| `get_results(tournament_id)` | Placements for a finished tournament |
| `get_tournament_info(tournament_id)` | Official name, date, entry status, entry form |

**No player data, ever.** Rosters and per-player pages are deliberately not scraped. For player or rating questions, go to the site. This exists to catch new signup-able events.

## New-event radar

`whats_new` needs history to compare against, so run a refresh on a schedule (cron, launchd, whatever you use):

```bash
cd /path/to/omnipong-mcp && uv run server.py --refresh
```

First run seeds a baseline silently and flags nothing. Every run after prints `NEW [...]` lines for events that just appeared, and writes `batches/<date>.json` — a full snapshot of every event.

**Daily, not weekly** — measured, not guessed (`Last-Modified` on all 292 entry-form PDFs, Aug 2026): new tournaments post every day of the week (Wed 59, Mon 53, Tue 47, Thu 46, Fri 38, Sat 33, Sun 16), and 86% of events end Sat/Sun so results land early week. A 2–3 day gap misses fresh postings and entry deadlines.

Takes ~13 min for 238 pages. Exits non-zero on failure.

## Everyone runs their own copy

There's no shared public server on purpose. Each install caches to its own `cache.db` and hits omnipong from its own machine — nobody's traffic pools onto one IP and gets that small volunteer-run site blocked.

`server.py --http` exists for serving your own agents on your own network. It has no auth and binds `0.0.0.0:8722` — don't put it on the public internet.

## Be polite

Cache is 15 min; refresh sleeps 0.2s between pages. Don't parallelize this. Omnipong is run by volunteers.

## Test

```bash
uv run test_smoke.py         # happy path: all four tools against the live site
uv run test_adversarial.py server.py   # hostile input, injection, concurrency
uv run audit.py              # are the selectors still reading the site correctly?
uv run test_audit.py         # does the audit actually catch a broken parser?
```

Real MCP client, real stdio, live site, no mocks. The smoke test asserts completeness (45 CA tournaments, 74 leagues, 12 camps, 148 national rows across 24 sections, known-good results for id 1277). The adversarial suite covers garbage enums, junk and out-of-range IDs, SQL-injection payloads, 5k-char unicode, path traversal, absurd day counts, and 8 concurrent callers — and asserts errors are *actionable*, not leaked tracebacks.

## When omnipong changes its HTML

The parser is fixed CSS selectors, written once. Scrapers don't break loudly — they
break *quietly*, returning `[]` or plausible-looking garbage that your agent reports
as fact. Two checks make that impossible to miss:

**Every call self-checks.** `list_tournaments` sweeps the page a second time for event
ids *without* using its own selectors (`table.omnipong`, `p[align]`). If that independent
read finds events the parser didn't return, it raises instead of returning a short list.
An empty listing is still legal — International is empty today — but silently dropping
visible events is not.

**`audit.py` catches the nastier failure.** If omnipong inserts a column, every fixed
index shifts: city holds the date, contact holds the ball type. Row counts stay identical
and nothing raises. So the audit compares the header row against a recorded baseline and
validates the shape of every field — dates match `MM/DD/YY`, status is one of the known
five, a city must not look like a date. Verified by shifting a column on a copy: 148 rows,
headers unchanged, **11 findings, exit 1**.

An audit that always prints OK is indistinguishable from one that checks nothing, so
`test_audit.py` proves it works, reproducibly: it corrupts the parser on a throwaway copy
in four ways omnipong could actually break it — inserted column, renamed table class,
stricter row filter, changed detail-page markup — and asserts the audit fails each time,
then that the real parser passes. Every mutation asserts its own target text was found, so
a stale mutation fails loudly instead of silently testing nothing. **5/5.**

Run the audit with your daily refresh:

```bash
cd /path/to/omnipong-mcp && uv run server.py --refresh && uv run audit.py
```

A failure names what changed, which is what an agent needs to re-derive the selectors —
read [SKILL.md](SKILL.md) first, it has the page structures and seven documented traps.

Not covered: Windows and Linux (macOS only), and long-run memory/disk growth.

Site notes — URL map, page structures, and seven traps that each cost a debug cycle — are in [SKILL.md](SKILL.md). Read it before touching the scraper. Drop it in `~/.claude/skills/omnipong/` to load it as an agent skill.

MIT.
