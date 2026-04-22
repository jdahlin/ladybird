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
        _generate_constructors(interface, g)

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

    # IDLGenerators.cpp:5707-5716 — constants on the constructor object.
    for constant in interface.constants:
        cg = g.fork()
        cg.set("constant.name", constant.name)
        from .types import generate_wrap_statement

        generate_wrap_statement(
            cg,
            constant.value,
            constant.type,
            interface,
            f"auto constant_{constant.name}_value =",
        )
        cg.append(
            '\n    define_direct_property("@constant.name@"_utf16_fly_string, '
            "constant_@constant.name@_value, JS::Attribute::Enumerable);\n"
        )

    if interface.static_attributes:
        raise NotImplementedError("static attributes are not yet supported on this rung")
    if interface.static_operations:
        raise NotImplementedError("static operations are not yet supported on this rung")

    g.append("\n}\n")

    # Static-attribute / static-function implementations follow here at later
    # rungs; for the empty interface none.

    # IDLGenerators.cpp:5782-5783 — trailing blank line in raw string.
    g.append("\n")


# Mirrors the shortest_length() helper inferred from get_function_shortest_length
# in Libraries/LibIDL/Types.h. Returns the count of required (non-optional,
# non-variadic) parameters.
def _shortest_length(parameters) -> int:
    return sum(1 for p in parameters if not p.optional and not p.variadic)


