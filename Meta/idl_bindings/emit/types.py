"""Type-system helpers — port of subset of generate_wrap_statement /
generate_to_cpp / cpp_type_name / generate_from_integral from
Meta/Lagom/Tools/CodeGenerators/LibWeb/BindingsGenerator/IDLGenerators.cpp.

Currently covers the simple type cases needed for constants and primitive
attribute getters: boolean, integer (all widths), floating-point, undefined.
String / sequence / Promise / dictionary / interface / union wrapping comes
in as later concept-ladder rungs need them.
"""

from __future__ import annotations

from ..ast import Interface
from ..ast import Type
from .source_generator import SourceGenerator

# IDL integer-type → C++ alias used in wrap statements.
# Mirrors the table in generate_from_integral (IDLGenerators.cpp:589-598).
_IDL_INTEGER_TO_CPP_WRAP = {
    "byte": "WebIDL::Byte",
    "octet": "WebIDL::Octet",
    "short": "WebIDL::Short",
    "unsigned short": "WebIDL::UnsignedShort",
    "long": "WebIDL::Long",
    "unsigned long": "WebIDL::UnsignedLong",
    "long long": "double",
    "unsigned long long": "double",
}


_IDL_STRING_NAMES = frozenset(
    {
        "ByteString",
        "CSSOMString",
        "DOMString",
        "Utf16DOMString",
        "USVString",
        "Utf16USVString",
    }
)


def is_string(type_: Type) -> bool:
    return type_.kind == "plain" and type_.name in _IDL_STRING_NAMES


def is_integer(type_: Type) -> bool:
    return type_.kind == "plain" and type_.name in _IDL_INTEGER_TO_CPP_WRAP


def is_floating_point(type_: Type) -> bool:
    return type_.kind == "plain" and type_.name in ("float", "double", "unrestricted float", "unrestricted double")


def is_boolean(type_: Type) -> bool:
    return type_.kind == "plain" and type_.name == "boolean"


def is_primitive(type_: Type) -> bool:
    return (
        is_integer(type_)
        or is_floating_point(type_)
        or is_boolean(type_)
        or (type_.kind == "plain" and type_.name == "bigint")
    )


def generate_wrap_statement(
    generator: SourceGenerator,
    value: str,
    type_: Type,
    interface: Interface,
    result_expression: str,
    *,
    recursion_depth: int = 0,
    is_optional: bool = False,
    iteration_index: int = 0,
) -> None:
    """Port of generate_wrap_statement (IDLGenerators.cpp:2008-...).

    Currently a *primitive-only* subset — handles boolean, integer (all widths),
    floating-point, and the explicit `undefined` type. Anything else raises
    NotImplementedError so the parity test surfaces it as a known gap rather
    than silently emitting the wrong code.
    """
    g = generator.fork()
    g.set("value", value)
    g.set("value_cpp_name", value.replace(".", "_"))
    g.set("result_expression", result_expression)
    g.set("recursion_depth", str(recursion_depth))
    g.set("iteration_index", str(iteration_index))

    # `undefined` short-circuits at the very top (IDLGenerators.cpp:2035-2040).
    if type_.kind == "plain" and type_.name == "undefined":
        g.append("\n    @result_expression@ JS::js_undefined();\n")
        return

    if (is_optional or type_.nullable) and type_.kind != "union":
        # Nullable / optional primitive wrap: opens an `if (value.has_value())`
        # then emits the wrap and closes with `} else { result = undefined; }`.
        # Below we handle only the non-optional / non-nullable case for
        # primitives, which covers all constant declarations we care about.
        raise NotImplementedError(f"nullable/optional wrap for {type_.name!r} is not yet supported")

    if is_boolean(type_) or is_floating_point(type_):
        # IDLGenerators.cpp:2166-2179 — non-nullable, non-optional path.
        g.append("\n    @result_expression@ JS::Value(@value@);\n")
        return

    if is_integer(type_):
        # IDLGenerators.cpp:2180-2181 → generate_from_integral.
        g.set("cpp_type", _IDL_INTEGER_TO_CPP_WRAP[type_.name])
        g.append("\n    @result_expression@ JS::Value(static_cast<@cpp_type@>(@value@));\n")
        return

    raise NotImplementedError(f"wrap statement for type {type_.name!r} (kind={type_.kind}) not supported yet")


def attribute_callback_basename(attribute) -> str:
    """Mirror the AttributeCallbackName / snake_case rule used for callback
    names (IDLGenerators.cpp:365-371)."""
    from .prototype import _to_snakecase  # avoid circular import at module load

    name = attribute.extended_attributes.get("AttributeCallbackName")
    if name:
        return name
    return _to_snakecase(attribute.name).replace("-", "_")


def attribute_cpp_name(attribute) -> str:
    """C++ name of the attribute getter — `[ImplementedAs]` or snake_case."""
    from .prototype import _to_snakecase

    name = attribute.extended_attributes.get("ImplementedAs")
    if name:
        return name
    return _to_snakecase(attribute.name).replace("-", "_")
