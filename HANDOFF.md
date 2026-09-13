# Omnipong agent system — handoff

## What this is

An MCP server that watches omnipong.com and tells agents about **new tournaments, leagues, and camps people can sign up for**. Any agent can query it instead of visiting the site.

**Scope rule:** never scrape player names, rosters, or per-player pages. For player/rating questions, point people at the site.

## Parser drift is handled (2026-08-02)

The parser is frozen CSS selectors, and scrapers break quietly rather than loudly.
Three layers now make a quiet break impossible:

1. `list_tournaments` re-reads each page for event ids **without** its own selectors
   and raises if it dropped any. An empty listing stays legal (International is empty).
2. `check_parser_health()` — an MCP tool, also `uv run audit.py` — compares live header
   rows to a recorded baseline and validates every field's shape, catching a column
   insert that shifts city into date while row counts stay identical.
3. `test_audit.py` corrupts the parser four ways and asserts the audit catches each,
   so the audit can't rot into always-OK. 5/5.

Chrome/Playwright was measured and rejected: browser DOM and raw HTML are identical
(174 rows, 26 tables, 24 `table.omnipong` both ways), so a browser adds latency and a
failure mode without adding information.

## Status: built and verified. Not yet proven unattended.

Everything below was tested against the live site through a real MCP client, not mocks.

**Accepted / verified**
- 4 MCP tools work over stdio and streamable-HTTP: `whats_new`, `list_tournaments`, `get_results`, `get_tournament_info`
- Covers tournaments (134 distinct), leagues (74), camps (12) = **220 events baselined**
- New-event detection proven: deleted 3 events from the index, re-ran, exactly those 3 were flagged
- Full refresh run end to end against the live site (238 pages, ~13 min)
- MCP SDK 2.0; scraper is Scrapling (no BeautifulSoup)
- Baseline is clean — `whats_new()` correctly returns 0 today

**Not yet proven**
- `whats_new` has never reported a genuinely new event. The mechanism was verified by removing 3 real
  events from the index and confirming exactly those 3 came back flagged; that test state was then
  reverted, and the baseline is clean (`whats_new()` returns 0 today, which is correct).
- Scheduling is external and not set up here.

## Next step

Point your external scheduler at `uv run server.py --refresh` once a day. After its first run, check
`refresh.log` for `NEW [...]` lines. A flood of "new" events means the baseline was reset without a
full seed run — see trap #1 in the skill.

## Distribution — see ARCHITECTURE.md (design evolved to hosted "one core, two faces")

The original design was "everyone clones and runs their own copy over stdio." The current
direction is a single hosted server exposing the same core two ways — an MCP face (Streamable
HTTP, for agents) and a REST/JSON face (for web apps like rubberr). Full rationale, distribution
plan (official MCP Registry), and what's ruled out is in **ARCHITECTURE.md**.

The old IP-pooling caution (many users' traffic on one IP getting a small volunteer site to block
it) is resolved by the cache: the 15-min TTL + daily snapshot mean the hosted server scrapes
omnipong at most once per URL per window no matter how many clients hit the faces — gentler on
omnipong than N separate scrapers, not harsher.

`--http` now ships abuse protection: a per-IP fixed-window rate limit (`OMNIPONG_RATE_LIMIT`,
default 60 / `OMNIPONG_RATE_WINDOW`, default 60s), always on, covering both faces. It still binds
`0.0.0.0:8722`; before a public deploy, put it behind TLS and fill the real host into `server.json`.

## Possible follow-ups (none started, none required)

- Push new events somewhere (Slack/email) instead of only logging them
- Event draw/bracket pages (`t=103` links one level down) — unexplored, non-player data

## Key files & locations

| Path | What it is |
|------|-----------|
| `./server.py` | The MCP server. All 5 tools, scraper, refresh + export + new-event detection, plus the REST face and per-IP rate limit under `--http`. |
| `./ARCHITECTURE.md` | The hosted "one core, two faces" design, distribution plan, and what's ruled out. |
| `./server.json` + `./PUBLISH.md` | Official MCP Registry entry (remote streamable-http) and the publish steps. |
| `./test_smoke.py` | Real smoke test — spawns the server over stdio, hits the live site, asserts completeness. `uv run test_smoke.py` |
| `./eval_harness.py` | Real eval of the two **hosted** faces — starts `server.py --http`, hits every REST endpoint + the MCP-over-HTTP client, checks both faces agree (one core), adversarial inputs, concurrency, and the rate-limit 429 boundary. Live site, no mocks. Last run 28/28. `uv run eval_harness.py` |
| `./README.md` | Tool table, install commands, refresh cadence and the evidence for it. |
| `./HANDOFF.md` | This file. |
| `./cache.db` | SQLite: cached raw HTML (15-min TTL) + `seen` table = the first-seen index behind `whats_new`. |
| `./batches/2026-08-01.json` | Batch #1 — every event with results/info, no player data. One file per day. |
| `./refresh.log` | Output of the last refresh run, including `NEW [...]` lines. |
| `./SKILL.md (also in ~/.claude/skills/omnipong/)` | Site navigation skill: URL/parameter map, 7 documented traps, scope rule. Read before touching the scraper. |
| `~/.claude/.../memory/omnipong-no-player-data.md` | Durable memory of the no-player-data rule. |

## Commands

```bash
cd /path/to/omnipong-mcp
uv run test_smoke.py            # verify everything against the live site
uv run server.py --refresh      # run daily from your external scheduler
uv run server.py --http         # serve at http://<host>:8722/mcp for other agents
claude mcp add omnipong -- uv run ./server.py
```

Never use `OMNIPONG_REFRESH_LIMIT` for a real seeding run — it deliberately skips baseline writes.
