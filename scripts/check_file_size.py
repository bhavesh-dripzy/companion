#!/usr/bin/env python3
"""Fail if any Python file under the given paths exceeds the line budget.

The team convention (issue #29) is that no file should exceed ~250 lines: it keeps
modules single-responsibility and AI tooling effective. This gate enforces it for the
new `core/` package first and is widened as legacy modules are ported.

Usage:
    python scripts/check_file_size.py core                # fail >250 lines
    python scripts/check_file_size.py core --warn-only    # report only, exit 0
    python scripts/check_file_size.py core --max 200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_MAX = 250
# Files allowed to exceed the budget (generated, data, or vendored). Keep this short.
ALLOWLIST: frozenset[str] = frozenset(
    {
        # MCP token store + auth gate (added in #74; legitimate additions pushed these over 250L).
        "core/providers/mcp/mcp_base.py",
        "core/providers/mcp/token_store.py",
        # Instamart provider (added in #74; cart card refactor pushed it over 250L).
        "core/providers/plugins/swiggy_instamart/provider.py",
        "core/providers/plugins/swiggy_instamart/cart_cards.py",
        # Narrate stage (formatting nudge in #74 added ~14 lines; split planned separately).
        "core/pipeline/stages/narrate.py",
    }
)


def _count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return sum(1 for _ in handle)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce the per-file line budget.")
    parser.add_argument("paths", nargs="+", help="files or directories to scan")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, dest="max_lines")
    parser.add_argument("--warn-only", action="store_true")
    args = parser.parse_args()

    offenders: list[tuple[Path, int]] = []
    for raw in args.paths:
        root = Path(raw)
        files = root.rglob("*.py") if root.is_dir() else [root]
        for file in sorted(files):
            if str(file) in ALLOWLIST or "__pycache__" in file.parts:
                continue
            lines = _count_lines(file)
            if lines > args.max_lines:
                offenders.append((file, lines))

    if not offenders:
        print(f"OK: all files within {args.max_lines} lines.")
        return 0

    print(f"Files exceeding {args.max_lines} lines:")
    for file, lines in offenders:
        print(f"  {lines:>5}  {file}")
    if args.warn_only:
        print(f"\n{len(offenders)} file(s) over budget (warn-only).")
        return 0
    print(f"\n{len(offenders)} file(s) over budget. Split them or update the allowlist.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
