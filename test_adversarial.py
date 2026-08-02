# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=2,<3"]
# ///
"""Adversarial pass: hostile inputs, concurrency, and hostile environment."""
import asyncio, json, sys, time
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = sys.argv[1]
results = []


def rec(name, ok, detail):
    results.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name}: {detail}")


async def call(s, tool, args):
    """Return ('ok', payload) or ('err', message) — never raise."""
    try:
        r = await s.call_tool(tool, args)
        if getattr(r, "isError", False):
            return "err", "".join(getattr(c, "text", "") for c in r.content)[:200]
        try:
            return "ok", [json.loads(c.text) for c in r.content] if r.content else []
        except json.JSONDecodeError:
            return "err", "".join(getattr(c, "text", "") for c in r.content)[:200]
    except Exception as e:
        return "err", f"{type(e).__name__}: {e}"[:200]


def clean_error(msg: str) -> bool:
    """An error a caller can act on — not a leaked internal exception."""
    m = str(msg)
    leaked = ("KeyError", "OverflowError", "Traceback", "sqlite3.", "AttributeError",
              "IndexError", "TypeError:")
    return not any(x in m for x in leaked)


async def main():
    params = StdioServerParameters(command="uv", args=["run", SERVER])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            # 1. garbage event_type -> clean, actionable error
            k, v = await call(s, "list_tournaments", {"event_type": "'; DROP TABLE seen;--"})
            rec("garbage event_type", k == "err" and clean_error(v) and "event_type" in str(v),
                f"{k}: {str(v)[:90]}")

            # 2. junk ids must be REJECTED, not forwarded to omnipong as requests
            for tid in (0, -5):
                k, v = await call(s, "get_results", {"tournament_id": tid})
                rec(f"get_results({tid}) rejected without fetching",
                    k == "err" and clean_error(v), f"{k}: {str(v)[:80]}")

            # a well-formed but unused id is a legitimate lookup: empty, not a crash
            k, v = await call(s, "get_results", {"tournament_id": 999999999})
            rec("get_results(unused id) -> empty", k == "ok", f"{k}: {str(v)[:70]}")

            # 3. SQL injection via state filter (whats_new builds SQL)
            k, v = await call(s, "whats_new", {"state": "' OR 1=1--", "open_only": False})
            rec("SQLi in whats_new.state", k == "ok" and v == [], f"{k}: {str(v)[:60]}")

            k, v = await call(s, "whats_new", {"event_type": "x'; DROP TABLE seen;--"})
            rec("SQLi in whats_new.event_type", k == "ok", f"{k}: {str(v)[:60]}")

            # 4. absurd numerics
            for d in (-1, 0, 10**9, 10**18):
                k, v = await call(s, "whats_new", {"days": d})
                rec(f"whats_new(days={d})", k == "ok" or clean_error(v),
                    f"{k}: {len(v) if k=='ok' else v}")

            # 5. type confusion — string where int expected
            k, v = await call(s, "get_results", {"tournament_id": "abc"})
            rec("get_results(str id) rejected", k == "err" and clean_error(v), f"{k}: {str(v)[:70]}")

            # 6. oversized / unicode input
            k, v = await call(s, "list_tournaments", {"keyword": "𝕏" * 5000})
            rec("5k-char unicode keyword", k == "ok" and v == [], f"{k}: {len(v) if k=='ok' else v}")

            # 7. path traversal flavored input
            k, v = await call(s, "list_tournaments", {"year": "../../etc/passwd"})
            rec("traversal in year", k == "ok" or "Traceback" not in str(v), f"{k}: {str(v)[:70]}")

            # 8. concurrency: 8 simultaneous calls, shared sqlite
            t0 = time.time()
            out = await asyncio.gather(*[call(s, "list_tournaments", {"state": "California"})
                                        for _ in range(8)])
            oks = sum(1 for k, _ in out if k == "ok")
            counts = {len(v) for k, v in out if k == "ok"}
            rec("8 concurrent calls", oks == 8 and len(counts) == 1,
                f"{oks}/8 ok, identical counts={counts}, {time.time()-t0:.1f}s")

    print("\n" + "=" * 60)
    bad = [r for r in results if not r[0]]
    print(f"{len(results)-len(bad)}/{len(results)} passed")
    for _, n, d in bad:
        print(f"  FAILED: {n} -> {d}")
    return 1 if bad else 0


sys.exit(asyncio.run(main()))
