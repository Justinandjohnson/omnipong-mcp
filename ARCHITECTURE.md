# Architecture — one core, two faces

**Problem this solves:** many people (and their agents) need omnipong tournament
data — new signup-able events, results, entry info — kept fresh daily, without
each person running their own scraper. rubberr (the web dashboard) needs the same
data. **No player data, ever** (§ constraint below).

The shape that serves all of them from one place:

```
                    omnipong.com  (the only thing we scrape)
                          │
                    scraper (server.py)         ← existing, frozen selectors + health check
                          │
                    ┌─────▼─────┐
                    │ JSON CORE │  batches/<date>.json  ← canonical daily snapshot
                    └─────┬─────┘     (already emitted by export_batch)
              ┌───────────┴───────────┐
       ┌──────▼──────┐         ┌───────▼────────┐
       │  MCP FACE   │         │   REST FACE    │
       │ Streamable  │         │  GET /events   │
       │ HTTP  /mcp  │         │  (plain JSON)  │
       └──────┬──────┘         └───────┬────────┘
        agents / LLM clients      rubberr + any web app
```

## The two faces, and who uses which

- **MCP face** (Streamable HTTP at `/mcp`) — for **agents / LLM clients only**.
  MCP is a JSON-RPC protocol *driven by a model*. It is the right and only face
  for "give my agent access to the data so it can manage things for me."
- **REST face** (plain `GET` returning JSON) — for **web apps** (rubberr) and any
  non-agent consumer. ✅ A web app must NOT talk MCP — MCP expects an LLM to pick
  tools and read tool results; a React app calling JSON-RPC tool-calls is friction
  for zero gain. Same data, thinner door.

Both faces read the **same canonical JSON core**, so rubberr and an agent can
never disagree about what's on the site today.

## Why remote MCP = Streamable HTTP (not stdio)

✅ Verified against the MCP spec (transports, rev 2026-07-28): **stdio is
local-subprocess only** — it cannot serve multiple remote users. A hosted,
multi-user MCP server MUST use **Streamable HTTP**. `server.py --http` already
runs this transport; that is the correct choice and stays.

## Distribution — how people get it

Ranked, one path each (no menu):

1. **Official MCP Registry** (`registry.modelcontextprotocol.io`) — the real front
   door for agents. It stores metadata + how to reach the server; for us that's a
   **remote** entry (a Streamable-HTTP URL), not a package install. ✅ `server.json`
   is written and validated against the current generic schema
   (`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`);
   name `io.github.Justinandjohnson/omnipong`. Publish with the `mcp-publisher` CLI
   (GitHub-OAuth login authorizes the namespace) — steps in `PUBLISH.md`. ⚠️ the
   remote `url` is a placeholder until the server is deployed behind TLS.
2. **Hosted REST endpoint** — for rubberr and web apps. No install, just a URL.
3. **Plugin** (Claude / Codex marketplace) — for people who want it wired into
   their agent in one click and don't want to touch a registry.
4. **agentskills.io skill** — optional, for a Hermes-style self-improving agent
   that should *learn* how to use these tools. Open standard; heavier than most
   users need.

❌ **Do NOT build a custom marketplace.** The official MCP Registry already is
the marketplace for MCP servers; a bespoke one is duplicate infrastructure to
maintain for no added reach.

## Explicitly excluded (and why)

- ❌ **"Code Execution with MCP"** (Anthropic, Nov 2025). Its 98.7% token saving
  comes from *not* loading hundreds/thousands of tool defs upfront and *not*
  double-passing large intermediate results through context. We have **5 tools**
  and small results. It does not meaningfully apply here — adding it is complexity
  for a saving we don't have. Revisit only if the tool count grows by ~100×.
- ❌ **A scraping swarm / multi-agent scrape.** The site's DOM == its raw HTML
  (verified prior session), so one polite fetch reads everything a swarm would.
  Frozen selectors + `_generic_ids()` + `check_parser_health()` already self-check
  for drift. More agents = more load on omnipong for zero new data.

## Self-heal — right-sized

- **Daily:** `server.py --refresh` (cron) re-scrapes, records first-seen, writes
  the snapshot. Pure code. `check_parser_health()` runs and reports drift.
- **Model in the loop ONLY on break:** the health check flips `ok:false` only when
  omnipong changes its HTML and the frozen selectors need re-deriving — rare. That
  is the one moment a model (or a human) is worth invoking. Everything else is
  deterministic code.

This matches the no-fallbacks rule: one method per job, clean error on break —
not a cascade of guessers.

## Standing constraints (do not violate)

- **No player data, ever.** Never scrape player names, rosters, or per-player
  pages. The product is *signup-able events*, not people. `export_batch()` and
  every tool already obey this.
- **No fallbacks.** One method per function, or a clean error.
- **`--http` currently binds `0.0.0.0:8722` with NO auth.** It MUST NOT go on the
  public internet as-is. Public hosting requires auth + rate-limiting first
  (tracked, not yet built).

## Build order

1. ✅ JSON core already exists (`export_batch` → `batches/<date>.json`).
2. Point the read path at the snapshot so both faces share one source of truth.
3. REST face (`GET /events`, `/results/<id>`, `/whats-new`) over the core.
4. Auth + rate-limit before any public host.
5. Publish to the official MCP Registry.

Confidence legend: ✅ verified against primary docs · ⚠️ vendor-stated / version-fluid · ❌ ruled out.
