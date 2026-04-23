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
    if interface.name in ("WorkerGlobalScope", "EventTarget"):
        return True
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
        # IDLGenerators.cpp:5814-5820 — global interfaces close the prototype class
        # early, then optionally emit the named-properties-object declarations.
        g.append("\n};\n")
        if interface.named_property_getter is not None:
            _generate_named_properties_object_declarations(interface, g)
        return

    # Non-global path: generate_prototype_or_global_mixin_declarations
    # IDLGenerators.cpp:3246-3364.
    _generate_prototype_or_global_mixin_declarations(interface, g)


# Port of IDLGenerators.cpp:3246-3364. Emits the per-member
# JS_DECLARE_NATIVE_FUNCTION lines that go inside the prototype/global-mixin
# class body.
def _generate_prototype_or_global_mixin_declarations(interface: Interface, generator: SourceGenerator) -> None:
    g = generator.fork()

    # Operations (one declaration per overload set, plus one per overload
    # if the set is bigger than one). Operation names are emitted in
    # snake_case via _make_input_acceptable_cpp.
    overload_sets: dict[str, list] = {}
    for op in interface.operations:
        if "FIXME" in op.extended_attributes:
            continue
        overload_sets.setdefault(op.name, []).append(op)

    for name, overloads in overload_sets.items():
        fg = g.fork()
        fg.set("function.name:snakecase", _make_input_acceptable_cpp(_to_snakecase(name)))
        # IDLGenerators.cpp:3253-3255 — note the trailing 8-space indent inside
        # the raw string, which lands on the next line as visible whitespace.
        fg.append("\n    JS_DECLARE_NATIVE_FUNCTION(@function.name:snakecase@);\n        ")
        if len(overloads) > 1:
            for i in range(len(overloads)):
                fg.set("overload_suffix", str(i))
                fg.append("\n    JS_DECLARE_NATIVE_FUNCTION(@function.name:snakecase@@overload_suffix@);\n")

    if interface.has_stringifier:
        sg = g.fork()
        sg.append("\n    JS_DECLARE_NATIVE_FUNCTION(to_string);\n        ")

    if interface.pair_iterator_types is not None:
        ig = g.fork()
        ig.append(
            "\n    JS_DECLARE_NATIVE_FUNCTION(entries);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(for_each);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(keys);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(values);\n        "
        )

    if interface.async_value_iterator_type is not None:
        ig = g.fork()
        ig.append("\n    JS_DECLARE_NATIVE_FUNCTION(values);\n        ")

    # IDLGenerators.cpp:3290-3315 — setlike declarations.
    if interface.set_entry_type is not None:
        sg = g.fork()
        sg.append(
            "\n    JS_DECLARE_NATIVE_FUNCTION(get_size);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(entries);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(values);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(for_each);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(has);\n"
        )
        op_names = {op.name for op in interface.operations}
        if "add" not in op_names and not interface.is_set_readonly:
            sg.append("\n    JS_DECLARE_NATIVE_FUNCTION(add);\n")
        if "delete" not in op_names and not interface.is_set_readonly:
            sg.append("\n    JS_DECLARE_NATIVE_FUNCTION(delete_);\n")
        if "clear" not in op_names and not interface.is_set_readonly:
            sg.append("\n    JS_DECLARE_NATIVE_FUNCTION(clear);\n")

    # IDLGenerators.cpp:3317-3344 — maplike declarations.
    if interface.map_key_type is not None:
        mg = g.fork()
        mg.append(
            "\n    JS_DECLARE_NATIVE_FUNCTION(get_size);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(entries);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(keys);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(values);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(for_each);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(get);\n"
            "    JS_DECLARE_NATIVE_FUNCTION(has);\n"
        )
        op_names = {op.name for op in interface.operations}
        if "set" not in op_names and not interface.is_map_readonly:
            mg.append("    JS_DECLARE_NATIVE_FUNCTION(set);\n")
        if "delete" not in op_names and not interface.is_map_readonly:
            mg.append("    JS_DECLARE_NATIVE_FUNCTION(delete_);\n")
        if "clear" not in op_names and not interface.is_map_readonly:
            mg.append("    JS_DECLARE_NATIVE_FUNCTION(clear);\n")
    # Named/indexed property getter declarations — the named getter already
    # goes through the operations loop above when it has an identifier; no
    # extra declaration needed here for the simple case.

    # Per-attribute getter/setter declarations. IDLGenerators.cpp:3340-3355.
    for attribute in interface.attributes:
        if "FIXME" in attribute.extended_attributes:
            continue
        ag = g.fork()
        # The C++ side computes getter/setter callback names via
        # `attribute_callback_name` (with `[AttributeCallbackName]` override
        # or snake_case). We replicate that name computation locally.
        cb_base = attribute.extended_attributes.get("AttributeCallbackName") or _to_snakecase(attribute.name).replace(
            "-", "_"
        )
        ag.set("attribute.getter_callback", f"{cb_base}_getter")
        ag.append("\n    JS_DECLARE_NATIVE_FUNCTION(@attribute.getter_callback@);\n")

        if (
            not attribute.readonly
            or "Replaceable" in attribute.extended_attributes
            or "PutForwards" in attribute.extended_attributes
            or "LegacyLenientSetter" in attribute.extended_attributes
        ):
            ag.set("attribute.setter_callback", f"{cb_base}_setter")
            ag.append("\n    JS_DECLARE_NATIVE_FUNCTION(@attribute.setter_callback@);\n")

    # IDLGenerators.cpp:3357-3361 — class closer + trailing blank line.
    g.append("\n\n};\n\n")
    # IDLGenerators.cpp:3363 — generate_enumerations follows.
    _generate_enumerations(interface, g)


