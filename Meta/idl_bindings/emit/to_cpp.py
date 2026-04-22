"""JS-value-to-C++ type coercion — port of generate_to_cpp et al.
(IDLGenerators.cpp:1830-1897 + the per-type helpers it dispatches to).

Currently covers the simple cases needed for operation/constructor/setter
parameters: boolean, integer, floating-point, string, enum, simple platform
object. Each unsupported case raises NotImplementedError so the parity
test surfaces it as a known gap.
"""

from __future__ import annotations

from ..ast import Interface
from ..ast import Parameter
from .source_generator import SourceGenerator
from .types import _cpp_type_name
from .types import is_boolean
from .types import is_enum
from .types import is_floating_point
from .types import is_integer
from .types import is_string

# Same map as in types.py but with `boolean` and `long long` matching
# IDLGenerators.cpp:625-635 (generate_to_integral table).
_TO_INTEGRAL = {
    "boolean": "bool",
    "byte": "WebIDL::Byte",
    "octet": "WebIDL::Octet",
    "short": "WebIDL::Short",
    "unsigned short": "WebIDL::UnsignedShort",
    "long": "WebIDL::Long",
    "long long": "WebIDL::LongLong",
    "unsigned long": "WebIDL::UnsignedLong",
    "unsigned long long": "WebIDL::UnsignedLongLong",
}


def _make_input_acceptable_cpp(s: str) -> str:
    from .prototype import _make_input_acceptable_cpp as f

    return f(s)


def generate_to_cpp(
    parameter: Parameter,
    js_name: str,
    js_suffix: str,
    cpp_name: str,
    interface: Interface,
    generator: SourceGenerator,
    *,
    optional: bool = False,
    optional_default_value: str | None = None,
    variadic: bool = False,
    recursion_depth: int = 0,
) -> None:
    """Port of generate_to_cpp (IDLGenerators.cpp:1830-1897)."""
    g = generator.fork()
    accept_cpp = _make_input_acceptable_cpp(cpp_name)
    legacy_null = "true" if "LegacyNullToEmptyString" in parameter.extended_attributes else "false"
    g.set("cpp_name", accept_cpp)
    g.set("js_name", js_name)
    g.set("js_suffix", js_suffix)
    g.set("legacy_null_to_empty_string", legacy_null)
    type_ = parameter.type
    assert type_ is not None
    g.set("parameter.type.name", type_.name)
    g.set("parameter.type.name.normalized", _cpp_type_name(type_))
    g.set("parameter.name", parameter.name)
    if optional_default_value is not None:
        g.set("parameter.optional_default_value", optional_default_value)

    if is_string(type_):
        _generate_to_string(
            parameter, type_, g, variadic=variadic, optional=optional, optional_default_value=optional_default_value
        )
        return
    if is_boolean(type_) or is_integer(type_):
        _generate_to_integral(parameter, type_, g, optional=optional, optional_default_value=optional_default_value)
        return
    if is_floating_point(type_):
        _generate_to_floating_point(
            parameter, type_, g, optional=optional, optional_default_value=optional_default_value
        )
        return
    if type_.kind == "plain" and type_.name == "any":
        _generate_to_any(g, optional=optional, optional_default_value=optional_default_value, variadic=variadic)
        return
    if type_.kind == "plain" and type_.name == "object":
        _generate_to_object(g, type_, optional=optional)
        return
    if type_.kind == "plain" and type_.name == "Promise":
        _generate_to_promise(g)
        return
    if type_.kind == "plain" and (type_.name == "BufferSource" or _is_js_builtin_buffer_type(type_.name)):
        _generate_to_buffer_source(g, type_, optional=optional)
        return
    if type_.kind == "plain" and type_.name == "ArrayBufferView":
        _generate_to_array_buffer_view(g, type_, optional=optional)
        return
    if is_enum(type_, interface):
        # Attribute setters return undefined instead of throwing on invalid
        # enum values (IDLGenerators.cpp:1872-1876).
        from ..ast import Attribute as _Attr

        throw_on_invalid = not isinstance(parameter, _Attr)
        _generate_to_enum(
            g,
            type_,
            interface,
            optional=optional,
            optional_default_value=optional_default_value,
            throw_on_invalid=throw_on_invalid,
        )
        return
    if type_.kind == "plain" and type_.name in interface.dictionaries:
        _generate_to_dictionary(g, type_, interface)
        return
    if type_.kind == "plain" and type_.name in interface.callback_functions:
        _generate_to_callback_function(g, type_, interface, optional=optional)
        return
    if type_.kind == "parameterized" and type_.name in ("sequence", "FrozenArray"):
        _generate_to_sequence(
            g,
            type_,
            js_name,
            js_suffix,
            accept_cpp,
            interface,
            optional=optional,
            optional_default_value=optional_default_value,
            recursion_depth=recursion_depth,
        )
        return
    if type_.kind == "union":
        _generate_to_union(
            parameter,
            type_,
            js_name,
            js_suffix,
            cpp_name,
            interface,
            g,
            optional=optional,
            optional_default_value=optional_default_value,
            variadic=variadic,
            recursion_depth=recursion_depth,
        )
        return
    callback_iface = _find_callback_interface(interface, type_.name)
    if callback_iface is not None:
        _generate_to_callback_interface(g, type_, callback_iface)
        return
    if type_.kind == "plain" and not type_.name.startswith("("):
        # Bare interface name → platform object.
        _generate_to_platform_object(g, type_, optional=optional)
        return
    raise NotImplementedError(f"to_cpp for type {type_.name!r} (kind={type_.kind}) not supported")


# Process-global counter mirroring the static `i` in
# generate_dictionary_to_cpp (IDLGenerators.cpp:700) — keeps generated
# variable names unique across nested/multiple dictionary coercions.
_DICTIONARY_INDEX = [0]


