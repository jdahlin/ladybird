"""Parity harness — diff Python bindings generator output against C++.

Usage:
    Meta/idl_parity.py --idl Libraries/LibWeb/HTML/WorkletGlobalScope.idl
    Meta/idl_parity.py --allowlist Meta/python_bindings_allowlist.txt
    Meta/idl_parity.py --all                    # everything in idl_files.cmake

C++ reference outputs are cached in Build/release/idl-parity-cache/ and
regenerated automatically when the BindingsGenerator binary is newer than
the cache. Pass --regen-cpp to force regeneration.

Python generation runs in-process (no subprocess startup overhead) and is
parallelized across all IDL files with a thread pool.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import subprocess
import sys

from concurrent.futures import ThreadPoolExecutor  # used for C++ cache regen only
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBWEB_ROOT = REPO_ROOT / "Libraries" / "LibWeb"

# Default cache location for C++ generator outputs.
CPP_CACHE_DIR = REPO_ROOT / "Build" / "release" / "idl-parity-cache"


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
    """Find build directories containing generated IDL files."""
    if env := os.environ.get("GENERATED_IDL_DIR"):
        return [Path(env)]
    generated_idl = "CSS/GeneratedCSSStyleProperties.idl"
    roots: set[Path] = {REPO_ROOT}
    ancestor = REPO_ROOT
    for _ in range(6):
        ancestor = ancestor.parent
        if not ancestor.is_dir():
            break
        try:
            for sibling in ancestor.iterdir():
                if sibling.is_dir() and (sibling / "Build").is_dir():
                    roots.add(sibling)
        except (PermissionError, OSError):
            break
    found = []
    for root in sorted(roots):
        try:
            for build_dir in sorted((root / "Build").glob("*/Libraries/LibWeb")):
                if (build_dir / generated_idl).is_file():
                    found.append(build_dir)
                    break
        except (PermissionError, OSError):
            pass
    return found


# ── C++ cache ────────────────────────────────────────────────────────────────


def _cpp_cache_stale(generator: Path, cache_dir: Path) -> bool:
    """Return True if the C++ cache needs to be regenerated."""
    marker = cache_dir / ".generator-mtime"
    if not marker.exists():
        return True
    try:
        return generator.stat().st_mtime > float(marker.read_text())
    except (OSError, ValueError):
        return True


def _run_cpp_one(idl: Path, cache_dir: Path, generator: Path, extra_paths: list[Path]) -> str | None:
    """Run C++ generator for one IDL file into cache_dir. Returns error string or None."""
    cmd = [str(generator), "-o", str(cache_dir), str(idl), str(LIBWEB_ROOT)]
    for p in extra_paths:
        cmd.append(str(p))
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        return e.stderr.decode("utf-8", errors="replace")
    return None


def ensure_cpp_cache(
    idls: list[Path],
    generator: Path,
    extra_paths: list[Path],
    cache_dir: Path,
    force: bool = False,
    jobs: int = 4,
) -> set[Path]:
    """Ensure the C++ cache is up-to-date. Returns set of IDLs that failed."""
    if not force and not _cpp_cache_stale(generator, cache_dir):
        return set()

    print(f"Regenerating C++ cache in {cache_dir} ...", file=sys.stderr)
    cache_dir.mkdir(parents=True, exist_ok=True)

    failed: set[Path] = set()

    def work(idl: Path) -> tuple[Path, str | None]:
        return idl, _run_cpp_one(idl, cache_dir, generator, extra_paths)

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        for idl, err in pool.map(work, idls):
            if err:
                print(f"C++ generator failed for {idl}:\n{err}", file=sys.stderr)
                failed.add(idl)

    if not failed:
        (cache_dir / ".generator-mtime").write_text(str(generator.stat().st_mtime))

    return failed


# ── Python in-process generation ─────────────────────────────────────────────


def _make_shared_resolver(extra_paths: list[Path]):
    """Create a shared ImportResolver that caches parsed imports across all IDL files."""
    from idl_bindings.resolver import ImportResolver

    bases = [LIBWEB_ROOT] + list(extra_paths)
    return ImportResolver(bases, strict=False)


def _run_python_one(idl: Path, resolver) -> tuple[str, str] | str:
    """Generate header+impl for one IDL file in-process using a shared resolver.

    Returns (header_text, impl_text) on success, or an error string on failure.
    """
    import traceback

    from idl_bindings.emit.main import generate_header
    from idl_bindings.emit.main import generate_implementation
    from idl_bindings.parser import Parser
    from idl_bindings.resolver import apply_local_partials
    from idl_bindings.resolver import compute_post_parse_names
    from idl_bindings.resolver import number_overload_sets
    from idl_bindings.resolver import resolve_includes
    from idl_bindings.resolver import resolve_typedefs

    try:
        source = idl.read_text()
        interface = Parser(source, str(idl)).parse()
        resolver.resolve_for(interface)
        apply_local_partials(interface)
        resolve_includes(interface)
        resolve_typedefs(interface)
        compute_post_parse_names(interface)
        number_overload_sets(interface)

        header = generate_header(interface)
        impl = generate_implementation(interface)
        return header, impl
    except Exception:
        return traceback.format_exc()


# ── Diff ─────────────────────────────────────────────────────────────────────


def diff_outputs(
    idl: Path,
    cache_dir: Path,
    py_header: str,
    py_impl: str,
) -> list[str]:
    """Diff Python output against cached C++ output for one IDL file."""
    stem = idl.stem
    pairs = [
        (cache_dir / f"{stem}.h", py_header, f"{stem}.h"),
        (cache_dir / f"{stem}.cpp", py_impl, f"{stem}.cpp"),
    ]
    output: list[str] = []
    for cpp_path, py_text, name in pairs:
        if not cpp_path.exists():
            output.append(f"--- (only in Python) {name}\n")
            output.extend(f"+{line}\n" for line in py_text.splitlines())
            continue
        cpp_text = cpp_path.read_text(errors="replace")
        if cpp_text == py_text:
            continue
        output.extend(
            difflib.unified_diff(
                cpp_text.splitlines(keepends=True),
                py_text.splitlines(keepends=True),
                fromfile=f"cpp/{name}",
                tofile=f"py/{name}",
                n=3,
            )
        )
    return output


# ── Per-file work ─────────────────────────────────────────────────────────────


def parity_one(idl: Path, cache_dir: Path, resolver, verbose: bool) -> tuple[bool, list[str]]:
    """Check parity for one IDL file. Returns (passed, diff_lines)."""
    result = _run_python_one(idl, resolver)
    if isinstance(result, str):
        return False, [f"Python generator failed for {idl}:\n{result}"]

    header, impl = result
    diff_lines = diff_outputs(idl, cache_dir, header, impl)
    return not diff_lines, diff_lines


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--idl", type=Path, help="parity-check one IDL file")
    sel.add_argument("--allowlist", type=Path, help="parity-check every IDL listed in this file")
    sel.add_argument("--all", action="store_true", help="parity-check every IDL in idl_files.cmake")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--regen-cpp", action="store_true", help="force regeneration of C++ cache")
    ap.add_argument(
        "--cpp-cache",
        type=Path,
        default=CPP_CACHE_DIR,
        help="directory for cached C++ outputs",
    )
    args = ap.parse_args()

    generator = find_cpp_generator()
    extra_paths = find_generated_idl_dirs()
    cache_dir: Path = args.cpp_cache

    if args.idl:
        idls: list[Path] = [args.idl.resolve()]
    elif args.allowlist:
        idls = []
        for line in args.allowlist.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = Path(line)
            idls.append((LIBWEB_ROOT / p).resolve() if not p.is_absolute() else p)
    else:
        cmake = REPO_ROOT / "Libraries" / "LibWeb" / "idl_files.cmake"
        idls = []
        for line in cmake.read_text().splitlines():
            m = re.match(r"\s*libweb_js_bindings\(([^)]+)\)", line)
            if m:
                idls.append((LIBWEB_ROOT / (m.group(1) + ".idl")).resolve())
        idls.sort()

    # Ensure C++ cache is populated.
    cpp_failed = ensure_cpp_cache(idls, generator, extra_paths, cache_dir, force=args.regen_cpp, jobs=args.jobs)

    pass_count = 0
    total = len(idls)

    sys.path.insert(0, str(REPO_ROOT / "Meta"))

    # One shared resolver so transitive imports are parsed only once.
    resolver = _make_shared_resolver(extra_paths)

    def work(idl: Path) -> tuple[Path, bool, list[str]]:
        if idl in cpp_failed:
            return idl, False, [f"C++ generator failed for {idl} (see above)\n"]
        ok, lines = parity_one(idl, cache_dir, resolver, args.verbose)
        return idl, ok, lines

    if total == 1:
        _, ok, lines = work(idls[0])
        if ok:
            pass_count = 1
            if args.verbose:
                print(f"OK  {idls[0]}")
        else:
            sys.stdout.writelines(lines)
    else:
        # Sequential — shared resolver is not thread-safe.
        for idl, ok, lines in map(work, idls):
            if ok:
                pass_count += 1
                if args.verbose:
                    print(f"OK  {idl}")
            else:
                print(f"DIFF {idl}")
                sys.stdout.writelines(lines)

    print(f"\n{pass_count}/{total} IDL files pass parity")
    return 0 if pass_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
