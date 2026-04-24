"""Post-parse resolution passes.

The parser produces a lexically-correct AST per .idl file. These passes
turn that into a *resolved* AST — one where cross-file references have
been chased, typedefs have been substituted, mixin includes have been
flattened, and overload sets have been numbered.

The C++ tool runs all of these inline at the end of Parser::parse()
(IDLParser.cpp:1296-1457). We split them out so the parser stays a pure
(source → AST) function and the resolution passes can be tested
independently.

Pass order matters and is fixed:
  1. resolve_imports     — chase `#import <path>` directives transitively.
  2. apply_partial_*     — fold partial interface/mixin/dictionary members
                           into their primaries (where partials match).
  3. resolve_includes    — apply `A includes B;` mixin flattening.
  4. resolve_typedefs    — substitute typedef names with underlying types.
  5. number_overload_sets — give same-name operations sequential indices.

Each pass mutates the Interface in place. None of them is reversible;
callers should pass a fresh parse() result.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Optional

from .ast import CallbackFunction
from .ast import Dictionary
from .ast import Enumeration
from .ast import Interface
from .ast import Parameter
from .ast import Type
from .ast import Typedef
from .parser import Parser

# --- AK::HashTable simulation for includes ordering ---
# C++ uses an unordered HashTable for mixin includes, so the iteration
# order depends on the AK string hash and linear probing. We replicate
# this to match C++ output exactly.


def _ak_string_hash(s: str) -> int:
    """Implements AK::string_hash from AK/HashFunctions.h."""
    h = 0
    for c in s:
        h = (h + ord(c)) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    h = (h + (h << 15)) & 0xFFFFFFFF
    return h


def _ak_hashtable_order(names: list[str]) -> list[str]:
    """Return names in the order AK::HashTable<ByteString> would iterate them.

    AK::HashTable (HashTable.h) uses Robin Hood hashing with linear probing,
    70% load factor, and an initial capacity of at least 8. It grows BEFORE
    inserting each element when should_grow() is true:
        should_grow() = ((size+1)*100) >= (capacity*70)
    and doubles to max(capacity*2, 8).

    Robin Hood hashing: when inserting, if the new element's probe length
    exceeds the occupying element's probe length, steal the bucket and
    displace the poorer element to a later slot.

    Duplicate handling: C++ HashTable::set() calls should_grow() BEFORE
    checking whether the key already exists. A duplicate insertion can
    trigger a phantom rehash (growing the table) but does NOT increment
    size — the existing entry is replaced in-place (a no-op for name sets).
    """
    _GROW_CAPACITY_AT_LEAST = 8
    _GROW_AT_LOAD_FACTOR_PERCENT = 70

    capacity = 0
    # Each bucket stores (name, ideal_bucket_index) or None.
    # probe_length = current_index - ideal_index (mod capacity).
    buckets: list[tuple[str, int] | None] = []
    size = 0
    # Fast membership test to detect duplicates without scanning all buckets.
    present: set[str] = set()

    def _should_grow() -> bool:
        return ((size + 1) * 100) >= (capacity * _GROW_AT_LOAD_FACTOR_PERCENT)

    def _insert_new(name: str) -> None:
        """Insert a name known NOT to be in the table already."""
        nonlocal size
        ideal = _ak_string_hash(name) % capacity
        cur_idx = ideal
        cur_probe = 0
        inserting_name = name
        inserting_ideal = ideal

        while True:
            if buckets[cur_idx] is None:
                buckets[cur_idx] = (inserting_name, inserting_ideal)
                size += 1
                return
            occ_name, occ_ideal = buckets[cur_idx]
            occ_probe = (cur_idx - occ_ideal) % capacity
            if cur_probe > occ_probe:
                # Robin Hood steal: displace the occupying element.
                buckets[cur_idx] = (inserting_name, inserting_ideal)
                inserting_name = occ_name
                inserting_ideal = occ_ideal
                cur_probe = occ_probe
            cur_idx = (cur_idx + 1) % capacity
            cur_probe += 1

    def rehash(new_cap: int) -> None:
        nonlocal capacity, buckets, size
        old = [b for b in buckets if b is not None]
        capacity = new_cap
        buckets = [None] * capacity
        size = 0
        for entry in old:
            _insert_new(entry[0])

    for name in names:
        # should_grow() is called BEFORE the duplicate check in C++, so a
        # duplicate can still trigger a phantom rehash.
        if _should_grow():
            rehash(max(capacity * 2, _GROW_CAPACITY_AT_LEAST))
        if name not in present:
            _insert_new(name)
            present.add(name)
        # else: duplicate — should_grow() already ran above; replace is a no-op.

    return [b[0] for b in buckets if b is not None]


# --- Context and Module (new batch-mode architecture, PR #9064) ---


@dataclass
class Module:
    """One IDL file's contribution to the global context.

    Mirrors IDL::Module in the new C++ batch-mode architecture (PR #9064).
    `interface` is None for files that only contain typedefs/enums/dicts
    without a primary interface declaration.
    """

    module_own_path: str
    interface: Optional[Interface] = None


@dataclass
class Context:
    """Global registry of all parsed IDL declarations.

    Built from all IDL files at once (batch mode). Mirrors IDL::Context
    in the new C++ batch-mode architecture (PR #9064).
    """

    interfaces: dict[str, Interface] = field(default_factory=dict)
    dictionaries: dict[str, Dictionary] = field(default_factory=dict)
    enumerations: dict[str, Enumeration] = field(default_factory=dict)
    typedefs: dict[str, Typedef] = field(default_factory=dict)
    callback_functions: dict[str, CallbackFunction] = field(default_factory=dict)
    mixins: dict[str, Interface] = field(default_factory=dict)
    partial_dictionaries: dict[str, list[Dictionary]] = field(default_factory=dict)
    partial_interfaces: list[Interface] = field(default_factory=list)
    partial_mixins: list[Interface] = field(default_factory=list)
    partial_namespaces: list[Interface] = field(default_factory=list)
    included_mixins: dict[str, list[str]] = field(default_factory=dict)
    modules: list[Module] = field(default_factory=list)
    # Interface container objects for dictionary-only IDL files (no interface
    # name, but contain dictionaries/typedefs). Stored separately so typedef
    # resolution can reach their dictionaries' member types.
    dictionary_containers: list[Interface] = field(default_factory=list)


def cpp_namespace_for_module_path(module_own_path: str) -> str:
    """Derive the C++ namespace from an IDL file's absolute path.

    Mirrors cpp_namespace_for_module_path from PR #9064:
    Finds "LibWeb" in the path parts, returns the NEXT part (the sub-namespace).
    Example: .../Libraries/LibWeb/HTML/Window.idl → "HTML"
    Falls back to the parent directory name if LibWeb is not found.
    """
    parts = Path(module_own_path).parts
    for i in range(len(parts) - 2):
        if parts[i] == "LibWeb":
            return parts[i + 1]
    if len(parts) >= 2:
        return parts[-2]
    return ""


def module_will_generate_code(module: Module) -> bool:
    """Mirror IDL::module_will_generate_code from PR #9064."""
    return module.interface is not None and _will_generate_code(module.interface)


def _will_generate_code(interface: Interface) -> bool:
    """Mirror IDL::Interface::will_generate_code (Types.h:356-359)."""
    if interface.name:
        return True
    if any(d.is_original_definition for d in interface.dictionaries.values()):
        return True
    if any(e.is_original_definition for e in interface.enumerations.values()):
        return True
    return False


def build_context(interfaces: list[Interface]) -> Context:
    """Build a Context from a list of parsed (and resolved) interfaces.

    Mirrors IDL::Context construction in PR #9064: each file registers its
    top-level declarations globally so the semantic include traversal can
    find them by name.

    Each Interface corresponds to one parsed IDL file. Primary interfaces,
    mixins, partial interfaces, dictionaries, enumerations, typedefs, and
    callback functions are all extracted and stored in the Context.
    """
    ctx = Context()

    for iface in interfaces:
        # Create a Module entry for this file.
        module_own_path = iface.filename
        primary: Optional[Interface] = None

        if iface.name:
            if iface.is_partial:
                # Route partial declarations to the appropriate bucket.
                if iface.is_mixin:
                    ctx.partial_mixins.append(iface)
                elif iface.is_namespace:
                    ctx.partial_namespaces.append(iface)
                else:
                    ctx.partial_interfaces.append(iface)
            elif iface.is_mixin:
                ctx.mixins[iface.name] = iface
            else:
                ctx.interfaces[iface.name] = iface
                primary = iface

        # Dictionaries declared in this file.
        for name, dictionary in iface.dictionaries.items():
            if dictionary.is_original_definition:
                ctx.dictionaries[name] = dictionary

        # Partial dictionaries.
        for name, partials in iface.partial_dictionaries.items():
            ctx.partial_dictionaries.setdefault(name, []).extend(partials)

        # Enumerations.
        for name, enumeration in iface.enumerations.items():
            if enumeration.is_original_definition:
                ctx.enumerations[name] = enumeration

        # Typedefs.
        for name, typedef in iface.typedefs.items():
            ctx.typedefs.setdefault(name, typedef)

        # Callback functions.
        for name, callback in iface.callback_functions.items():
            ctx.callback_functions.setdefault(name, callback)

        # Includes statements → included_mixins.
        # Use a list (not a set) to preserve duplicates: C++ HashTable::set()
        # calls should_grow() before the duplicate check, so a duplicate
        # insertion from another file (e.g. WorkerNavigator.idl re-including
        # GlobalPrivacyControl for Navigator) can trigger a phantom rehash that
        # changes the bucket layout even though the logical set is unchanged.
        for target_name, mixin_name in iface.includes_statements:
            ctx.included_mixins.setdefault(target_name, []).append(mixin_name)

        # Mixins declared in this file (inline mixin declarations inside an IDL
        # that also has a primary interface).
        for name, mixin in iface.mixins.items():
            ctx.mixins.setdefault(name, mixin)

        # Partial mixin/interface/namespace declarations that are nested inside a
        # file that also has a primary interface (e.g. AnimationEvent.idl defines
        # `partial interface mixin GlobalEventHandlers { onanimationcancel; ... }`).
        for partial in iface.partial_mixins:
            ctx.partial_mixins.append(partial)
        for partial in iface.partial_interfaces:
            ctx.partial_interfaces.append(partial)
        for partial in iface.partial_namespaces:
            ctx.partial_namespaces.append(partial)

        ctx.modules.append(Module(module_own_path=module_own_path, interface=primary))

        # Track dictionary-only IDL files so typedef resolution can reach their
        # dictionary member types even though they have no interface name.
        if not iface.name and iface.dictionaries:
            ctx.dictionary_containers.append(iface)

    return ctx


def resolve_context(ctx: Context) -> None:
    """Global resolution passes for batch mode (PR #9064).

    Applies all resolution passes across ALL interfaces in the context.
    Must be called after build_context() and before generation.
    Mutates ctx.interfaces in place.
    """
    # 1. Apply all partial declarations globally.
    _apply_global_partials(ctx)

    # 2. Resolve includes (mixin flattening) for all interfaces. Must happen
    #    BEFORE typedef resolution so mixin operations are merged first, then
    #    all typedefs (including those from merged mixin operations) are resolved.
    #    Mirrors C++ Context::resolve() order: resolve_partials_and_mixins first,
    #    then resolve_typedefs (IDLParser.cpp:1457-1464).
    for iface in ctx.interfaces.values():
        _resolve_includes_with_context(iface, ctx)

    # 3. Resolve typedef aliases globally (merge ctx.typedefs into each
    #    interface first, then substitute typedef names with their target types).
    for iface in ctx.interfaces.values():
        _resolve_typedefs_with_context(iface, ctx)

    # Also resolve typedefs for dictionary-only IDL files. These have interface
    # objects with no name (so they're not in ctx.interfaces), but their
    # dictionaries' member types may reference typedef names that need resolving.
    for iface in ctx.dictionary_containers:
        _resolve_typedefs_with_context(iface, ctx)

    # 4. Compute post-parse names for all interfaces.
    for iface in ctx.interfaces.values():
        compute_post_parse_names(iface)

    # 5. Number overload sets for all interfaces.
    for iface in ctx.interfaces.values():
        number_overload_sets(iface)


def _apply_global_partials(ctx: Context) -> None:
    """Apply all partial declarations to their primaries (batch-mode pass)."""
    for partial in ctx.partial_interfaces:
        # Skip [Exposed=Nobody] partial interfaces — they are test/internal
        # stubs not exposed to any JS global scope. C++ generator omits them.
        if partial.extended_attributes.get("Exposed") == "Nobody":
            continue
        primary = ctx.interfaces.get(partial.name)
        if primary is not None:
            _extend_with_partial_interface(primary, partial)

    for partial in ctx.partial_namespaces:
        primary = ctx.interfaces.get(partial.name)
        if primary is not None and primary.is_namespace:
            _extend_with_partial_interface(primary, partial)

    for partial in ctx.partial_mixins:
        mixin = ctx.mixins.get(partial.name)
        if mixin is not None:
            _extend_with_partial_interface(mixin, partial)

    for dict_name, partials in ctx.partial_dictionaries.items():
        primary = ctx.dictionaries.get(dict_name)
        if primary is not None:
            for partial in partials:
                primary.members.extend(partial.members)


def _resolve_typedefs_with_context(interface: Interface, ctx: Context) -> None:
    """Resolve typedefs using both local and global typedef map."""
    # Build merged typedef map: global typedefs as base, local override.
    merged = dict(ctx.typedefs)
    merged.update(interface.typedefs)
    interface.typedefs = merged
    resolve_typedefs(interface)


def _resolve_includes_with_context(interface: Interface, ctx: Context) -> None:
    """Apply 'A includes B' mixin statements using the global mixin registry."""
    if not interface.has_primary_interface:
        return

    # Use the global insertion sequence from ctx.included_mixins, which
    # preserves duplicates from across all IDL files in file-sorted order.
    # This is the full sequence the C++ HashTable received, including phantom
    # duplicate insertions that can trigger rehashes.
    insertion_sequence = ctx.included_mixins.get(interface.name, [])

    # C++ uses an unordered AK::HashTable to store mixin names, so the
    # iteration order (and thus the attribute insertion order) follows the
    # hash table's bucket order, NOT the IDL declaration order. Replicate
    # this so generated code matches C++ byte-for-byte.
    ordered_mixin_names = _ak_hashtable_order(insertion_sequence)

    for mixin_name in ordered_mixin_names:
        mixin = ctx.mixins.get(mixin_name) or interface.mixins.get(mixin_name)
        if mixin is None:
            continue

        interface.attributes.extend(mixin.attributes)
        interface.constants.extend(mixin.constants)
        interface.operations.extend(mixin.operations)
        interface.static_operations.extend(mixin.static_operations)
        if mixin.has_unscopable_member:
            interface.has_unscopable_member = True
        if mixin.has_stringifier and not interface.has_stringifier:
            interface.has_stringifier = True
            interface.stringifier_attribute = mixin.stringifier_attribute
            interface.stringifier_extended_attributes = mixin.stringifier_extended_attributes


# --- pass 1: #import resolution ---


class ImportResolver:
    """Walks `#import <path>` directives and merges imported files in.

    Mirrors IDLParser.cpp:191-234 (resolve_import) and 1271-1342
    (the import-merging block at the end of Parser::parse).

    Parameters
    ----------
    import_base_paths
        List of directories to search for an imported path. Mirrors
        IDL::Parser::import_base_paths. Typically `["Libraries/LibWeb"]`.
    """

    def __init__(self, import_base_paths: list[Path], strict: bool = True):
        self.import_base_paths = list(import_base_paths)
        # When False, an unresolvable #import is dropped from `imported_modules`
        # rather than raising. Useful when running outside a build (where
        # generated .idl files like CSS/GeneratedCSSStyleProperties.idl
        # are not present on disk).
        self.strict = strict
        # `unresolved_imports` collects (importer, raw_path) tuples for
        # imports skipped under strict=False. Useful for diagnostics.
        self.unresolved_imports: list[tuple[str, str]] = []
        # Cache: real-path → resolved Interface. Mirrors
        # top_level_resolved_imports in the C++ side.
        self._resolved: dict[Path, Interface] = {}
        # In-flight set for circular-import detection.
        self._in_flight: set[Path] = set()

    def resolve_for(self, interface: Interface) -> list[Interface]:
        """Resolve every `#import <path>` recorded on `interface`.

        Returns the list of imported Interfaces (in declaration order).
        Mutates `interface.imported_modules` to absolute paths and merges
        the imported declarations into `interface` per the C++ tool's
        rules at IDLParser.cpp:1296-1342.
        """
        imports: list[Interface] = []
        # Mark this interface's file as in-flight so that any circular import
        # chain leading back to it receives an empty stub (mirroring C++, where
        # imports are resolved before the body is parsed, so a circular import
        # sees an empty interface with no enumerations/dictionaries yet).
        own_path = Path(interface.filename) if interface.filename else None
        added_to_flight = False
        if own_path and own_path not in self._in_flight and own_path not in self._resolved:
            self._in_flight.add(own_path)
            added_to_flight = True

        # `imported_modules` was populated by the parser with the raw path
        # strings from `#import <...>`. Resolve and replace.
        raw_paths = list(interface.imported_modules)
        interface.imported_modules = []

        for raw in raw_paths:
            try:
                resolved = self._resolve_path(raw, source_filename=interface.filename)
            except FileNotFoundError:
                if self.strict:
                    raise
                self.unresolved_imports.append((interface.filename, raw))
                continue
            interface.imported_modules.append(str(resolved))
            imported = self._load(resolved)
            imports.append(imported)

        # Record the full Interface objects so the include BFS can walk them.
        interface.imported_interfaces = list(imports)

        for imported in imports:
            self._merge_import(interface, imported)

        if added_to_flight:
            self._in_flight.discard(own_path)
            # Fix up stubs left by circular imports that pointed back to this
            # interface while it was in-flight.  The primary interface is not in
            # _resolved (only dependencies go there), so temporarily register it
            # so _fixup_stubs can find and replace any empty stubs with the real
            # object.
            was_in_resolved = own_path in self._resolved
            if not was_in_resolved:
                self._resolved[own_path] = interface
            for cached in list(self._resolved.values()):
                self._fixup_stubs(cached)
            if not was_in_resolved:
                del self._resolved[own_path]
        return imports

    def _resolve_path(self, raw: str, source_filename: str) -> Path:
        for base in self.import_base_paths:
            candidate = (base / raw).resolve()
            if candidate.exists():
                return candidate
        raise FileNotFoundError(f"#import: failed to find {raw!r} from {source_filename} in {self.import_base_paths}")

    def _load(self, real_path: Path) -> Interface:
        if real_path in self._resolved:
            return self._resolved[real_path]
        if real_path in self._in_flight:
            # C++ IDLParser.cpp resolves #imports BEFORE parsing the file body, so a
            # circular import sees an empty (no enums/dicts yet) interface from the
            # cache. Mimic that by returning a stub — the merge contributes nothing,
            # matching the C++ behaviour where circular imports are no-ops for enum
            # propagation.
            return Interface(filename=str(real_path))
        self._in_flight.add(real_path)
        try:
            source = real_path.read_text()
            interface = Parser(source, str(real_path)).parse()
            # Cache *before* recursing so diamond imports (A→B→C, A→C) don't
            # look circular. Mirrors IDLParser.cpp:1268
            # (`top_level_resolved_imports().set(this_module, &interface);`),
            # which seeds the cache with the current file before resolving its
            # imports.
            self._resolved[real_path] = interface
            self.resolve_for(interface)
            # Resolve typedefs on the imported interface too — codegen for
            # things like [Default] toJSON walks the parent chain and reads
            # attribute.type, which must be the underlying type (not the
            # typedef name).
            resolve_typedefs(interface)
            # Compute the post-parse names on the imported file too — codegen
            # reads `implemented_name` etc. on imports when it walks the import
            # graph for #include emission.
            compute_post_parse_names(interface)
            # Fix up any stubs in imported_interfaces that referred to files that
            # were in-flight when they were imported but have since been cached.
            self._fixup_stubs(interface)
        finally:
            self._in_flight.discard(real_path)
        return interface

    def _fixup_stubs(self, interface: Interface) -> None:
        """Replace stub Interface objects in imported_interfaces with the real
        cached object, if available.

        Stubs (name='') are created when a file is imported while it is still
        being loaded (circular import). After the loading completes the real
        interface is in _resolved, but the stub reference lingers in any
        imported_interfaces list that was frozen at stub-creation time.
        This fixup ensures the BFS in emit_includes_for_all_imports sees the
        real interface (with the correct name) so it can emit the right
        #include.
        """
        for i, imp in enumerate(interface.imported_interfaces):
            if imp.filename and not imp.name:
                real = self._resolved.get(Path(imp.filename))
                if real is not None:
                    interface.imported_interfaces[i] = real

    def _merge_import(self, target: Interface, imported: Interface) -> None:
        # Mirrors IDLParser.cpp:1296-1342. Each imported declaration kind is
        # surfaced on the importing Interface; for dictionaries/enumerations
        # the C++ side flips `is_original_definition = False` on the copy so
        # downstream codegen knows not to emit the dict/enum twice.

        # Partial interfaces matching the primary name extend the primary in-place.
        for partial in imported.partial_interfaces:
            if partial.name == target.name and target.has_primary_interface:
                _extend_with_partial_interface(target, partial)

        for name, dictionary in imported.dictionaries.items():
            # Only copy in if the target doesn't already have a local (original)
            # definition — the C++ parser processes #imports first and local
            # declarations afterward, so locals naturally win there. We mirror
            # that order-sensitivity here without changing our evaluation order.
            existing = target.dictionaries.get(name)
            if existing is not None and existing.is_original_definition:
                continue
            copy = Dictionary(
                extended_attributes=dict(dictionary.extended_attributes),
                parent_name=dictionary.parent_name,
                members=list(dictionary.members),
                is_original_definition=False,
            )
            target.dictionaries[name] = copy

        for name, partials in imported.partial_dictionaries.items():
            target.partial_dictionaries.setdefault(name, []).extend(partials)

        for name, enumeration in imported.enumerations.items():
            # Preserve the target's own original definition — only add if the
            # target doesn't already have this enum as its own declaration.
            # (Mirrors the C++ context.enumerations model where own_enumerations
            # tracks which names this interface declared; imports only ADD new
            # names, they never overwrite an is_original_definition=True entry.)
            existing = target.enumerations.get(name)
            if existing is not None and existing.is_original_definition:
                continue
            copy_enum = type(enumeration)(
                extended_attributes=dict(enumeration.extended_attributes),
                values=list(enumeration.values),
                first_member=enumeration.first_member,
                translated_cpp_names=dict(enumeration.translated_cpp_names),
                is_original_definition=False,
            )
            target.enumerations[name] = copy_enum

        # partial_namespaces: matched by name (the C++ side compares
        # namespace_class strings, but the underlying check is by name).
        for partial in imported.partial_namespaces:
            if partial.name == target.name and target.is_namespace:
                _extend_with_partial_interface(target, partial)

        # Plain typedefs and callback_functions: shallow copy.
        for name, typedef in imported.typedefs.items():
            target.typedefs.setdefault(name, typedef)
        for name, callback in imported.callback_functions.items():
            target.callback_functions.setdefault(name, callback)

        # Mixins: imported by reference (the C++ side keeps the same pointer);
        # we just record the import. Conflict detection is left to the bindings
        # generator since multiple imports of the same mixin file are common.
        for name, mixin in imported.mixins.items():
            target.mixins.setdefault(name, mixin)

        # Partial mixins from the import are immediately folded into any
        # matching mixin (C++: IDLParser.cpp:1338-1342).
        for partial in imported.partial_mixins:
            existing = target.mixins.get(partial.name)
            if existing is not None:
                _extend_with_partial_interface(existing, partial)

        # Includes statements travel with the importer too.
        for stmt in imported.includes_statements:
            if stmt not in target.includes_statements:
                target.includes_statements.append(stmt)


def _merge_extended_attrs_into_member(member, partial_extended_attributes: dict):
    """Merge partial interface extended attributes into a member copy.

    Mirrors IDLParser.cpp:extend_with_partial_interface which calls
    `function_copy.extended_attributes.update(partial.extended_attributes)`.
    Returns a shallow copy with merged extended_attributes (existing values win).
    """
    import copy

    m = copy.copy(member)
    merged = dict(partial_extended_attributes)
    merged.update(m.extended_attributes)  # member's own attrs override partial's
    m.extended_attributes = merged
    return m


def _extend_with_partial_interface(target: Interface, partial: Interface) -> None:
    """Mirror IDL::Interface::extend_with_partial_interface.

    Concatenates the partial's members onto the target. Extended attributes
    of the partial interface are merged into each copied member, mirroring the
    C++ behavior where `function_copy.extended_attributes.update(partial.extended_attributes)`.
    """
    partial_ea = partial.extended_attributes
    target.attributes.extend(_merge_extended_attrs_into_member(a, partial_ea) for a in partial.attributes)
    target.static_attributes.extend(_merge_extended_attrs_into_member(a, partial_ea) for a in partial.static_attributes)
    target.constants.extend(partial.constants)
    target.constructors.extend(_merge_extended_attrs_into_member(c, partial_ea) for c in partial.constructors)
    target.operations.extend(_merge_extended_attrs_into_member(op, partial_ea) for op in partial.operations)
    target.static_operations.extend(
        _merge_extended_attrs_into_member(op, partial_ea) for op in partial.static_operations
    )
    if partial.has_stringifier and not target.has_stringifier:
        target.has_stringifier = True
        target.stringifier_attribute = partial.stringifier_attribute
        target.stringifier_extended_attributes = partial.stringifier_extended_attributes
    if partial.value_iterator_type is not None and target.value_iterator_type is None:
        target.value_iterator_type = partial.value_iterator_type
    if partial.pair_iterator_types is not None and target.pair_iterator_types is None:
        target.pair_iterator_types = partial.pair_iterator_types
    if partial.async_value_iterator_type is not None and target.async_value_iterator_type is None:
        target.async_value_iterator_type = partial.async_value_iterator_type
        target.async_iterator_parameters = list(partial.async_iterator_parameters)
    if partial.set_entry_type is not None and target.set_entry_type is None:
        target.set_entry_type = partial.set_entry_type
        target.is_set_readonly = partial.is_set_readonly
    if partial.map_key_type is not None and target.map_key_type is None:
        target.map_key_type = partial.map_key_type
        target.map_value_type = partial.map_value_type
        target.is_map_readonly = partial.is_map_readonly
    if partial.named_property_getter is not None and target.named_property_getter is None:
        target.named_property_getter = partial.named_property_getter
    if partial.named_property_setter is not None and target.named_property_setter is None:
        target.named_property_setter = partial.named_property_setter
    if partial.named_property_deleter is not None and target.named_property_deleter is None:
        target.named_property_deleter = partial.named_property_deleter
    if partial.indexed_property_getter is not None and target.indexed_property_getter is None:
        target.indexed_property_getter = partial.indexed_property_getter
    if partial.indexed_property_setter is not None and target.indexed_property_setter is None:
        target.indexed_property_setter = partial.indexed_property_setter
    # Merge extended attributes — keep target's existing values on conflict.
    for k, v in partial.extended_attributes.items():
        target.extended_attributes.setdefault(k, v)
    # Carry over included mixins (mixins themselves are copied via _merge_import).
    for stmt in partial.includes_statements:
        if stmt not in target.includes_statements:
            target.includes_statements.append(stmt)


# --- pass 2: apply local partials ---


def apply_local_partials(interface: Interface) -> None:
    """Fold partial declarations declared in the same file into their primary.

    Mirrors the C++ block at IDLParser.cpp:1344-1348 (partial mixins) and the
    treatment of partial_interfaces / partial_namespaces during codegen.
    """
    # Local partial interfaces matching the primary name.
    matched_indices: list[int] = []
    for i, partial in enumerate(interface.partial_interfaces):
        if partial.name == interface.name and interface.has_primary_interface:
            _extend_with_partial_interface(interface, partial)
            matched_indices.append(i)
    for i in reversed(matched_indices):
        del interface.partial_interfaces[i]

    matched_indices = []
    for i, partial in enumerate(interface.partial_namespaces):
        if partial.name == interface.name and interface.is_namespace:
            _extend_with_partial_interface(interface, partial)
            matched_indices.append(i)
    for i in reversed(matched_indices):
        del interface.partial_namespaces[i]

    # Partial mixins fold into matching mixins.
    matched_indices = []
    for i, partial in enumerate(interface.partial_mixins):
        existing = interface.mixins.get(partial.name)
        if existing is not None:
            _extend_with_partial_interface(existing, partial)
            matched_indices.append(i)
    for i in reversed(matched_indices):
        del interface.partial_mixins[i]


# --- pass 3: includes-statement (mixin) resolution ---


def resolve_includes(interface: Interface) -> None:
    """Apply `Foo includes Bar;` by copying Bar's members into Foo.

    Mirrors IDLParser.cpp:1351-1373. Only includes statements naming the
    primary interface are folded; statements naming other types are left
    in place for downstream consumers.
    """
    if not interface.has_primary_interface:
        return

    for target_name, mixin_name in interface.includes_statements:
        if target_name != interface.name:
            continue
        mixin = interface.mixins.get(mixin_name)
        if mixin is None:
            # Not a hard error — cross-file mixins may be resolved at the
            # bindings-generator layer. Skip silently for now.
            continue

        interface.attributes.extend(mixin.attributes)
        interface.constants.extend(mixin.constants)
        interface.operations.extend(mixin.operations)
        interface.static_operations.extend(mixin.static_operations)
        if mixin.has_unscopable_member:
            interface.has_unscopable_member = True
        if mixin.has_stringifier:
            if interface.has_stringifier:
                raise ValueError(
                    f"Both interface '{interface.name}' and mixin '{mixin_name}' have defined stringifier attributes"
                )
            interface.has_stringifier = True
            interface.stringifier_attribute = mixin.stringifier_attribute
            interface.stringifier_extended_attributes = mixin.stringifier_extended_attributes


# --- pass 4: typedef resolution ---


def resolve_typedefs(interface: Interface) -> None:
    """Substitute typedef names in every type position with their target type.

    Mirrors IDLParser.cpp:1375-1416. Recurses through ParameterizedType
    parameters and UnionType member types so e.g. `sequence<MyTypedef>`
    becomes `sequence<UnderlyingType>`.

    Extended attributes attached to a typedef carry over to the use site
    when the use site has a matching attribute slot (parameter, attribute,
    dictionary member). Mirrors the `extended_attributes` argument of the
    C++ resolve_typedef helper.
    """
    typedefs = interface.typedefs

    def resolve_type(type_: Type, attrs: dict[str, str] | None = None) -> Type:
        if type_.kind == "parameterized":
            new_params = [resolve_type(p) for p in type_.parameters]
            result = _shallow_copy_type(type_)
            result.parameters = new_params
            return result
        if type_.kind == "union":
            new_members = [resolve_type(m) for m in type_.union_member_types]
            result = _shallow_copy_type(type_)
            result.union_member_types = new_members
            return result
        # Plain.
        td = typedefs.get(type_.name)
        if td is None or td.type is None:
            return type_
        # Mirror IDLParser.cpp:1212-1214 — the use-site nullable OVERWRITES
        # the typedef target's nullability. Counter-intuitive but matches
        # the C++ behavior: `typedef Foo? Bar; attribute Bar baz;` resolves
        # with baz.type.nullable = false. (You'd need `attribute Bar? baz;`
        # to get nullable.)
        nullable = type_.nullable
        if attrs is not None:
            for k, v in td.extended_attributes.items():
                attrs.setdefault(k, v)
        # Copy the typedef's stored Type so we don't mutate the shared cached
        # object — the shared resolver caches imported Interface objects and
        # their Type instances are referenced by many primary IDL files.
        # Mutating in place (as the C++ single-run tool does) would corrupt
        # the cache for subsequent files.
        new_type = _shallow_copy_type(td.type)
        new_type.nullable = nullable
        new_type = resolve_type(new_type, attrs)
        return new_type

    for attribute in interface.attributes:
        if attribute.type is not None:
            attribute.type = resolve_type(attribute.type, attribute.extended_attributes)
    for attribute in interface.static_attributes:
        if attribute.type is not None:
            attribute.type = resolve_type(attribute.type, attribute.extended_attributes)
    for constant in interface.constants:
        if constant.type is not None:
            constant.type = resolve_type(constant.type)
    for ctor in interface.constructors:
        _resolve_parameters(ctor.parameters, resolve_type)
    for op in interface.operations:
        if op.return_type is not None:
            op.return_type = resolve_type(op.return_type)
        _resolve_parameters(op.parameters, resolve_type)
    for op in interface.static_operations:
        if op.return_type is not None:
            op.return_type = resolve_type(op.return_type)
        _resolve_parameters(op.parameters, resolve_type)
    if interface.value_iterator_type is not None:
        interface.value_iterator_type = resolve_type(interface.value_iterator_type)
    if interface.pair_iterator_types is not None:
        a, b = interface.pair_iterator_types
        interface.pair_iterator_types = (resolve_type(a), resolve_type(b))
    if interface.async_value_iterator_type is not None:
        interface.async_value_iterator_type = resolve_type(interface.async_value_iterator_type)
    _resolve_parameters(interface.async_iterator_parameters, resolve_type)
    if interface.set_entry_type is not None:
        interface.set_entry_type = resolve_type(interface.set_entry_type)
    if interface.map_key_type is not None:
        interface.map_key_type = resolve_type(interface.map_key_type)
    if interface.map_value_type is not None:
        interface.map_value_type = resolve_type(interface.map_value_type)
    for special in (
        interface.named_property_getter,
        interface.named_property_setter,
        interface.named_property_deleter,
        interface.indexed_property_getter,
        interface.indexed_property_setter,
    ):
        if special is None:
            continue
        if special.return_type is not None:
            special.return_type = resolve_type(special.return_type)
        _resolve_parameters(special.parameters, resolve_type)
    for dictionary in interface.dictionaries.values():
        for member in dictionary.members:
            if member.type is not None:
                member.type = resolve_type(member.type, member.extended_attributes)
    for partials in interface.partial_dictionaries.values():
        for dictionary in partials:
            for member in dictionary.members:
                if member.type is not None:
                    member.type = resolve_type(member.type, member.extended_attributes)
    for callback in interface.callback_functions.values():
        if callback.return_type is not None:
            callback.return_type = resolve_type(callback.return_type)
        _resolve_parameters(callback.parameters, resolve_type)


def _resolve_parameters(parameters: list[Parameter], resolve_type) -> None:
    for parameter in parameters:
        if parameter.type is not None:
            parameter.type = resolve_type(parameter.type, parameter.extended_attributes)


def _shallow_copy_type(type_: Type) -> Type:
    return Type(
        name=type_.name,
        nullable=type_.nullable,
        kind=type_.kind,
        parameters=list(type_.parameters),
        union_member_types=list(type_.union_member_types),
    )


# --- pass 5: overload set numbering ---


def number_overload_sets(interface: Interface) -> None:
    """Assign each operation an `overload_index` and an `is_overloaded` flag.

    Mirrors IDLParser.cpp:1418-1457. Operations carrying `[FIXME]` are skipped
    (treated as not contributing to the overload set), matching the C++ check.

    `overload_index` and `is_overloaded` are stored as dynamic attributes on
    Operation/Constructor — they aren't declared on the dataclass because
    the C++ side computes them post-parse and codegen reads them by name.
    """
    _number(interface.operations)
    _number(interface.static_operations)
    _number(interface.constructors)


def _number(items: list) -> None:
    sets: dict[str, list] = OrderedDict()
    for item in items:
        # Constructors have no `name` attr; group them all under "constructor".
        name = getattr(item, "name", "") or "constructor"
        attrs = getattr(item, "extended_attributes", {})
        if "FIXME" in attrs:
            continue
        bucket = sets.setdefault(name, [])
        item.overload_index = len(bucket)
        item.is_overloaded = False
        bucket.append(item)
    for bucket in sets.values():
        if len(bucket) <= 1:
            continue
        for item in bucket:
            item.is_overloaded = True


# --- pass 6: compute post-parse name fields ---


# A subset of `Libraries/LibWeb/<dir>/` directory names whose interfaces are
# referenced as `<dir>::<name>` rather than just `<name>` in generated code.
# Mirrors the `libweb_interface_namespaces` array in
# Meta/Lagom/Tools/CodeGenerators/LibWeb/BindingsGenerator/Namespaces.h.
# Populated lazily on first use from the C++ header so the two stay in sync.
_LIBWEB_INTERFACE_NAMESPACES: tuple[str, ...] | None = None


def _libweb_interface_namespaces() -> tuple[str, ...]:
    global _LIBWEB_INTERFACE_NAMESPACES
    if _LIBWEB_INTERFACE_NAMESPACES is not None:
        return _LIBWEB_INTERFACE_NAMESPACES
    # Locate Namespaces.h relative to this file so the lookup works regardless
    # of the cwd. The file lives next to BindingsGenerator/main.cpp.
    here = Path(__file__).resolve()
    repo_root = here.parents[2]  # .../Meta/idl_bindings/resolver.py → repo root
    namespaces_h = repo_root / "Meta/Lagom/Tools/CodeGenerators/LibWeb/BindingsGenerator/Namespaces.h"
    out: list[str] = []
    if namespaces_h.exists():
        # The file is a simple list of `"Foo"sv,` entries inside an Array<>{}.
        text = namespaces_h.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('"') and line.endswith('"sv,'):
                out.append(line[1:-4])
    _LIBWEB_INTERFACE_NAMESPACES = tuple(out)
    return _LIBWEB_INTERFACE_NAMESPACES


def compute_post_parse_names(interface: Interface) -> None:
    """Set the computed name fields on `interface`.

    Mirrors the tail of IDLParser.cpp:parse_interface (lines 833-856) and
    main.cpp:60-69 (for `fully_qualified_name`). Splitting this out keeps
    the parser pure and lets the resolver run it after include flattening.

    For files with no primary interface (typedef-only files, mixin-only
    files) the C++ tool leaves these fields at their default empty string —
    parse_interface is never called. We mirror that early-return so codegen
    sees the same empty values and emits the same (empty-class-name) output.
    """
    if not interface.has_primary_interface:
        return

    # `[ImplementedAs=...]` overrides the C++ symbol name.
    interface.implemented_name = interface.extended_attributes.get("ImplementedAs", interface.name)

    # `[LegacyNamespace=Web]` produces a "Web.Foo" qualified name used in
    # interface tables and a couple of error messages.
    legacy_namespace = interface.extended_attributes.get("LegacyNamespace")
    if legacy_namespace:
        interface.namespaced_name = f"{legacy_namespace}.{interface.name}"
    else:
        interface.namespaced_name = interface.name

    # main.cpp computes fully_qualified_name by finding "LibWeb" in the path
    # and using the next component as the namespace (PR #9064 new logic).
    if interface.filename:
        ns = cpp_namespace_for_module_path(interface.filename)
        if ns:
            interface.fully_qualified_name = f"{ns}::{interface.implemented_name}"
        else:
            interface.fully_qualified_name = interface.implemented_name
    else:
        interface.fully_qualified_name = interface.implemented_name

    # Class names used as JS prototype/constructor identifiers.
    interface.constructor_class = f"{interface.implemented_name}Constructor"
    interface.prototype_class = f"{interface.implemented_name}Prototype"
    parent = interface.parent_name or "Object"
    interface.prototype_base_class = f"{parent}Prototype"
    interface.namespace_class = f"{interface.name}Namespace"
    interface.global_mixin_class = f"{interface.name}GlobalMixin"


# --- driver ---


def parse_and_resolve(source: str, filename: str, import_base_paths: list[Path]) -> Interface:
    """Convenience: parse a .idl file, run all post-parse passes, return the AST.

    `filename` is the absolute path of the source. `import_base_paths` is the
    search path for `#import <...>` resolution.
    """
    interface = Parser(source, filename).parse()
    resolver = ImportResolver(import_base_paths)
    resolver.resolve_for(interface)
    apply_local_partials(interface)
    resolve_includes(interface)
    resolve_typedefs(interface)
    number_overload_sets(interface)
    compute_post_parse_names(interface)
    return interface