def _generate_to_dictionary(generator, type_, interface: Interface) -> None:
    name = type_.name
    generator.append(
        "\n"
        "    if (!@js_name@@js_suffix@.is_nullish() && !@js_name@@js_suffix@.is_object())\n"
        '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@parameter.type.name@");\n'
        "\n"
        "    @parameter.type.name.normalized@ @cpp_name@ {};\n"
    )
    current_name = name
    while True:
        dictionary = interface.dictionaries[current_name]
        members = list(dictionary.members)
        for partial in interface.partial_dictionaries.get(current_name, []):
            members.extend(partial.members)
        for member in members:
            i = _DICTIONARY_INDEX[0]
            mg = generator.fork()
            mg.set("member_key", member.name)
            from .prototype import _make_input_acceptable_cpp
            from .prototype import _to_snakecase

            member_js = _make_input_acceptable_cpp(_to_snakecase(member.name))
            value_name = f"{member_js}_value_{i}"
            prop_value_name = f"{member_js}_property_value_{i}"
            mg.set("member_name", member_js)
            mg.set("member_value_name", value_name)
            mg.set("member_property_value_name", prop_value_name)
            mg.append(
                "\n"
                "    auto @member_property_value_name@ = JS::js_undefined();\n"
                "    if (@js_name@@js_suffix@.is_object())\n"
                '        @member_property_value_name@ = TRY(@js_name@@js_suffix@.as_object().get("@member_key@"_utf16_fly_string));\n'
            )
            if member.required:
                mg.append(
                    "\n"
                    "    if (@member_property_value_name@.is_undefined())\n"
                    '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::MissingRequiredProperty, "@member_key@");\n'
                )
            elif member.default_value is None:
                mg.append("\n    if (!@member_property_value_name@.is_undefined()) {\n")
            # Recursive: coerce the member's value via generate_to_cpp.
            generate_to_cpp(
                member,
                prop_value_name,
                "",
                value_name,
                interface,
                mg,
                optional=not member.required,
                optional_default_value=member.default_value,
            )
            mg.append("\n    @cpp_name@.@member_name@ = @member_value_name@;\n")
            if not member.required and member.default_value is None:
                mg.append("\n    }\n")
            # Advance counter by 1 (matches C++'s trailing `i++;` — the counter
            # may have been moved by the recursive generate_to_cpp call above,
            # and we still want exactly one additional bump per member).
            _DICTIONARY_INDEX[0] += 1
        if not dictionary.parent_name:
            break
        if dictionary.parent_name not in interface.dictionaries:
            break
        current_name = dictionary.parent_name


def _generate_to_callback_function(generator, type_, interface: Interface, *, optional) -> None:
    """Port of generate_callback_function_to_cpp (IDLGenerators.cpp:1184-1220)."""
    g = generator
    callback = interface.callback_functions[type_.name]
    if callback.return_type and callback.return_type.kind == "parameterized" and callback.return_type.name == "Promise":
        g.set("operation_returns_promise", "WebIDL::OperationReturnsPromise::Yes")
    else:
        g.set("operation_returns_promise", "WebIDL::OperationReturnsPromise::No")

    if not type_.nullable and not callback.is_legacy_treat_non_object_as_null:
        # Match the C++ raw-string literal layout: the opener ends with `\n`
        # before the optional `&&` clause, so without the optional clause
        # the `)` falls onto a separate line.
        g.append("\n    if (!@js_name@@js_suffix@.is_function()\n")
        if optional:
            g.append("&& !@js_name@@js_suffix@.is_undefined()")
        g.append(
            ")\n        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAFunction, @js_name@@js_suffix@);\n"
        )
    if optional or type_.nullable or callback.is_legacy_treat_non_object_as_null:
        g.append(
            "\n"
            "    GC::Ptr<WebIDL::CallbackType> @cpp_name@;\n"
            "    if (@js_name@@js_suffix@.is_object())\n"
            "        @cpp_name@ = vm.heap().allocate<WebIDL::CallbackType>(@js_name@@js_suffix@.as_object(), HTML::incumbent_realm(), @operation_returns_promise@);\n"
        )
    else:
        g.append(
            "\n"
            "    auto @cpp_name@ = vm.heap().allocate<WebIDL::CallbackType>(@js_name@@js_suffix@.as_object(), HTML::incumbent_realm(), @operation_returns_promise@);\n"
        )


def _generate_to_string(parameter, type_, g, *, variadic, optional, optional_default_value):
    is_utf16 = "Utf16" in type_.name
    is_fly = "FlyString" in parameter.extended_attributes
    g.set(
        "string_type",
        ("Utf16FlyString" if is_fly else "Utf16String") if is_utf16 else ("FlyString" if is_fly else "String"),
    )
    g.set("string_suffix", "_utf16" if is_utf16 else "_string")
    if type_.name in ("USVString", "Utf16USVString"):
        g.set("to_string", "to_utf16_usv_string" if is_utf16 else "to_usv_string")
    elif type_.name == "ByteString":
        g.set("to_string", "to_byte_string")
    else:
        g.set("to_string", "to_utf16_string" if is_utf16 else "to_string")

    if variadic:
        g.append(
            "\n"
            "    Vector<@string_type@> @cpp_name@;\n"
            "\n"
            "    if (vm.argument_count() > @js_suffix@) {\n"
            "        @cpp_name@.ensure_capacity(vm.argument_count() - @js_suffix@);\n"
            "\n"
            "        for (size_t i = @js_suffix@; i < vm.argument_count(); ++i) {\n"
            "            auto to_string_result = TRY(WebIDL::@to_string@(vm, vm.argument(i)));\n"
            "            @cpp_name@.unchecked_append(move(to_string_result));\n"
            "        }\n"
            "    }\n"
        )
        return

    if not optional:
        if not type_.nullable:
            g.append(
                "\n"
                "    @string_type@ @cpp_name@;\n"
                "    if (!@legacy_null_to_empty_string@ || !@js_name@@js_suffix@.is_null()) {\n"
                "        @cpp_name@ = TRY(WebIDL::@to_string@(vm, @js_name@@js_suffix@));\n"
                "    }\n"
            )
        else:
            g.append(
                "\n"
                "    Optional<@string_type@> @cpp_name@;\n"
                "    if (!@js_name@@js_suffix@.is_nullish())\n"
                "        @cpp_name@ = TRY(WebIDL::@to_string@(vm, @js_name@@js_suffix@));\n"
            )
        return

    may_be_null = optional_default_value is None or optional_default_value == "null"
    if may_be_null:
        g.append("\n    Optional<@string_type@> @cpp_name@;\n")
    else:
        g.append("\n    @string_type@ @cpp_name@;\n")

    if type_.nullable:
        g.append(
            "\n"
            "    if (!@js_name@@js_suffix@.is_undefined()) {\n"
            "        if (!@js_name@@js_suffix@.is_null())\n"
            "            @cpp_name@ = TRY(WebIDL::@to_string@(vm, @js_name@@js_suffix@));\n"
            "    }"
        )
    else:
        g.append(
            "\n"
            "    if (!@js_name@@js_suffix@.is_undefined()) {\n"
            "        if (!@legacy_null_to_empty_string@ || !@js_name@@js_suffix@.is_null())\n"
            "            @cpp_name@ = TRY(WebIDL::@to_string@(vm, @js_name@@js_suffix@));\n"
            "    }"
        )

    if not may_be_null:
        g.append(" else {\n        @cpp_name@ = @parameter.optional_default_value@@string_suffix@;\n    }\n")
    # Intentionally no trailing newline for the may_be_null case — mirrors
    # the C++ raw-string `})~~~"` at IDLGenerators.cpp:572 which also has none.


