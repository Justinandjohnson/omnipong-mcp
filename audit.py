# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp[cli]>=2,<3", "scrapling[fetchers]>=0.4.12,<0.5"]
# ///
"""Parser audit CLI — is the scraper still reading omnipong correctly?

The checks live in server.check_parser_health() so agents can call them as an MCP
tool too. This is just the command-line face of it: prints the report, exits
non-zero on any finding so cron/CI notices.

    uv run audit.py
"""
import sys

import server

result = server.check_parser_health()
print("\n".join(result["report"]))
print()
if result["ok"]:
    print("AUDIT OK — selectors still match the live site, every field validates.")
    sys.exit(0)
print(f"AUDIT FAILED — {len(result['findings'])} finding(s). The parser needs "
      f"re-deriving; see SKILL.md for the page structures.")
sys.exit(1)
