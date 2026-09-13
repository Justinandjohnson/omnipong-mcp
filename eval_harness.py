# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=2,<3", "httpx>=0.27"]
# ///
"""Real eval harness for the HOSTED faces of server.py.

test_smoke.py already proves the 5 tools over a real stdio MCP client. This harness
proves the HTTP layer that hosting adds, end to end, against the LIVE omnipong.com —
no mocks, no stubs:

  1. REST face  — every GET endpoint over a real socket, real data assertions.
  2. MCP face   — a real Streamable-HTTP MCP client: initialize, list_tools, call_tool.
  3. One core   — REST /events and the MCP list_tournaments tool must return the SAME
                  ids for the same query (proves both faces read one source of truth).
  4. Adversarial— bad enums, non-int ids, out-of-range ids, injection, absurd numbers;
                  every error must be a clean JSON body, never a leaked traceback / 500.
  5. Concurrency— 8 simultaneous callers get identical results, no lock/corruption.
  6. Rate limit — a fresh server with LIMIT=5: exactly 5 pass, the 6th is 429+Retry-After.

Run:  uv run eval_harness.py
Exits non-zero on the first failure. Every PASS line names what it actually verified.
"""
import asyncio
import json
import os
import socket
import subprocess
import time
from contextlib import closing
from typing import NoReturn

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

HOST = "127.0.0.1"
PORT = 8722
BASE = f"http://{HOST}:{PORT}"
KNOWN_DONE_ID = 1277           # a finished tournament, asserted in test_smoke too
KNOWN_WINNER = "Jishan Liang"  # first place on id 1277 (recorded)

_passes = 0


def ok(msg: str) -> None:
    global _passes
    _passes += 1
    print(f"PASS  {msg}")


def die(msg: str) -> NoReturn:
    print(f"FAIL  {msg}")
    raise SystemExit(1)


def check(cond: bool, msg: str) -> None:
    ok(msg) if cond else die(msg)


def _port_open() -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, PORT)) == 0