def _generate_to_integral(parameter, type_, g, *, optional, optional_default_value):
    cpp_t = _TO_INTEGRAL[type_.name]
    g.set("cpp_type", cpp_t)
    g.set("enforce_range", "Yes" if "EnforceRange" in parameter.extended_attributes else "No")
    g.set("clamp", "Yes" if "Clamp" in parameter.extended_attributes else "No")

    if optional_default_value == "null":
        optional_default_value = None

    if (not optional and not type_.nullable) or optional_default_value is not None:
        g.append("\n    @cpp_type@ @cpp_name@;\n")
    else:
        g.append("\n    Optional<@cpp_type@> @cpp_name@;\n")

    if type_.nullable:
        g.append("\n    if (!@js_name@@js_suffix@.is_null() && !@js_name@@js_suffix@.is_undefined())\n")
    elif optional:
        g.append("\n    if (!@js_name@@js_suffix@.is_undefined())\n")

    if cpp_t == "bool":
        g.append("\n    @cpp_name@ = @js_name@@js_suffix@.to_boolean();\n")
    else:
        g.append(
            "\n    @cpp_name@ = TRY(WebIDL::convert_to_int<@cpp_type@>(vm, @js_name@@js_suffix@, WebIDL::EnforceRange::@enforce_range@, WebIDL::Clamp::@clamp@));\n"
        )

    if optional_default_value is not None:
        g.append("\n    else\n        @cpp_name@ = static_cast<@cpp_type@>(@parameter.optional_default_value@);\n")


def _generate_to_floating_point(parameter, type_, g, *, optional, optional_default_value):
    """Port of generate_floating_point_to_cpp (IDLGenerators.cpp:824-878)."""
    if type_.name == "unrestricted float":
        g.set("parameter.type.name", "float")
    elif type_.name == "unrestricted double":
        g.set("parameter.type.name", "double")
    # Plain "float" / "double" inherits the parameter.type.name from the
    # caller's generate_to_cpp fork.

    is_wrapped_in_optional = False
    if not optional:
        g.append("\n    @parameter.type.name@ @cpp_name@ = TRY(@js_name@@js_suffix@.to_double(vm));\n")
    else:
        if optional_default_value is not None and optional_default_value != "null":
            g.append("\n    @parameter.type.name@ @cpp_name@;\n")
        else:
            is_wrapped_in_optional = True
            g.append("\n    Optional<@parameter.type.name@> @cpp_name@;\n")
        g.append(
            "\n"
            "    if (!@js_name@@js_suffix@.is_undefined())\n"
            "        @cpp_name@ = TRY(@js_name@@js_suffix@.to_double(vm));\n"
        )
        if optional_default_value is not None and optional_default_value != "null":
            g.append("\n    else\n        @cpp_name@ = @parameter.optional_default_value@;\n")
        else:
            g.append("\n")

    if type_.name in ("float", "double"):
        if is_wrapped_in_optional:
            g.append(
                "\n"
                "    if (@cpp_name@.has_value() && (isinf(*@cpp_name@) || isnan(*@cpp_name@))) {\n"
                '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::InvalidRestrictedFloatingPointParameter, "@parameter.name@");\n'
                "    }\n    "
            )
        else:
            g.append(
                "\n"
                "    if (isinf(@cpp_name@) || isnan(@cpp_name@)) {\n"
                '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::InvalidRestrictedFloatingPointParameter, "@parameter.name@");\n'
                "    }\n    "
            )


def _generate_to_any(g, *, optional, optional_default_value, variadic):
    if variadic:
        g.append(
            "\n"
            "    GC::RootVector<JS::Value> @cpp_name@ { vm.heap() };\n"
            "\n"
            "    if (vm.argument_count() > @js_suffix@) {\n"
            "        @cpp_name@.ensure_capacity(vm.argument_count() - @js_suffix@);\n"
            "\n"
            "        for (size_t i = @js_suffix@; i < vm.argument_count(); ++i)\n"
            "            @cpp_name@.unchecked_append(vm.argument(i));\n"
            "    }\n"
        )
        return
    if not optional:
        g.append("\n    auto @cpp_name@ = @js_name@@js_suffix@;\n")
    else:
        g.append(
            "\n"
            "    JS::Value @cpp_name@ = JS::js_undefined();\n"
            "    if (!@js_name@@js_suffix@.is_undefined())\n"
            "        @cpp_name@ = @js_name@@js_suffix@;\n"
        )
        if optional_default_value is not None:
            if optional_default_value == "null":
                g.append("\n    else\n        @cpp_name@ = JS::js_null();\n")
            else:
                # Numeric default (int / unsigned).
                g.append("\n    else\n        @cpp_name@ = JS::Value(@parameter.optional_default_value@);\n")


def _generate_to_object(g, type_, *, optional):
    # Port of generate_object_to_cpp (IDLGenerators.cpp:893-923).
    if type_.nullable:
        g.append(
            "\n"
            "    Optional<GC::Root<JS::Object>> @cpp_name@;\n"
            "    if (!@js_name@@js_suffix@.is_null() && !@js_name@@js_suffix@.is_undefined()) {\n"
            "        if (!@js_name@@js_suffix@.is_object())\n"
            "            return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObject, @js_name@@js_suffix@);\n"
            "        @cpp_name@ = GC::make_root(@js_name@@js_suffix@.as_object());\n"
            "    }\n"
        )
    elif optional:
        g.append(
            "\n"
            "    Optional<GC::Root<JS::Object>> @cpp_name@;\n"
            "    if (!@js_name@@js_suffix@.is_undefined()) {\n"
            "        if (!@js_name@@js_suffix@.is_object())\n"
            "            return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObject, @js_name@@js_suffix@);\n"
            "        @cpp_name@ = GC::make_root(@js_name@@js_suffix@.as_object());\n"
            "    }\n"
        )
    else:
        g.append(
            "\n"
            "    if (!@js_name@@js_suffix@.is_object())\n"
            "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObject, @js_name@@js_suffix@);\n"
            "    auto @cpp_name@ = GC::make_root(@js_name@@js_suffix@.as_object());\n"
        )


def _generate_to_enum(g, type_, interface: Interface, *, optional, optional_default_value, throw_on_invalid=True):
    """Port of generate_enum_to_cpp (IDLGenerators.cpp:1054-1112)."""
    enum = interface.enumerations[type_.name]
    if optional_default_value is not None:
        default_name = optional_default_value
        if default_name.startswith('"') and default_name.endswith('"'):
            default_name = default_name[1:-1]
    else:
        default_name = enum.first_member
    g.set("enum.default.cpp_value", enum.translated_cpp_names[default_name])
    g.set("js_name.as_string", f"{g.get('js_name')}{g.get('js_suffix')}_string")
    g.append(
        "\n    @parameter.type.name.normalized@ @cpp_name@ { @parameter.type.name.normalized@::@enum.default.cpp_value@ };\n"
    )
    if optional:
        g.append("\n    if (!@js_name@@js_suffix@.is_undefined()) {\n")
    g.append("\n    auto @js_name.as_string@ = TRY(@js_name@@js_suffix@.to_string(vm));\n")
    first = True
    for name, cpp_value in enum.translated_cpp_names.items():
        else_prefix = "" if first else "else "
        first = False
        g.set("enum.alt.name", name)
        g.set("enum.alt.value", cpp_value)
        g.set("else", else_prefix)
        g.append(
            '\n    @else@if (@js_name.as_string@ == "@enum.alt.name@"sv)\n'
            "        @cpp_name@ = @parameter.type.name.normalized@::@enum.alt.value@;\n"
        )
    if throw_on_invalid:
        g.append(
            "\n    else\n"
            '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::InvalidEnumerationValue, @js_name.as_string@, "@parameter.type.name@");\n'
        )
    else:
        g.append("\n    else\n        return JS::js_undefined();\n")
    if optional:
        g.append("\n    }\n")


