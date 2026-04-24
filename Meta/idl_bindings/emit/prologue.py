"""Implementation file prologue — port of generate_implementation_prologue
(IDLGenerators.cpp:6122-6195) and emit_includes_for_all_dependencies
(new batch-mode architecture from PR #9064).

The prologue is the giant fixed include block + the per-interface include
+ the `namespace Web::Bindings {` opener that sits at the top of every
generated .cpp.

PR #9064 changes:
- Added `#include <LibWeb/DOM/Document.h>` to the fixed block.
- Removed the `using namespace Web::*` block entirely.
- Replaced BFS over imported_interfaces with semantic type traversal
  (emit_includes_for_all_dependencies) that collects modules by looking
  at which types are actually referenced in the interface.
"""

from __future__ import annotations

from pathlib import Path

from ..ast import Interface
from .source_generator import SourceGenerator


def _will_generate_code(interface: Interface) -> bool:
    """Mirror IDL::Interface::will_generate_code (Types.h:356-359)."""
    if interface.name:
        return True
    if any(d.is_original_definition for d in interface.dictionaries.values()):
        return True
    if any(e.is_original_definition for e in interface.enumerations.values()):
        return True
    return False


def _generate_include_for_interface(generator: SourceGenerator, interface: Interface) -> None:
    """Mirror IDLGenerators.cpp:445-463 (generate_include_for_interface).

    In batch mode (no -i flag), g_header_search_paths is empty so the path
    is used as-is (absolute). We replicate that: use the absolute IDL path
    with .h extension, using ImplementedAs if present.
    """
    g = generator.fork()
    path_string = interface.filename or ""
    include_title = interface.implemented_name if interface.implemented_name else Path(path_string).stem
    include_path = f"{Path(path_string).parent}/{include_title}.h"
    g.set("include.path", include_path)
    g.append("\n#include <@include.path@>\n")


