"""Corpus runner for the Python IDL parser and resolver.

Walks Libraries/LibWeb/**/*.idl, parses each file, optionally runs the
post-parse resolution passes (#import, partials, includes, typedefs,
overload-set numbering), and reports failures.

Usage:
    python3 -m idl_bindings._corpus            # parse only
    python3 -m idl_bindings._corpus --resolve  # parse + run all resolver passes
    python3 -m idl_bindings._corpus -v         # also list passing files
    python3 -m idl_bindings._corpus -k <pat>   # only files whose path contains <pat>
"""

from __future__ import annotations

import argparse
import sys
import traceback

from collections import Counter
from pathlib import Path

from .parser import ParseError
from .parser import Parser
from .resolver import ImportResolver
from .resolver import apply_local_partials
from .resolver import number_overload_sets
from .resolver import resolve_includes
from .resolver import resolve_typedefs


def find_idl_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.idl"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="Libraries/LibWeb", help="root to walk")
    ap.add_argument("--resolve", action="store_true", help="run all post-parse passes")
    ap.add_argument(
        "--strict-imports",
        action="store_true",
        help="fail on unresolvable #import (default: skip and continue)",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-k", "--keyword", default="", help="filter by substring of path")
    ap.add_argument("--max-fail-detail", type=int, default=20)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    files = find_idl_files(root)
    if args.keyword:
        files = [f for f in files if args.keyword in str(f)]

    failures: list[tuple[Path, str]] = []
    for path in files:
        try:
            src = path.read_text()
            interface = Parser(src, str(path)).parse()
            if args.resolve:
                resolver = ImportResolver([root], strict=args.strict_imports)
                resolver.resolve_for(interface)
                apply_local_partials(interface)
                resolve_includes(interface)
                resolve_typedefs(interface)
                number_overload_sets(interface)
            if args.verbose:
                print(f"OK  {path}")
        except ParseError as exc:
            failures.append((path, str(exc)))
        except Exception:
            failures.append((path, traceback.format_exc()))

    print()
    n_total = len(files)
    n_pass = n_total - len(failures)
    print(f"Passed {n_pass}/{n_total}")

    if failures:
        # Group failures by message head (first 80 chars of last line) so we
        # can spot patterns quickly.
        buckets: Counter[str] = Counter()
        for _path, msg in failures:
            head = msg.strip().splitlines()[-1][:120]
            buckets[head] += 1
        print()
        print("Failure clusters (top 15):")
        for head, count in buckets.most_common(15):
            print(f"  {count:4d}  {head}")

        print()
        print(f"First {min(args.max_fail_detail, len(failures))} failures:")
        for path, msg in failures[: args.max_fail_detail]:
            last_line = msg.strip().splitlines()[-1]
            print(f"  {path}: {last_line}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
