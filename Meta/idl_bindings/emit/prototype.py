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
    # generate_prototype_or_global_mixin_definitions emits per-attribute /
    # per-operation bodies — none for an empty interface.
    if (
        interface.attributes
        or interface.operations
        or interface.has_stringifier
        or interface.named_property_getter
        or interface.named_property_setter
        or interface.named_property_deleter
        or interface.indexed_property_getter
        or interface.indexed_property_setter
    ):
        raise NotImplementedError("prototype member definitions are not yet supported on this rung")


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

    # Skipped at this rung: unscopable_object, constants, overload_sets,
    # attributes, pair iterator, async iterator, setlike, maplike, named_property_*,
    # indexed_property_*. They all add lines here at later rungs.

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
