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
from pathlib import Path

from .ast import Dictionary
from .ast import Interface
from .ast import Parameter
from .ast import Type
from .parser import Parser

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
            raise RuntimeError(f"Circular #import detected: {real_path}")
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
        finally:
            self._in_flight.discard(real_path)
        return interface

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
            existing = target.enumerations.get(name)
            if existing is not None and existing.is_original_definition:
                continue
            # Copy with is_original_definition=False.
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


def _extend_with_partial_interface(target: Interface, partial: Interface) -> None:
    """Mirror IDL::Interface::extend_with_partial_interface.

    Concatenates the partial's members onto the target. Extended attributes
    and stringifier state are merged conservatively.
    """
    target.attributes.extend(partial.attributes)
    target.static_attributes.extend(partial.static_attributes)
    target.constants.extend(partial.constants)
    target.constructors.extend(partial.constructors)
    target.operations.extend(partial.operations)
    target.static_operations.extend(partial.static_operations)
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
            type_.parameters = [resolve_type(p) for p in type_.parameters]
            return type_
        if type_.kind == "union":
            type_.union_member_types = [resolve_type(m) for m in type_.union_member_types]
            return type_
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
        # Mirror IDLParser.cpp:1213-1214 — share the typedef's stored Type
        # instance and overwrite its nullable flag in place. This is how the
        # C++ side behaves (NonnullRefPtr<Type const> with const_cast); the
        # last use-site to resolve a given typedef wins for the nullable bit
        # on the shared instance, which downstream codegen reads when wrapping.
        new_type = td.type
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

    # main.cpp computes fully_qualified_name from the IDL file's parent
    # directory (e.g. Libraries/LibWeb/HTML/Foo.idl → "HTML"). If that
    # directory is in the libweb_interface_namespaces list, the name is
    # qualified.
    if interface.filename:
        parts = Path(interface.filename).parts
        # Take the directory just above the file (e.g. ".../HTML/Foo.idl" → "HTML").
        directory = parts[-2] if len(parts) >= 2 else ""
        if directory in _libweb_interface_namespaces():
            interface.fully_qualified_name = f"{directory}::{interface.implemented_name}"
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