def emit_includes_for_all_dependencies(
    interface: Interface,
    generator: SourceGenerator,
    context=None,
    is_iterator: bool = False,
    is_async_iterator: bool = False,
) -> None:
    """New semantic include traversal from PR #9064.

    Collects all modules (interfaces, dictionaries, etc.) that are actually
    referenced by this interface's members. Emits an #include for each that
    will_generate_code(), sorted by module_own_path.

    In batch mode (context is not None), types are looked up in the global
    context registry. Falls back to the BFS-over-imports strategy when no
    context is available (old single-file mode).
    """
    # Collect all interfaces that need to be included by traversing types.
    seen_paths: set[str] = set()
    modules_to_include: list[Interface] = []

    def add_interface(iface: Interface) -> None:
        path = iface.filename
        if not path or path in seen_paths:
            return
        if not _will_generate_code(iface):
            return
        seen_paths.add(path)
        modules_to_include.append(iface)

    # Build a reverse map from IDL path → primary interface so we can add
    # includes for files that define enumerations or dictionaries (which
    # aren't themselves interfaces but live in a file that has one).
    _path_to_interface: dict[str, Interface] = {}
    if context is not None:
        for _mod in context.modules:
            if _mod.interface is not None and _mod.module_own_path:
                _path_to_interface[_mod.module_own_path] = _mod.interface

    def add_include_for_path(path: str) -> None:
        iface = _path_to_interface.get(path)
        if iface is not None:
            add_interface(iface)

    def lookup_interface_by_name(name: str):
        """Look up an interface by type name, using context first."""
        if context is not None:
            return context.interfaces.get(name)
        for imp in interface.imported_interfaces:
            if imp.name == name:
                return imp
        return None

    def collect_from_type(type_obj) -> None:
        if type_obj is None:
            return
        if type_obj.kind == "parameterized":
            for param in type_obj.parameters:
                collect_from_type(param)
            return
        if type_obj.kind == "union":
            for member in type_obj.union_member_types:
                collect_from_type(member)
            return
        # Plain type: look up in context or imported_interfaces.
        iface = lookup_interface_by_name(type_obj.name)
        if iface is not None:
            add_interface(iface)
            return
        if context is not None:
            # If the type is an enumeration, include the file that defines it.
            # Mirrors add_enumeration_include_dependency (IDLGenerators.cpp:385).
            enum_obj = context.enumerations.get(type_obj.name)
            if enum_obj is not None and enum_obj.module_own_path:
                add_include_for_path(enum_obj.module_own_path)
                return

            # If the type is a dictionary, include its file and recurse into it.
            # Mirrors add_dictionary_include_dependency (IDLGenerators.cpp:377).
            dict_obj = context.dictionaries.get(type_obj.name)
            if dict_obj is None:
                dict_obj = interface.dictionaries.get(type_obj.name)
            if dict_obj is not None:
                if dict_obj.module_own_path:
                    add_include_for_path(dict_obj.module_own_path)
                # Also recurse into dictionary members so referenced types get included.
                collect_from_dictionary_chain(type_obj.name, set())
                return

            # If the type is a callback function, traverse its parameter types
            # and return type so any referenced interfaces are included.
            cb = context.callback_functions.get(type_obj.name)
            if cb is not None:
                if cb.return_type is not None:
                    collect_from_type(cb.return_type)
                for param in cb.parameters:
                    if param.type is not None:
                        collect_from_type(param.type)
                return
            # If the type is a typedef, resolve it and recurse.
            td = context.typedefs.get(type_obj.name)
            if td is None:
                td = interface.typedefs.get(type_obj.name)
            if td is not None and td.type is not None:
                collect_from_type(td.type)

    def _lookup_dict(name):
        d = interface.dictionaries.get(name)
        if d is not None:
            return d
        if context is not None:
            return context.dictionaries.get(name)
        return None

    def collect_from_dictionary_chain(dict_name: str, visited: set, *, add_self_include: bool = False) -> None:
        """Traverse dictionary and all parent dictionaries for type includes."""
        if dict_name in visited:
            return
        visited.add(dict_name)
        dictionary = _lookup_dict(dict_name)
        if dictionary is None:
            return
        # Add file include for this dictionary (mirrors C++ add_dictionary_include_dependency).
        # The top-level call from collect_from_type already added the include, but
        # parent chain entries need it too.
        if add_self_include and dictionary.module_own_path:
            add_include_for_path(dictionary.module_own_path)
        for member in dictionary.members:
            if member.type is not None:
                collect_from_type(member.type)
        if dictionary.parent_name:
            collect_from_dictionary_chain(dictionary.parent_name, visited, add_self_include=True)

    def collect_from_parameters(params) -> None:
        for param in params:
            if param.type is not None:
                collect_from_type(param.type)

    def collect_from_operation(op) -> None:
        if op is None:
            return
        if op.return_type is not None:
            collect_from_type(op.return_type)
        collect_from_parameters(op.parameters)

    # Add self first.
    add_interface(interface)

    # Add parent if present.
    if interface.parent_name:
        parent_iface = lookup_interface_by_name(interface.parent_name)
        if parent_iface is not None:
            add_interface(parent_iface)

    # Collect from all members.
    for attr in interface.attributes:
        if attr.type is not None:
            collect_from_type(attr.type)
    for attr in interface.static_attributes:
        if attr.type is not None:
            collect_from_type(attr.type)
    for const in interface.constants:
        if const.type is not None:
            collect_from_type(const.type)
    for ctor in interface.constructors:
        collect_from_parameters(ctor.parameters)
    for fn in interface.operations:
        collect_from_operation(fn)
    for fn in interface.static_operations:
        collect_from_operation(fn)
    if interface.value_iterator_type is not None:
        collect_from_type(interface.value_iterator_type)
    if interface.pair_iterator_types is not None:
        collect_from_type(interface.pair_iterator_types[0])
        collect_from_type(interface.pair_iterator_types[1])
    if interface.async_value_iterator_type is not None:
        collect_from_type(interface.async_value_iterator_type)
    collect_from_parameters(interface.async_iterator_parameters)
    if interface.set_entry_type is not None:
        collect_from_type(interface.set_entry_type)
    if interface.map_key_type is not None:
        collect_from_type(interface.map_key_type)
    if interface.map_value_type is not None:
        collect_from_type(interface.map_value_type)
    collect_from_operation(interface.named_property_getter)
    collect_from_operation(interface.named_property_setter)
    collect_from_operation(interface.named_property_deleter)
    collect_from_operation(interface.indexed_property_getter)
    collect_from_operation(interface.indexed_property_setter)

    # Own dictionaries: collect types used in their members and parent chain.
    for dict_name in interface.own_dictionaries:
        collect_from_dictionary_chain(dict_name, set())

    # Sort by absolute path (mirrors C++ quick_sort by module_own_path).
    modules_to_include.sort(key=lambda i: i.filename or "")

    # Emit includes.
    for iface in modules_to_include:
        _generate_include_for_interface(generator, iface)

    # Iterator includes.
    if is_iterator:
        iterator_path = interface.fully_qualified_name.replace("::", "/") + "Iterator"
        ig = generator.fork()
        ig.set("iterator_class.path", iterator_path)
        ig.append("\n#   include <LibWeb/@iterator_class.path@.h>\n")
    if is_async_iterator:
        iterator_path = interface.fully_qualified_name.replace("::", "/") + "AsyncIterator"
        ig = generator.fork()
        ig.set("iterator_class.path", iterator_path)
        ig.append("\n#   include <LibWeb/@iterator_class.path@.h>\n")