def _generate_named_properties_object_declarations(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_named_properties_object_declarations (IDLGenerators.cpp:3554-3584)."""
    g = generator.fork()
    g.set("named_properties_class", f"{interface.name}Properties")
    g.append(
        "\n"
        "class @named_properties_class@ : public JS::Object {\n"
        "    JS_OBJECT(@named_properties_class@, JS::Object);\n"
        "    GC_DECLARE_ALLOCATOR(@named_properties_class@);\n"
        "public:\n"
        "    explicit @named_properties_class@(JS::Realm&);\n"
        "    virtual void initialize(JS::Realm&) override;\n"
        "    virtual ~@named_properties_class@() override;\n"
        "\n"
        "    JS::Realm& realm() const { return m_realm; }\n"
        "private:\n"
        "    virtual JS::ThrowCompletionOr<Optional<JS::PropertyDescriptor>> internal_get_own_property(JS::PropertyKey const&) const override;\n"
        "    virtual JS::ThrowCompletionOr<bool> internal_define_own_property(JS::PropertyKey const&, JS::PropertyDescriptor&, Optional<JS::PropertyDescriptor>* precomputed_get_own_property = nullptr) override;\n"
        "    virtual JS::ThrowCompletionOr<bool> internal_delete(JS::PropertyKey const&) override;\n"
        "    virtual JS::ThrowCompletionOr<bool> internal_set_prototype_of(JS::Object* prototype) override;\n"
        "    virtual JS::ThrowCompletionOr<bool> internal_prevent_extensions() override;\n"
        "\n"
        "    virtual bool eligible_for_own_property_enumeration_fast_path() const override final { return false; }\n"
        "\n"
        "    virtual void visit_edges(Visitor&) override;\n"
        "\n"
        "    GC::Ref<JS::Realm> m_realm; // [[Realm]]\n"
        "};\n"
    )


def _generate_named_properties_object_definitions(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_named_properties_object_definitions (IDLGenerators.cpp:3586-3728)."""
    g = generator.fork()
    g.set("name", interface.name)
    g.set("parent_name", interface.parent_name)
    g.set("prototype_base_class", interface.prototype_base_class)
    g.set("named_properties_class", f"{interface.name}Properties")
    has_unenumerable = "LegacyUnenumerableNamedProperties" in interface.extended_attributes
    g.append(
        "\n"
        "GC_DEFINE_ALLOCATOR(@named_properties_class@);\n"
        "\n"
        "@named_properties_class@::@named_properties_class@(JS::Realm& realm)\n"
        "  : JS::Object(realm, nullptr, MayInterfereWithIndexedPropertyAccess::Yes)\n"
        "  , m_realm(realm)\n"
        "{\n"
        "}\n"
        "\n"
        "@named_properties_class@::~@named_properties_class@()\n"
        "{\n"
        "}\n"
        "\n"
        "void @named_properties_class@::initialize(JS::Realm& realm)\n"
        "{\n"
        "    Base::initialize(realm);\n"
        "    auto& vm = realm.vm();\n"
        "\n"
        '    // The class string of a named properties object is the concatenation of the interface\'s identifier and the string "Properties".\n'
        '    define_direct_property(vm.well_known_symbol_to_string_tag(), JS::PrimitiveString::create(vm, "@named_properties_class@"_string), JS::Attribute::Configurable);\n'
    )
    if interface.prototype_base_class == "ObjectPrototype":
        g.append("\n\n    set_prototype(realm.intrinsics().object_prototype());\n")
    else:
        g.append(
            '\n\n    set_prototype(&ensure_web_prototype<@prototype_base_class@>(realm, "@parent_name@"_fly_string));\n'
        )
    enumerable_val = "false" if has_unenumerable else "true"
    g.append(
        "\n"
        "};\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#named-properties-object-getownproperty\n"
        "JS::ThrowCompletionOr<Optional<JS::PropertyDescriptor>> @named_properties_class@::internal_get_own_property(JS::PropertyKey const& property_name) const\n"
        "{\n"
        "    auto& realm = this->realm();\n"
        "\n"
        "    // 1. Let A be the interface for the named properties object O.\n"
        "    using A = @name@;\n"
        "\n"
        "    // 2. Let object be O.[[Realm]]'s global object.\n"
        "    // 3. Assert: object implements A.\n"
        "    auto& object = as<A>(realm.global_object());\n"
        "\n"
        "    // 4. If the result of running the named property visibility algorithm with property name P and object object is true, then:\n"
        "    if (TRY(object.is_named_property_exposed_on_object(property_name))) {\n"
        "        auto property_name_string = property_name.to_string().to_utf8_but_should_be_ported_to_utf16();\n"
        "\n"
        "        // 1. Let operation be the operation used to declare the named property getter.\n"
        "        // 2. Let value be an uninitialized variable.\n"
        "        // 3. If operation was defined without an identifier, then set value to the result of performing the steps listed in the interface description to determine the value of a named property with P as the name.\n"
        "        // 4. Otherwise, operation was defined with an identifier. Set value to the result of performing the method steps of operation with « P » as the only argument value.\n"
        "        auto value = object.named_item_value(property_name_string);\n"
        "\n"
        "        // 5. Let desc be a newly created Property Descriptor with no fields.\n"
        "        JS::PropertyDescriptor descriptor;\n"
        "\n"
        "        // 6. Set desc.[[Value]] to the result of converting value to an ECMAScript value.\n"
        "        descriptor.value = value;\n"
        "\n"
        f"        // 7. If A implements an interface with the [LegacyUnenumerableNamedProperties] extended attribute, then set desc.[[Enumerable]] to false, otherwise set it to true.\n"
        f"        descriptor.enumerable = {enumerable_val};\n"
        "\n"
        "        // 8. Set desc.[[Writable]] to true and desc.[[Configurable]] to true.\n"
        "        descriptor.writable = true;\n"
        "        descriptor.configurable = true;\n"
        "\n"
        "        // 9. Return desc.\n"
        "        return descriptor;\n"
        "    }\n"
        "\n"
        "    // 5. Return OrdinaryGetOwnProperty(O, P).\n"
        "    return JS::Object::internal_get_own_property(property_name);\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#named-properties-object-defineownproperty\n"
        "JS::ThrowCompletionOr<bool> @named_properties_class@::internal_define_own_property(JS::PropertyKey const&, JS::PropertyDescriptor&, Optional<JS::PropertyDescriptor>*)\n"
        "{\n"
        "    // 1. Return false.\n"
        "    return false;\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#named-properties-object-delete\n"
        "JS::ThrowCompletionOr<bool> @named_properties_class@::internal_delete(JS::PropertyKey const&)\n"
        "{\n"
        "    // 1. Return false.\n"
        "    return false;\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#named-properties-object-setprototypeof\n"
        "JS::ThrowCompletionOr<bool> @named_properties_class@::internal_set_prototype_of(JS::Object* prototype)\n"
        "{\n"
        "    // 1. If O’s associated realm’s is global prototype chain mutable is true, return ? OrdinarySetPrototypeOf(O, V).\n"
        "    // NB: This is only ever true for ShadowRealms.\n"
        "\n"
        "    // 2. Return ? SetImmutablePrototype(O, V).\n"
        "    return set_immutable_prototype(prototype);\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#named-properties-object-preventextensions\n"
        "JS::ThrowCompletionOr<bool> @named_properties_class@::internal_prevent_extensions()\n"
        "{\n"
        "    // 1. Return false.\n"
        "    // Note: this keeps named properties object extensible by making [[PreventExtensions]] fail.\n"
        "    return false;\n"
        "}\n"
        "\n"
        "void @named_properties_class@::visit_edges(Visitor& visitor)\n"
        "{\n"
        "    Base::visit_edges(visitor);\n"
        "    visitor.visit(m_realm);\n"
        "}\n"
    )


# Port of IDLGenerators.cpp:3162-3171 (get_best_value_for_underlying_enum_type).
def _best_underlying_enum_type(size: int) -> str:
    if size < 0xFF:
        return "u8"
    if size < 0xFFFF:
        return "u16"
    raise AssertionError(f"enum too large: {size}")


# Port of IDLGenerators.cpp:3201-3244 (generate_enumerations). Called from
# both generate_prototype_or_global_mixin_declarations and a couple of other
# spots (they emit the same enum-type definitions in different files). Skips
# enums that aren't original definitions (those came in from #imports).
def _generate_enumerations(interface: Interface, generator: SourceGenerator) -> None:
    g = generator.fork()
    for type_name, enumeration in interface.enumerations.items():
        if not enumeration.is_original_definition:
            continue
        eg = g.fork()
        eg.set("enum.type.name", type_name)
        eg.set("enum.underlying_type", _best_underlying_enum_type(len(enumeration.translated_cpp_names)))
        eg.append("\nenum class @enum.type.name@ : @enum.underlying_type@ {\n")
        for cpp_name in enumeration.translated_cpp_names.values():
            eg.set("enum.entry", cpp_name)
            eg.append("\n    @enum.entry@,\n")
        eg.append("\n};\n")
        eg.append("\ninline String idl_enum_to_string(@enum.type.name@ value)\n{\n    switch (value) {\n")
        for value, cpp_name in enumeration.translated_cpp_names.items():
            eg.set("enum.entry", cpp_name)
            eg.set("enum.string", value)
            eg.append('\n    case @enum.type.name@::@enum.entry@:\n        return "@enum.string@"_string;\n')
        eg.append("\n    }\n    VERIFY_NOT_REACHED();\n}\n")


# IDLGenerators.cpp:make_input_acceptable_cpp helper (file-scope). Maps
# C++ keywords to a `_`-suffixed form and replaces `-` with `_`.
_CPP_KEYWORD_RESERVED = frozenset(
    {
        "break",
        "char",
        "class",
        "continue",
        "default",
        "delete",
        "for",
        "initialize",
        "inline",
        "mutable",
        "namespace",
        "operator",
        "register",
        "switch",
        "template",
    }
)


def _make_input_acceptable_cpp(s: str) -> str:
    if s in _CPP_KEYWORD_RESERVED:
        return s + "_"
    return s.replace("-", "_")


def _to_snakecase(s: str) -> str:
    """Mirror AK::String::to_snakecase.

    Inserts `_` before each uppercase letter (except at the start), then
    lowercases. Adjacent uppercases stay together until the *last* one in
    a run, so "URLSearchParams" → "url_search_params" and "HTMLElement"
    → "html_element".
    """
    # Mirror AK::StringUtils::to_snakecase (StringUtils.cpp:306-330) exactly.
    if not s:
        return s
    out: list[str] = []
    for i, ch in enumerate(s):
        insert = False
        if i > 0:
            prev = s[i - 1]
            if prev.isascii() and prev.islower() and prev.isalpha() and ch.isascii() and ch.isupper() and ch.isalpha():
                insert = True
            elif i < len(s) - 1:
                nxt = s[i + 1]
                if ch.isascii() and ch.isupper() and ch.isalpha() and nxt.isascii() and nxt.islower() and nxt.isalpha():
                    insert = True
        if insert:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def generate_prototype_implementation(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_prototype_implementation (IDLGenerators.cpp:5826-5912).

    Empty-interface case only at this rung: no Global, no DOMException,
    no parent_name, no callback interface body.
    """
    g = generator.fork()
    g.set("parent_name", interface.parent_name)
    g.set("prototype_class", interface.prototype_class)
    g.set("prototype_base_class", interface.prototype_base_class)
    has_immutable_prototype = _interface_prototype_has_immutable_prototype(interface)

    # IDLGenerators.cpp:5835-5853 — allocator, ctor signature.
    g.append(
        "\n"
        "GC_DEFINE_ALLOCATOR(@prototype_class@);\n"
        "\n"
        "@prototype_class@::@prototype_class@([[maybe_unused]] JS::Realm& realm)"
    )
    if interface.name == "DOMException":
        g.append("\n    : Object(ConstructWithPrototypeTag::Tag, realm.intrinsics().error_prototype())\n")
    elif interface.parent_name:
        g.append("\n    : Object(realm, nullptr)\n")
    else:
        g.append("\n    : Object(ConstructWithPrototypeTag::Tag, realm.intrinsics().object_prototype())\n")

    # IDLGenerators.cpp:5855-5862 — ctor + dtor bodies.
    g.append("\n{\n}\n\n@prototype_class@::~@prototype_class@()\n{\n}\n")

    if has_immutable_prototype:
        g.append(
            "\n"
            "\n"
            "// https://webidl.spec.whatwg.org/#es-interface-prototype-object\n"
            "JS::ThrowCompletionOr<bool> @prototype_class@::internal_set_prototype_of(JS::Object* prototype)\n"
            "{\n"
            "    return set_immutable_prototype(prototype);\n"
            "}\n"
        )

    is_global_interface = "Global" in interface.extended_attributes
    if is_global_interface:
        # IDLGenerators.cpp:5877-5898 — mostly-empty prototype for [Global] interfaces.
        supports_named = interface.named_property_getter is not None
        g.append("\nvoid @prototype_class@::initialize(JS::Realm& realm)\n{\n    Base::initialize(realm);\n")
        if supports_named:
            named_props_class = f"{interface.name}Properties"
            g.set("named_properties_class", named_props_class)
            g.set("namespaced_name", interface.namespaced_name)
            g.append(
                "\n"
                '    define_direct_property(vm().well_known_symbol_to_string_tag(), JS::PrimitiveString::create(vm(), "@namespaced_name@"_string), JS::Attribute::Configurable);\n'
                '    set_prototype(&ensure_web_prototype<@prototype_class@>(realm, "@named_properties_class@"_fly_string));\n'
            )
        else:
            g.append(
                '\n    set_prototype(&ensure_web_prototype<@prototype_base_class@>(realm, "@parent_name@"_fly_string));\n'
            )
        g.append("\n}\n")
        if supports_named:
            _generate_named_properties_object_definitions(interface, g)
        return
    if interface.is_callback_interface:
        # Simple callback-interface path (IDLGenerators.cpp:5901-5907).
        g.append(
            "\n"
            "void @prototype_class@::initialize(JS::Realm& realm)\n"
            "{\n"
            "    Base::initialize(realm);\n"
            "    set_prototype(realm.intrinsics().object_prototype());\n"
            "}\n"
        )
        return

    # Non-global, non-callback path: two initialize calls (No / Yes
    # GenerateUnforgeables) + definitions. For the empty-interface rung the
    # body is the simplest possible.
    _generate_prototype_or_global_mixin_initialization(interface, g, generate_unforgeables=False)
    _generate_prototype_or_global_mixin_initialization(interface, g, generate_unforgeables=True)
    # IDLGenerators.cpp:5911 calls generate_prototype_or_global_mixin_definitions.
    _generate_prototype_or_global_mixin_definitions(interface, g)


def _generate_prototype_or_global_mixin_definitions(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_prototype_or_global_mixin_definitions
    (IDLGenerators.cpp:4408-...).

    Currently emits only the per-attribute getter (and a not-yet-supported
    raise for setters and operations). Subsequent rungs add operation
    bodies, attribute setters, stringifier, iterators, setlike/maplike,
    named/indexed property handlers.
    """
    # Named/indexed property handlers: the named getter body itself is emitted
    # by the regular operations loop. The special iteration-method define_direct_property
    # is emitted in _generate_prototype_or_global_mixin_initialization.
    is_global_interface = "Global" in interface.extended_attributes
    class_name = interface.global_mixin_class if is_global_interface else interface.prototype_class

    if interface.pair_iterator_types is not None:
        generator.set("iterator_name", f"{interface.name}Iterator")

    # IDLGenerators.cpp:4429-4456 — impl_from helpers, emitted only when the
    # interface has at least one member that needs `impl_from`.
    needs_impl_from = bool(
        interface.attributes
        or interface.operations
        or interface.has_stringifier
        or interface.pair_iterator_types is not None
        or interface.async_value_iterator_type is not None
        or interface.set_entry_type is not None
        or interface.map_key_type is not None
    )
    if needs_impl_from:
        ig = generator.fork()
        ig.set("fully_qualified_name", interface.fully_qualified_name)
        ig.set("namespaced_name", interface.namespaced_name)
        ig.append(
            "\n"
            "[[maybe_unused]] static JS::ThrowCompletionOr<@fully_qualified_name@*> impl_from(JS::VM& vm, JS::Value js_value)\n"
            "{\n"
        )
        if interface.name in ("EventTarget", "Window"):
            ig.append(
                "\n"
                "    if (auto window_proxy = js_value.as_if<HTML::WindowProxy>())\n"
                "        return window_proxy->window().ptr();\n"
            )
        ig.append(
            "\n"
            "    if (auto impl = js_value.as_if<@fully_qualified_name@>())\n"
            "        return impl.ptr();\n"
            '    return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@namespaced_name@");\n'
            "}\n"
            "\n"
            "[[maybe_unused]] static JS::ThrowCompletionOr<@fully_qualified_name@*> impl_from(JS::VM& vm)\n"
            "{\n"
            "    auto this_value = vm.this_value();\n"
            "    if (this_value.is_nullish())\n"
            "        this_value = &vm.current_realm()->global_object();\n"
            "    return impl_from(vm, this_value);\n"
            "}\n"
            "\n"
        )

    for attribute in interface.attributes:
        if "FIXME" in attribute.extended_attributes:
            # FIXME-marked attributes are already handled in the init body
            # via the Unimplemented placeholder; no body needed.
            continue
        _generate_attribute_getter(attribute, interface, class_name, generator)
        if not attribute.readonly or any(
            k in attribute.extended_attributes for k in ("Replaceable", "PutForwards", "LegacyLenientSetter")
        ):
            _generate_attribute_setter(attribute, interface, class_name, generator)

    # IDLGenerators.cpp:4866-4886 — per-operation bodies + overload arbiters.
    from .operations import generate_function

    overload_groups: dict[str, list] = {}
    for op in interface.operations:
        if "FIXME" in op.extended_attributes:
            continue
        overload_groups.setdefault(op.name, []).append(op)

    for op in interface.operations:
        if "FIXME" in op.extended_attributes:
            continue
        if "Default" in op.extended_attributes:
            if op.name == "toJSON" and op.return_type is not None and op.return_type.name == "object":
                _generate_default_to_json_function(class_name, interface, generator)
                continue
            raise NotImplementedError(f"[Default] {op.name} operation not supported")
        generate_function(op, interface, class_name, static=False, generator=generator)

    # IDLGenerators.cpp:4882-4886 — overload arbiters for multi-overload sets.
    for name, group in overload_groups.items():
        if len(group) > 1:
            from .overload_arbiter import generate_overload_arbiter

            generate_overload_arbiter(
                group,
                name,
                interface,
                class_name,
                is_constructor=False,
                generator=generator,
            )

    # IDLGenerators.cpp:4888-4915 — stringifier body.
    if interface.has_stringifier:
        sg = generator.fork()
        sg.set("class_name", class_name)
        from .prototype import _to_snakecase as _snake

        if interface.stringifier_attribute is not None:
            sg.set("attribute.cpp_getter_name", _snake(interface.stringifier_attribute.name))
        sg.append(
            "\n"
            "JS_DEFINE_NATIVE_FUNCTION(@class_name@::to_string)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@class_name@::to_string");\n'
            "    [[maybe_unused]] auto& realm = *vm.current_realm();\n"
            "    auto* impl = TRY(impl_from(vm));\n"
            "\n"
        )
        if interface.stringifier_attribute is not None:
            sg.append(
                "\n    auto retval = TRY(throw_dom_exception_if_needed(vm, [&] { return impl->@attribute.cpp_getter_name@(); }));\n"
            )
        else:
            sg.append(
                "\n    auto retval = TRY(throw_dom_exception_if_needed(vm, [&] { return impl->to_string(); }));\n"
            )
        sg.append("\n\n    return JS::PrimitiveString::create(vm, move(retval));\n}\n")

    # IDLGenerators.cpp:4917-4970 — pair iterator function bodies.
    if interface.pair_iterator_types is not None:
        _generate_pair_iterator_definitions(interface, class_name, generator)

    # IDLGenerators.cpp:4971-4996 — async iterator values body.
    if interface.async_value_iterator_type is not None:
        _generate_async_iterator_values_definitions(interface, class_name, generator)

    # IDLGenerators.cpp:4998-5155 — setlike function bodies.
    if interface.set_entry_type is not None:
        _generate_setlike_definitions(interface, class_name, generator)

    # IDLGenerators.cpp:5157-5319 — maplike function bodies.
    if interface.map_key_type is not None:
        _generate_maplike_definitions(interface, class_name, generator)

    # IDLGenerators.cpp:3174-3200 — [GenerateToValue] dictionaries.
    _generate_dictionaries(interface, generator)


def _generate_setlike_definitions(interface: Interface, class_name: str, generator: SourceGenerator) -> None:
    """Port of IDLGenerators.cpp:4998-5155 (setlike function bodies)."""
    set_entry_type = interface.set_entry_type
    assert set_entry_type is not None
    sg = generator.fork()
    sg.set("class_name", class_name)
    if set_entry_type.kind == "plain" and (
        set_entry_type.name in ("DOMString", "USVString", "ByteString", "CSSOMString")
        or set_entry_type.name in interface.enumerations
    ):
        value_type_check = (
            "\n"
            "    if (!value_arg.is_string()) {\n"
            '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "String");\n'
            "    }\n"
        )
    else:
        type_name = set_entry_type.name
        value_type_check = (
            f"\n"
            f"    if (!value_arg.is_object() || !is<{type_name}>(value_arg.as_object())) {{\n"
            f'        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "{type_name}");\n'
            f"    }}\n"
        )
    sg.set("value_type_check", value_type_check)
    sg.append(
        "\n"
        "// https://webidl.spec.whatwg.org/#js-set-size\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::get_size)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::size");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Set> set = impl->set_entries();\n"
        "\n"
        "    return set->set_size();\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-set-entries\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::entries)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::entries");\n'
        "    auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Set> set = impl->set_entries();\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return JS::SetIterator::create(realm, *set, Object::PropertyKind::KeyAndValue); }));\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-set-values\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::values)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::values");\n'
        "    auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Set> set = impl->set_entries();\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return JS::SetIterator::create(realm, *set, Object::PropertyKind::Value); }));\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-set-forEach\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::for_each)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::for_each");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Set> set = impl->set_entries();\n"
        "\n"
        "    auto callback = vm.argument(0);\n"
        "    if (!callback.is_function())\n"
        "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAFunction, callback);\n"
        "\n"
        "    for (auto& entry : *set) {\n"
        "        auto value = entry.key;\n"
        "        TRY(JS::call(vm, callback.as_function(), vm.argument(1), value, value, impl));\n"
        "    }\n"
        "\n"
        "    return JS::js_undefined();\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-set-has\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::has)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::has");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Set> set = impl->set_entries();\n"
        "\n"
        "    auto value_arg = vm.argument(0);\n"
        "    @value_type_check@\n"
        "\n"
        "    // FIXME: If value is -0, set value to +0.\n"
        "    // What? Which interfaces have a number as their set type?\n"
        "\n"
        "    return set->set_has(value_arg);\n"
        "}\n"
    )
    op_names = {op.name for op in interface.operations}
    if "add" not in op_names and not interface.is_set_readonly:
        sg.append(
            "\n"
            "// https://webidl.spec.whatwg.org/#js-set-add\n"
            "JS_DEFINE_NATIVE_FUNCTION(@class_name@::add)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@class_name@::add");\n'
            "    auto* impl = TRY(impl_from(vm));\n"
            "\n"
            "    GC::Ref<JS::Set> set = impl->set_entries();\n"
            "\n"
            "    auto value_arg = vm.argument(0);\n"
            "    @value_type_check@\n"
            "\n"
            "    // FIXME: If value is -0, set value to +0.\n"
            "    // What? Which interfaces have a number as their set type?\n"
            "\n"
            "    set->set_add(value_arg);\n"
            "    impl->on_set_modified_from_js({});\n"
            "\n"
            "    return impl;\n"
            "}\n"
        )
    if "delete" not in op_names and not interface.is_set_readonly:
        sg.append(
            "\n"
            "// https://webidl.spec.whatwg.org/#js-set-delete\n"
            "JS_DEFINE_NATIVE_FUNCTION(@class_name@::delete_)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@class_name@::delete_");\n'
            "    auto* impl = TRY(impl_from(vm));\n"
            "\n"
            "    GC::Ref<JS::Set> set = impl->set_entries();\n"
            "\n"
            "    auto value_arg = vm.argument(0);\n"
            "    @value_type_check@\n"
            "\n"
            "    // FIXME: If value is -0, set value to +0.\n"
            "    // What? Which interfaces have a number as their set type?\n"
            "\n"
            "    auto result = set->set_remove(value_arg);\n"
            "    impl->on_set_modified_from_js({});\n"
            "    return result;\n"
            "}\n"
        )
    if "clear" not in op_names and not interface.is_set_readonly:
        sg.append(
            "\n"
            "// https://webidl.spec.whatwg.org/#js-set-clear\n"
            "JS_DEFINE_NATIVE_FUNCTION(@class_name@::clear)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@class_name@::clear");\n'
            "    auto* impl = TRY(impl_from(vm));\n"
            "\n"
            "    GC::Ref<JS::Set> set = impl->set_entries();\n"
            "\n"
            "    set->set_clear();\n"
            "    impl->on_set_modified_from_js({});\n"
            "\n"
            "    return JS::js_undefined();\n"
            "}\n"
        )


