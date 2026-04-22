"""CLI entry point: parse an .idl file and dump its AST.

Usage:
    python3 -m idl_bindings <path/to/file.idl>            # JSON, fully resolved
    python3 -m idl_bindings <path/to/file.idl> --no-resolve
    python3 -m idl_bindings <path/to/file.idl> --import-base Libraries/LibWeb
    python3 -m idl_bindings <path/to/file.idl> --strict-imports

The output is the same shape that `Lagom::BindingsGenerator --emit-ir-json`
will eventually produce (per Phase 1 of the rewrite plan), so the same JSON
diff tool works on either side.
"""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from . import ir_json
from .parser import Parser
from .resolver import ImportResolver
from .resolver import apply_local_partials
from .resolver import number_overload_sets
from .resolver import resolve_includes
from .resolver import resolve_typedefs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("idl", type=Path, help="path to a .idl file")
    ap.add_argument(
        "--import-base",
        action="append",
        type=Path,
        default=[],
        help="search path for #import resolution (may be repeated; defaults to the IDL file's parent's parent)",
    )
    ap.add_argument(
        "--no-resolve",
        action="store_true",
        help="skip post-parse passes; emit the raw parser output",
    )
    ap.add_argument(
        "--strict-imports",
        action="store_true",
        help="fail on unresolvable #import (default: skip and continue)",
    )
    ap.add_argument(
        "--compact",
        action="store_true",
        help="emit one line of JSON instead of pretty-printed",
    )
    args = ap.parse_args(argv)

    source_path = args.idl.resolve()
    interface = Parser(source_path.read_text(), str(source_path)).parse()

    if not args.no_resolve:
        bases = args.import_base or [source_path.parent.parent]
        resolver = ImportResolver(bases, strict=args.strict_imports)
        resolver.resolve_for(interface)
        apply_local_partials(interface)
        resolve_includes(interface)
        resolve_typedefs(interface)
        number_overload_sets(interface)

    indent = None if args.compact else 2
    sys.stdout.write(ir_json.dumps(interface, indent=indent))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
