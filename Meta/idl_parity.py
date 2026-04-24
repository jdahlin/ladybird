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


def _all_idl_files(extra_paths: list[Path]) -> list[Path]:
    """Return all IDL files needed by the batch C++ generator.

    Includes files from idl_files.cmake (libweb_js_bindings, libweb_support_idl,
    and libweb_generated_support_idl entries). Generated IDL files that don't
    exist in the source tree are looked up in the worktree's own build directory
    first (to avoid picking up stale files from sibling repos).
    The new batch-mode C++ generator needs ALL IDL files at once so it can
    resolve mixins and partial interfaces globally.
    """
    cmake = REPO_ROOT / "Libraries" / "LibWeb" / "idl_files.cmake"

    # Prefer the worktree's own build directory for generated IDL files.
    # extra_paths may include build dirs from sibling repos; those can have
    # different (incompatible) generated IDL versions.
    own_build_dir = REPO_ROOT / "Build" / "release" / "Libraries" / "LibWeb"
    search_dirs = [own_build_dir] + list(extra_paths)

    result: list[Path] = []
    seen: set[Path] = set()
    for line in cmake.read_text().splitlines():
        # Accept libweb_js_bindings(...), libweb_support_idl(...), and
        # libweb_generated_support_idl(...) entries.
        m = re.match(
            r"\s*(?:libweb_js_bindings|libweb_support_idl|libweb_generated_support_idl)\(([^)]+)\)",
            line,
        )
        if m:
            rel = m.group(1) + ".idl"
            # First try the source tree.
            p = (LIBWEB_ROOT / rel).resolve()
            if not p.exists():
                # Fall back to build directories (own worktree first).
                for build_dir in search_dirs:
                    candidate = (build_dir / rel).resolve()
                    if candidate.exists():
                        p = candidate
                        break
            if p.exists() and p not in seen:
                result.append(p)
                seen.add(p)

    result.sort()
    return result


def _run_cpp_batch(all_idls: list[Path], cache_dir: Path, generator: Path) -> str | None:
    """Run C++ generator for all IDL files at once (batch mode, PR #9064).

    The new BindingsGenerator binary accepts all IDL files as positional arguments
    and generates output files into `cache_dir`. No -i flag is needed.
    ALL IDL files must be passed (not just the subset being checked) so the
    generator can resolve cross-file references (mixins, partials, etc.).
    Returns error string or None on success.
    """
    cmd = [str(generator), "-o", str(cache_dir)] + [str(idl) for idl in all_idls]
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
    """Ensure the C++ cache is up-to-date. Returns set of IDLs that failed.

    PR #9064: uses batch mode — ALL IDL files are passed to a single generator
    invocation so cross-file references (mixins, partials) resolve correctly.
    The `idls` parameter controls which files we check parity for, but the
    generator always runs with the complete set.
    """
    if not force and not _cpp_cache_stale(generator, cache_dir):
        return set()

    print(f"Regenerating C++ cache in {cache_dir} ...", file=sys.stderr)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Always run with ALL IDL files (including generated ones) so cross-file
    # resolution works correctly.
    all_idls = _all_idl_files(extra_paths)

    # Batch: run generator once with all IDL files.
    err = _run_cpp_batch(all_idls, cache_dir, generator)
    if err:
        print(f"C++ generator failed:\n{err}", file=sys.stderr)
        # We don't know which individual file failed; mark the requested set as failed.
        return set(idls)

    (cache_dir / ".generator-mtime").write_text(str(generator.stat().st_mtime))
    return set()


# ── Python in-process generation ─────────────────────────────────────────────


def _build_global_context(extra_paths: list[Path]):
    """Parse all IDL files and build a fully-resolved global Context.

    Mirrors the batch-mode architecture from PR #9064: every IDL file is parsed
    once, all declarations are registered in a global Context, and then all
    resolution passes (partials, mixins, typedefs, post-parse names, overloads)
    are applied globally.
    """
    import traceback

    from idl_bindings.parser import Parser
    from idl_bindings.resolver import build_context
    from idl_bindings.resolver import resolve_context

    all_idl_paths = _all_idl_files(extra_paths)

    interfaces = []
    parse_errors: list[str] = []
    for idl_path in all_idl_paths:
        try:
            source = idl_path.read_text()
            iface = Parser(source, str(idl_path)).parse()
            interfaces.append(iface)
        except Exception:
            parse_errors.append(f"Parse error in {idl_path}:\n{traceback.format_exc()}")

    if parse_errors:
        for err in parse_errors[:5]:
            print(err, file=sys.stderr)

    ctx = build_context(interfaces)
    resolve_context(ctx)
    return ctx