def _generate_maplike_definitions(interface: Interface, class_name: str, generator: SourceGenerator) -> None:
    """Port of IDLGenerators.cpp:5157-5319 (maplike function bodies)."""
    map_key_type = interface.map_key_type
    map_value_type = interface.map_value_type
    assert map_key_type is not None
    assert map_value_type is not None
    mg = generator.fork()
    mg.set("class_name", class_name)

    if map_key_type.kind == "plain" and map_key_type.name in ("DOMString", "USVString", "ByteString", "CSSOMString"):
        key_arg_converted = "JS::PrimitiveString::create(vm, TRY(key_arg.to_string(vm)));"
    else:
        raise NotImplementedError(f"maplike with non-string key type {map_key_type.name}")

    mg.set("key_arg_converted_to_idl_type", key_arg_converted)
    mg.append(
        "\n"
        "// https://webidl.spec.whatwg.org/#js-map-size\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::get_size)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::size");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Map> map = impl->map_entries();\n"
        "\n"
        "    return map->map_size();\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-map-entries\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::entries)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::entries");\n'
        "    auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Map> map = impl->map_entries();\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return JS::MapIterator::create(realm, *map, Object::PropertyKind::KeyAndValue); }));\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-map-keys\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::keys)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::keys");\n'
        "    auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Map> map = impl->map_entries();\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return JS::MapIterator::create(realm, *map, Object::PropertyKind::Key); }));\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-map-values\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::values)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::values");\n'
        "    auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Map> map = impl->map_entries();\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return JS::MapIterator::create(realm, *map, Object::PropertyKind::Value); }));\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-map-forEach\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::for_each)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::for_each");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Map> map = impl->map_entries();\n"
        "\n"
        "    auto callback = vm.argument(0);\n"
        "    if (!callback.is_function())\n"
        "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAFunction, callback);\n"
        "\n"
        "    for (auto& entry : *map)\n"
        "        TRY(JS::call(vm, callback.as_function(), vm.argument(1), entry.key, entry.value, impl));\n"
        "\n"
        "    return JS::js_undefined();\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-map-get\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::get)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::get");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Map> map = impl->map_entries();\n"
        "\n"
        "    auto key_arg = vm.argument(0);\n"
        "    auto key = @key_arg_converted_to_idl_type@\n"
        "\n"
        "    // FIXME: If key is -0, set key to +0.\n"
        "    // What? Which interfaces have a number as their map key type?\n"
        "\n"
        "    auto result = map->map_get(key);\n"
        "\n"
        "    if (!result.has_value())\n"
        "        return JS::js_undefined();\n"
        "\n"
        "    return result.release_value();\n"
        "}\n"
        "\n"
        "// https://webidl.spec.whatwg.org/#js-map-has\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::has)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::has");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    GC::Ref<JS::Map> map = impl->map_entries();\n"
        "\n"
        "    auto key_arg = vm.argument(0);\n"
        "    auto key = @key_arg_converted_to_idl_type@\n"
        "\n"
        "    // FIXME: If key is -0, set key to +0.\n"
        "    // What? Which interfaces have a number as their map key type?\n"
        "\n"
        "    return map->map_has(key);\n"
        "}\n"
    )
    op_names = {op.name for op in interface.operations}
    if "delete" not in op_names and not interface.is_map_readonly:
        mg.append(
            "\n"
            "// https://webidl.spec.whatwg.org/#js-map-delete\n"
            "JS_DEFINE_NATIVE_FUNCTION(@class_name@::delete_)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@class_name@::delete_");\n'
            "    auto* impl = TRY(impl_from(vm));\n"
            "\n"
            "    GC::Ref<JS::Map> map = impl->map_entries();\n"
            "\n"
            "    auto key_arg = vm.argument(0);\n"
            "    auto key = @key_arg_converted_to_idl_type@\n"
            "\n"
            "    // FIXME: If key is -0, set key to +0.\n"
            "    // What? Which interfaces have a number as their map key type?\n"
            "\n"
            "    auto result = map->map_remove(key);\n"
            "    impl->on_map_modified_from_js({});\n"
            "\n"
            "    return result;\n"
            "}\n"
        )
    if "clear" not in op_names and not interface.is_map_readonly:
        mg.append(
            "\n"
            "// https://webidl.spec.whatwg.org/#js-map-clear\n"
            "JS_DEFINE_NATIVE_FUNCTION(@class_name@::clear)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@class_name@::clear");\n'
            "    auto* impl = TRY(impl_from(vm));\n"
            "\n"
            "    GC::Ref<JS::Map> map = impl->map_entries();\n"
            "\n"
            "    map->map_clear();\n"
            "    impl->on_map_modified_from_js({});\n"
            "\n"
            "    return JS::js_undefined();\n"
            "}\n"
        )


