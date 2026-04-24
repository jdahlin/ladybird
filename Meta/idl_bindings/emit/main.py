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
from .types import _set_active_context


def generate_header(interface: Interface, context=None) -> str:
    _set_active_context(context)
    try:
        return _generate_header_impl(interface)
    finally:
        _set_active_context(None)


def _generate_header_impl(interface: Interface) -> str:
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
        _generate_namespace_header(interface, g)
    else:
        generate_constructor_header(interface, g)
        generate_prototype_header(interface, g)

    if interface.pair_iterator_types is not None:
        _generate_iterator_prototype_header(interface, g)

    if interface.async_value_iterator_type is not None:
        _generate_async_iterator_prototype_header(interface, g)

    if "Global" in interface.extended_attributes:
        _generate_global_mixin_header(interface, g)

    # IDLGenerators.cpp:6224-6226 — common epilogue.
    g.append("\n} // namespace Web::Bindings\n")
    return builder.to_string()


def generate_implementation(interface: Interface, context=None) -> str:
    _set_active_context(context)
    try:
        return _generate_implementation_impl(interface, context)
    finally:
        _set_active_context(None)


def _generate_implementation_impl(interface: Interface, context=None) -> str:
    from .to_cpp import _DICTIONARY_INDEX

    # Reset counter only in single-file (no context) mode.  In batch mode the
    # caller manages the counter so it accumulates across files, matching the
    # C++ static variable (IDLGenerators.cpp:729: `static auto i = 0`).
    if context is None:
        _DICTIONARY_INDEX[0] = 0

    builder = StringBuilder()
    g = SourceGenerator(builder)

    generate_implementation_prologue(interface, g, context=context)

    if interface.is_namespace:
        _generate_namespace_implementation(interface, g)
    else:
        generate_constructor_implementation(interface, g)
        generate_prototype_implementation(interface, g)

    if interface.pair_iterator_types is not None:
        _generate_iterator_prototype_implementation(interface, g)

    if interface.async_value_iterator_type is not None:
        _generate_async_iterator_prototype_implementation(interface, g)

    if "Global" in interface.extended_attributes:
        _generate_global_mixin_implementation(interface, g)

    # IDLGenerators.cpp:6249-6251 — common epilogue.
    g.append("\n} // namespace Web::Bindings\n")
    return builder.to_string()