# Keep backward-compat alias so existing call-sites don't break during transition.
emit_includes_for_all_imports = emit_includes_for_all_dependencies


def generate_implementation_prologue(interface: Interface, generator: SourceGenerator, context=None) -> None:
    g = generator.fork()
    g.set("bindings_name", interface.implemented_name)
    if interface.parent_name:
        g.set("parent_bindings_name", interface.parent_name)

    # IDLGenerators.cpp:6130-6177 — fixed include block. The leading "\n"
    # mirrors the C++ raw-string literal which starts with a newline after
    # the opening paren.
    # PR #9064: added `#include <LibWeb/DOM/Document.h>` after Range.h.
    g.append(
        "\n"
        "#include <AK/Function.h>\n"
        "#include <AK/TypeCasts.h>\n"
        "#include <LibGC/Heap.h>\n"
        "#include <LibIDL/Types.h>\n"
        "#include <LibJS/Runtime/AbstractOperations.h>\n"
        "#include <LibJS/Runtime/Array.h>\n"
        "#include <LibJS/Runtime/ArrayBuffer.h>\n"
        "#include <LibJS/Runtime/DataView.h>\n"
        "#include <LibJS/Runtime/Error.h>\n"
        "#include <LibJS/Runtime/FunctionObject.h>\n"
        "#include <LibJS/Runtime/GlobalObject.h>\n"
        "#include <LibJS/Runtime/Iterator.h>\n"
        "#include <LibJS/Runtime/PrimitiveString.h>\n"
        "#include <LibJS/Runtime/PromiseConstructor.h>\n"
        "#include <LibJS/Runtime/TypedArray.h>\n"
        "#include <LibJS/Runtime/Value.h>\n"
        "#include <LibJS/Runtime/ValueInlines.h>\n"
        "#include <LibWeb/Bindings/@bindings_name@.h>\n"
        "#include <LibWeb/Bindings/ExceptionOrUtils.h>\n"
        "#include <LibWeb/Bindings/Intrinsics.h>\n"
        "#include <LibWeb/Bindings/MainThreadVM.h>\n"
        "#include <LibWeb/Bindings/PrincipalHostDefined.h>\n"
        "#include <LibWeb/DOM/Document.h>\n"
        "#include <LibWeb/DOM/Element.h>\n"
        "#include <LibWeb/DOM/ElementFactory.h>\n"
        "#include <LibWeb/DOM/Event.h>\n"
        "#include <LibWeb/DOM/IDLEventListener.h>\n"
        "#include <LibWeb/DOM/NodeFilter.h>\n"
        "#include <LibWeb/DOM/Range.h>\n"
        "#include <LibWeb/HTML/CustomElements/CustomElementDefinition.h>\n"
        "#include <LibWeb/HTML/CustomElements/CustomElementRegistry.h>\n"
        "#include <LibWeb/HTML/Numbers.h>\n"
        "#include <LibWeb/HTML/Scripting/Environments.h>\n"
        "#include <LibWeb/HTML/Scripting/SimilarOriginWindowAgent.h>\n"
        "#include <LibWeb/HTML/Window.h>\n"
        "#include <LibWeb/HTML/WindowProxy.h>\n"
        "#include <LibWeb/Infra/Strings.h>\n"
        "#include <LibWeb/Namespace.h>\n"
        "#include <LibWeb/WebIDL/AbstractOperations.h>\n"
        "#include <LibWeb/WebIDL/AsyncIterator.h>\n"
        "#include <LibWeb/WebIDL/Buffers.h>\n"
        "#include <LibWeb/WebIDL/CallbackType.h>\n"
        "#include <LibWeb/WebIDL/OverloadResolution.h>\n"
        "#include <LibWeb/WebIDL/Promise.h>\n"
        "#include <LibWeb/WebIDL/Tracing.h>\n"
        "#include <LibWeb/WebIDL/Types.h>\n"
        "\n"
    )

    if interface.parent_name:
        # IDLGenerators.cpp:6181-6185.
        g.append(
            "\n"
            "#if __has_include(<LibWeb/Bindings/@parent_bindings_name@.h>)\n"
            "#    include <LibWeb/Bindings/@parent_bindings_name@.h>\n"
            "#endif\n"
            "\n"
        )

    emit_includes_for_all_dependencies(
        interface,
        g,
        context=context,
        is_iterator=interface.pair_iterator_types is not None,
        is_async_iterator=interface.async_value_iterator_type is not None,
    )

    # PR #9064: No more `using namespace Web::*` block.

    # IDLGenerators.cpp:6191-6193 — namespace open.
    g.append("\nnamespace Web::Bindings {\n\n")