def _generate_dictionaries(interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_dictionaries (IDLGenerators.cpp:3174-3200).

    Emits a <snake_name>_to_value helper for each dictionary marked [GenerateToValue].
    """
    from ..ast import Type
    from .types import generate_wrap_statement

    for dict_name, dictionary in interface.dictionaries.items():
        if not dictionary.is_original_definition:
            continue
        if "GenerateToValue" not in dictionary.extended_attributes:
            continue

        snake_name = _make_input_acceptable_cpp(_to_snakecase(dict_name))
        dict_type_name = _make_input_acceptable_cpp(dict_name)

        dg = generator.fork()
        dg.set("dictionary.name", dict_type_name)
        dg.set("dictionary.name:snakecase", snake_name)
        dg.append(
            "\n"
            "JS::Value @dictionary.name:snakecase@_to_value(JS::Realm&, @dictionary.name@ const&);\n"
            "JS::Value @dictionary.name:snakecase@_to_value(JS::Realm& realm, @dictionary.name@ const& dictionary)\n"
            "{\n"
            "    auto& vm = realm.vm();\n"
            "    @dictionary.name@ copy = dictionary;\n"
        )
        dict_type = Type(name=dict_name, nullable=False)
        generate_wrap_statement(dg, "copy", dict_type, interface, "return")
        dg.append("\n}\n")


def _generate_pair_iterator_definitions(interface: Interface, class_name: str, generator: SourceGenerator) -> None:
    """Port of the pair-iterator function bodies (IDLGenerators.cpp:4917-4970)."""
    from .types import generate_wrap_statement

    ig = generator.fork()
    ig.set("class_name", class_name)

    key_type, value_type = interface.pair_iterator_types

    ig.append(
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::entries)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::entries");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return @iterator_name@::create(*impl, Object::PropertyKind::KeyAndValue); }));\n"
        "}\n"
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::for_each)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::for_each");\n'
        "    [[maybe_unused]] auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    auto callback = vm.argument(0);\n"
        "    if (!callback.is_function())\n"
        "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAFunction, callback);\n"
        "\n"
        "    auto this_value = vm.this_value();\n"
        "    TRY(impl->for_each([&](auto key, auto value) -> JS::ThrowCompletionOr<void> {\n"
    )

    # generate_variable_statement for key
    ig2 = ig.fork()
    ig2.set("variable_name", "wrapped_key")
    ig2.append("\n    JS::Value @variable_name@;\n")
    generate_wrap_statement(ig, "key", key_type, interface, "wrapped_key = ")

    # generate_variable_statement for value
    ig3 = ig.fork()
    ig3.set("variable_name", "wrapped_value")
    ig3.append("\n    JS::Value @variable_name@;\n")
    generate_wrap_statement(ig, "value", value_type, interface, "wrapped_value = ")

    ig.append(
        "\n"
        "        TRY(JS::call(vm, callback.as_function(), vm.argument(1), wrapped_value, wrapped_key, this_value));\n"
        "        return {};\n"
        "    }));\n"
        "\n"
        "    return JS::js_undefined();\n"
        "}\n"
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::keys)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::keys");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return @iterator_name@::create(*impl, Object::PropertyKind::Key);  }));\n"
        "}\n"
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::values)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::values");\n'
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    return TRY(throw_dom_exception_if_needed(vm, [&] { return @iterator_name@::create(*impl, Object::PropertyKind::Value); }));\n"
        "}\n"
    )


def _generate_async_iterator_values_definitions(
    interface: Interface, class_name: str, generator: SourceGenerator
) -> None:
    """Port of the async-iterator values body (IDLGenerators.cpp:4971-4996)."""
    from .to_cpp import generate_arguments

    ig = generator.fork()
    ig.set("class_name", class_name)
    ig.set("iterator_name", f"{interface.name}AsyncIterator")

    ig.append(
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::values)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::values");\n'
        "    auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
    )

    args = generate_arguments(interface.async_iterator_parameters, interface, ig)

    ig.append(
        "\n    return TRY(throw_dom_exception_if_needed(vm, [&] { return @iterator_name@::create(realm, Object::PropertyKind::Value, *impl"
    )
    if args:
        ig.set("iterator_arguments", args)
        ig.append(", @iterator_arguments@")
    ig.append("); }));\n}\n")


def _generate_attribute_getter(attribute, interface: Interface, class_name: str, generator: SourceGenerator) -> None:
    """Port of the simple-getter slice of generate_prototype_or_global_mixin_definitions
    (IDLGenerators.cpp:4493-4856 — the non-Reflect, non-Promise, non-Cached path).
    """
    from .types import attribute_callback_basename
    from .types import attribute_cpp_name
    from .types import generate_wrap_statement

    is_cached = "CachedAttribute" in attribute.extended_attributes

    is_promise = attribute.type and attribute.type.name == "Promise"
    is_reflect = "Reflect" in attribute.extended_attributes
    if is_reflect:
        type_name = attribute.type.name if attribute.type else ""
        is_nullable = bool(getattr(attribute.type, "nullable", False))
        attr_type = attribute.type
        is_frozen_array_of_element = (
            attr_type.kind == "parameterized"
            and attr_type.name == "FrozenArray"
            and len(attr_type.parameters) == 1
            and attr_type.parameters[0].name == "Element"
            and is_nullable
        )
        # Supported reflect type slice.
        supported = (
            type_name == "DOMString"
            or type_name == "boolean"
            or type_name == "USVString"
            or type_name == "long"
            or type_name == "unsigned long"
            or (type_name == "Element" and is_nullable)
            or is_frozen_array_of_element
        )
        if not supported:
            raise NotImplementedError(f"[Reflect] getter for type '{type_name}' not yet supported ({attribute.name})")

    g = generator.fork()
    g.set("class_name", class_name)
    g.set("attribute.name", attribute.name)
    g.set("attribute.getter_callback", f"{attribute_callback_basename(attribute)}_getter")
    g.set("attribute.cpp_name", attribute_cpp_name(attribute))
    if is_reflect:
        reflect_name = attribute.extended_attributes.get("Reflect") or attribute.name.lower()
        g.set("attribute.reflect_name", reflect_name)

    g.append(
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::@attribute.getter_callback@)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::@attribute.getter_callback@");\n'
        "    [[maybe_unused]] auto& realm = *vm.current_realm();\n"
    )
    if is_promise:
        # IDLGenerators.cpp:4500-4504 — steps lambda wrapper.
        g.append("\n    auto steps = [&]() -> JS::ThrowCompletionOr<GC::Ptr<WebIDL::Promise>> {\n")
    g.append("\n    [[maybe_unused]] auto* impl = TRY(impl_from(vm));\n")

    if is_cached:
        # IDLGenerators.cpp:4511-4518 — check cached value first.
        g.append(
            "\n"
            "    auto cached_@attribute.cpp_name@ = impl->cached_@attribute.cpp_name@();\n"
            "    if (cached_@attribute.cpp_name@)\n"
            "        return cached_@attribute.cpp_name@;\n"
        )

    if is_reflect:
        type_name = attribute.type.name
        if type_name == "DOMString" and is_nullable:
            # IDLGenerators.cpp:4587-4667 — DOMString? branch.
            g.append('\n    auto content_attribute_value = impl->attribute("@attribute.reflect_name@"_fly_string);\n')
            if "Enumerated" in attribute.extended_attributes:
                enum_type = attribute.extended_attributes["Enumerated"]
                enumeration = interface.enumerations[enum_type]
                mvd = enumeration.extended_attributes.get("MissingValueDefault", "")
                ivd = enumeration.extended_attributes.get("InvalidValueDefault", "")
                valid_values = ", ".join(f'"{v}"_string' for v in enumeration.values)
                g.set("missing_enum_default_value", mvd)
                g.set("invalid_enum_default_value", ivd)
                g.set("valid_enum_values", valid_values)
                g.append('\n    auto retval = impl->attribute("@attribute.reflect_name@"_fly_string);\n')
                g.append("\n    Array valid_values { @valid_enum_values@ };\n    ")
                if "InvalidValueDefault" in enumeration.extended_attributes:
                    g.append(
                        "\n"
                        "\n"
                        "    if (retval.has_value()) {\n"
                        "        auto found = false;\n"
                        "        for (auto const& value : valid_values) {\n"
                        "            if (value.equals_ignoring_ascii_case(retval.value())) {\n"
                        "                found = true;\n"
                        "                retval = value;\n"
                        "                break;\n"
                        "            }\n"
                        "        }\n"
                        "\n"
                        "        if (!found)\n"
                        '            retval = "@invalid_enum_default_value@"_string;\n'
                        "    }\n"
                        "    "
                    )
                if "MissingValueDefault" in enumeration.extended_attributes:
                    g.append(
                        '\n    if (!retval.has_value())\n        retval = "@missing_enum_default_value@"_string;\n    '
                    )
                g.append("\n    VERIFY(!retval.has_value() || valid_values.contains_slow(retval.value()));\n")
            else:
                g.append("\n    auto retval = move(content_attribute_value);\n")
        elif type_name == "DOMString":
            g.append(
                "\n"
                '    auto contentAttributeValue = impl->attribute("@attribute.reflect_name@"_fly_string);\n'
                "\n"
                "    auto retval = contentAttributeValue.value_or(String {});\n"
            )
            if "Enumerated" in attribute.extended_attributes:
                enum_type = attribute.extended_attributes["Enumerated"]
                enumeration = interface.enumerations[enum_type]
                mvd = enumeration.extended_attributes.get("MissingValueDefault", "")
                ivd = enumeration.extended_attributes.get("InvalidValueDefault", "")
                valid_values = ", ".join(f'"{v}"_string' for v in enumeration.values)
                g.set("missing_enum_default_value", mvd)
                g.set("invalid_enum_default_value", ivd)
                g.set("valid_enum_values", valid_values)
                g.append(
                    "\n"
                    "    auto did_set_to_missing_value = false;\n"
                    "    if (!contentAttributeValue.has_value()) {\n"
                    '        retval = "@missing_enum_default_value@"_string;\n'
                    "        did_set_to_missing_value = true;\n"
                    "    }\n"
                    "\n"
                    "    Array valid_values { @valid_enum_values@ };\n"
                    "\n"
                    "    auto has_keyword = false;\n"
                    "    for (auto const& value : valid_values) {\n"
                    "        if (value.equals_ignoring_ascii_case(retval)) {\n"
                    "            has_keyword = true;\n"
                    "            retval = value;\n"
                    "            break;\n"
                    "        }\n"
                    "    }\n"
                    "\n"
                    "    if (!has_keyword && !did_set_to_missing_value)\n"
                    '        retval = "@invalid_enum_default_value@"_string;\n'
                    "    "
                )
        elif type_name == "boolean":
            g.append('\n    auto retval = impl->has_attribute("@attribute.reflect_name@"_fly_string);\n')
        elif type_name == "long":
            g.append(
                "\n"
                "    i32 retval = 0;\n"
                '    auto content_attribute_value = impl->get_attribute("@attribute.reflect_name@"_fly_string);\n'
                "    if (content_attribute_value.has_value()) {\n"
                "        auto maybe_parsed_value = Web::HTML::parse_integer(*content_attribute_value);\n"
                "        if (maybe_parsed_value.has_value())\n"
                "            retval = *maybe_parsed_value;\n"
                "    }\n"
            )
        elif type_name == "unsigned long":
            g.append(
                "\n"
                "    u32 retval = 0;\n"
                '    auto content_attribute_value = impl->get_attribute("@attribute.reflect_name@"_fly_string);\n'
                "    u32 minimum = 0;\n"
                "    u32 maximum = 2147483647;\n"
                "    if (content_attribute_value.has_value()) {\n"
                "        auto parsed_value = Web::HTML::parse_non_negative_integer(*content_attribute_value);\n"
                "        if (parsed_value.has_value()) {\n"
                "            if (*parsed_value >= minimum && *parsed_value <= maximum) {\n"
                "                retval = *parsed_value;\n"
                "            }\n"
                "        }\n"
                "    }\n"
            )
        elif type_name == "USVString":
            g.append('\n    auto content_attribute_value = impl->attribute("@attribute.reflect_name@"_fly_string);\n')
            if "URL" in attribute.extended_attributes:
                g.append(
                    "\n"
                    "    if (!content_attribute_value.has_value())\n"
                    "        return JS::PrimitiveString::create(vm, String {});\n"
                    "\n"
                    "    auto url_string = impl->document().encoding_parse_and_serialize_url(*content_attribute_value);\n"
                    "    if (url_string.has_value())\n"
                    "        return JS::PrimitiveString::create(vm, url_string.release_value());\n"
                )
            g.append(
                "\n"
                "    String retval;\n"
                "    if (content_attribute_value.has_value())\n"
                "        retval = MUST(Infra::convert_to_scalar_value_string(*content_attribute_value));\n"
            )
        elif type_name == "Element" and is_nullable:
            # IDLGenerators.cpp:4764-4773 — Reflect Element? via get_the_attribute_associated_element.
            g.append(
                "\n"
                '    static auto content_attribute = "@attribute.reflect_name@"_fly_string;\n'
                "\n"
                "    auto retval = impl->get_the_attribute_associated_element(content_attribute, TRY(throw_dom_exception_if_needed(vm, [&] { return impl->@attribute.cpp_name@(); })));\n"
            )
        elif is_frozen_array_of_element:
            # IDLGenerators.cpp:4775-4812 — Reflect FrozenArray<Element>? via get_the_attribute_associated_elements.
            g.set("attribute.cpp_name", attribute_cpp_name(attribute))
            g.append(
                "\n"
                '    static auto content_attribute = "@attribute.reflect_name@"_fly_string;\n'
                "\n"
                "    auto retval = impl->get_the_attribute_associated_elements(content_attribute, TRY(throw_dom_exception_if_needed(vm, [&] { return impl->@attribute.cpp_name@(); })));\n"
            )
            # cached element array handling
            g.append(
                "\n"
                "    auto cached_@attribute.cpp_name@ = TRY(throw_dom_exception_if_needed(vm, [&] { return impl->cached_@attribute.cpp_name@(); }));\n"
                "    if (WebIDL::lists_contain_same_elements(cached_@attribute.cpp_name@, retval))\n"
                "        return cached_@attribute.cpp_name@;\n"
                "\n"
                "    auto result = TRY([&]() -> JS::ThrowCompletionOr<JS::Value> {\n"
            )
            generate_wrap_statement(g, "retval", attribute.type, interface, "return")
            g.append(
                "\n"
                "    }());\n"
                "\n"
                "    if (result.is_null()) {\n"
                "        TRY(throw_dom_exception_if_needed(vm, [&] { impl->set_cached_@attribute.cpp_name@({}); }));\n"
                "    } else {\n"
                "        auto& array = as<JS::Array>(result.as_object());\n"
                "        TRY(throw_dom_exception_if_needed(vm, [&] { impl->set_cached_@attribute.cpp_name@(&array); }));\n"
                "    }\n"
                "\n"
                "    return result;\n"
                "\n"
                "}\n"
            )
            return
    else:
        g.append(
            "\n    auto retval = TRY(throw_dom_exception_if_needed(vm, [&] { return impl->@attribute.cpp_name@(); }));\n"
        )
    if is_promise:
        # IDLGenerators.cpp:4810-4823 — close steps lambda + handle exception.
        g.append(
            "\n"
            "        return retval;\n"
            "    };\n"
            "\n"
            "    auto maybe_retval = steps();\n"
            "\n"
            "    // And then, if an exception E was thrown:\n"
            "    // 1. If attribute’s type is a promise type, then return ! Call(%Promise.reject%, %Promise%, «E»).\n"
            "    // 2. Otherwise, end these steps and allow the exception to propagate.\n"
            "    if (maybe_retval.is_throw_completion())\n"
            "        return WebIDL::create_rejected_promise(realm, maybe_retval.error_value())->promise();\n"
            "\n"
            "    auto retval = maybe_retval.release_value();\n"
        )

    # generate_return_statement / cache_result (IDLGenerators.cpp:4826-4840).
    if is_cached:
        cpp_name = attribute_cpp_name(attribute)
        generate_wrap_statement(g, "retval", attribute.type, interface, f"cached_{cpp_name} =")
        g.append(f"\n    impl->set_cached_{cpp_name}(cached_{cpp_name});\n    return cached_{cpp_name};\n\n}}\n")
    else:
        generate_wrap_statement(g, "retval", attribute.type, interface, "return")
        g.append("\n}\n")


def _generate_prototype_or_global_mixin_initialization(
    interface: Interface, generator: SourceGenerator, generate_unforgeables: bool
) -> None:
    """Port of generate_prototype_or_global_mixin_initialization
    (IDLGenerators.cpp:3746-4154).

    This rung (empty interface): no global, no unscopable, no constants,
    no overload sets, no attributes, no iterators, no setlike, no maplike,
    no Window-only members.
    """
    g = generator.fork()
    is_global_interface = "Global" in interface.extended_attributes
    class_name = interface.global_mixin_class if is_global_interface else interface.prototype_class
    g.set("name", interface.name)
    g.set("namespaced_name", interface.namespaced_name)
    g.set("class_name", class_name)
    g.set("fully_qualified_name", interface.fully_qualified_name)
    g.set("parent_name", interface.parent_name)
    g.set("prototype_base_class", interface.prototype_base_class)
    g.set("prototype_name", interface.prototype_class)

    define_on_existing_object = is_global_interface or generate_unforgeables
    if define_on_existing_object:
        g.set("define_direct_accessor", "object.define_direct_accessor")
        g.set("define_direct_property", "object.define_direct_property")
        g.set("define_native_accessor", "object.define_native_accessor")
        g.set("define_native_function", "object.define_native_function")
        g.set("set_prototype", "object.set_prototype")
    else:
        g.set("define_direct_accessor", "define_direct_accessor")
        g.set("define_direct_property", "define_direct_property")
        g.set("define_native_accessor", "define_native_accessor")
        g.set("define_native_function", "define_native_function")
        g.set("set_prototype", "set_prototype")

    # IDLGenerators.cpp:3780-3795 — opener.
    if generate_unforgeables:
        g.append(
            "\n"
            "void @class_name@::define_unforgeable_attributes(JS::Realm& realm, [[maybe_unused]] JS::Object& object)\n"
            "{\n"
        )
    elif is_global_interface:
        g.append("\nvoid @class_name@::initialize(JS::Realm& realm, JS::Object& object)\n{\n")
    else:
        g.append("\nvoid @class_name@::initialize(JS::Realm& realm)\n{\n")

    # IDLGenerators.cpp:3797-3801 — vm alias.
    # The C++ raw string is `R"~~~(\n\n    ...vm = realm.vm();\n\n)~~~"` —
    # both ends embed an extra newline.
    g.append("\n\n    [[maybe_unused]] auto& vm = realm.vm();\n\n")

    # IDLGenerators.cpp:3805-3813 — default_attributes (different mask depending
    # on whether we're declaring unforgeables). Same `\n...\n` shape on both ends.
    if not generate_unforgeables:
        g.append(
            "\n    [[maybe_unused]] u8 default_attributes = JS::Attribute::Enumerable | JS::Attribute::Configurable | JS::Attribute::Writable;\n"
        )
    else:
        g.append("\n    [[maybe_unused]] u8 default_attributes = JS::Attribute::Enumerable;\n")

    # IDLGenerators.cpp:3815-3840 — set_prototype (only for No, only for non-global).
    if not generate_unforgeables:
        if interface.name == "DOMException":
            g.append("\n\n    @set_prototype@(realm.intrinsics().error_prototype());\n")
        elif interface.prototype_base_class == "ObjectPrototype":
            g.append("\n\n    @set_prototype@(realm.intrinsics().object_prototype());\n\n")
        elif is_global_interface:
            g.append('\n    @set_prototype@(&ensure_web_prototype<@prototype_name@>(realm, "@name@"_fly_string));\n')
        else:
            g.append(
                '\n\n    @set_prototype@(&ensure_web_prototype<@prototype_base_class@>(realm, "@parent_name@"_fly_string));\n\n'
            )

    # IDLGenerators.cpp:3842-3846 — unscopable object allocation.
    if interface.has_unscopable_member and not generate_unforgeables:
        g.append("\n    auto unscopable_object = JS::Object::create(realm, nullptr);\n")

    # IDLGenerators.cpp:3849-3859 — separate StringBuilder for [Exposed=Window]-only
    # members. Members marked [Exposed=Window] in an interface that's exposed
    # to a wider set get routed here, then wrapped in `if (is<HTML::Window>(...))`.
    from .source_generator import SourceGenerator as _SG
    from .source_generator import StringBuilder as _SB

    window_builder = _SB()
    window_g = _SG(window_builder, dict(g._mapping))

    def _pick(extended_attributes: dict) -> SourceGenerator:
        exposed = extended_attributes.get("Exposed")
        if exposed and exposed.strip() == "Window":
            return window_g
        return g

    # IDLGenerators.cpp:3861-3941 — per-attribute define_native_accessor.
    # (Note: in the C++ source, the attributes loop comes BEFORE the
    # constants loop. Match that ordering.)
    from .types import attribute_callback_basename

    for attribute in interface.attributes:
        has_unforgeable = "LegacyUnforgeable" in attribute.extended_attributes
        if generate_unforgeables and not has_unforgeable:
            continue
        if not generate_unforgeables and has_unforgeable:
            continue
        if "Experimental" in attribute.extended_attributes:
            raise NotImplementedError(f"[Experimental] attribute init not yet supported ({attribute.name})")

        ag = _pick(attribute.extended_attributes).fork()
        if "SecureContext" in attribute.extended_attributes:
            ag.append(
                "\n"
                "    if (HTML::is_secure_context(Bindings::principal_host_defined_environment_settings_object(realm))) {"
            )
        cb_base = attribute_callback_basename(attribute)
        ag.set("attribute.name", attribute.name)
        ag.set("attribute.getter_callback", f"{cb_base}_getter")
        ag.set("attribute.setter_callback", f"{cb_base}_setter")

        if "FIXME" in attribute.extended_attributes:
            # IDLGenerators.cpp:3880-3890.
            ag.append(
                "\n"
                '    @define_direct_property@("@attribute.name@"_utf16_fly_string, JS::js_undefined(), default_attributes | JS::Attribute::Unimplemented);\n'
                "            "
            )
            continue

        if has_unforgeable:
            # IDLGenerators.cpp:3898-3900 — unforgeable getter via ensure_web_unforgeable_function.
            ag.append(
                "\n"
                '    auto native_@attribute.getter_callback@ = host_defined_intrinsics(realm).ensure_web_unforgeable_function("@namespaced_name@"_utf16_fly_string, "@attribute.name@"_utf16_fly_string, @attribute.getter_callback@, UnforgeableKey::Type::Getter);\n'
            )
        else:
            # IDLGenerators.cpp:3901-3903 — non-unforgeable getter NativeFunction.
            ag.append(
                "\n"
                '    auto native_@attribute.getter_callback@ = JS::NativeFunction::create(realm, @attribute.getter_callback@, 0, "@attribute.name@"_utf16_fly_string, &realm, "get"sv);\n'
            )

        if not attribute.readonly or any(
            k in attribute.extended_attributes for k in ("Replaceable", "PutForwards", "LegacyLenientSetter")
        ):
            if has_unforgeable:
                # IDLGenerators.cpp:3907-3909 — unforgeable setter.
                ag.append(
                    "\n"
                    '    auto native_@attribute.setter_callback@ = host_defined_intrinsics(realm).ensure_web_unforgeable_function("@namespaced_name@"_utf16_fly_string, "@attribute.name@"_utf16_fly_string, @attribute.setter_callback@, UnforgeableKey::Type::Setter);\n'
                )
            else:
                ag.append(
                    "\n"
                    '    auto native_@attribute.setter_callback@ = JS::NativeFunction::create(realm, @attribute.setter_callback@, 1, "@attribute.name@"_utf16_fly_string, &realm, "set"sv);\n'
                )
        else:
            # IDLGenerators.cpp:3917-3920 — null setter.
            ag.append("\n    GC::Ptr<JS::NativeFunction> native_@attribute.setter_callback@;\n")

        # IDLGenerators.cpp:3922-3926 — Unscopable: mark in unscopable_object.
        if "Unscopable" in attribute.extended_attributes:
            ag.append(
                "\n"
                '    MUST(unscopable_object->create_data_property("@attribute.name@"_utf16_fly_string, JS::Value(true)));\n'
            )

        # IDLGenerators.cpp:3928-3930 — wire the accessor into the object.
        ag.append(
            "\n"
            '    @define_direct_accessor@("@attribute.name@"_utf16_fly_string, native_@attribute.getter_callback@, native_@attribute.setter_callback@, default_attributes);\n'
        )

        # IDLGenerators.cpp:3932-3935 — close SecureContext block.
        if "SecureContext" in attribute.extended_attributes:
            ag.append("\n    }")

    # Skipped at this rung: unscopable_object, overload_sets, attributes,
    # pair iterator, async iterator, setlike, maplike, named_property_*,
    # indexed_property_*. They all add lines here at later rungs.

    # IDLGenerators.cpp:3943-3955 — function loop with the unforgeable filter
    # AND the FIXME placeholder branch.
    for op in interface.operations:
        has_unforgeable = "LegacyUnforgeable" in op.extended_attributes
        if generate_unforgeables and not has_unforgeable:
            continue
        if not generate_unforgeables and has_unforgeable:
            continue
        if "FIXME" not in op.extended_attributes:
            continue
        fg = _pick(op.extended_attributes).fork()
        fg.set("function.name", op.name)
        fg.append(
            "\n"
            '        @define_direct_property@("@function.name@"_utf16_fly_string, JS::js_undefined(), default_attributes | JS::Attribute::Unimplemented);\n'
            "            "
        )

    # IDLGenerators.cpp:3957-3971 — constants on the prototype (only when
    # not generating unforgeables). The C++ source emits constants BEFORE
    # the operations loop.
    if not generate_unforgeables:
        from .types import generate_wrap_statement

        for constant in interface.constants:
            cg = g.fork()
            cg.set("constant.name", constant.name)
            generate_wrap_statement(
                cg,
                constant.value,
                constant.type,
                interface,
                f"auto constant_{constant.name}_value =",
            )
            cg.append(
                '\n    @define_direct_property@("@constant.name@"_utf16_fly_string, '
                "constant_@constant.name@_value, JS::Attribute::Enumerable);\n"
            )

    # IDLGenerators.cpp:3973-4006 — per-operation define_native_function.
    overload_groups: dict[str, list] = {}
    for op in interface.operations:
        if "FIXME" in op.extended_attributes:
            continue
        overload_groups.setdefault(op.name, []).append(op)
    for name, group in overload_groups.items():
        first = group[0]
        has_unforgeable = "LegacyUnforgeable" in first.extended_attributes
        if generate_unforgeables and not has_unforgeable:
            continue
        if not generate_unforgeables and has_unforgeable:
            continue
        from .prototype import _make_input_acceptable_cpp
        from .prototype import _to_snakecase

        og = _pick(first.extended_attributes).fork()
        og.set("function.name", name)
        og.set("function.name:snakecase", _make_input_acceptable_cpp(_to_snakecase(name)))
        shortest = min(sum(1 for p in op2.parameters if not p.optional and not p.variadic) for op2 in group)
        og.set("function.length", str(shortest))
        if "SecureContext" in first.extended_attributes:
            og.append(
                "\n"
                "    if (HTML::is_secure_context(Bindings::principal_host_defined_environment_settings_object(realm))) {"
            )
        if any("Unscopable" in op.extended_attributes for op in group):
            og.append(
                "\n"
                '    MUST(unscopable_object->create_data_property("@function.name@"_utf16_fly_string, JS::Value(true)));\n'
            )
        og.append(
            "\n"
            '    @define_native_function@(realm, "@function.name@"_utf16_fly_string, @function.name:snakecase@, @function.length@, default_attributes);\n'
        )
        if "SecureContext" in first.extended_attributes:
            og.append("\n    }")

    # IDLGenerators.cpp:4008-4022 — stringifier toString registration.
    if getattr(interface, "has_stringifier", False):
        should = True
        stringifier_attr = getattr(interface, "stringifier_attribute", None)
        if stringifier_attr is not None:
            has_unf = "LegacyUnforgeable" in stringifier_attr.extended_attributes
            if (generate_unforgeables and not has_unf) or (not generate_unforgeables and has_unf):
                should = False
        if should:
            stringifier_eas = (
                stringifier_attr.extended_attributes
                if stringifier_attr is not None
                else (interface.stringifier_extended_attributes or {})
            )
            _pick(stringifier_eas).append(
                "\n"
                '    @define_native_function@(realm, "toString"_utf16_fly_string, to_string, 0, default_attributes);\n'
            )

    # IDLGenerators.cpp:4026-4040 — indexed property getter triggers iterator
    # methods. For value iterators, also entries/keys/values/forEach.
    if interface.indexed_property_getter is not None and not generate_unforgeables:
        g.append(
            "\n"
            "    @define_direct_property@(vm.well_known_symbol_iterator(), realm.intrinsics().array_prototype()->get_without_side_effects(vm.names.values), JS::Attribute::Configurable | JS::Attribute::Writable);\n"
        )
        if interface.value_iterator_type is not None:
            g.append(
                "\n"
                "    @define_direct_property@(vm.names.entries, realm.intrinsics().array_prototype()->get_without_side_effects(vm.names.entries), default_attributes);\n"
                "    @define_direct_property@(vm.names.keys, realm.intrinsics().array_prototype()->get_without_side_effects(vm.names.keys), default_attributes);\n"
                "    @define_direct_property@(vm.names.values, realm.intrinsics().array_prototype()->get_without_side_effects(vm.names.values), default_attributes);\n"
                "    @define_direct_property@(vm.names.forEach, realm.intrinsics().array_prototype()->get_without_side_effects(vm.names.forEach), default_attributes);\n"
            )

    # IDLGenerators.cpp:4066-4097 — setlike method registration.
    if interface.set_entry_type is not None and not generate_unforgeables:
        sg = g.fork()
        sg.append(
            "\n"
            "    @define_native_accessor@(realm, vm.names.size, get_size, nullptr, JS::Attribute::Enumerable | JS::Attribute::Configurable);\n"
            "    @define_native_function@(realm, vm.names.entries, entries, 0, default_attributes);\n"
            "    // NOTE: Keys intentionally returns values for setlike\n"
            "    @define_native_function@(realm, vm.names.keys, values, 0, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.values, values, 0, default_attributes);\n"
            "    @define_direct_property@(vm.well_known_symbol_iterator(), get_without_side_effects(vm.names.values), JS::Attribute::Configurable | JS::Attribute::Writable);\n"
            "    @define_native_function@(realm, vm.names.forEach, for_each, 1, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.has, has, 1, default_attributes);\n"
        )
        op_names = {op.name for op in interface.operations}
        if "add" not in op_names and not interface.is_set_readonly:
            sg.append("\n    @define_native_function@(realm, vm.names.add, add, 1, default_attributes);\n")
        if "delete" not in op_names and not interface.is_set_readonly:
            sg.append("\n    @define_native_function@(realm, vm.names.delete_, delete_, 1, default_attributes);\n")
        if "clear" not in op_names and not interface.is_set_readonly:
            sg.append("\n    @define_native_function@(realm, vm.names.clear, clear, 0, default_attributes);\n")

    # IDLGenerators.cpp:4099-4121 — maplike method registration.
    if interface.map_key_type is not None and not generate_unforgeables:
        mg = g.fork()
        mg.append(
            "\n"
            "    @define_native_accessor@(realm, vm.names.size, get_size, nullptr, JS::Attribute::Enumerable | JS::Attribute::Configurable);\n"
            "    @define_native_function@(realm, vm.names.entries, entries, 0, default_attributes);\n"
            "    @define_direct_property@(vm.well_known_symbol_iterator(), get_without_side_effects(vm.names.entries), JS::Attribute::Configurable | JS::Attribute::Writable);\n"
            "    @define_native_function@(realm, vm.names.keys, keys, 0, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.values, values, 0, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.forEach, for_each, 1, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.get, get, 1, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.has, has, 1, default_attributes);\n"
        )
        op_names = {op.name for op in interface.operations}
        if "set" not in op_names and not interface.is_map_readonly:
            mg.append("    @define_native_function@(realm, vm.names.set, set, 2, default_attributes);\n")
        if "delete" not in op_names and not interface.is_map_readonly:
            mg.append("    @define_native_function@(realm, vm.names.delete_, delete_, 1, default_attributes);\n")
        if "clear" not in op_names and not interface.is_map_readonly:
            mg.append("    @define_native_function@(realm, vm.names.clear, clear, 0, default_attributes);\n")

    # IDLGenerators.cpp:4042-4056 — pair iterator method registration.
    if interface.pair_iterator_types is not None and not generate_unforgeables:
        g.append(
            "\n"
            "    @define_native_function@(realm, vm.names.entries, entries, 0, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.forEach, for_each, 1, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.keys, keys, 0, default_attributes);\n"
            "    @define_native_function@(realm, vm.names.values, values, 0, default_attributes);\n"
            "\n"
            "    @define_direct_property@(vm.well_known_symbol_iterator(), get_without_side_effects(vm.names.entries), JS::Attribute::Configurable | JS::Attribute::Writable);\n"
        )

    # IDLGenerators.cpp:4057-4064 — async iterator method registration.
    if interface.async_value_iterator_type is not None and not generate_unforgeables:
        g.append(
            "\n"
            "    @define_native_function@(realm, vm.names.values, values, 0, default_attributes);\n"
            "\n"
            "    @define_direct_property@(vm.well_known_symbol_async_iterator(), get_without_side_effects(vm.names.values), JS::Attribute::Configurable | JS::Attribute::Writable);\n"
        )

    # IDLGenerators.cpp:4123-4127 — register the unscopable object.
    if interface.has_unscopable_member and not generate_unforgeables:
        g.append(
            "\n"
            "    @define_direct_property@(vm.well_known_symbol_unscopables(), unscopable_object, JS::Attribute::Configurable);\n"
        )

    # IDLGenerators.cpp:4129-4133 — to_string_tag (only for No).
    if not generate_unforgeables:
        g.append(
            '\n    @define_direct_property@(vm.well_known_symbol_to_string_tag(), JS::PrimitiveString::create(vm, "@namespaced_name@"_string), JS::Attribute::Configurable);\n'
        )

    # IDLGenerators.cpp:4135-4143 — Window-only members section.
    window_text = window_builder.to_string()
    if window_text:
        wg = g.fork()
        wg.set("defines", window_text)
        wg.append("\n    if (is<HTML::Window>(realm.global_object())) {\n@defines@\n    }\n")

    # IDLGenerators.cpp:4145-4149 — Base::initialize for non-global initialize().
    if not define_on_existing_object:
        g.append("\n    Base::initialize(realm);\n")

    # IDLGenerators.cpp:4151-4153 — close.
    g.append("\n}\n")


def _generate_attribute_setter(attribute, interface: Interface, class_name: str, generator: SourceGenerator) -> None:
    """Port of generate_attribute_setter (IDLGenerators.cpp:4156-4405).

    Currently the simple (non-Reflect, non-CEReactions) path: PutForwards
    and Replaceable are early-return shortcuts; LegacyLenientSetter
    returns undefined; everything else coerces value with generate_to_cpp
    and calls impl->set_<cpp_name>().
    """
    from .to_cpp import generate_to_cpp
    from .types import attribute_callback_basename
    from .types import attribute_cpp_name

    is_reflect = "Reflect" in attribute.extended_attributes
    has_ce = "CEReactions" in attribute.extended_attributes
    if is_reflect:
        type_name = attribute.type.name if attribute.type else ""
        is_nullable = bool(getattr(attribute.type, "nullable", False))
        attr_type = attribute.type
        is_frozen_array_of_element = (
            attr_type is not None
            and attr_type.kind == "parameterized"
            and attr_type.name == "FrozenArray"
            and len(attr_type.parameters) == 1
            and attr_type.parameters[0].name == "Element"
            and is_nullable
        )
        supported_setter = (
            (type_name == "DOMString" and not is_nullable)
            or type_name == "boolean"
            or type_name == "USVString"
            or type_name == "long"
            or (type_name == "unsigned long" and not is_nullable)
            or (is_nullable and type_name not in ("Element",))
            or (is_nullable and type_name == "Element")
            or is_frozen_array_of_element
        )
        if not supported_setter:
            raise NotImplementedError(f"[Reflect] setter for type '{type_name}' not yet supported ({attribute.name})")

    g = generator.fork()
    cb = attribute_callback_basename(attribute)
    g.set("class_name", class_name)
    g.set("attribute.name", attribute.name)
    g.set("attribute.setter_callback", f"{cb}_setter")
    g.set("attribute.cpp_name", attribute_cpp_name(attribute))
    if is_reflect:
        reflect_name = attribute.extended_attributes.get("Reflect") or attribute.name.lower()
        g.set("attribute.reflect_name", reflect_name)

    g.append(
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::@attribute.setter_callback@)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::@attribute.setter_callback@");\n'
        "    [[maybe_unused]] auto& realm = *vm.current_realm();\n"
        "\n"
        "    // 1. Let V be undefined.\n"
        "    auto value = JS::js_undefined();\n"
        "\n"
        "    // 2. If any arguments were passed, then set V to the value of the first argument passed.\n"
        "    if (vm.argument_count() > 0)\n"
        "        value = vm.argument(0);\n"
        "\n"
        "    // 3. Let id be attribute’s identifier.\n"
        "    // 4. Let idlObject be null.\n"
        "    // 5. If attribute is a regular attribute:\n"
        "\n"
        "    // 1. Let jsValue be the this value, if it is not null or undefined, or realm’s global object otherwise.\n"
        "    //   (This will subsequently cause a TypeError in a few steps, if the global object does not implement target and [LegacyLenientThis] is not specified.)\n"
        '    // FIXME: 2. If jsValue is a platform object, then perform a security check, passing jsValue, id, and "setter".\n'
        "    // 3. Let validThis be true if jsValue implements target, or false otherwise.\n"
        "    auto maybe_impl = impl_from(vm);\n"
        "\n"
        "    // 4. If validThis is false and attribute was not specified with the [LegacyLenientThis] extended attribute, then throw a TypeError.\n"
    )
    if "LegacyLenientThis" not in attribute.extended_attributes:
        g.append("\n    auto impl = TRY(maybe_impl);\n")

    if has_ce:
        g.append(
            "\n"
            "    auto& reactions_stack = HTML::relevant_similar_origin_window_agent(*impl).custom_element_reactions_stack;\n"
            "    reactions_stack.element_queue_stack.append({});\n"
        )

    if "Replaceable" in attribute.extended_attributes:
        g.append(
            "\n"
            "    // 1. Perform ? CreateDataPropertyOrThrow(jsValue, id, V).\n"
            '    TRY(impl->create_data_property_or_throw("@attribute.name@"_utf16_fly_string, value));\n'
            "\n"
            "    // 2. Return undefined.\n"
            "    return JS::js_undefined();\n"
            "}\n"
        )
        return

    if "LegacyLenientThis" in attribute.extended_attributes:
        g.append(
            "\n"
            "    if (maybe_impl.is_error())\n"
            "        return JS::js_undefined();\n"
            "\n"
            "    auto impl = maybe_impl.release_value();\n"
        )

    if "LegacyLenientSetter" in attribute.extended_attributes:
        g.append("\n    (void)impl;\n    return JS::js_undefined();\n}\n")
        return

    if "PutForwards" in attribute.extended_attributes:
        g.set("put_forwards_identifier", attribute.extended_attributes["PutForwards"])
        g.append(
            "\n"
            "    // 1. Let Q be ? Get(jsValue, id).\n"
            '    auto receiver_value = TRY(impl->get("@attribute.name@"_utf16_fly_string));\n'
            "\n"
            "    // 2. If Q is not an Object, then throw a TypeError.\n"
            "    if (!receiver_value.is_object())\n"
            "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObject, receiver_value);\n"
            "    auto& receiver = receiver_value.as_object();\n"
            "\n"
            "    // 3. Let forwardId be the identifier argument of the [PutForwards] extended attribute.\n"
            '    auto forward_id = "@put_forwards_identifier@"_utf16_fly_string;\n'
            "\n"
            "    // 4. Perform ? Set(Q, forwardId, V, false).\n"
            "    TRY(receiver.set(JS::PropertyKey { forward_id, JS::PropertyKey::StringMayBeNumber::No }, value, JS::Object::ShouldThrowExceptions::No));\n"
            "\n"
            "    // 5. Return undefined.\n"
            "    return JS::js_undefined();\n"
            "}\n"
        )
        return

    # Coerce value into cpp_value.
    generate_to_cpp(attribute, "value", "", "cpp_value", interface, g)

    if is_reflect:
        type_name = attribute.type.name
        is_nullable = bool(getattr(attribute.type, "nullable", False))
        attr_type_s = attribute.type
        is_frozen_array_of_element = (
            attr_type_s is not None
            and attr_type_s.kind == "parameterized"
            and attr_type_s.name == "FrozenArray"
            and len(attr_type_s.parameters) == 1
            and attr_type_s.parameters[0].name == "Element"
            and is_nullable
        )
        if type_name == "boolean":
            g.append(
                "\n"
                "    if (!cpp_value)\n"
                '        impl->remove_attribute("@attribute.reflect_name@"_fly_string);\n'
                "    else\n"
                '        impl->set_attribute_value("@attribute.reflect_name@"_fly_string, String {});\n'
            )
        elif type_name == "unsigned long":
            g.append(
                "\n"
                "    u32 minimum = 0;\n"
                "    u32 new_value = minimum;\n"
                "    if (cpp_value >= minimum && cpp_value <= 2147483647)\n"
                "        new_value = cpp_value;\n"
                '    impl->set_attribute_value("@attribute.reflect_name@"_fly_string, String::number(new_value));\n'
            )
        elif (
            type_name in ("byte", "octet", "short", "unsigned short", "long", "long long", "unsigned long long")
            and not is_nullable
        ):
            g.append(
                '\n    impl->set_attribute_value("@attribute.reflect_name@"_fly_string, String::number(cpp_value));\n'
            )
        elif type_name == "Element" and is_nullable:
            # IDLGenerators.cpp:4294-4320 — Reflect Element? setter.
            g.append(
                "\n"
                '    static auto content_attribute = "@attribute.reflect_name@"_fly_string;\n'
                "\n"
                "    if (!cpp_value) {\n"
                "        impl->set_@attribute.cpp_name@({});\n"
                "        impl->remove_attribute(content_attribute);\n"
                "        return JS::js_undefined();\n"
                "    }\n"
                "\n"
                "    impl->set_attribute_value(content_attribute, String {});\n"
                "\n"
                "    impl->set_@attribute.cpp_name@(*cpp_value);\n"
            )
        elif is_frozen_array_of_element:
            # IDLGenerators.cpp:4321-4365 — Reflect FrozenArray<Element>? setter.
            g.append(
                "\n"
                '    static auto content_attribute = "@attribute.reflect_name@"_fly_string;\n'
                "\n"
                "    if (!cpp_value.has_value()) {\n"
                "        impl->set_@attribute.cpp_name@({});\n"
                "        impl->remove_attribute(content_attribute);\n"
                "        return JS::js_undefined();\n"
                "    }\n"
                "\n"
                "    impl->set_attribute_value(content_attribute, String {});\n"
                "\n"
                "    Vector<GC::Weak<DOM::Element>> elements;\n"
                "    elements.ensure_capacity(cpp_value->size());\n"
                "\n"
                "    for (auto const& element : *cpp_value) {\n"
                "        elements.unchecked_append(*element);\n"
                "    }\n"
                "\n"
                "    impl->set_@attribute.cpp_name@(move(elements));\n"
            )
        elif is_nullable:
            g.append(
                "\n"
                "    if (!cpp_value.has_value())\n"
                '        impl->remove_attribute("@attribute.reflect_name@"_fly_string);\n'
                "    else\n"
                '        impl->set_attribute_value("@attribute.reflect_name@"_fly_string, cpp_value.value());\n'
            )
        else:
            g.append('\n    impl->set_attribute_value("@attribute.reflect_name@"_fly_string, cpp_value);\n')
        if has_ce:
            g.append(
                "\n"
                "    auto queue = reactions_stack.element_queue_stack.take_last();\n"
                "    Bindings::invoke_custom_element_reactions(queue);\n"
            )
    else:
        if not has_ce:
            g.append(
                "\n    TRY(throw_dom_exception_if_needed(vm, [&] { return impl->set_@attribute.cpp_name@(cpp_value); }));\n"
            )
        else:
            g.append(
                "\n"
                "    auto maybe_exception = throw_dom_exception_if_needed(vm, [&] { return impl->set_@attribute.cpp_name@(cpp_value); });\n"
                "\n"
                "    auto queue = reactions_stack.element_queue_stack.take_last();\n"
                "    Bindings::invoke_custom_element_reactions(queue);\n"
                "\n"
                "    if (maybe_exception.is_error())\n"
                "        return maybe_exception.release_error();\n"
            )

    g.append("\n    return JS::js_undefined();\n}\n")


def _create_an_inheritance_stack(interface: Interface) -> list[Interface]:
    """Port of create_an_inheritance_stack (IDLGenerators.cpp:3370-3394).

    Walks the parent chain using imported_interfaces.
    """
    stack = [interface]
    current = interface
    while current.parent_name:
        imp = None
        for imported in current.imported_interfaces:
            if imported.name == current.parent_name:
                imp = imported
                break
        if imp is None:
            break
        stack.append(imp)
        current = imp
    return stack


def _generate_default_to_json_function(class_name: str, interface: Interface, generator: SourceGenerator) -> None:
    """Port of generate_default_to_json_function (IDLGenerators.cpp:3518-3552)."""
    from .types import generate_wrap_statement
    from .types import is_json

    g = generator.fork()
    g.set("class_name", class_name)
    g.append(
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::to_json)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::to_json");\n'
        "    auto& realm = *vm.current_realm();\n"
        "    auto* impl = TRY(impl_from(vm));\n"
        "\n"
        "    auto result = JS::Object::create(realm, realm.intrinsics().object_prototype());\n"
    )

    chain = _create_an_inheritance_stack(interface)
    # Process in reverse (ancestors first).
    from .source_generator import SourceGenerator as _SG2
    from .source_generator import StringBuilder as _SB2

    for iface in reversed(chain):
        has_default_to_json = any(
            op.name == "toJSON" and "Default" in op.extended_attributes for op in iface.operations
        )
        if not has_default_to_json:
            continue

        # Mirror the C++ window_exposed_only_members pattern in collect_attribute_values.
        window_builder2 = _SB2()
        window_g2 = _SG2(window_builder2, dict(g._mapping))

        def _pick_tojson(ea: dict, _wg: SourceGenerator = window_g2) -> SourceGenerator:
            exposed = ea.get("Exposed")
            if exposed and exposed.strip() == "Window":
                return _wg
            return g

        for attribute in iface.attributes:
            if "FIXME" in attribute.extended_attributes:
                continue
            if attribute.type is None or not is_json(attribute.type, iface):
                continue
            return_value_name = f"{_to_snakecase(attribute.name)}_retval"
            ag = _pick_tojson(attribute.extended_attributes).fork()
            ag.set("attribute.name", attribute.name)
            ag.set("attribute.return_value_name", return_value_name)
            impl_as = attribute.extended_attributes.get("ImplementedAs")
            if impl_as:
                ag.set("attribute.cpp_name", impl_as)
            else:
                ag.set("attribute.cpp_name", _make_input_acceptable_cpp(_to_snakecase(attribute.name)))
            reflect = attribute.extended_attributes.get("Reflect")
            if reflect is not None:
                reflect_name = reflect or attribute.name
                ag.set("attribute.reflect_name", reflect_name)
            else:
                ag.set("attribute.reflect_name", _to_snakecase(attribute.name))

            if "Reflect" in attribute.extended_attributes:
                if attribute.type.name != "boolean":
                    ag.append(
                        "\n"
                        '    auto @attribute.return_value_name@ = impl->get_attribute_value("@attribute.reflect_name@"_fly_string);\n'
                    )
                else:
                    ag.append(
                        "\n"
                        '    auto @attribute.return_value_name@ = impl->has_attribute("@attribute.reflect_name@"_fly_string);\n'
                    )
            else:
                ag.append(
                    "\n"
                    "    auto @attribute.return_value_name@ = TRY(throw_dom_exception_if_needed(vm, [&] { return impl->@attribute.cpp_name@(); }));\n"
                )

            ag.append("\n    JS::Value @attribute.return_value_name@_wrapped;\n")
            generate_wrap_statement(
                ag,
                return_value_name,
                attribute.type,
                iface,
                f"{return_value_name}_wrapped =",
            )
            ag.append(
                '\n    MUST(result->create_data_property("@attribute.name@"_utf16_fly_string, @attribute.return_value_name@_wrapped));\n'
            )

        for constant in iface.constants:
            cg = g.fork()
            cg.set("constant.name", constant.name)
            from .types import generate_wrap_statement as _wrap

            _wrap(
                cg,
                constant.value,
                constant.type,
                iface,
                f"auto constant_{constant.name}_value =",
            )
            cg.append(
                '\n    MUST(result->create_data_property("@constant.name@"_utf16_fly_string, constant_@constant.name@_value));\n'
            )

        # Emit window-only members block, mirroring IDLGenerators.cpp:3505-3515.
        if window_builder2.to_string():
            wg2 = g.fork()
            wg2.set("defines", window_builder2.to_string())
            wg2.append("\n    if (is<HTML::Window>(realm.global_object())) {\n@defines@\n    }\n")

    g.append("\n    return result;\n}\n")
