"""Constructor class emitter — port of generate_constructor_header /
generate_constructor_implementation from
Meta/Lagom/Tools/CodeGenerators/LibWeb/BindingsGenerator/IDLGenerators.cpp
(lines 5572-5644, 5646-5784).

The interface's "constructor" is the JS NativeFunction subclass that
implements `new Foo(...)` and the static side (Foo.staticMethod()).

This file currently implements the **header** path for interfaces with no
static attributes, no extra constructor overloads, and no static functions
(concept-ladder rungs 2 + early simple interfaces). Implementation and
richer cases follow in later commits.
"""

from __future__ import annotations

from ..ast import Interface
from .source_generator import SourceGenerator


def generate_constructor_header(interface: Interface, generator: SourceGenerator) -> None:
    # IDLGenerators.cpp:5572-5644 (consolidated form: no leading #pragma /
    # #include / namespace; the wrapping is done by generate_header).
    g = generator.fork()
    g.set("constructor_class", interface.constructor_class)

    g.append(
        "\n"
        "class @constructor_class@ : public JS::NativeFunction {\n"
        "    JS_OBJECT(@constructor_class@, JS::NativeFunction);\n"
        "    GC_DECLARE_ALLOCATOR(@constructor_class@);\n"
        "public:\n"
        "    explicit @constructor_class@(JS::Realm&);\n"
        "    virtual void initialize(JS::Realm&) override;\n"
        "    virtual ~@constructor_class@() override;\n"
        "\n"
        "    virtual JS::ThrowCompletionOr<JS::Value> call() override;\n"
    )

    if not interface.is_callback_interface:
        g.append(
            "\n"
            "    virtual JS::ThrowCompletionOr<GC::Ref<JS::Object>> construct(JS::FunctionObject& new_target) override;\n"
            "\n"
            "private:\n"
            "    virtual bool has_constructor() const override { return true; }\n"
        )

    # Static attribute / constructor-overload / static-overload declarations
    # are emitted here too. Skipped for the empty-interface rung — added when
    # the concept-ladder reaches static members.

    g.append("\n};\n")
