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


# Hand-curated list mirroring is_platform_object in IDLGenerators.cpp:31-199.
_PLATFORM_OBJECT_TYPES = frozenset(
    {
        "AbortSignal",
        "Animation",
        "AnimationEffect",
        "AnimationTimeline",
        "Attr",
        "AudioBuffer",
        "AudioContext",
        "AudioListener",
        "AudioNode",
        "AudioParam",
        "AudioScheduledSourceNode",
        "AudioTrack",
        "BaseAudioContext",
        "Blob",
        "CacheStorage",
        "CanvasGradient",
        "CanvasPattern",
        "CanvasRenderingContext2D",
        "ClipboardItem",
        "CloseWatcher",
        "Credential",
        "CredentialsContainer",
        "CryptoKey",
        "CSSKeywordValue",
        "CSSNumericArray",
        "CSSNumericValue",
        "CSSStyleValue",
        "CSSTransformComponent",
        "CSSUnitValue",
        "CSSUnparsedValue",
        "CSSVariableReferenceValue",
        "CustomElementRegistry",
        "CustomStateSet",
        "DataTransfer",
        "Document",
        "DocumentType",
        "DOMMatrix",
        "DOMMatrixReadOnly",
        "DOMRectReadOnly",
        "DynamicsCompressorNode",
        "ElementInternals",
        "EventTarget",
        "External",
        "FederatedCredential",
        "File",
        "FileList",
        "FontFace",
        "FormData",
        "Gamepad",
        "GamepadButton",
        "GamepadHapticActuator",
        "HTMLCollection",
        "IDBCursor",
        "IDBCursorWithValue",
        "IDBIndex",
        "IDBKeyRange",
        "IDBObjectStore",
        "IDBRecord",
        "IDBTransaction",
        "ImageBitmap",
        "ImageData",
        "Instance",
        "IntersectionObserverEntry",
        "KeyframeEffect",
        "MediaKeySystemAccess",
        "MediaList",
        "MediaDeviceInfo",
        "MediaDevices",
        "MediaSource",
        "Memory",
        "MediaStream",
        "MediaStreamTrack",
        "MediaStreamTrackEvent",
        "MessagePort",
        "Module",
        "MutationRecord",
        "NamedNodeMap",
        "NavigationDestination",
        "NavigationHistoryEntry",
        "Node",
        "OffscreenCanvas",
        "OffscreenCanvasRenderingContext2D",
        "Origin",
        "PasswordCredential",
        "Path2D",
        "PerformanceEntry",
        "PerformanceMark",
        "PerformanceNavigation",
        "PeriodicWave",
        "ReadableStreamBYOBReader",
        "ReadableStreamDefaultReader",
        "RadioNodeList",
        "Range",
        "ReadableStream",
        "Request",
        "Response",
        "Selection",
        "ServiceWorkerContainer",
        "ServiceWorkerRegistration",
        "SVGLength",
        "SVGNumber",
        "SVGTransform",
        "ShadowRoot",
        "SourceBuffer",
        "SpeechGrammar",
        "SpeechGrammarList",
        "SpeechRecognition",
        "SpeechRecognitionAlternative",
        "SpeechRecognitionPhrase",
        "SpeechRecognitionResult",
        "SpeechRecognitionResultList",
        "SpeechSynthesis",
        "SpeechSynthesisUtterance",
        "SpeechSynthesisVoice",
        "Storage",
        "Table",
        "Text",
        "TextMetrics",
        "TextTrack",
        "TimeRanges",
        "TrustedHTML",
        "TrustedScript",
        "TrustedScriptURL",
        "TrustedTypePolicy",
        "TrustedTypePolicyFactory",
        "URLSearchParams",
        "VTTRegion",
        "VideoTrack",
        "VideoTrackList",
        "ViewTransition",
        "WebGL2RenderingContext",
        "WebGLActiveInfo",
        "WebGLBuffer",
        "WebGLFramebuffer",
        "WebGLObject",
        "WebGLProgram",
        "WebGLQuery",
        "WebGLRenderbuffer",
        "WebGLRenderingContext",
        "WebGLSampler",
        "WebGLShader",
        "WebGLShaderPrecisionFormat",
        "WebGLSync",
        "WebGLTexture",
        "WebGLTransformFeedback",
        "WebGLUniformLocation",
        "WebGLVertexArrayObject",
        "WebGLVertexArrayObjectOES",
        "Window",
        "WindowProxy",
        "WritableStream",
        "XPathResult",
        "XRSession",
        "XRWebGLLayer",
    }
)