def _batch_generate_all(context, target_idls: set) -> dict:
    """Generate all modules in sorted path order, returning outputs for targets.

    Processes ALL will_generate_code() modules in the order they appear in
    context.modules so that the global dictionary counter accumulates exactly
    as in the C++ batch (IDLGenerators.cpp static `i`).

    Returns a dict mapping Path → (header, impl) | str (error).
    """
    import traceback

    from idl_bindings.emit.main import generate_header
    from idl_bindings.emit.main import generate_implementation
    from idl_bindings.emit.to_cpp import _DICTIONARY_INDEX
    from idl_bindings.resolver import module_will_generate_code

    _DICTIONARY_INDEX[0] = 0
    results = {}

    for module in context.modules:
        if not module_will_generate_code(module):
            continue
        iface = module.interface
        path = Path(module.module_own_path)
        try:
            header = generate_header(iface, context)
            impl = generate_implementation(iface, context)
        except Exception:
            err = traceback.format_exc()
            if path in target_idls:
                results[path] = err
            continue
        if path in target_idls:
            results[path] = (header, impl)

    return results


def _run_python_one(idl: Path, context) -> tuple[str, str] | str:
    """Generate header+impl for one IDL file using the global Context.

    Returns (header_text, impl_text) on success, or an error string on failure.
    NOTE: For single-file parity, the counter may not match C++ batch output.
          Use _batch_generate_all for correct batch-mode counter accumulation.
    """
    import traceback

    from idl_bindings.emit.main import generate_header
    from idl_bindings.emit.main import generate_implementation
    from idl_bindings.resolver import module_will_generate_code

    try:
        idl_str = str(idl)
        module = None
        for m in context.modules:
            if m.module_own_path == idl_str:
                module = m
                break

        if module is None:
            return f"No module found for {idl}\n"

        if not module_will_generate_code(module):
            return f"Module {idl.stem} will not generate code\n"

        iface = module.interface
        header = generate_header(iface, context)
        impl = generate_implementation(iface, context)
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


def parity_one(idl: Path, cache_dir: Path, context, verbose: bool) -> tuple[bool, list[str]]:
    """Check parity for one IDL file. Returns (passed, diff_lines)."""
    result = _run_python_one(idl, context)
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

    # Build a single global Context from all IDL files (batch mode, PR #9064).
    print("Building Python context...", file=sys.stderr)
    context = _build_global_context(extra_paths)

    idls_set = set(idls)

    if total == 1:
        # Single-file mode: still use _batch_generate_all so the dictionary
        # counter accumulates exactly as in C++ batch.
        target = idls[0]
        if target in cpp_failed:
            sys.stdout.write(f"C++ generator failed for {target} (see above)\n")
            pass_count = 0
        else:
            batch = _batch_generate_all(context, idls_set)
            result = batch.get(target)
            if result is None:
                stem = target.stem
                cpp_has_output = (cache_dir / f"{stem}.h").exists() or (cache_dir / f"{stem}.cpp").exists()
                if cpp_has_output:
                    sys.stdout.write(f"Python generator produced no output for {target}\n")
                else:
                    pass_count = 1
                    if args.verbose:
                        print(f"OK  {target}")
            elif isinstance(result, str):
                sys.stdout.write(f"Python generator failed for {target}:\n{result}")
            else:
                header, impl = result
                diff_lines = diff_outputs(target, cache_dir, header, impl)
                if diff_lines:
                    sys.stdout.writelines(diff_lines)
                else:
                    pass_count = 1
                    if args.verbose:
                        print(f"OK  {target}")
    else:
        # Multi-file mode: generate all files in one batch pass so the
        # dictionary counter (C++ static `i`) accumulates in the right order.
        batch = _batch_generate_all(context, idls_set)

        for idl in idls:
            if idl in cpp_failed:
                print(f"DIFF {idl}")
                sys.stdout.write(f"C++ generator failed for {idl} (see above)\n")
                continue

            result = batch.get(idl)
            if result is None:
                # Python produced no output. Check whether C++ also produced
                # no output (e.g. mixin-only IDL files generate nothing).
                stem = idl.stem
                cpp_has_output = (cache_dir / f"{stem}.h").exists() or (cache_dir / f"{stem}.cpp").exists()
                if cpp_has_output:
                    print(f"DIFF {idl}")
                    sys.stdout.write(f"Python generator produced no output for {idl}\n")
                else:
                    pass_count += 1
                    if args.verbose:
                        print(f"OK  {idl}")
                continue

            if isinstance(result, str):
                print(f"DIFF {idl}")
                sys.stdout.write(f"Python generator failed for {idl}:\n{result}")
                continue

            header, impl = result
            diff_lines = diff_outputs(idl, cache_dir, header, impl)
            if diff_lines:
                print(f"DIFF {idl}")
                sys.stdout.writelines(diff_lines)
            else:
                pass_count += 1
                if args.verbose:
                    print(f"OK  {idl}")

    print(f"\n{pass_count}/{total} IDL files pass parity")
    return 0 if pass_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
