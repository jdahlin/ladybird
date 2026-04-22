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

    # setlike / maplike / named-property-handler declarations are added at
    # their respective concept-ladder rungs.
    if interface.set_entry_type is not None:
        raise NotImplementedError("setlike declarations are not yet supported")
    if interface.map_key_type is not None:
        raise NotImplementedError("maplike declarations are not yet supported")
    if interface.named_property_getter is not None:
        raise NotImplementedError("named property getter declarations are not yet supported")
    if interface.indexed_property_getter is not None:
        raise NotImplementedError("indexed property getter declarations are not yet supported")

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
    if not s:
        return s
    out: list[str] = []
    for i, ch in enumerate(s):
        if ch.isupper():
            # Insert underscore if previous char is lowercase, or if the
            # next char is lowercase (mid-run boundary like "URLSearch" →
            # "url_search").
            if i > 0:
                prev = s[i - 1]
                nxt = s[i + 1] if i + 1 < len(s) else ""
                if prev.islower() or (nxt.islower() and prev.isupper()):
                    out.append("_")
            out.append(ch.lower())
        else:
            out.append(ch)
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
        # Global interfaces use a different shape — handled at concept-ladder rung 39.
        raise NotImplementedError("global interface implementation is not yet supported")
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
    if interface.has_stringifier:
        raise NotImplementedError("stringifier definitions are not yet supported")
    if interface.named_property_getter is not None:
        raise NotImplementedError("named property getter definitions are not yet supported")
    if interface.named_property_setter is not None:
        raise NotImplementedError("named property setter definitions are not yet supported")
    if interface.named_property_deleter is not None:
        raise NotImplementedError("named property deleter definitions are not yet supported")
    if interface.indexed_property_getter is not None:
        raise NotImplementedError("indexed property getter definitions are not yet supported")
    if interface.indexed_property_setter is not None:
        raise NotImplementedError("indexed property setter definitions are not yet supported")
    if interface.pair_iterator_types is not None:
        raise NotImplementedError("pair iterator definitions are not yet supported")
    if interface.async_value_iterator_type is not None:
        raise NotImplementedError("async iterator definitions are not yet supported")
    if interface.set_entry_type is not None:
        raise NotImplementedError("setlike definitions are not yet supported")
    if interface.map_key_type is not None:
        raise NotImplementedError("maplike definitions are not yet supported")
    if interface.operations:
        raise NotImplementedError("operation definitions are not yet supported")

    is_global_interface = "Global" in interface.extended_attributes
    class_name = interface.global_mixin_class if is_global_interface else interface.prototype_class

    # IDLGenerators.cpp:4429-4456 — impl_from helpers, emitted only when the
    # interface has at least one member that needs `impl_from`.
    needs_impl_from = bool(
        interface.attributes
        or interface.operations
        or interface.has_stringifier
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
        # Attribute setters add another emit; this rung only handles readonly.
        if not attribute.readonly or any(
            k in attribute.extended_attributes for k in ("Replaceable", "PutForwards", "LegacyLenientSetter")
        ):
            raise NotImplementedError("attribute setter definitions are not yet supported on this rung")


def _generate_attribute_getter(attribute, interface: Interface, class_name: str, generator: SourceGenerator) -> None:
    """Port of the simple-getter slice of generate_prototype_or_global_mixin_definitions
    (IDLGenerators.cpp:4493-4856 — the non-Reflect, non-Promise, non-Cached path).
    """
    from .types import attribute_callback_basename
    from .types import attribute_cpp_name
    from .types import generate_wrap_statement

    if attribute.extended_attributes.keys() & {"Reflect", "CachedAttribute"}:
        raise NotImplementedError(f"[Reflect]/[CachedAttribute] attribute body not yet supported ({attribute.name})")
    if attribute.type and attribute.type.name == "Promise":
        raise NotImplementedError(f"Promise-typed attribute body not yet supported ({attribute.name})")

    g = generator.fork()
    g.set("class_name", class_name)
    g.set("attribute.getter_callback", f"{attribute_callback_basename(attribute)}_getter")
    g.set("attribute.cpp_name", attribute_cpp_name(attribute))

    g.append(
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::@attribute.getter_callback@)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::@attribute.getter_callback@");\n'
        "    [[maybe_unused]] auto& realm = *vm.current_realm();\n"
    )
    g.append("\n    [[maybe_unused]] auto* impl = TRY(impl_from(vm));\n")
    g.append(
        "\n    auto retval = TRY(throw_dom_exception_if_needed(vm, [&] { return impl->@attribute.cpp_name@(); }));\n"
    )
    # generate_return_statement: just a wrap with "return" as result expression.
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
            g.append('    @set_prototype@(&ensure_web_prototype<@prototype_name@>(realm, "@name@"_fly_string));\n')
        else:
            g.append(
                '\n\n    @set_prototype@(&ensure_web_prototype<@prototype_base_class@>(realm, "@parent_name@"_fly_string));\n\n'
            )

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
        if "SecureContext" in attribute.extended_attributes:
            raise NotImplementedError(f"[SecureContext] attribute init not yet supported ({attribute.name})")
        if "Unscopable" in attribute.extended_attributes:
            raise NotImplementedError(f"[Unscopable] attribute init not yet supported ({attribute.name})")

        ag = g.fork()
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
            raise NotImplementedError(f"[LegacyUnforgeable] attribute init not yet supported ({attribute.name})")

        # IDLGenerators.cpp:3901-3903 — non-unforgeable getter NativeFunction.
        ag.append(
            "\n"
            '    auto native_@attribute.getter_callback@ = JS::NativeFunction::create(realm, @attribute.getter_callback@, 0, "@attribute.name@"_utf16_fly_string, &realm, "get"sv);\n'
        )

        if not attribute.readonly or any(
            k in attribute.extended_attributes for k in ("Replaceable", "PutForwards", "LegacyLenientSetter")
        ):
            ag.append(
                "\n"
                '    auto native_@attribute.setter_callback@ = JS::NativeFunction::create(realm, @attribute.setter_callback@, 1, "@attribute.name@"_utf16_fly_string, &realm, "set"sv);\n'
            )
        else:
            # IDLGenerators.cpp:3917-3920 — null setter.
            ag.append("\n    GC::Ptr<JS::NativeFunction> native_@attribute.setter_callback@;\n")

        # IDLGenerators.cpp:3928-3930 — wire the accessor into the object.
        ag.append(
            "\n"
            '    @define_direct_accessor@("@attribute.name@"_utf16_fly_string, native_@attribute.getter_callback@, native_@attribute.setter_callback@, default_attributes);\n'
        )

    # Skipped at this rung: unscopable_object, overload_sets, attributes,
    # pair iterator, async iterator, setlike, maplike, named_property_*,
    # indexed_property_*. They all add lines here at later rungs.

    # IDLGenerators.cpp:3957-3971 — constants on the prototype (only when
    # not generating unforgeables). The C++ side emits constants AFTER
    # attributes (and AFTER operations, when those land here).
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

    # IDLGenerators.cpp:4129-4133 — to_string_tag (only for No).
    if not generate_unforgeables:
        g.append(
            '\n    @define_direct_property@(vm.well_known_symbol_to_string_tag(), JS::PrimitiveString::create(vm, "@namespaced_name@"_string), JS::Attribute::Configurable);\n'
        )

    # Window-only members section is empty for this rung.

    # IDLGenerators.cpp:4145-4149 — Base::initialize for non-global initialize().
    if not define_on_existing_object:
        g.append("\n    Base::initialize(realm);\n")

    # IDLGenerators.cpp:4151-4153 — close.
    g.append("\n}\n")
