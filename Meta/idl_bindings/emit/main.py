"""Top-level emitter — port of generate_header / generate_implementation
from Meta/Lagom/Tools/CodeGenerators/LibWeb/BindingsGenerator/IDLGenerators.cpp
(lines 6197-6252).

These are the entry points that main.cpp calls. They wrap a common prologue +
epilogue around the per-kind generators (namespace OR constructor+prototype,
plus optional iterator / async-iterator / global-mixin sections).
"""

from __future__ import annotations

from ..ast import Interface
from .constructor import generate_constructor_header
from .constructor import generate_constructor_implementation
from .prologue import generate_implementation_prologue
from .prototype import generate_prototype_header
from .prototype import generate_prototype_implementation
from .source_generator import SourceGenerator
from .source_generator import StringBuilder


def generate_header(interface: Interface) -> str:
    builder = StringBuilder()
    g = SourceGenerator(builder)

    # IDLGenerators.cpp:6199-6206 — common prologue.
    g.append(
        "#pragma once\n"
        "\n"
        "#include <LibJS/Runtime/NativeFunction.h>\n"
        "#include <LibJS/Runtime/Object.h>\n"
        "\n"
        "namespace Web::Bindings {\n"
        "\n"
    )

    if interface.is_namespace:
        # generate_namespace_header — added at concept-ladder rung 40.
        raise NotImplementedError("namespace_header is not yet implemented")
    else:
        generate_constructor_header(interface, g)
        generate_prototype_header(interface, g)

    if interface.pair_iterator_types is not None:
        # generate_iterator_prototype_header — concept-ladder rung 27.
        raise NotImplementedError("iterator_prototype_header is not yet implemented")

    if interface.async_value_iterator_type is not None:
        # generate_async_iterator_prototype_header — rung 28.
        raise NotImplementedError("async_iterator_prototype_header is not yet implemented")

    if "Global" in interface.extended_attributes:
        # generate_global_mixin_header — rung 39.
        raise NotImplementedError("global_mixin_header is not yet implemented")

    # IDLGenerators.cpp:6224-6226 — common epilogue.
    g.append("\n} // namespace Web::Bindings\n")
    return builder.to_string()


def generate_implementation(interface: Interface) -> str:
    builder = StringBuilder()
    g = SourceGenerator(builder)

    generate_implementation_prologue(interface, g)

    if interface.is_namespace:
        # generate_namespace_implementation — concept-ladder rung 40.
        raise NotImplementedError("namespace_implementation is not yet implemented")
    else:
        generate_constructor_implementation(interface, g)
        generate_prototype_implementation(interface, g)

    if interface.pair_iterator_types is not None:
        raise NotImplementedError("iterator_prototype_implementation is not yet implemented")

    if interface.async_value_iterator_type is not None:
        raise NotImplementedError("async_iterator_prototype_implementation is not yet implemented")

    if "Global" in interface.extended_attributes:
        raise NotImplementedError("global_mixin_implementation is not yet implemented")

    # IDLGenerators.cpp:6249-6251 — common epilogue.
    g.append("\n} // namespace Web::Bindings\n")
    return builder.to_string()
