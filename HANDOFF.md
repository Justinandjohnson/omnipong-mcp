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

## Distribution: GitHub, everyone runs their own copy

No hosted server. Each user clones and runs `server.py` over stdio — their machine, their
`cache.db`, their IP. That deletes the whole class of problem rather than solving it: no auth to
build, no rate limits, no stranger's traffic pooling onto one IP and getting omnipong (a small
volunteer-run site) to block it.

`--http` stays for serving your own agents on your own LAN. It binds `0.0.0.0:8722` with no auth,
so it must not go on the public internet as-is.

## Possible follow-ups (none started, none required)

- Push new events somewhere (Slack/email) instead of only logging them
- Event draw/bracket pages (`t=103` links one level down) — unexplored, non-player data

## Key files & locations

| Path | What it is |
|------|-----------|
| `./server.py` | The MCP server. All 4 tools, scraper, refresh + export + new-event detection. |
| `./test_smoke.py` | Real smoke test — spawns the server over stdio, hits the live site, asserts completeness. `uv run test_smoke.py` |
| `./README.md` | Tool table, install commands, refresh cadence and the evidence for it. |
| `./HANDOFF.md` | This file. |
| `./cache.db` | SQLite: cached raw HTML (15-min TTL) + `seen` table = the first-seen index behind `whats_new`. |
| `./batches/2026-08-01.json` | Batch #1 — every event with results/info, no player data. One file per day. |
| `./refresh.log` | Output of the last refresh run, including `NEW [...]` lines. |
| `~/.claude/skills/omnipong/SKILL.md` | Site navigation skill: URL/parameter map, 7 documented traps, scope rule. Read before touching the scraper. |
| `~/.claude/projects/-project-/memory/omnipong-no-player-data.md` | Durable memory of the no-player-data rule. |

## Commands

```bash
cd /path/to/omnipong-mcp
uv run test_smoke.py            # verify everything against the live site
uv run server.py --refresh      # run daily from your external scheduler
uv run server.py --http         # serve at http://<host>:8722/mcp for other agents
claude mcp add omnipong -- uv run ./server.py
```

Never use `OMNIPONG_REFRESH_LIMIT` for a real seeding run — it deliberately skips baseline writes.
