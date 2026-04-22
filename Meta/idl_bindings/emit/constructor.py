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


def generate_constructor_implementation(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_constructor_implementation (IDLGenerators.cpp:5646-5784).

    Currently produces output for the empty-interface case (no constructors,
    no static attributes, no constants, no static functions, no callback).
    """
    g = generator.fork()
    g.set("name", interface.name)
    g.set("namespaced_name", interface.namespaced_name)
    g.set("prototype_class", interface.prototype_class)
    g.set("constructor_class", interface.constructor_class)
    g.set("fully_qualified_name", interface.fully_qualified_name)
    g.set("parent_name", interface.parent_name)
    g.set("prototype_base_class", interface.prototype_base_class)
    g.set("constructor.length", "0")

    g.append(
        "\n"
        "GC_DEFINE_ALLOCATOR(@constructor_class@);\n"
        "\n"
        "@constructor_class@::@constructor_class@(JS::Realm& realm)\n"
        '    : NativeFunction("@name@"_utf16_fly_string, realm.intrinsics().function_prototype())\n'
        "{\n"
        "}\n"
        "\n"
        "@constructor_class@::~@constructor_class@()\n"
        "{\n"
        "}\n"
        "\n"
        "JS::ThrowCompletionOr<JS::Value> @constructor_class@::call()\n"
        "{\n"
        '    return vm().throw_completion<JS::TypeError>(JS::ErrorType::ConstructorWithoutNew, "@namespaced_name@");\n'
        "}\n"
        "\n"
    )

    if not interface.is_callback_interface:
        # IDLGenerators.cpp:3118-3160 (generate_constructors). Empty case
        # (interface.constructors is empty): emit the "not a constructor"
        # throw stub.
        if not interface.constructors:
            g.append(
                "\n"
                "JS::ThrowCompletionOr<GC::Ref<JS::Object>> @constructor_class@::construct([[maybe_unused]] FunctionObject& new_target)\n"
                "{\n"
                '    WebIDL::log_trace(vm(), "@constructor_class@::construct");\n'
                "\n"
                '    return vm().throw_completion<JS::TypeError>(JS::ErrorType::NotAConstructor, "@namespaced_name@");\n'
                "}\n"
            )
        else:
            raise NotImplementedError("constructors are not yet supported on this rung")

    # IDLGenerators.cpp:5680-5688: initialize() opener.
    g.append(
        "\n"
        "\n"
        "void @constructor_class@::initialize(JS::Realm& realm)\n"
        "{\n"
        "    auto& vm = this->vm();\n"
        "    [[maybe_unused]] u8 default_attributes = JS::Attribute::Enumerable;\n"
        "\n"
        "    Base::initialize(realm);\n"
    )

    # IDLGenerators.cpp:5690-5694: parent prototype hookup. Skipped for
    # interfaces whose parent is "Object" (i.e. no IDL parent).
    if not interface.is_callback_interface and interface.prototype_base_class != "ObjectPrototype":
        g.append(
            '\n    set_prototype(&ensure_web_constructor<@prototype_base_class@>(realm, "@parent_name@"_fly_string));\n'
        )

    # IDLGenerators.cpp:5696-5699: length + name properties.
    g.append(
        "\n"
        "    define_direct_property(vm.names.length, JS::Value(@constructor.length@), JS::Attribute::Configurable);\n"
        '    define_direct_property(vm.names.name, JS::PrimitiveString::create(vm, "@name@"_string), JS::Attribute::Configurable);\n'
    )

    if not interface.is_callback_interface:
        g.append(
            "\n"
            '    define_direct_property(vm.names.prototype, &ensure_web_prototype<@prototype_class@>(realm, "@namespaced_name@"_fly_string), 0);\n'
        )

    # Constants / static attributes / static operations come at later rungs;
    # for the empty interface there are none.
    if interface.constants:
        raise NotImplementedError("constants are not yet supported on this rung")
    if interface.static_attributes:
        raise NotImplementedError("static attributes are not yet supported on this rung")
    if interface.static_operations:
        raise NotImplementedError("static operations are not yet supported on this rung")

    g.append("\n}\n")

    # Static-attribute / static-function implementations follow here at later
    # rungs; for the empty interface none.

    # IDLGenerators.cpp:5782-5783 — trailing blank line in raw string.
    g.append("\n")