def start_server(rate_limit: int, rate_window: int = 60) -> subprocess.Popen:
    """Start a REAL `server.py --http` subprocess and wait until the port accepts.
    Readiness is a raw TCP connect only — it consumes zero HTTP requests, so the
    rate-limit phase starts with a clean budget."""
    env = {**os.environ,
           "OMNIPONG_RATE_LIMIT": str(rate_limit),
           "OMNIPONG_RATE_WINDOW": str(rate_window)}
    proc = subprocess.Popen(["uv", "run", "server.py", "--http"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):  # up to ~60s: first run resolves deps + imports scrapling
        if proc.poll() is not None:
            die(f"server exited early (code {proc.returncode}) before binding {PORT}")
        if _port_open():
            return proc
        time.sleep(0.5)
    proc.terminate()
    die(f"server never bound {PORT}")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    # the port must actually free before the next phase reuses it
    for _ in range(20):
        if not _port_open():
            return
        time.sleep(0.25)


def no_traceback(body: str) -> bool:
    """A leaked Python traceback reaching the caller is a defect, not 'errored right'."""
    return "Traceback (most recent call last)" not in body and "  File \"" not in body


# ── the MCP face over a real Streamable-HTTP client ──────────────────────────
async def mcp_probe() -> tuple[list[str], set[int]]:
    """Returns (tool names, CA tournament ids seen through the MCP face)."""
    async with streamable_http_client(f"{BASE}/mcp") as streams:
        read, write, *_ = streams
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            r = await s.call_tool("list_tournaments", {"state": "California"})
            ca = [json.loads(c.text) for c in r.content]
            return tools, {t["tournament_id"] for t in ca if t.get("tournament_id")}


def functional_phase() -> None:
    print("\n=== functional phase (REST + MCP faces, live site) ===")
    proc = start_server(rate_limit=100000)
    try:
        with httpx.Client(base_url=BASE, timeout=90) as c:
            # health
            r = c.get("/health")
            check(r.status_code in (200, 503) and "ok" in r.json(),
                  f"GET /health -> {r.status_code} with ok/findings body")
            check(r.status_code == 200 and r.json()["ok"],
                  "parser health is ok (selectors still read the live site)")

            # CORS — the REST face exists to serve browser web apps on another origin
            # (rubberr on :3001). A browser blocks the response with no ACAO header even
            # though the GET is 200, so a missing header silently breaks every web-app
            # caller. httpx doesn't enforce CORS, but the header is checkable directly.
            origin = "http://localhost:3001"
            pf = c.request("OPTIONS", "/events",
                           headers={"Origin": origin,
                                    "Access-Control-Request-Method": "GET"})
            check(pf.status_code in (200, 204)
                  and pf.headers.get("access-control-allow-origin") in ("*", origin),
                  f"CORS preflight OPTIONS /events -> {pf.status_code}, ACAO="
                  f"{pf.headers.get('access-control-allow-origin')!r}")
            g = c.get("/events", params={"event_type": "tournaments"},
                      headers={"Origin": origin})
            check(g.headers.get("access-control-allow-origin") in ("*", origin),
                  "GET /events carries Access-Control-Allow-Origin for a browser origin")

            # /events California
            r = c.get("/events", params={"state": "California"})
            ca = r.json()
            check(r.status_code == 200 and isinstance(ca, list) and len(ca) > 5,
                  f"GET /events?state=California -> 200, {len(ca)} rows (>5)")
            check(all(t["state_section"].strip() == "California" for t in ca),
                  "every /events CA row is actually in the California section")
            rest_ca_ids = {t["tournament_id"] for t in ca if t.get("tournament_id")}

            # leagues / camps (6-column rows a tournament-only parser would miss)
            for et, floor in (("leagues", 20), ("camps", 5)):
                r = c.get("/events", params={"event_type": et})
                rows = r.json()
                check(r.status_code == 200 and len(rows) >= floor
                      and all(x["tournament_id"] for x in rows),
                      f"GET /events?event_type={et} -> 200, {len(rows)} rows (>={floor}), all have ids")

            # national completeness
            r = c.get("/events")
            allt = r.json()
            states = {t["state_section"] for t in allt}
            check(r.status_code == 200 and len(states) > 20,
                  f"GET /events (national) -> 200, {len(allt)} rows across {len(states)} sections (>20)")
            check(all(t["name"] and t["date"] and t["status"] and t["tournament_id"] for t in allt),
                  "every national row has name+date+status+id (no silently dropped fields)")

            # results (known-good)
            r = c.get(f"/results/{KNOWN_DONE_ID}")
            res = r.json()
            rows = [row for tbl in res["tables"] for row in tbl]
            check(r.status_code == 200 and len(rows) >= 5,
                  f"GET /results/{KNOWN_DONE_ID} -> 200, {len(rows)} placement rows (>=5)")
            check(any(row.get("First Place") == KNOWN_WINNER for row in rows),
                  f"results/{KNOWN_DONE_ID} still shows known winner {KNOWN_WINNER!r}")

            # info for a currently-open CA tournament
            open_t = next((t for t in ca if t["status"] == "Enter" and t["tournament_id"]), None)
            if open_t:
                r = c.get(f"/info/{open_t['tournament_id']}")
                info = r.json()
                check(r.status_code == 200 and info.get("tournament_name") and info.get("details"),
                      f"GET /info/{open_t['tournament_id']} -> 200 with name+details")
            else:
                ok("no CA tournament open for entry today — /info skipped (not a failure)")

            # whats-new
            r = c.get("/whats-new", params={"days": 30})
            new = r.json()
            check(r.status_code == 200 and isinstance(new, list),
                  f"GET /whats-new?days=30 -> 200, {len(new)} events (list)")
            check(all(e["status"] == "Enter" for e in new),
                  "every /whats-new event is open for entry (open_only default honored)")

            # ── adversarial: every error must be clean JSON, never a 500/traceback ──
            print("--- adversarial ---")
            r = c.get("/events", params={"event_type": "garbage"})
            check(r.status_code == 400 and "error" in r.json() and no_traceback(r.text),
                  f"bad event_type -> {r.status_code} clean error (no traceback)")

            r = c.get("/results/abc")  # route is {int}: a non-int can't match -> 404
            check(r.status_code == 404 and no_traceback(r.text),
                  f"GET /results/abc (non-int) -> {r.status_code}, no traceback")

            r = c.get("/results/999999999")  # valid int, no such tournament
            check(r.status_code in (200, 400, 502) and no_traceback(r.text),
                  f"GET /results/999999999 (out-of-range id) -> {r.status_code}, clean (no 500/traceback)")

            r = c.get("/whats-new", params={"days": "notanumber"})
            check(r.status_code == 400 and "error" in r.json() and no_traceback(r.text),
                  f"whats-new days=notanumber -> {r.status_code} clean error")

            r = c.get("/whats-new", params={"days": -5})
            check(r.status_code in (200, 400) and no_traceback(r.text),
                  f"whats-new days=-5 (absurd) -> {r.status_code}, clean (no 500/traceback)")

            r = c.get("/events", params={"state": "'; DROP TABLE seen;--"})
            check(r.status_code in (200, 400) and no_traceback(r.text)
                  and (r.status_code == 400 or r.json() == []),
                  f"SQL-injection state -> {r.status_code}, no traceback, no rows leaked")

            r = c.get("/events", params={"keyword": "../../../../etc/passwd"})
            check(r.status_code in (200, 400) and no_traceback(r.text),
                  f"path-traversal keyword -> {r.status_code}, clean")

            # ── the MCP face, over a real Streamable-HTTP MCP client ──
            print("--- MCP face ---")
            tools, mcp_ca_ids = asyncio.run(mcp_probe())
            check(set(tools) == {"list_tournaments", "whats_new", "get_results",
                                 "get_tournament_info", "check_parser_health"},
                  f"MCP list_tools over Streamable-HTTP = the 5 tools ({sorted(tools)})")
            check("get_players" not in tools,
                  "MCP face exposes NO player-scraping tool (no player data, ever)")

            # ── one core: both faces must agree on the same query ──
            check(mcp_ca_ids == rest_ca_ids and len(mcp_ca_ids) > 5,
                  f"one core: MCP and REST return identical CA ids "
                  f"({len(mcp_ca_ids)} each, symmetric_diff={mcp_ca_ids ^ rest_ca_ids})")

            # ── concurrency: 8 simultaneous callers, identical results, no corruption ──
            print("--- concurrency ---")

            async def hammer():
                async def one(client):
                    rr = await client.get("/events", params={"state": "California"})
                    return rr.status_code, {t["tournament_id"] for t in rr.json()}
                async with httpx.AsyncClient(base_url=BASE, timeout=90) as ac:
                    return await asyncio.gather(*[one(ac) for _ in range(8)])
            results = asyncio.run(hammer())
            codes = {c_ for c_, _ in results}
            idsets = [ids for _, ids in results]
            check(codes == {200} and all(s == idsets[0] for s in idsets),
                  f"8 concurrent /events?state=California -> all 200, identical id sets")
    finally:
        stop_server(proc)


def rate_limit_phase() -> None:
    print("\n=== rate-limit phase (fresh server, LIMIT=5/60s) ===")
    proc = start_server(rate_limit=5, rate_window=60)
    try:
        # A path that matches NO route: the limiter (pure-ASGI, wraps everything)
        # still counts it, and the app 404s fast — so we test the limit boundary
        # without 7 live scrapes of the volunteer site.
        codes, retry_after, body_ok = [], None, False
        with httpx.Client(base_url=BASE, timeout=30) as c:
            for _ in range(7):
                r = c.get("/__ratecheck__")
                codes.append(r.status_code)
                if r.status_code == 429 and retry_after is None:
                    retry_after = r.headers.get("Retry-After")
                    body_ok = "error" in r.json() and no_traceback(r.text)
        check(429 not in codes[:5],
              f"first 5 requests under the limit are NOT rate-limited (codes {codes[:5]})")
        check(codes[5] == 429 and codes[6] == 429,
              f"6th and 7th requests ARE 429 (codes {codes[5:]})")
        check(retry_after is not None and retry_after.isdigit() and int(retry_after) >= 1,
              f"429 carries a valid Retry-After header ({retry_after}s)")
        check(body_ok, "429 body is a clean JSON error (no traceback)")
    finally:
        stop_server(proc)


def main() -> None:
    if not os.path.exists("server.py"):
        die("run from the omnipong-mcp directory (server.py not found)")
    functional_phase()
    rate_limit_phase()
    print(f"\nEVAL OK — {_passes} checks passed against the live site, both faces.")


if __name__ == "__main__":
    main()
