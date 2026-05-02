"""Deprecated thin shim — use `wordforge review` instead.

All logic moved to src/wordforge/reviewer/ and src/wordforge/cli.py:review_cmd.
Preserved only so operators with `uv run python scripts/review_and_fix.py ...`
in runbooks or cron continue to work. Argv is forwarded verbatim to
`wordforge review`.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    # Rewrite argv so typer sees the subcommand name.
    os.execvp("uv", ["uv", "run", "wordforge", "review", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
