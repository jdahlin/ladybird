"""Parity harness — run the C++ and Python bindings generators on the same
.idl file and diff their outputs.

Phase 1 of the rewrite plan
(/home/jdahlin/.claude/plans/how-can-we-rewrite-snoopy-pebble.md). For one
or many IDL files, runs both generators in two tmpdirs, then walks both
trees and byte-compares each emitted file. Exit code 0 iff every file in
scope is byte-identical; non-zero with a unified diff otherwise.

Usage:
    Meta/idl_parity.py --idl Libraries/LibWeb/HTML/WorkletGlobalScope.idl
    Meta/idl_parity.py --allowlist Meta/python_bindings_allowlist.txt
    Meta/idl_parity.py --all                    # everything in idl_files.cmake

The C++ tool is located via $BINDINGS_GENERATOR (env var) or by searching
the standard build directories. The Python tool is invoked as
`python3 -m idl_bindings.emit <args...>`.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import subprocess
import sys
import tempfile

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBWEB_ROOT = REPO_ROOT / "Libraries" / "LibWeb"


def find_cpp_generator() -> Path:
    if env := os.environ.get("BINDINGS_GENERATOR"):
        return Path(env)
    candidates = [
        REPO_ROOT / "Build/release/bin/BindingsGenerator",
        REPO_ROOT / "Build/release/Lagom/bin/BindingsGenerator",
        REPO_ROOT / "Build/lagom/bin/BindingsGenerator",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise RuntimeError(
        "Could not find a built BindingsGenerator binary. "
        "Set $BINDINGS_GENERATOR or build via `Meta/ladybird.py build BindingsGenerator`."
    )


def find_generated_idl_dirs() -> list[Path]:
    """Find build directories that contain generated IDL files (e.g. GeneratedCSSStyleProperties.idl)."""
    if env := os.environ.get("GENERATED_IDL_DIR"):
        return [Path(env)]
    generated_idl = "CSS/GeneratedCSSStyleProperties.idl"
    # Collect candidate roots: the repo itself + repos in sibling directories up to 6 levels up
    roots: set[Path] = {REPO_ROOT}
    ancestor = REPO_ROOT
    for _ in range(6):
        ancestor = ancestor.parent
        if not ancestor.is_dir():
            break
        try:
            siblings = list(ancestor.iterdir())
        except (PermissionError, OSError):
            break
        for sibling in siblings:
            try:
                if sibling.is_dir() and (sibling / "Build").is_dir():
                    roots.add(sibling)
            except (PermissionError, OSError):
                pass
    found = []
    for root in sorted(roots):
        try:
            for build_dir in sorted((root / "Build").glob("*/Libraries/LibWeb")):
                if (build_dir / generated_idl).is_file():
                    found.append(build_dir)
                    break  # One per root is enough
        except (PermissionError, OSError):
            pass
    return found


def run_cpp(idl: Path, output_dir: Path, generator: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    extra_paths = find_generated_idl_dirs()
    cmd = [str(generator), "-o", str(output_dir), str(idl), str(LIBWEB_ROOT)]
    for p in extra_paths:
        cmd.append(str(p))
    subprocess.run(
        cmd,
        check=True,
        capture_output=True,
    )


def run_python(idl: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    extra_paths = find_generated_idl_dirs()
    cmd = [
        sys.executable,
        "-m",
        "idl_bindings.emit",
        "-o",
        str(output_dir),
        str(idl),
        str(LIBWEB_ROOT),
    ]
    for p in extra_paths:
        cmd.append(str(p))
    subprocess.run(
        cmd,
        cwd=str(REPO_ROOT / "Meta"),
        check=True,
        capture_output=True,
    )


def diff_dirs(cpp_dir: Path, py_dir: Path, label: str) -> list[str]:
    """Return a list of unified-diff lines (empty iff byte-identical).

    Surfaces files-only-in-one-side as well as differing contents.
    """
    cpp_files = {p.name: p for p in cpp_dir.iterdir() if p.is_file()}
    py_files = {p.name: p for p in py_dir.iterdir() if p.is_file()}

    output: list[str] = []
    for name in sorted(cpp_files.keys() | py_files.keys()):
        cpp = cpp_files.get(name)
        py = py_files.get(name)
        if cpp is None:
            output.append(f"--- (only in Python) {name} ---")
            output.append(py.read_text())  # type: ignore[union-attr]
            continue
        if py is None:
            output.append(f"--- (only in C++) {name} ---")
            output.append(cpp.read_text())
            continue
        cpp_bytes = cpp.read_bytes()
        py_bytes = py.read_bytes()
        if cpp_bytes == py_bytes:
            continue
        cpp_lines = cpp_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        py_lines = py_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)
        output.extend(
            difflib.unified_diff(
                cpp_lines,
                py_lines,
                fromfile=f"cpp/{name}",
                tofile=f"py/{name}",
                n=3,
            )
        )
    return output


def parity_one(idl: Path, generator: Path, verbose: bool = False) -> bool:
    with tempfile.TemporaryDirectory(prefix="idl-parity-") as tmpdir:
        cpp_dir = Path(tmpdir) / "cpp"
        py_dir = Path(tmpdir) / "py"
        try:
            run_cpp(idl, cpp_dir, generator)
        except subprocess.CalledProcessError as exc:
            print(f"C++ generator failed for {idl}:", file=sys.stderr)
            print(exc.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
            return False
        try:
            run_python(idl, py_dir)
        except subprocess.CalledProcessError as exc:
            print(f"Python generator failed for {idl}:", file=sys.stderr)
            print(exc.stderr.decode("utf-8", errors="replace"), file=sys.stderr)
            return False

        diff_lines = diff_dirs(cpp_dir, py_dir, idl.name)
        if not diff_lines:
            if verbose:
                print(f"OK  {idl}")
            return True
        print(f"DIFF {idl}")
        sys.stdout.writelines(diff_lines)
        if not diff_lines or not diff_lines[-1].endswith("\n"):
            print()
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--idl", type=Path, help="parity-check one IDL file")
    sel.add_argument(
        "--allowlist",
        type=Path,
        help="parity-check every IDL listed in this file (one path per line)",
    )
    sel.add_argument(
        "--all",
        action="store_true",
        help="parity-check every IDL in Libraries/LibWeb (slow, mostly for progress tracking)",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    generator = find_cpp_generator()

    if args.idl:
        idls: list[Path] = [args.idl.resolve()]
    elif args.allowlist:
        idls = []
        for line in args.allowlist.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            idls.append((LIBWEB_ROOT / line).resolve() if not Path(line).is_absolute() else Path(line))
    else:  # --all — use idl_files.cmake as the authoritative list
        cmake = REPO_ROOT / "Libraries" / "LibWeb" / "idl_files.cmake"
        idls = []
        for line in cmake.read_text().splitlines():
            m = re.match(r"\s*libweb_js_bindings\(([^)]+)\)", line)
            if m:
                idls.append((LIBWEB_ROOT / (m.group(1) + ".idl")).resolve())
        idls.sort()

    pass_count = 0
    for idl in idls:
        if parity_one(idl, generator, verbose=args.verbose):
            pass_count += 1

    total = len(idls)
    print(f"\n{pass_count}/{total} IDL files pass parity")
    return 0 if pass_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
