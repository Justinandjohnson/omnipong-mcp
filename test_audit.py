# /// script
# requires-python = ">=3.10"
# ///
"""Mutation test: does audit.py actually catch a broken parser?

An audit that only ever prints OK is worthless — it looks identical to an audit
that checks nothing. So break the parser on a throwaway copy in the specific ways
omnipong could break it, and assert the audit fails. Then assert the real parser
passes, so we know it isn't just failing everything.

Each mutation asserts its own text was actually found and replaced. A mutation that
silently no-ops would "pass" while testing nothing, which is the exact failure this
test exists to prevent.

The live cache.db is copied in so every mutation reads byte-identical HTML — a
controlled experiment, not three separate scrapes of a site that may have changed
underneath us.

    uv run test_audit.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent

# (name, find, replace, what it simulates)
MUTATIONS = [
    ("column shift", '"city": _text(tds[3])', '"city": _text(tds[4])',
     "omnipong inserts a column — row count identical, every field silently wrong"),
    ("table class renamed", "table.omnipong", "table.RENAMED",
     "the listing tables stop matching the selector entirely"),
    ("row filter too strict", "if len(tds) < 6:", "if len(tds) < 7:",
     "a stricter column count silently drops all 6-column leagues and camps"),
    ("results page selector", 'h2 = page.css("h2")', 'h2 = page.css("h2.GONE")',
     "the tournament info page changes markup and get_tournament_info goes blank"),
]

# server.py mentions table.omnipong in two different parsers (the listing and the
# results/info pages). Replacing only the first hits _parse_tables and leaves the
# listing untouched, which looks like a passing audit while testing nothing.
REPLACE_ALL = True


def run_audit(workdir: Path) -> int:
    r = subprocess.run(["uv", "run", "audit.py"], cwd=workdir,
                       capture_output=True, text=True)
    return r.returncode


def staged(tmp: Path) -> Path:
    """A working copy of the server + audit, sharing the live HTML cache."""
    d = tmp / f"case{len(list(tmp.iterdir()))}"
    d.mkdir()
    for f in ("server.py", "audit.py"):
        shutil.copy(HERE / f, d / f)
    cache = HERE / "cache.db"
    if cache.exists():          # keeps every case on identical HTML, and stays polite
        shutil.copy(cache, d / "cache.db")
    return d


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        clean = staged(tmp)
        code = run_audit(clean)
        print(f"unmodified parser -> exit {code} ({'OK' if code == 0 else 'FAILED'})")
        if code != 0:
            failures.append("audit fails on the REAL parser — it is broken, or omnipong changed")

        for name, find, repl, simulates in MUTATIONS:
            d = staged(tmp)
            src = (d / "server.py").read_text()
            assert find in src, f"mutation {name!r} is stale: {find!r} not in server.py"
            (d / "server.py").write_text(src.replace(find, repl))

            code = run_audit(d)
            caught = code != 0
            print(f"{name:22s} -> exit {code} ({'caught' if caught else 'MISSED'})  # {simulates}")
            if not caught:
                failures.append(f"audit did NOT catch {name}")

    print()
    for f in failures:
        print("FAILED:", f)
    print(f"{len(MUTATIONS) + 1 - len(failures)}/{len(MUTATIONS) + 1} passed")
    return 1 if failures else 0


sys.exit(main())
