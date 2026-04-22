"""Prototype class emitter — port of generate_prototype_header /
generate_prototype_implementation from
Meta/Lagom/Tools/CodeGenerators/LibWeb/BindingsGenerator/IDLGenerators.cpp
(lines 5786-5912).

The "prototype" is the JS Object subclass that holds an interface's
instance methods and properties.

This file currently implements the **header** path for non-global,
non-callback interfaces with no overload sets, no stringifier, no iterators,
no setlike/maplike, no constants, and no attributes (concept-ladder rung
2: empty interface like WorkletGlobalScope.idl). Richer cases follow.
"""

from __future__ import annotations

from ..ast import Interface
from .source_generator import SourceGenerator


# https://webidl.spec.whatwg.org/#es-immutable-prototype-exotic-objects
# IDLGenerators.cpp:3731 (interface_prototype_has_immutable_prototype):
# A platform object's prototype is *immutable* when the interface declares
# `[Global]` or one of a few legacy attributes. For now (empty-interface
# rung) we only need to recognize the simplest case — no Global, no others —
# returning False.
def _interface_prototype_has_immutable_prototype(interface: Interface) -> bool:
    if "Global" in interface.extended_attributes:
        return True
    # The C++ helper also checks for "LegacyPlatformObject"-shape interfaces
    # (those with named property handlers). Add when concept ladder reaches them.
    return False


def generate_prototype_header(interface: Interface, generator: SourceGenerator) -> None:
    g = generator.fork()
    g.set("prototype_class", interface.prototype_class)
    has_immutable_prototype = _interface_prototype_has_immutable_prototype(interface)

    g.append(
        "\n"
        "class @prototype_class@ : public JS::Object {\n"
        "    JS_OBJECT(@prototype_class@, JS::Object);\n"
        "    GC_DECLARE_ALLOCATOR(@prototype_class@);\n"
        "public:\n"
        "    static void define_unforgeable_attributes(JS::Realm&, JS::Object&);\n"
        "\n"
        "    explicit @prototype_class@(JS::Realm&);\n"
        "    virtual void initialize(JS::Realm&) override;\n"
        "    virtual ~@prototype_class@() override;\n"
    )

    if has_immutable_prototype:
        g.append(
            "\n"
            "private:\n"
            "    virtual JS::ThrowCompletionOr<bool> internal_set_prototype_of(JS::Object* prototype) override;\n"
        )

    is_global_interface = "Global" in interface.extended_attributes
    if is_global_interface:
        # IDLGenerators.cpp:5814 — global interfaces close the class here and
        # may follow with named-properties-object declarations.
        g.append("\n};\n")
        # Named properties object declarations are added at later rungs.
        return

    # Non-global path: generate_prototype_or_global_mixin_declarations
    # IDLGenerators.cpp:3246-3364. For an empty interface it just emits the
    # class closer. Attribute / overload / iterator / setlike output is
    # added at later rungs.
    g.append("\n\n};\n\n")
