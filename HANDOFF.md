# Omnipong agent system — handoff

## What this is

An MCP server that watches omnipong.com and tells agents about **new tournaments, leagues, and camps people can sign up for**. Any agent can query it instead of visiting the site.

**Scope rule:** never scrape player names, rosters, or per-player pages. For player/rating questions, point people at the site.

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

## NEXT TASK: secure public deploy (designed, NOT yet built)

Goal: let other people try it with zero blast radius on Jared.

**The risk that drives the design:** if a stranger's agent can make the server fetch from
omnipong.com, they can hammer that small volunteer-run site *from our IP* and get us blocked.
Auth alone does not fix this. So:

**Split it in two.**

1. **Private collector** (Jared's machine / his external scheduler) — the ONLY thing that ever
   talks to omnipong.com. Runs `--refresh` daily, produces `cache.db` + `batches/<date>.json`.
2. **Public server** (separate host, e.g. Render free tier) — serves **read-only from a copy of
   that data** and is hard-blocked from making any outbound request to omnipong.

Implementation checklist (none done yet):
- [ ] `--public` flag: `_fetch()` raises instead of hitting the network; cache miss = clean error,
      never a live fetch. This is the load-bearing control — test it with the network unplugged.
- [ ] Bearer-token auth, **one token per user** in an env var, so revoking one doesn't affect others.
- [ ] Per-token rate limit.
- [ ] Deploy public server on its own host — not Jared's machine, no credentials, no personal data
      (the no-player-data rule already means there is nothing sensitive to leak).
- [ ] Ship data by committing/uploading a snapshot, or a small pull job — never give the public
      host scrape rights.
- [ ] Verify from outside: no token = 401, bad token = 401, valid token = data, and confirm the
      public host makes zero requests to omnipong under load.

Do NOT expose the current `--http` mode publicly as-is: it binds `0.0.0.0:8722`, has no auth,
and will fetch live on a cache miss.

## Possible follow-ups (none started, none required)

- Push new events somewhere (Slack/email) instead of only logging them
- Deploy `--http` mode on a real host so other people's agents can hit it
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