def _generate_global_mixin_header(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_global_mixin_header (IDLGenerators.cpp:6086-6104)."""
    from .prototype import _generate_prototype_or_global_mixin_declarations

    g = generator.fork()
    g.set("class_name", interface.global_mixin_class)
    g.append(
        "\n"
        "class @class_name@ {\n"
        "public:\n"
        "    void initialize(JS::Realm&, JS::Object&);\n"
        "    void define_unforgeable_attributes(JS::Realm&, JS::Object&);\n"
        "    @class_name@();\n"
        "    virtual ~@class_name@();\n"
        "\n"
        "private:\n"
    )
    _generate_prototype_or_global_mixin_declarations(interface, g)


def _generate_global_mixin_implementation(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_global_mixin_implementation (IDLGenerators.cpp:6106-6120)."""
    from .prototype import _generate_prototype_or_global_mixin_definitions
    from .prototype import _generate_prototype_or_global_mixin_initialization

    g = generator.fork()
    g.set("class_name", interface.global_mixin_class)
    g.set("prototype_name", interface.prototype_class)
    g.append("\n@class_name@::@class_name@() = default;\n@class_name@::~@class_name@() = default;\n")
    _generate_prototype_or_global_mixin_initialization(interface, g, generate_unforgeables=False)
    _generate_prototype_or_global_mixin_initialization(interface, g, generate_unforgeables=True)
    _generate_prototype_or_global_mixin_definitions(interface, g)


def _generate_namespace_header(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_namespace_header (IDLGenerators.cpp:5363-5416)."""
    from .prototype import _make_input_acceptable_cpp
    from .prototype import _to_snakecase

    g = generator.fork()
    g.set("namespace_class", interface.namespace_class)
    g.append(
        "\n"
        "class @namespace_class@ final : public JS::Object {\n"
        "    JS_OBJECT(@namespace_class@, JS::Object);\n"
        "    GC_DECLARE_ALLOCATOR(@namespace_class@);\n"
        "public:\n"
        "    explicit @namespace_class@(JS::Realm&);\n"
        "    virtual void initialize(JS::Realm&) override;\n"
        "    virtual ~@namespace_class@() override;\n"
        "\n"
        "private:\n"
    )

    if "WithGCVisitor" in interface.extended_attributes:
        g.append("\n    virtual void visit_edges(JS::Cell::Visitor&) override;\n")

    if "WithFinalizer" in interface.extended_attributes:
        g.append(
            "\npublic:\n"
            "    static constexpr bool OVERRIDES_FINALIZE = true;\n"
            "\n"
            "private:\n"
            "    virtual void finalize() override;\n"
        )

    overload_sets: dict[str, list] = {}
    for op in interface.operations:
        if "FIXME" in op.extended_attributes:
            continue
        overload_sets.setdefault(op.name, []).append(op)

    for name, overloads in overload_sets.items():
        fg = g.fork()
        fg.set("function.name:snakecase", _make_input_acceptable_cpp(_to_snakecase(name)))
        fg.append("\n    JS_DECLARE_NATIVE_FUNCTION(@function.name:snakecase@);\n")
        if len(overloads) > 1:
            for i in range(len(overloads)):
                fg.set("overload_suffix", str(i))
                fg.append("\n    JS_DECLARE_NATIVE_FUNCTION(@function.name:snakecase@@overload_suffix@);\n")

    g.append("\n};\n")


def _generate_namespace_implementation(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_namespace_implementation (IDLGenerators.cpp:5498-5570)."""
    from .operations import generate_function
    from .prototype import _make_input_acceptable_cpp
    from .prototype import _to_snakecase

    g = generator.fork()
    g.set("name", interface.name)
    g.set("namespace_class", interface.namespace_class)
    g.set("interface_name", interface.name)
    g.append(
        "\n"
        "GC_DEFINE_ALLOCATOR(@namespace_class@);\n"
        "\n"
        "@namespace_class@::@namespace_class@(JS::Realm& realm)\n"
        "    : Object(ConstructWithPrototypeTag::Tag, realm.intrinsics().object_prototype())\n"
        "{\n"
        "}\n"
        "\n"
        "@namespace_class@::~@namespace_class@()\n"
        "{\n"
        "}\n"
        "\n"
        "void @namespace_class@::initialize(JS::Realm& realm)\n"
        "{\n"
        "    [[maybe_unused]] auto& vm = this->vm();\n"
        "\n"
        "    Base::initialize(realm);\n"
        "\n"
        '    define_direct_property(vm.well_known_symbol_to_string_tag(), JS::PrimitiveString::create(vm, "@interface_name@"_string), JS::Attribute::Configurable);\n'
        "\n"
    )

    # define_the_operations — mirrors IDLGenerators.cpp:5477-5496.
    overload_sets: dict[str, list] = {}
    for op in interface.operations:
        if "FIXME" in op.extended_attributes:
            continue
        overload_sets.setdefault(op.name, []).append(op)

    for name, group in overload_sets.items():
        og = g.fork()
        og.set("function.name", name)
        og.set("function.name:snakecase", _make_input_acceptable_cpp(_to_snakecase(name)))
        shortest = min(sum(1 for p in op.parameters if not p.optional and not p.variadic) for op in group)
        og.set("function.length", str(shortest))
        if group[0].extended_attributes.get("LegacyUnforgeable"):
            og.set("function.attributes", "JS::Attribute::Enumerable")
        else:
            og.set(
                "function.attributes",
                "JS::Attribute::Writable | JS::Attribute::Enumerable | JS::Attribute::Configurable",
            )
        og.append(
            '\n    define_native_function(realm, "@function.name@"_utf16_fly_string, @function.name:snakecase@, @function.length@, @function.attributes@);\n'
        )

    if "WithInitializer" in interface.extended_attributes:
        g.append("\n\n    @name@::initialize(*this, realm);\n")

    g.append("\n}\n")

    if "WithGCVisitor" in interface.extended_attributes:
        g.append(
            "\n"
            "void @namespace_class@::visit_edges(JS::Cell::Visitor& visitor)\n"
            "{\n"
            "    Base::visit_edges(visitor);\n"
            "    @name@::visit_edges(*this, visitor);\n"
            "}\n"
        )

    if "WithFinalizer" in interface.extended_attributes:
        g.append("\nvoid @namespace_class@::finalize()\n{\n    Base::finalize();\n    @name@::finalize(*this);\n}\n")

    # Per-operation bodies — mirrors IDLGenerators.cpp:5560-5564.
    # C++ uses interface.name (not fully_qualified_name) as the call qualifier.
    for op in interface.operations:
        if "FIXME" in op.extended_attributes:
            continue
        generate_function(
            op,
            interface,
            interface.namespace_class,
            static=True,
            generator=generator,
            interface_name_override=interface.name,
        )

    # Overload arbiters — mirrors IDLGenerators.cpp:5565-5569.
    for name, group in overload_sets.items():
        if len(group) > 1:
            from .overload_arbiter import generate_overload_arbiter

            generate_overload_arbiter(
                group,
                name,
                interface,
                interface.namespace_class,
                is_constructor=False,
                generator=generator,
            )


def _generate_iterator_prototype_header(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_iterator_prototype_header (IDLGenerators.cpp:5915-5935)."""
    g = generator.fork()
    g.set("prototype_class", f"{interface.name}IteratorPrototype")
    g.append(
        "\n"
        "class @prototype_class@ : public JS::Object {\n"
        "    JS_OBJECT(@prototype_class@, JS::Object);\n"
        "    GC_DECLARE_ALLOCATOR(@prototype_class@);\n"
        "public:\n"
        "    explicit @prototype_class@(JS::Realm&);\n"
        "    virtual void initialize(JS::Realm&) override;\n"
        "    virtual ~@prototype_class@() override;\n"
        "\n"
        "private:\n"
        "    JS_DECLARE_NATIVE_FUNCTION(next);\n"
        "};\n"
    )


def _generate_iterator_prototype_implementation(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_iterator_prototype_implementation (IDLGenerators.cpp:5937-5984)."""
    g = generator.fork()
    g.set("name", f"{interface.name}Iterator")
    g.set("parent_name", interface.parent_name)
    g.set("prototype_class", f"{interface.name}IteratorPrototype")
    g.set("to_string_tag", f"{interface.name} Iterator")
    g.set("prototype_base_class", interface.prototype_base_class)
    g.set("fully_qualified_name", f"{interface.fully_qualified_name}Iterator")
    g.append(
        "\n"
        "GC_DEFINE_ALLOCATOR(@prototype_class@);\n"
        "\n"
        "@prototype_class@::@prototype_class@(JS::Realm& realm)\n"
        "    : Object(ConstructWithPrototypeTag::Tag, realm.intrinsics().iterator_prototype())\n"
        "{\n"
        "}\n"
        "\n"
        "@prototype_class@::~@prototype_class@()\n"
        "{\n"
        "}\n"
        "\n"
        "void @prototype_class@::initialize(JS::Realm& realm)\n"
        "{\n"
        "    auto& vm = this->vm();\n"
        "    Base::initialize(realm);\n"
        "    define_native_function(realm, vm.names.next, next, 0, JS::Attribute::Writable | JS::Attribute::Enumerable | JS::Attribute::Configurable);\n"
        '    define_direct_property(vm.well_known_symbol_to_string_tag(), JS::PrimitiveString::create(vm, "@to_string_tag@"_string), JS::Attribute::Configurable);\n'
        "}\n"
        "\n"
        "static JS::ThrowCompletionOr<@fully_qualified_name@*> iterator_impl_from(JS::VM& vm)\n"
        "{\n"
        "    auto this_object = TRY(vm.this_value().to_object(vm));\n"
        "    if (!is<@fully_qualified_name@>(*this_object))\n"
        '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@name@");\n'
        "    return static_cast<@fully_qualified_name@*>(this_object.ptr());\n"
        "}\n"
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@prototype_class@::next)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@prototype_class@::next");\n'
        "    auto* impl = TRY(iterator_impl_from(vm));\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return impl->next(); }));\n"
        "}\n"
    )


def _generate_async_iterator_prototype_header(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_async_iterator_prototype_header (IDLGenerators.cpp:5986-6016)."""
    has_return = "DefinesAsyncIteratorReturn" in interface.extended_attributes

    g = generator.fork()
    g.set("prototype_class", f"{interface.name}AsyncIteratorPrototype")
    g.append(
        "\n"
        "class @prototype_class@ : public JS::Object {\n"
        "    JS_OBJECT(@prototype_class@, JS::Object);\n"
        "    GC_DECLARE_ALLOCATOR(@prototype_class@);\n"
        "\n"
        "public:\n"
        "    explicit @prototype_class@(JS::Realm&);\n"
        "    virtual void initialize(JS::Realm&) override;\n"
        "    virtual ~@prototype_class@() override;\n"
        "\n"
        "private:\n"
        "    JS_DECLARE_NATIVE_FUNCTION(next);\n"
        "    "
    )
    if has_return:
        g.append("\n    JS_DECLARE_NATIVE_FUNCTION(return_);\n")
    g.append("\n};\n")


def _generate_async_iterator_prototype_implementation(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_async_iterator_prototype_implementation (IDLGenerators.cpp:6018-6083)."""
    has_return = "DefinesAsyncIteratorReturn" in interface.extended_attributes

    g = generator.fork()
    g.set("name", f"{interface.name}AsyncIterator")
    g.set("prototype_class", f"{interface.name}AsyncIteratorPrototype")
    g.set("to_string_tag", f"{interface.name} AsyncIterator")
    g.set("fully_qualified_name", f"{interface.fully_qualified_name}AsyncIterator")
    g.append(
        "\n"
        "GC_DEFINE_ALLOCATOR(@prototype_class@);\n"
        "\n"
        "@prototype_class@::@prototype_class@(JS::Realm& realm)\n"
        "    : Object(ConstructWithPrototypeTag::Tag, realm.intrinsics().async_iterator_prototype())\n"
        "{\n"
        "}\n"
        "\n"
        "@prototype_class@::~@prototype_class@()\n"
        "{\n"
        "}\n"
        "\n"
        "void @prototype_class@::initialize(JS::Realm& realm)\n"
        "{\n"
        "    auto& vm = this->vm();\n"
        "    Base::initialize(realm);\n"
        '    define_direct_property(vm.well_known_symbol_to_string_tag(), JS::PrimitiveString::create(vm, "@to_string_tag@"_string), JS::Attribute::Configurable);\n'
        "\n"
        "    define_native_function(realm, vm.names.next, next, 0, JS::default_attributes);"
    )
    if has_return:
        g.append("\n    define_native_function(realm, vm.names.return_, return_, 1, JS::default_attributes);")
    g.append(
        "\n"
        "}\n"
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@prototype_class@::next)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@prototype_class@::next");\n'
        "    auto& realm = *vm.current_realm();\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] {\n"
        '        return WebIDL::AsyncIterator::next<@fully_qualified_name@>(realm, "@name@"sv);\n'
        "    }));\n"
        "}\n"
    )
    if has_return:
        g.append(
            "\n"
            "JS_DEFINE_NATIVE_FUNCTION(@prototype_class@::return_)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@prototype_class@::return");\n'
            "    auto& realm = *vm.current_realm();\n"
            "\n"
            "    auto value = vm.argument(0);\n"
            "\n"
            "    return TRY(throw_dom_exception_if_needed(vm, [&] {\n"
            '        return WebIDL::AsyncIterator::return_<@fully_qualified_name@>(realm, "@name@"sv, value);\n'
            "    }));\n"
            "}\n"
        )