def _generate_to_platform_object(g, type_, *, optional):
    """Port of generate_platform_object_to_cpp.

    For a non-nullable, non-optional platform object the C++ does:
        auto* @cpp_name@ = TRY(impl_from(vm, @js_name@@js_suffix@));
    But that uses an `impl_from` taking a JS::Value which only exists when
    the surrounding interface is in the same C++ namespace as the parameter.
    We use the same shape but allow the type to differ via @cpp_type@.
    """
    # IDLGenerators.cpp:787-822. Use @parameter.type.name.normalized@ (already
    # set on the generator by generate_to_cpp) rather than @cpp_type@.
    if not type_.nullable:
        if not optional:
            g.append(
                "\n"
                "    if (!@js_name@@js_suffix@.is_object() || !is<@parameter.type.name.normalized@>(@js_name@@js_suffix@.as_object()))\n"
                '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@parameter.type.name@");\n'
                "\n"
                "    auto& @cpp_name@ = static_cast<@parameter.type.name.normalized@&>(@js_name@@js_suffix@.as_object());\n"
            )
        else:
            g.append(
                "\n"
                "    GC::Ptr<@parameter.type.name.normalized@> @cpp_name@;\n"
                "    if (!@js_name@@js_suffix@.is_undefined()) {\n"
                "        if (!@js_name@@js_suffix@.is_object() || !is<@parameter.type.name.normalized@>(@js_name@@js_suffix@.as_object()))\n"
                '            return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@parameter.type.name@");\n'
                "\n"
                "        @cpp_name@ = static_cast<@parameter.type.name.normalized@&>(@js_name@@js_suffix@.as_object());\n"
                "    }\n"
            )
    else:
        g.append("\n    GC::Ptr<@parameter.type.name.normalized@> @cpp_name@;\n")
        g.append(
            "\n"
            "    if (!@js_name@@js_suffix@.is_nullish()) {\n"
            "        if (!@js_name@@js_suffix@.is_object() || !is<@parameter.type.name.normalized@>(@js_name@@js_suffix@.as_object()))\n"
            '            return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@parameter.type.name@");\n'
            "\n"
            "        @cpp_name@ = &static_cast<@parameter.type.name.normalized@&>(@js_name@@js_suffix@.as_object());\n"
            "    }\n"
        )


def generate_arguments(parameters, interface: Interface, generator: SourceGenerator) -> str:
    """Port of generate_arguments (IDLGenerators.cpp:1922-...).

    Emits the per-argument coercion code into `generator`'s builder, and
    returns a comma-separated string of C++ parameter expressions to splice
    into the impl call.
    """
    names = []
    for index, parameter in enumerate(parameters):
        cpp_name = _make_input_acceptable_cpp(_to_snake(parameter.name))
        if parameter.variadic:
            names.append(f"move({cpp_name})")
        else:
            names.append(cpp_name)
            ag = generator.fork()
            ag.append(f"\n    auto arg{index} = vm.argument({index});\n")
        generate_to_cpp(
            parameter,
            "arg",
            str(index),
            _to_snake(parameter.name),
            interface,
            generator,
            optional=parameter.optional,
            optional_default_value=parameter.default_value,
            variadic=parameter.variadic,
        )
    return ", ".join(names)


def _to_snake(s):
    from .prototype import _to_snakecase

    return _to_snakecase(s)


def _find_callback_interface(interface: Interface, type_name: str):
    """Walk imported_interfaces looking for a callback interface with this name.

    Mirrors the semantics of IDL::Interface::referenced_interface +
    callback_interface_for_type from IDLGenerators.cpp.
    """
    seen: set[str] = set()
    queue = [interface]
    while queue:
        i = queue.pop(0)
        if i.filename in seen:
            continue
        seen.add(i.filename)
        if i.is_callback_interface and i.name == type_name:
            return i
        for imp in i.imported_interfaces:
            if imp.filename not in seen:
                queue.append(imp)
    return None


def _generate_to_callback_interface(g, type_, callback_interface) -> None:
    """Port of generate_callback_interface_to_cpp (IDLGenerators.cpp:761-785)."""
    g.set("cpp_type", callback_interface.implemented_name)
    if type_.nullable:
        g.append(
            "\n"
            "    @cpp_type@* @cpp_name@ = nullptr;\n"
            "    if (!@js_name@@js_suffix@.is_nullish()) {\n"
            "        if (!@js_name@@js_suffix@.is_object())\n"
            "            return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObject, @js_name@@js_suffix@);\n"
            "\n"
            "        auto callback_type = vm.heap().allocate<WebIDL::CallbackType>(@js_name@@js_suffix@.as_object(), HTML::incumbent_realm());\n"
            "        @cpp_name@ = TRY(throw_dom_exception_if_needed(vm, [&] { return @cpp_type@::create(realm, callback_type); }));\n"
            "    }\n"
        )
    else:
        g.append(
            "\n"
            "    if (!@js_name@@js_suffix@.is_object())\n"
            "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObject, @js_name@@js_suffix@);\n"
            "\n"
            "    auto callback_type = vm.heap().allocate<WebIDL::CallbackType>(@js_name@@js_suffix@.as_object(), HTML::incumbent_realm());\n"
            "    auto @cpp_name@ = TRY(throw_dom_exception_if_needed(vm, [&] { return @cpp_type@::create(realm, callback_type); }));\n"
        )


_JS_BUILTIN_BUFFER_TYPES = frozenset(
    {
        "ArrayBuffer",
        "SharedArrayBuffer",
        "DataView",
        "Int8Array",
        "Int16Array",
        "Int32Array",
        "BigInt64Array",
        "Uint8Array",
        "Uint16Array",
        "Uint32Array",
        "BigUint64Array",
        "Uint8ClampedArray",
        "Float16Array",
        "Float32Array",
        "Float64Array",
    }
)


def _is_js_builtin_buffer_type(name: str) -> bool:
    return name in _JS_BUILTIN_BUFFER_TYPES


