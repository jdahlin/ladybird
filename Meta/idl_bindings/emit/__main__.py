"""CLI for the Python bindings emitter.

Mirrors the C++ tool's CLI shape (Meta/Lagom/Tools/CodeGenerators/LibWeb/
BindingsGenerator/main.cpp) so Meta/idl_parity.py can swap them with the
same arguments:

    python3 -m idl_bindings.emit -o <output-dir> <idl-file> [import-base-path...]

Generates `<basename>.h` and `<basename>.cpp` per the consolidated layout
introduced by commit fd44da6829 ("LibWeb/Bindings: Emit one bindings header
and cpp per IDL"). --depfile and --depfile-prefix accepted for CLI parity
but the depfile is currently a no-op (the parity test doesn't read it).
"""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from ..parser import Parser
from ..resolver import ImportResolver
from ..resolver import apply_local_partials
from ..resolver import compute_post_parse_names
from ..resolver import number_overload_sets
from ..resolver import resolve_includes
from ..resolver import resolve_typedefs
from .main import generate_header
from .main import generate_implementation


def _write_if_changed(path: Path, contents: str) -> None:
    """Mirror Core::File write_if_changed semantics from main.cpp:114-132.

    Only touch the file when bytes differ, so Ninja's depfile-driven
    rebuilds match the C++ behavior.
    """
    raw = contents.encode("utf-8")
    if path.exists() and path.read_bytes() == raw:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output-path", type=Path, default=Path("-"))
    ap.add_argument("-d", "--depfile", type=Path, default=None)
    ap.add_argument("-p", "--depfile-prefix", default=None)
    ap.add_argument(
        "-i",
        "--header-include-path",
        action="append",
        default=[],
        help="(accepted for CLI parity; unused)",
    )
    ap.add_argument("idl", type=Path)
    ap.add_argument("import_base_paths", nargs="*", type=Path)
    args = ap.parse_args(argv)

    idl_path = args.idl.resolve()
    bases = args.import_base_paths or [idl_path.parent]

    interface = Parser(idl_path.read_text(), str(idl_path)).parse()
    resolver = ImportResolver(bases, strict=False)
    resolver.resolve_for(interface)
    apply_local_partials(interface)
    resolve_includes(interface)
    resolve_typedefs(interface)
    number_overload_sets(interface)
    compute_post_parse_names(interface)

    basename = idl_path.stem  # e.g. "WorkletGlobalScope"
    output_dir = args.output_path

    header = generate_header(interface)
    _write_if_changed(output_dir / f"{basename}.h", header)
    try:
        impl = generate_implementation(interface)
    except NotImplementedError:
        # Concept-ladder is still climbing — emit only the .h until the .cpp
        # path lands. The parity script will surface the missing file as a
        # "(only in C++)" diff.
        return 0
    _write_if_changed(output_dir / f"{basename}.cpp", impl)

    return 0


if __name__ == "__main__":
    sys.exit(main())
