# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=2,<3"]
# ///
"""Real smoke test: spawns server.py over stdio, calls every tool against live omnipong.com."""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    params = StdioServerParameters(command="uv", args=["run", "server.py"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", tools)
            assert set(tools) == {"list_tournaments", "whats_new", "get_results", "get_tournament_info"}
            assert "get_players" not in tools, "player scraping must stay removed"

            r = await s.call_tool("list_tournaments", {"state": "California"})
            ca = [json.loads(c.text) for c in r.content]
            print(f"CA tournaments: {len(ca)}")
            assert len(ca) > 5, ca
            assert all(t["state_section"].strip() == "California" for t in ca)
            sample = next(t for t in ca if t["tournament_id"])
            print("sample:", json.dumps(sample, indent=1))

            open_t = next((t for t in ca if t["status"] == "Enter"), sample)

            done = next(t for t in ca if t["status"] == "Results" and t["tournament_id"])
            r = await s.call_tool("get_results", {"tournament_id": done["tournament_id"]})
            results = json.loads(r.content[0].text)
            print(f"results page '{results['title']}': {sum(len(t) for t in results['tables'])} rows")

            r = await s.call_tool("get_tournament_info", {"tournament_id": open_t["tournament_id"]})
            info = json.loads(r.content[0].text)
            assert info["tournament_name"] and info["details"], info
            print(f"info: {info['tournament_name'][:40]!r} | {info['details'][0]}")

            # leagues and camps use 6-column rows; a tournament-only parser misses them
            for et, floor in (("leagues", 20), ("camps", 5)):
                r = await s.call_tool("list_tournaments", {"event_type": et})
                rows = [json.loads(c.text) for c in r.content]
                assert len(rows) >= floor and all(x["tournament_id"] for x in rows), et
                print(f"{et}: {len(rows)} rows")

            # completeness: national listing covers many states, every row has the core fields
            r = await s.call_tool("list_tournaments", {})
            all_t = [json.loads(c.text) for c in r.content]
            states = {t["state_section"] for t in all_t}
            print(f"national: {len(all_t)} tournaments across {len(states)} sections")
            assert len(states) > 20, states
            missing = [t for t in all_t if not (t["name"] and t["date"] and t["status"])]
            assert not missing, missing[:3]
            assert sum(1 for t in all_t if not t["tournament_id"]) == 0, "rows without ids"

            # a finished tournament must yield real placements
            r = await s.call_tool("get_results", {"tournament_id": 1277})
            res = json.loads(r.content[0].text)
            rows = [row for tbl in res["tables"] for row in tbl]
            assert len(rows) >= 5 and any("First Place" in row for row in rows), rows[:2]
            print(f"known-good results (id 1277): {len(rows)} events, winner={rows[0]['First Place']}")

            # whats_new must be backed by a real, populated baseline — not an empty table
            # that would let every assertion pass vacuously.
            import sqlite3
            con = sqlite3.connect("cache.db")
            seen = con.execute("SELECT COUNT(*), SUM(first_seen='seed') FROM seen").fetchone()
            con.close()
            assert seen[0] >= 200, f"baseline too small to trust: {seen[0]} rows"
            print(f"baseline: {seen[0]} events tracked ({seen[1]} seeded)")

            r = await s.call_tool("whats_new", {"days": 30})
            new = [json.loads(c.text) for c in r.content] if r.content else []
            print(f"whats_new(30d, open only): {len(new)} events")
            # every reported event must exist in the live listing right now
            live = {t["tournament_id"] for t in all_t}
            for e in new:
                print(f"  [{e['event_type']}] {e['name'][:45]} | {e['city']} | {e['date']}")
                assert e["status"] == "Enter", f"open_only leaked {e['status']}"
                if e["event_type"] == "tournaments":
                    assert e["tournament_id"] in live, f"reported event not on the live site: {e}"

            print("SMOKE OK")


sys.exit(asyncio.run(main()))