def _generate_to_promise(g) -> None:
    """Port of generate_promise_to_cpp (IDLGenerators.cpp:880-890)."""
    g.append(
        "\n"
        "    // 1. Let promiseCapability be ? NewPromiseCapability(%Promise%).\n"
        "    auto promise_capability = TRY(JS::new_promise_capability(vm, realm.intrinsics().promise_constructor()));\n"
        "    // 2. Perform ? Call(promiseCapability.[[Resolve]], undefined, « V »).\n"
        "    TRY(JS::call(vm, *promise_capability->resolve(), JS::js_undefined(), @js_name@@js_suffix@));\n"
        "    // 3. Return promiseCapability.\n"
        "    auto @cpp_name@ = GC::make_root(promise_capability);\n"
    )


def _generate_to_array_buffer_view(g, type_, *, optional) -> None:
    """Port of generate_array_buffer_view_to_cpp (IDLGenerators.cpp:978-1007)."""
    g.append("\n    GC::Root<WebIDL::ArrayBufferView> @cpp_name@;\n")
    if type_.nullable:
        g.append("\n    if (!@js_name@@js_suffix@.is_null() && !@js_name@@js_suffix@.is_undefined()) {\n")
    g.append(
        "\n"
        "        if (!@js_name@@js_suffix@.is_object() || !(is<JS::TypedArrayBase>(@js_name@@js_suffix@.as_object()) || is<JS::DataView>(@js_name@@js_suffix@.as_object())))\n"
        '            return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@parameter.type.name@");\n'
        "\n"
        "        @cpp_name@ = GC::make_root(realm.create<WebIDL::ArrayBufferView>(@js_name@@js_suffix@.as_object()));\n"
    )
    if type_.nullable:
        g.append("\n    }\n")
    if optional:
        g.append("\n        }\n")


def _generate_to_buffer_source(g, type_, *, optional) -> None:
    """Port of generate_buffer_source_to_cpp (IDLGenerators.cpp:925-976)."""
    indent_levels = 2 if optional else 1
    indent = " " * (indent_levels * 4)
    g.set("buffer_source.indent", indent)
    if optional or type_.nullable:
        g.append("\n    Optional<GC::Root<WebIDL::BufferSource>> @cpp_name@;\n")
    else:
        g.append("\n    GC::Root<WebIDL::BufferSource> @cpp_name@;\n")
    if optional:
        g.append("\n    if (!@js_name@@js_suffix@.is_undefined()) {\n")
    elif type_.nullable:
        g.append(
            "\n"
            "    if (@js_name@@js_suffix@.is_undefined())\n"
            '        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@parameter.type.name@");\n'
        )
    if type_.nullable:
        g.append("\n@buffer_source.indent@if (!@js_name@@js_suffix@.is_null()) {\n")
    g.append(
        "\n@buffer_source.indent@    if (!@js_name@@js_suffix@.is_object() || !(is<JS::TypedArrayBase>(@js_name@@js_suffix@.as_object()) || is<JS::ArrayBuffer>(@js_name@@js_suffix@.as_object()) || is<JS::DataView>(@js_name@@js_suffix@.as_object())))\n"
        '@buffer_source.indent@        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObjectOfType, "@parameter.type.name@");\n'
        "\n@buffer_source.indent@    @cpp_name@ = GC::make_root(realm.create<WebIDL::BufferSource>(@js_name@@js_suffix@.as_object()));\n"
    )
    if type_.nullable:
        g.append("\n@buffer_source.indent@}\n")
    if optional:
        g.append("\n    }\n")


_INTEGER_TO_VECTOR_TYPE = {
    "byte": "WebIDL::Byte",
    "octet": "WebIDL::Octet",
    "short": "WebIDL::Short",
    "unsigned short": "WebIDL::UnsignedShort",
    "long": "WebIDL::Long",
    "unsigned long": "WebIDL::UnsignedLong",
    "long long": "WebIDL::LongLong",
    "unsigned long long": "WebIDL::UnsignedLongLong",
}


# (cpp_type_name, sequence_storage_type) — Vector or RootVector.
def _idl_type_name_to_cpp_type(t, interface) -> tuple[str, str]:
    """Port of idl_type_name_to_cpp_type (IDLGenerators.cpp:291-...).

    Returns (cpp_type_name, "Vector"|"GC::RootVector").
    """
    # Platform objects (interfaces): T → GC::Root<T>.
    if (
        t.kind == "plain"
        and t.name
        not in (
            "any",
            "undefined",
            "object",
            "boolean",
            "float",
            "double",
            "unrestricted float",
            "unrestricted double",
            "bigint",
            "DOMString",
            "ByteString",
            "USVString",
            "Utf16DOMString",
            "Utf16USVString",
            "CSSOMString",
            "Promise",
            "ArrayBufferView",
            "BufferSource",
        )
        and t.name not in _INTEGER_TO_VECTOR_TYPE
    ):
        if _is_js_builtin_buffer_type(t.name):
            return (f"GC::Root<JS::{t.name}>", "GC::RootVector")
        if t.name in interface.callback_functions:
            return ("GC::Root<WebIDL::CallbackType>", "GC::RootVector")
        cb_iface = _find_callback_interface(interface, t.name)
        if cb_iface is not None:
            return (f"GC::Root<{cb_iface.implemented_name}>", "GC::RootVector")
        if t.name in interface.enumerations:
            return (t.name, "Vector")
        if t.name in interface.dictionaries:
            return (t.name, "Vector")
        # Platform object.
        return (f"GC::Root<{t.name}>", "GC::RootVector")
    if is_string(t):
        if "Utf16" in t.name:
            return ("Utf16String", "Vector")
        return ("String", "Vector")
    if t.name in ("double", "unrestricted double") and not t.nullable:
        return ("double", "Vector")
    if t.name in ("float", "unrestricted float") and not t.nullable:
        return ("float", "Vector")
    if t.name == "boolean" and not t.nullable:
        return ("bool", "Vector")
    if t.name in _INTEGER_TO_VECTOR_TYPE and not t.nullable:
        return (_INTEGER_TO_VECTOR_TYPE[t.name], "Vector")
    if t.name == "any":
        return ("JS::Value", "GC::RootVector")
    if t.kind == "union":
        from .types import union_type_to_variant

        return (union_type_to_variant(t, interface), "Vector")
    raise NotImplementedError(f"idl_type_name_to_cpp_type for {t.name!r}")