def _generate_constructors(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_constructors (IDLGenerators.cpp:3118-3160).

    Empty case: emit the throwing `construct` stub. Non-empty case:
    iterate constructors, generate one body each.
    """
    if not interface.constructors:
        # IDLGenerators.cpp:3139-3149 — `not a constructor` placeholder.
        generator.append(
            "\n"
            "JS::ThrowCompletionOr<GC::Ref<JS::Object>> @constructor_class@::construct([[maybe_unused]] FunctionObject& new_target)\n"
            "{\n"
            '    WebIDL::log_trace(vm(), "@constructor_class@::construct");\n'
            "\n"
            '    return vm().throw_completion<JS::TypeError>(JS::ErrorType::NotAConstructor, "@namespaced_name@");\n'
            "}\n"
        )
        return

    # IDLGenerators.cpp:3120-3136 — compute shortest_length across all
    # constructors and set it on the parent generator (so initialize()
    # picks up the right number for `define_direct_property(length, ...)`).
    has_html_constructor = any("HTMLConstructor" in c.extended_attributes for c in interface.constructors)
    if has_html_constructor:
        raise NotImplementedError("[HTMLConstructor] is not yet supported")

    shortest = min(_shortest_length(c.parameters) for c in interface.constructors)
    generator.set("constructor.length", str(shortest))

    for ctor in interface.constructors:
        _generate_constructor(ctor, interface, generator)
    # generate_overload_arbiter for multi-constructor sets is added later.
    if any(getattr(c, "is_overloaded", False) for c in interface.constructors):
        raise NotImplementedError("constructor overload arbiter not yet supported")


def _generate_constructor(constructor, interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_constructor (IDLGenerators.cpp:3026-3116).

    Currently only supports zero-parameter, non-HTMLConstructor cases.
    """
    if constructor.parameters:
        raise NotImplementedError("constructor parameters are not yet supported")

    g = generator.fork()
    g.set("constructor_class", interface.constructor_class)
    g.set("interface_fully_qualified_name", interface.fully_qualified_name)
    g.set(
        "overload_suffix",
        str(getattr(constructor, "overload_index", 0)) if getattr(constructor, "is_overloaded", False) else "",
    )
    g.append(
        "\n"
        "JS::ThrowCompletionOr<GC::Ref<JS::Object>> @constructor_class@::construct@overload_suffix@([[maybe_unused]] FunctionObject& new_target)\n"
        "{\n"
        '    WebIDL::log_trace(vm(), "@constructor_class@::construct@overload_suffix@");\n'
    )
    g.append("\n    auto& vm = this->vm();\n    auto& realm = *vm.current_realm();\n")
    g.append(
        "\n"
        "    // To internally create a new object implementing the interface @name@:\n"
        "\n"
        '    // 3.2. Let prototype be ? Get(newTarget, "prototype").\n'
        "    auto prototype = TRY(new_target.get(vm.names.prototype));\n"
        "\n"
        "    // 3.3. If Type(prototype) is not Object, then:\n"
        "    if (!prototype.is_object()) {\n"
        "        // 1. Let targetRealm be ? GetFunctionRealm(newTarget).\n"
        "        auto* target_realm = TRY(JS::get_function_realm(vm, new_target));\n"
        "\n"
        "        // 2. Set prototype to the interface prototype object for interface in targetRealm.\n"
        "        VERIFY(target_realm);\n"
        '        prototype = &Bindings::ensure_web_prototype<@prototype_class@>(*target_realm, "@namespaced_name@"_fly_string);\n'
        "    }\n"
        "\n"
        "    // 4. Let instance be MakeBasicObject( « [[Prototype]], [[Extensible]], [[Realm]], [[PrimaryInterface]] »).\n"
        "    // 5. Set instance.[[Realm]] to realm.\n"
        "    // 6. Set instance.[[PrimaryInterface]] to interface.\n"
    )
    # No-args path (IDLGenerators.cpp:3078-3081).
    g.append(
        "\n"
        "    auto impl = TRY(throw_dom_exception_if_needed(vm, [&] { return @fully_qualified_name@::construct_impl(realm); }));\n"
    )
    g.append(
        "\n"
        "    // 7. Set instance.[[Prototype]] to prototype.\n"
        "    VERIFY(prototype.is_object());\n"
        "    impl->set_prototype(&prototype.as_object());\n"
        "\n"
        '    // FIXME: Steps 8...11. of the "internally create a new object implementing the interface @name@" algorithm\n'
        "    // (https://webidl.spec.whatwg.org/#js-platform-objects) are currently not handled, or are handled within @fully_qualified_name@::construct_impl().\n"
        "    //  8. Let interfaces be the inclusive inherited interfaces of interface.\n"
        "    //  9. For every interface ancestor interface in interfaces:\n"
        "    //    9.1. Let unforgeables be the value of the [[Unforgeables]] slot of the interface object of ancestor interface in realm.\n"
        "    //    9.2. Let keys be ! unforgeables.[[OwnPropertyKeys]]().\n"
        "    //    9.3. For each element key of keys:\n"
        "    //      9.3.1. Let descriptor be ! unforgeables.[[GetOwnProperty]](key).\n"
        "    //      9.3.2. Perform ! DefinePropertyOrThrow(instance, key, descriptor).\n"
        "    //  10. If interface is declared with the [Global] extended attribute, then:\n"
        "    //    10.1. Define the regular operations of interface on instance, given realm.\n"
        "    //    10.2. Define the regular attributes of interface on instance, given realm.\n"
        "    //    10.3. Define the iteration methods of interface on instance given realm.\n"
        "    //    10.4. Define the asynchronous iteration methods of interface on instance given realm.\n"
        "    //    10.5. Define the global property references on instance, given realm.\n"
        "    //    10.6. Set instance.[[SetPrototypeOf]] as defined in § 3.8.1 [[SetPrototypeOf]].\n"
        "    //  11. Otherwise, if interfaces contains an interface which supports indexed properties, named properties, or both:\n"
        "    //    11.1. Set instance.[[GetOwnProperty]] as defined in § 3.9.1 [[GetOwnProperty]].\n"
        "    //    11.2. Set instance.[[Set]] as defined in § 3.9.2 [[Set]].\n"
        "    //    11.3. Set instance.[[DefineOwnProperty]] as defined in § 3.9.3 [[DefineOwnProperty]].\n"
        "    //    11.4. Set instance.[[Delete]] as defined in § 3.9.4 [[Delete]].\n"
        "    //    11.5. Set instance.[[PreventExtensions]] as defined in § 3.9.5 [[PreventExtensions]].\n"
        "    //    11.6. Set instance.[[OwnPropertyKeys]] as defined in § 3.9.6 [[OwnPropertyKeys]].\n"
        "\n"
        "    return *impl;\n"
        "}\n"
    )