def _is_platform_object_name(name: str) -> bool:
    return name.endswith("Element") or name.endswith("Event") or name in _PLATFORM_OBJECT_TYPES


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


def is_enum(type_: Type, interface: Interface) -> bool:
    return type_.kind == "plain" and type_.name in interface.enumerations


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

    # IDLGenerators.cpp:2017-2025 + the enum special case at 2244-2245.
    uses_value_access = is_optional and (
        type_.kind == "union"
        or is_string(type_)
        or type_.name in ("sequence", "FrozenArray")
        or is_primitive(type_)
        or type_.name in interface.enumerations
        or type_.name in interface.dictionaries
    )
    # Enums also use .value() for *nullable* (not just optional), per
    # IDLGenerators.cpp:2244 (set value to value.value() when nullable too).
    if is_enum(type_, interface) and type_.nullable:
        uses_value_access = True
    g.set("value_non_optional", f"{value}.value()" if uses_value_access else value)
    g.set("type", _cpp_type_name(type_))

    # Compound wrap: if nullable or optional (except union), open an
    # `if (value.has_value())` / `if (value)` block. IDLGenerators.cpp:2042-2069.
    wrap_in_if = False
    if (is_optional or type_.nullable) and type_.kind != "union":
        if (
            is_string(type_)
            or type_.name in ("sequence", "FrozenArray")
            or is_primitive(type_)
            or type_.name in interface.enumerations
            or type_.name in interface.dictionaries
        ):
            g.append("\n    if (@value@.has_value()) {\n")
        else:
            g.append("\n    if (@value@) {\n")
        wrap_in_if = True
    if is_optional and type_.kind == "union":
        g.append("\n    if (@value@.has_value()) {\n")
        wrap_in_if = True

    if is_boolean(type_) or is_floating_point(type_):
        # IDLGenerators.cpp:2166-2179.
        if type_.nullable:
            g.append("\n    @result_expression@ JS::Value(@value@.release_value());\n")
        elif is_optional:
            g.append("\n    @result_expression@ JS::Value(@value_non_optional@);\n")
        else:
            g.append("\n    @result_expression@ JS::Value(@value@);\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if is_integer(type_):
        # IDLGenerators.cpp:2180-2181 → generate_from_integral.
        g.set("cpp_type", _IDL_INTEGER_TO_CPP_WRAP[type_.name])
        if type_.nullable or is_optional:
            g.append("\n    @result_expression@ JS::Value(static_cast<@cpp_type@>(@value@.value()));\n")
        else:
            g.append("\n    @result_expression@ JS::Value(static_cast<@cpp_type@>(@value@));\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if is_string(type_):
        # IDLGenerators.cpp:2071-2083.
        if type_.nullable or is_optional:
            g.append(
                "\n    @result_expression@ JS::PrimitiveString::create(vm, const_cast<decltype(@value@)&>(@value@).release_value());\n"
            )
        else:
            g.append("\n    @result_expression@ JS::PrimitiveString::create(vm, @value@);\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if is_enum(type_, interface):
        g.append(
            "\n    @result_expression@ JS::PrimitiveString::create(vm, Bindings::idl_enum_to_string(@value_non_optional@));\n"
        )
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "plain" and type_.name in ("Location", "Uint8Array", "Uint8ClampedArray", "any"):
        # IDLGenerators.cpp:2182-2185 — these all just pass through.
        g.append("\n    @result_expression@ @value_non_optional@;\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    # IDLGenerators.cpp:2269-2272 — callback interface.
    from .to_cpp import _find_callback_interface

    if _find_callback_interface(interface, type_.name) is not None:
        g.append("\n  @result_expression@ @value@->callback().callback;\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "plain" and type_.name in interface.callback_functions:
        # IDLGenerators.cpp:2249-2268.
        callback = interface.callback_functions[type_.name]
        if callback.is_legacy_treat_non_object_as_null and not type_.nullable:
            g.append(
                "\n"
                "  if (!@value_non_optional@) {\n"
                "      @result_expression@ JS::js_null();\n"
                "  } else {\n"
                "      @result_expression@ @value_non_optional@->callback;\n"
                "  }\n"
            )
        else:
            g.append("\n  @result_expression@ @value_non_optional@->callback;\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "plain" and type_.name in interface.dictionaries:
        # IDLGenerators.cpp:2273-2336 — wrap a struct into a JS object by
        # iterating each member and create_data_property'ing it.
        _generate_dictionary_wrap(
            g,
            type_,
            value,
            interface,
            recursion_depth,
            iteration_index,
        )
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "plain" and type_.name == "object":
        # IDLGenerators.cpp:2337-2340.
        g.append("\n    @result_expression@ JS::Value(const_cast<JS::Object*>(@value_non_optional@));\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.name == "Promise":
        # IDLGenerators.cpp:2186-2189.
        g.append("\n    @result_expression@ GC::Ref { as<JS::Promise>(*@value_non_optional@->promise()) };\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "parameterized" and type_.name in ("sequence", "FrozenArray"):
        # IDLGenerators.cpp:2084-2132.
        elem_type = type_.parameters[0]
        g.append("\n    auto new_array@recursion_depth@_@iteration_index@ = MUST(JS::Array::create(realm, 0));\n")
        if type_.nullable or is_optional:
            g.append(
                "\n"
                "    auto& @value_cpp_name@_non_optional = @value@.value();\n"
                "    for (size_t i@recursion_depth@ = 0; i@recursion_depth@ < @value_cpp_name@_non_optional.size(); ++i@recursion_depth@) {\n"
                "        auto& element@recursion_depth@ = @value_cpp_name@_non_optional.at(i@recursion_depth@);\n"
            )
        else:
            g.append(
                "\n"
                "    for (size_t i@recursion_depth@ = 0; i@recursion_depth@ < @value@.size(); ++i@recursion_depth@) {\n"
                "        auto& element@recursion_depth@ = @value@.at(i@recursion_depth@);\n"
            )
        # Platform-object element: unwrap GC::Root via *element.
        if elem_type.kind == "plain" and _is_platform_object_name(elem_type.name):
            g.append("\n        auto* wrapped_element@recursion_depth@ = &(*element@recursion_depth@);\n")
        else:
            g.append("JS::Value wrapped_element@recursion_depth@;\n")
            generate_wrap_statement(
                g,
                f"element{recursion_depth}",
                elem_type,
                interface,
                f"wrapped_element{recursion_depth} =",
                recursion_depth=recursion_depth + 1,
            )
        g.append(
            "\n"
            "        auto property_index@recursion_depth@ = JS::PropertyKey { i@recursion_depth@ };\n"
            "        MUST(new_array@recursion_depth@_@iteration_index@->create_data_property(property_index@recursion_depth@, wrapped_element@recursion_depth@));\n"
            "    }\n"
        )
        if type_.name == "FrozenArray":
            g.append(
                "\n    TRY(new_array@recursion_depth@_@iteration_index@->set_integrity_level(IntegrityLevel::Frozen));\n"
            )
        g.append("\n    @result_expression@ new_array@recursion_depth@_@iteration_index@;\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "plain" and type_.name in ("ArrayBufferView", "BufferSource"):
        # IDLGenerators.cpp:2190-2193.
        g.append("\n    @result_expression@ JS::Value(@value_non_optional@->raw_object());\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "plain":
        g.append("\n    @result_expression@ &const_cast<@type@&>(*@value_non_optional@);\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    raise NotImplementedError(f"wrap statement for type {type_.name!r} (kind={type_.kind}) not supported yet")


def _close_wrap_if(g, type_, is_optional, wrap_in_if) -> None:
    """Mirror IDLGenerators.cpp:2347-2358 — the closer for the nullable/optional
    wrap block. For nullable non-union types, emit `} else { result = null; }`.
    For optional (any type), emit just `}`.
    """
    if not wrap_in_if:
        return
    if type_.nullable and type_.kind != "union":
        g.append("\n    } else {\n        @result_expression@ JS::js_null();\n    }\n")
    elif is_optional:
        g.append("\n    }\n")


_JS_BUILTIN_BUFFER_TYPES = frozenset(
    {
        "ArrayBuffer",
        "SharedArrayBuffer",
        "DataView",
        "Int8Array",
        "Uint8Array",
        "Uint8ClampedArray",
        "Int16Array",
        "Uint16Array",
        "Int32Array",
        "Uint32Array",
        "BigInt64Array",
        "BigUint64Array",
        "Float16Array",
        "Float32Array",
        "Float64Array",
    }
)


def _cpp_type_name(type_: Type) -> str:
    """Subset of cpp_type_name (IDLGenerators.cpp:225-234)."""
    from ..resolver import _libweb_interface_namespaces

    if type_.name in _libweb_interface_namespaces():
        return f"{type_.name}::{type_.name}"
    if type_.name in _JS_BUILTIN_BUFFER_TYPES:
        return f"JS::{type_.name}"
    return type_.name


def attribute_callback_basename(attribute) -> str:
    """Mirror the AttributeCallbackName / snake_case rule used for callback
    names (IDLGenerators.cpp:365-371)."""
    from .prototype import _to_snakecase  # avoid circular import at module load

    name = attribute.extended_attributes.get("AttributeCallbackName")
    if name:
        return name
    return _to_snakecase(attribute.name).replace("-", "_")


def attribute_cpp_name(attribute) -> str:
    """C++ name of the attribute getter — `[ImplementedAs]` or snake_case.

    Mirrors IDLGenerators.cpp:4469-4474: if `[ImplementedAs]` is present
    use it verbatim; otherwise snake_case the attribute name and route it
    through make_input_acceptable_cpp (which handles C++ keyword clashes
    like `operator` → `operator_`).
    """
    from .prototype import _make_input_acceptable_cpp
    from .prototype import _to_snakecase

    name = attribute.extended_attributes.get("ImplementedAs")
    if name:
        return name
    return _make_input_acceptable_cpp(_to_snakecase(attribute.name))


def _generate_dictionary_wrap(g, type_, value, interface: Interface, recursion_depth, iteration_index) -> None:
    """Port of the dictionary branch of generate_wrap_statement
    (IDLGenerators.cpp:2273-2336).
    """
    from .prototype import _make_input_acceptable_cpp
    from .prototype import _to_snakecase

    g.append(
        "\n"
        "    {\n"
        "        auto dictionary_object@recursion_depth@ = JS::Object::create(realm, realm.intrinsics().object_prototype());\n"
    )
    next_iteration = iteration_index + 1
    current = interface.dictionaries[type_.name]
    current_name = type_.name
    while True:
        for member in current.members:
            g.set("member_key", member.name)
            member_key_js = f"{_make_input_acceptable_cpp(_to_snakecase(member.name))}{recursion_depth}"
            g.set("member_name", member_key_js)
            member_value_js = f"{member_key_js}_value"
            g.set("member_value", member_value_js)
            wrapped_value_name = f"wrapped_{member_value_js}"
            g.set("wrapped_value_name", wrapped_value_name)
            is_opt = (
                not member.required
                and "GenerateAsRequired" not in member.extended_attributes
                and member.default_value is None
            )
            if is_opt:
                g.append("\n        Optional<JS::Value> @wrapped_value_name@;\n")
            else:
                g.append("\n        JS::Value @wrapped_value_name@;\n")
            next_iteration += 1
            sep = "->" if type_.nullable else "."
            value_member = f"{value}{sep}{_to_snakecase(member.name)}"
            generate_wrap_statement(
                g,
                value_member,
                member.type,
                interface,
                f"{wrapped_value_name} =",
                recursion_depth=recursion_depth + 1,
                is_optional=is_opt,
                iteration_index=next_iteration,
            )
            if is_opt:
                g.append(
                    "\n"
                    "        if (@wrapped_value_name@.has_value())\n"
                    '            MUST(dictionary_object@recursion_depth@->create_data_property("@member_key@"_utf16_fly_string, @wrapped_value_name@.release_value()));\n'
                )
            else:
                g.append(
                    "\n"
                    '        MUST(dictionary_object@recursion_depth@->create_data_property("@member_key@"_utf16_fly_string, @wrapped_value_name@));\n'
                )
        if not current.parent_name or current.parent_name not in interface.dictionaries:
            break
        current_name = current.parent_name
        current = interface.dictionaries[current_name]
    g.append("\n        @result_expression@ dictionary_object@recursion_depth@;\n    }\n")