def _generate_to_sequence(
    g, type_, js_name, js_suffix, cpp_name, interface, *, optional, optional_default_value, recursion_depth
) -> None:
    """Port of generate_sequence_to_cpp + generate_sequence_from_iterable
    (IDLGenerators.cpp:1222-1292 + 1953-2006).
    """
    elem_type = type_.parameters[0]
    elem_cpp_name, storage = _idl_type_name_to_cpp_type(elem_type, interface)
    g.set("recursion_depth", str(recursion_depth))
    g.set("sequence.type", elem_cpp_name)
    g.set("sequence.storage_type", storage)

    if optional or type_.nullable:
        if optional_default_value is None:
            g.append("\n    Optional<@sequence.storage_type@<@sequence.type@>> @cpp_name@;\n")
        else:
            if optional_default_value != "[]":
                raise NotImplementedError(f"sequence default {optional_default_value!r}")
            if storage == "Vector":
                g.append("\n    @sequence.storage_type@<@sequence.type@> @cpp_name@;\n")
            else:
                g.append("\n    @sequence.storage_type@<@sequence.type@> @cpp_name@ { vm.heap() };\n")
        if optional:
            g.append("\n    if (!@js_name@@js_suffix@.is_undefined()) {\n")
        else:
            g.append("\n    if (!@js_name@@js_suffix@.is_nullish()) {\n")

    g.append(
        "\n"
        "    if (!@js_name@@js_suffix@.is_object())\n"
        "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotAnObject, @js_name@@js_suffix@);\n"
        "\n"
        "    auto @js_name@@js_suffix@_iterator_method@recursion_depth@ = TRY(@js_name@@js_suffix@.get_method(vm, vm.well_known_symbol_iterator()));\n"
        "    if (!@js_name@@js_suffix@_iterator_method@recursion_depth@)\n"
        "        return vm.throw_completion<JS::TypeError>(JS::ErrorType::NotIterable, @js_name@@js_suffix@);\n"
    )

    inner_cpp_name = f"{cpp_name}_non_optional" if (optional or type_.nullable) else cpp_name
    iterable_name = f"{js_name}{js_suffix}"
    iterator_method_name = f"{js_name}{js_suffix}_iterator_method{recursion_depth}"
    # Use a fork so the inner generator's `cpp_name` rebind doesn't leak.
    _generate_sequence_from_iterable(
        g.fork(),
        type_,
        inner_cpp_name,
        iterable_name,
        iterator_method_name,
        interface,
        recursion_depth + 1,
        elem_cpp_name,
        storage,
    )

    if optional or type_.nullable:
        g.append("\n        @cpp_name@ = move(@cpp_name@_non_optional);\n    }\n")


def _generate_sequence_from_iterable(
    g, type_, cpp_name, iterable_name, iterator_method_name, interface, recursion_depth, elem_cpp_name, storage
):
    g.set("cpp_name", cpp_name)
    g.set("iterable_cpp_name", iterable_name)
    g.set("iterator_method_cpp_name", iterator_method_name)
    g.set("recursion_depth", str(recursion_depth))
    g.set("sequence.type", elem_cpp_name)
    g.set("sequence.storage_type", storage)

    g.append(
        "\n"
        "    auto @iterable_cpp_name@_iterator@recursion_depth@ = TRY(JS::get_iterator_from_method(vm, @iterable_cpp_name@, *@iterator_method_cpp_name@));\n"
    )
    if storage == "Vector":
        g.append("\n    @sequence.storage_type@<@sequence.type@> @cpp_name@;\n")
    else:
        g.append("\n    @sequence.storage_type@<@sequence.type@> @cpp_name@ { vm.heap() };\n")
    g.append(
        "\n"
        "    for (;;) {\n"
        "        auto next@recursion_depth@ = TRY(JS::iterator_step(vm, @iterable_cpp_name@_iterator@recursion_depth@));\n"
        "        if (!next@recursion_depth@.has<JS::IterationResult>())\n"
        "            break;\n"
        "\n"
        "        auto next_item@recursion_depth@ = TRY(next@recursion_depth@.get<JS::IterationResult>().value);\n"
    )
    # Coerce element. C++ uses parameter with empty extended_attributes, name=iterable_name.
    from ..ast import Parameter as _P

    element_param = _P(name=iterable_name, type=type_.parameters[0], extended_attributes={})
    generate_to_cpp(
        element_param,
        "next_item",
        str(recursion_depth),
        f"sequence_item{recursion_depth}",
        interface,
        g,
        optional=False,
        variadic=False,
        recursion_depth=recursion_depth,
    )
    g.append("\n    @cpp_name@.append(sequence_item@recursion_depth@);\n    }\n")


