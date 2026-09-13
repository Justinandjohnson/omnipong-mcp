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
| `check_parser_health()` | Whether the scraper is still reading the site correctly. `{"ok": bool, "findings": [...]}` — ask for this before trusting a surprising answer |

**No player data, ever.** Rosters and per-player pages are deliberately not scraped. For player or rating questions, go to the site. This exists to catch new signup-able events.

## New-event radar

`whats_new` needs history to compare against, so run a refresh on a schedule (cron, launchd, whatever you use):

```bash
cd /path/to/omnipong-mcp && uv run server.py --refresh
```

First run seeds a baseline silently and flags nothing. Every run after prints `NEW [...]` lines for events that just appeared, and writes `batches/<date>.json` — a full snapshot of every event.

**Daily, not weekly** — measured, not guessed (`Last-Modified` on all 292 entry-form PDFs, Aug 2026): new tournaments post every day of the week (Wed 59, Mon 53, Tue 47, Thu 46, Fri 38, Sat 33, Sun 16), and 86% of events end Sat/Sun so results land early week. A 2–3 day gap misses fresh postings and entry deadlines.

Takes ~13 min for 238 pages. Exits non-zero on failure.

## Two ways to run it

**Local (stdio):** clone and run over stdio — your machine, your `cache.db`. Simplest for one person.

**Hosted (`--http`, two faces):** one server, same data, two doors — an **MCP face** at `/mcp` (Streamable HTTP, for agents) and a plain-JSON **REST face** for web apps:

| Endpoint | Returns |
|---|---|
| `GET /events?event_type=tournaments\|leagues\|camps\|international&state=&keyword=&year=` | array of events |
| `GET /whats-new?days=7&open_only=true&state=&event_type=` | events first seen in the last N days |
| `GET /results/{id}` · `GET /info/{id}` | one tournament's results / entry info |
| `GET /health` | parser-health check (`200` ok, `503` drifted) |

The REST face sends CORS headers so a browser web app on another origin (rubberr on `:3001`) can actually read it — without them the browser blocks the response even though the `GET` is `200`. Any origin is allowed by default (read-only public JSON, no cookies); set `OMNIPONG_CORS_ORIGINS` (comma-separated) to lock it to specific origins for a real deploy.

Abuse protection is always on: a per-IP rate limit (`OMNIPONG_RATE_LIMIT`, default 60 / `OMNIPONG_RATE_WINDOW`, default 60s). The cache means the hosted server scrapes omnipong at most once per URL per 15 min regardless of client count — gentler on that small volunteer-run site than many separate scrapers. Put TLS in front before exposing it publicly. See `ARCHITECTURE.md`.

## Be polite

Cache is 15 min; refresh sleeps 0.2s between pages. Don't parallelize this. Omnipong is run by volunteers.

## Test

```bash
uv run test_smoke.py         # happy path: all five tools over a real stdio MCP client
uv run eval_harness.py       # the HOSTED faces: REST + MCP-over-HTTP, one-core check, rate limit
uv run test_adversarial.py server.py   # hostile input, injection, concurrency
uv run audit.py              # are the selectors still reading the site correctly?
uv run test_audit.py         # does the audit actually catch a broken parser?
```

Real MCP client, real stdio, live site, no mocks. The smoke test asserts completeness (45 CA tournaments, 74 leagues, 12 camps, 148 national rows across 24 sections, known-good results for id 1277). The adversarial suite covers garbage enums, junk and out-of-range IDs, SQL-injection payloads, 5k-char unicode, path traversal, absurd day counts, and 8 concurrent callers — and asserts errors are *actionable*, not leaked tracebacks.

**`eval_harness.py` proves the two hosted faces** (what `--http` adds) end to end against the live site, starting a real `server.py --http` subprocess and hitting it over real sockets: every REST endpoint with real-data assertions; a **CORS** check (preflight + a browser-origin `GET` both carry `Access-Control-Allow-Origin`, so web-app callers on another origin aren't silently blocked); the MCP face over a real Streamable-HTTP client (`initialize` → `list_tools` = the 5 tools, no `get_players`); a **one-core** check that REST `/events` and the MCP `list_tournaments` tool return *identical* ids for the same query; the adversarial set above; 8 concurrent callers returning identical results; and a fresh server at `LIMIT=5` proving exactly 5 requests pass then the 6th is `429` with a valid `Retry-After`. Last run: **30/30** checks passed.

**Known boundary — trust ids from the listing.** omnipong's info endpoint never 404s: given a nonexistent tournament id it returns a *plausible but wrong* real tournament (verified — different bogus ids return different arbitrary tournaments). `get_results` on a bad id is safe (empty `tables`), but `get_tournament_info` will hand back a confident wrong answer. Always pass ids obtained from `list_tournaments` / `whats_new` (which is how agents and the tests use it). This is the site's behavior, not a parser fault, and there is no reliable signal to detect it — so, per the no-fallbacks rule, no double-scrape guard is added.

## When omnipong changes its HTML

The parser is fixed CSS selectors, written once. Scrapers don't break loudly — they
break *quietly*, returning `[]` or plausible-looking garbage that your agent reports
as fact. Two checks make that impossible to miss:

**Every call self-checks.** `list_tournaments` sweeps the page a second time for event
ids *without* using its own selectors (`table.omnipong`, `p[align]`). If that independent
read finds events the parser didn't return, it raises instead of returning a short list.
An empty listing is still legal — International is empty today — but silently dropping
visible events is not.

**The health check is a tool, not just a script.** `check_parser_health()` is exposed
over MCP, so your agent can verify the plumbing itself — "are you sure there are no
tournaments in Ohio?" becomes a question it can actually answer. `audit.py` is the same
check from the command line.

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

## Cross-platform

All four suites run on **Windows, Linux and macOS** via GitHub Actions
([.github/workflows/ci.yml](.github/workflows/ci.yml)) — weekly and on demand, not on
every push, because they hit the live volunteer-run site. Last run: identical results on
all three (smoke OK, audit OK, mutation 5/5, adversarial 14/14).

Not covered: long-run memory/disk growth over months of daily refreshes.

Site notes — URL map, page structures, and seven traps that each cost a debug cycle — are in [SKILL.md](SKILL.md). Read it before touching the scraper. Drop it in `~/.claude/skills/omnipong/` to load it as an agent skill.

MIT.