def _generate_to_union(
    parameter,
    type_,
    js_name: str,
    js_suffix: str,
    cpp_name: str,
    interface: Interface,
    generator: SourceGenerator,
    *,
    optional,
    optional_default_value,
    variadic,
    recursion_depth: int,
) -> None:
    """Port of generate_union_to_cpp (IDLGenerators.cpp:1295-1827)."""
    from ..ast import Parameter
    from .types import _is_platform_object_name
    from .types import flattened_member_types
    from .types import includes_nullable_type
    from .types import includes_undefined
    from .types import is_string
    from .types import union_type_to_variant

    ug = generator.fork()
    ug.set("union_type", union_type_to_variant(type_, interface))
    ug.set("recursion_depth", str(recursion_depth))

    types = flattened_member_types(type_)

    # Dictionary lookup — iterate interface.dictionaries to match the C++ ordering.
    dictionary_type = None
    for dict_name in interface.dictionaries:
        for t in types:
            if t.name == dict_name:
                dictionary_type = t
                break
        if dictionary_type is not None:
            break

    if dictionary_type is not None:
        dg = ug.fork()
        dg.set("dictionary.type", dictionary_type.name)
        dg.append(
            "\n"
            "    auto @js_name@@js_suffix@_to_dictionary = [&vm, &realm](JS::Value @js_name@@js_suffix@) -> JS::ThrowCompletionOr<@dictionary.type@> {\n"
            "        // This might be unused.\n"
            "        (void)realm;\n"
        )
        dict_param = Parameter(name=cpp_name, type=dictionary_type, extended_attributes={})
        generate_to_cpp(
            dict_param,
            js_name,
            js_suffix,
            "dictionary_union_type",
            interface,
            dg,
            optional=False,
            optional_default_value=None,
            variadic=False,
            recursion_depth=recursion_depth + 1,
        )
        dg.append("\n        return dictionary_union_type;\n    };\n")

    to_variant_captures = "&vm, &realm"
    if dictionary_type is not None:
        to_variant_captures += f", &{js_name}{js_suffix}_to_dictionary"
    ug.set("to_variant_captures", to_variant_captures)

    ug.append(
        "\n"
        "    auto @js_name@@js_suffix@_to_variant = [@to_variant_captures@](JS::Value @js_name@@js_suffix@) -> JS::ThrowCompletionOr<@union_type@> {\n"
        "        // These might be unused.\n"
        "        (void)vm;\n"
        "        (void)realm;\n"
    )

    if includes_undefined(type_):
        # NOTE: C++ writes to scoped_generator (outer) here (IDLGenerators.cpp:1368).
        generator.append("\n        if (@js_name@@js_suffix@.is_undefined())\n            return Empty {};\n")

    if includes_nullable_type(type_):
        ug.append("\n        if (@js_name@@js_suffix@.is_nullish())\n            return Empty {};\n")
    elif dictionary_type is not None:
        ug.append(
            "\n"
            "        if (@js_name@@js_suffix@.is_nullish())\n"
            "            return @union_type@ { TRY(@js_name@@js_suffix@_to_dictionary(@js_name@@js_suffix@)) };\n"
        )

    includes_object = any(t.name == "object" for t in types)

    ug.append(
        "\n"
        "        if (@js_name@@js_suffix@.is_object()) {\n"
        "            [[maybe_unused]] auto& @js_name@@js_suffix@_object = @js_name@@js_suffix@.as_object();\n"
    )

    includes_platform_object = any(_is_platform_object_name(t.name) for t in types)
    if includes_platform_object:
        ug.append("\n            if (is<PlatformObject>(@js_name@@js_suffix@_object)) {\n")
        for t in types:
            if not _is_platform_object_name(t.name):
                continue
            pg = ug.fork()
            pg.set("platform_object_type", t.name)
            pg.append(
                "\n"
                "                if (auto* @js_name@@js_suffix@_result = as_if<@platform_object_type@>(@js_name@@js_suffix@_object))\n"
                "                    return GC::make_root(*@js_name@@js_suffix@_result);\n"
            )
        if includes_object:
            ug.append("\n                return GC::make_root(@js_name@@js_suffix@_object);\n")
        ug.append("\n            }\n")

    includes_window_proxy = any(t.name == "WindowProxy" for t in types)
    if includes_window_proxy:
        ug.append(
            "\n"
            "            if (auto* @js_name@@js_suffix@_result = as_if<WindowProxy>(@js_name@@js_suffix@_object))\n"
            "                return GC::make_root(*@js_name@@js_suffix@_result);\n"
        )

    if any(t.name == "BufferSource" for t in types) and not includes_object:
        ug.append(
            "\n"
            "            if (is<JS::ArrayBuffer>(@js_name@@js_suffix@_object) || is<JS::DataView>(@js_name@@js_suffix@_object) || is<JS::TypedArrayBase>(@js_name@@js_suffix@_object)) {\n"
            "                GC::Ref<WebIDL::BufferSource> source_object = realm.create<WebIDL::BufferSource>(@js_name@@js_suffix@_object);\n"
            "                return GC::make_root(source_object);\n"
            "            }\n"
        )

    if any(t.name == "ArrayBuffer" for t in types) or includes_object:
        ug.append(
            "\n"
            "            if (is<JS::ArrayBuffer>(@js_name@@js_suffix@_object))\n"
            "                return GC::make_root(@js_name@@js_suffix@_object);\n"
        )

    if any(t.name == "DataView" for t in types) or includes_object:
        ug.append(
            "\n"
            "            if (is<JS::DataView>(@js_name@@js_suffix@_object))\n"
            "                return GC::make_root(@js_name@@js_suffix@_object);\n"
        )

    typed_arrays = {
        "Int8Array",
        "Int16Array",
        "Int32Array",
        "Uint8Array",
        "Uint16Array",
        "Uint32Array",
        "Uint8ClampedArray",
        "BigInt64Array",
        "BigUint64Array",
        "Float16Array",
        "Float32Array",
        "Float64Array",
    }
    typed_array_name = next((t.name for t in types if t.name in typed_arrays), None)
    if typed_array_name is not None:
        ug.set("typed_array_type", typed_array_name)
        ug.append(
            "\n"
            "            if (auto* typed_array = as_if<JS::@typed_array_type@>(@js_name@@js_suffix@_object))\n"
            "                return GC::make_root(*typed_array);\n"
        )
    elif includes_object:
        ug.append(
            "\n"
            "            if (is<JS::TypedArrayBase>(@js_name@@js_suffix@_object))\n"
            "                return GC::make_root(@js_name@@js_suffix@_object);\n"
        )

    includes_callable = any(t.name == "Function" for t in types)
    if includes_callable:
        ug.append(
            "\n"
            "            if (@js_name@@js_suffix@_object.is_function())\n"
            "                return vm.heap().allocate<WebIDL::CallbackType>(@js_name@@js_suffix@.as_function(), HTML::incumbent_realm());\n"
        )

    sequence_type = next((t for t in types if t.name == "sequence"), None)
    if sequence_type is not None:
        ug.append(
            "\n        auto method = TRY(@js_name@@js_suffix@.get_method(vm, vm.well_known_symbol_iterator()));\n"
        )
        ug.append("\n        if (method) {\n")
        elem_cpp, elem_storage = _idl_type_name_to_cpp_type(sequence_type.parameters[0], interface)
        _generate_sequence_from_iterable(
            ug,
            sequence_type,
            cpp_name,
            f"{js_name}{js_suffix}",
            "method",
            interface,
            recursion_depth + 1,
            elem_cpp,
            elem_storage,
        )
        ug.append("\n\n            return @cpp_name@;\n        }\n")

    if dictionary_type is not None:
        ug.append("\n        return @union_type@ { TRY(@js_name@@js_suffix@_to_dictionary(@js_name@@js_suffix@)) };\n")

    record_type = next((t for t in types if t.name == "record"), None)
    if record_type is not None:
        rec_param = Parameter(name=cpp_name, type=record_type, extended_attributes={})
        generate_to_cpp(
            rec_param,
            js_name,
            js_suffix,
            "record_union_type",
            interface,
            ug,
            optional=False,
            optional_default_value=None,
            variadic=False,
            recursion_depth=recursion_depth + 1,
        )
        ug.append("\n        return record_union_type;\n")

    for t in types:
        cb = _find_callback_interface(interface, t.name)
        if cb is None:
            continue
        cb_param = Parameter(name=cpp_name, type=t, extended_attributes={})
        generate_to_cpp(
            cb_param,
            js_name,
            js_suffix,
            "callback_interface_union_type",
            interface,
            ug,
            optional=False,
            optional_default_value=None,
            variadic=False,
            recursion_depth=recursion_depth + 1,
        )
        ug.append("\n        return callback_interface_union_type;\n")
        break

    if includes_object:
        ug.append("\n        return @js_name@@js_suffix@_object;\n")

    ug.append("\n        }\n")

    includes_boolean = any(t.name == "boolean" for t in types)
    if includes_boolean:
        ug.append(
            "\n"
            "        if (@js_name@@js_suffix@.is_boolean())\n"
            "            return @union_type@ { @js_name@@js_suffix@.as_bool() };\n"
        )

    def _is_numeric(t):
        return t.name in (
            "byte",
            "octet",
            "short",
            "unsigned short",
            "long",
            "unsigned long",
            "long long",
            "unsigned long long",
            "float",
            "double",
            "unrestricted float",
            "unrestricted double",
        )

    numeric_type = next((t for t in types if _is_numeric(t)), None)
    if numeric_type is not None:
        ug.append("\n        if (@js_name@@js_suffix@.is_number()) {\n")
        num_param = Parameter(type=numeric_type, name=parameter.name, default_value=None, extended_attributes={})
        generate_to_cpp(
            num_param,
            js_name,
            js_suffix,
            f"{js_name}{js_suffix}_number",
            interface,
            ug,
            optional=False,
            optional_default_value=None,
            variadic=False,
            recursion_depth=recursion_depth + 1,
        )
        ug.append("\n            return { @js_name@@js_suffix@_number };\n        }\n")

    includes_bigint = any(t.name == "bigint" for t in types)
    if includes_bigint:
        ug.append(
            "\n        if (@js_name@@js_suffix@.is_bigint())\n            return @js_name@@js_suffix@.as_bigint();\n"
        )

    includes_enumeration = any(t.name in interface.enumerations for t in types)
    if includes_enumeration:
        ug.append(
            "\n"
            "        if (@js_name@@js_suffix@.is_string()) {\n"
            "            auto @js_name@@js_suffix@_enum_string = TRY(@js_name@@js_suffix@.to_string(vm));\n"
        )
        for t in types:
            if t.name not in interface.enumerations:
                continue
            enum = interface.enumerations[t.name]
            enum_cpp, _ = _idl_type_name_to_cpp_type(t, interface)
            eg = ug.fork()
            eg.set("enum.type", enum_cpp)
            for raw, cpp_val in enum.translated_cpp_names.items():
                eg.set("enum.alt.name", raw)
                eg.set("enum.alt.value", cpp_val)
                eg.append(
                    "\n"
                    '            if (@js_name@@js_suffix@_enum_string == "@enum.alt.name@"sv)\n'
                    "                return @union_type@ { @enum.type@::@enum.alt.value@ };\n"
                )
        ug.append("\n        }\n")

    string_type = next((t for t in types if is_string(t)), None)
    if string_type is not None:
        str_param = Parameter(
            type=string_type,
            name=parameter.name,
            extended_attributes=parameter.extended_attributes,
        )
        generate_to_cpp(
            str_param,
            js_name,
            js_suffix,
            f"{js_name}{js_suffix}_string",
            interface,
            ug,
            optional=False,
            optional_default_value=None,
            variadic=False,
            recursion_depth=recursion_depth + 1,
        )
        ug.append("\n        return { @js_name@@js_suffix@_string };\n")
    elif numeric_type is not None and includes_bigint:
        ug.append(
            "\n"
            "        auto x = TRY(@js_name@@js_suffix@.to_numeric(vm));\n"
            "        if (x.is_bigint())\n"
            "            return x.as_bigint();\n"
            "        VERIFY(x.is_number());\n"
        )
        num_param = Parameter(type=numeric_type, name=parameter.name, default_value=None, extended_attributes={})
        generate_to_cpp(
            num_param,
            "x",
            "",
            "x_number",
            interface,
            ug,
            optional=False,
            optional_default_value=None,
            variadic=False,
            recursion_depth=recursion_depth + 1,
        )
        ug.append("\n        return x_number;\n")
    elif numeric_type is not None:
        num_param = Parameter(type=numeric_type, name=parameter.name, default_value=None, extended_attributes={})
        generate_to_cpp(
            num_param,
            js_name,
            js_suffix,
            f"{js_name}{js_suffix}_number",
            interface,
            ug,
            optional=False,
            optional_default_value=None,
            variadic=False,
            recursion_depth=recursion_depth + 1,
        )
        ug.append("\n        return { @js_name@@js_suffix@_number };\n")
    elif includes_boolean:
        ug.append("\n        return @union_type@ { @js_name@@js_suffix@.to_boolean() };\n")
    elif includes_bigint:
        ug.append("\n        return TRY(@js_name@@js_suffix@.to_bigint(vm));\n")
    else:
        ug.append('\n        return vm.throw_completion<JS::TypeError>("No union types matched"sv);\n')

    ug.append("\n    };\n")

    if not variadic:
        if not optional:
            ug.append("\n    @union_type@ @cpp_name@ = TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n")
        else:
            if optional_default_value is None:
                ug.append(
                    "\n"
                    "    Optional<@union_type@> @cpp_name@;\n"
                    "    if (!@js_name@@js_suffix@.is_undefined())\n"
                    "        @cpp_name@ = TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n"
                )
            elif optional_default_value == "null":
                if includes_nullable_type(type_):
                    ug.append(
                        "\n"
                        "    @union_type@ @cpp_name@ = @js_name@@js_suffix@.is_undefined() ? @union_type@ { Empty {} } : TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n"
                    )
                else:
                    ug.append(
                        "\n"
                        "    Optional<@union_type@> @cpp_name@;\n"
                        "    if (!@js_name@@js_suffix@.is_nullish())\n"
                        "        @cpp_name@ = TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n"
                    )
            elif optional_default_value == '""':
                ug.append(
                    "\n"
                    "    @union_type@ @cpp_name@ = @js_name@@js_suffix@.is_undefined() ? TRY(@js_name@@js_suffix@_to_variant(JS::Value(JS::PrimitiveString::create(vm, String {})))) : TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n"
                )
            elif optional_default_value.startswith('"') and optional_default_value.endswith('"'):
                ug.set("default_string_value", optional_default_value)
                ug.append(
                    "\n"
                    "    @union_type@ @cpp_name@ = @js_name@@js_suffix@.is_undefined() ? TRY(@js_name@@js_suffix@_to_variant(JS::Value(JS::PrimitiveString::create(vm, MUST(String::from_utf8(@default_string_value@sv)))))) : TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n"
                )
            elif optional_default_value == "{}":
                ug.append(
                    "\n"
                    "    @union_type@ @cpp_name@ = @js_name@@js_suffix@.is_undefined() ? TRY(@js_name@@js_suffix@_to_dictionary(@js_name@@js_suffix@)) : TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n"
                )
            elif optional_default_value in ("true", "false") or _is_numeric_literal(optional_default_value):
                ug.append(
                    "\n"
                    "    @union_type@ @cpp_name@ = @js_name@@js_suffix@.is_undefined() ? @parameter.optional_default_value@ : TRY(@js_name@@js_suffix@_to_variant(@js_name@@js_suffix@));\n"
                )
            else:
                raise NotImplementedError(f"union optional default {optional_default_value!r}")
    else:
        ug.append(
            "\n"
            "        Vector<@union_type@> @cpp_name@;\n"
            "\n"
            "        if (vm.argument_count() > @js_suffix@) {\n"
            "            @cpp_name@.ensure_capacity(vm.argument_count() - @js_suffix@);\n"
            "\n"
            "            for (size_t i = @js_suffix@; i < vm.argument_count(); ++i) {\n"
            "                auto result = TRY(@js_name@@js_suffix@_to_variant(vm.argument(i)));\n"
            "                @cpp_name@.unchecked_append(move(result));\n"
            "            }\n"
            "        }\n"
            "    "
        )


def _is_numeric_literal(s: str) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        pass
    try:
        float(s)
        return True
    except ValueError:
        return False
