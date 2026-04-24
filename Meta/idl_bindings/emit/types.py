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
from ..resolver import cpp_namespace_for_module_path
from .naming import _make_input_acceptable_cpp
from .naming import _to_snakecase
from .source_generator import SourceGenerator

# Module-level context set during batch generation.  The generator sets this
# before calling generate_header/generate_implementation so that type helpers
# can look up globally declared interfaces, dictionaries, enumerations, etc.
# without requiring context to be threaded through every helper signature.
_active_context = None


def _set_active_context(ctx) -> None:
    global _active_context
    _active_context = ctx


def _get_active_context():
    return _active_context


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
        "ResizeObserverSize",
        "Selection",
        "ServiceWorkerContainer",
        "ServiceWorkerRegistration",
        "StaticRange",
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
    if type_.kind != "plain":
        return False
    if type_.name in interface.enumerations:
        return True
    if _active_context is not None and type_.name in _active_context.enumerations:
        return True
    return False


def is_json(type_: Type, interface: Interface) -> bool:
    """Port of Type::is_json (Types.cpp:192-...)."""
    if is_primitive(type_):
        return True
    if is_string(type_) or is_enum(type_, interface):
        return True
    if type_.name == "object":
        return True
    if type_.kind == "union":
        return all(is_json(t, interface) for t in type_.union_member_types or [])
    if type_.name in interface.typedefs:
        td = interface.typedefs[type_.name]
        if td.type is not None:
            return is_json(td.type, interface)
    if type_.kind == "parameterized" and type_.name in ("sequence", "FrozenArray", "record"):
        return all(is_json(p, interface) for p in type_.parameters)
    if type_.name in interface.dictionaries:
        d = interface.dictionaries[type_.name]
        return all(is_json(m.type, interface) for m in d.members if m.type is not None)
    # Also resolve typedefs from global context.
    if type_.kind == "plain" and _active_context is not None:
        td = _active_context.typedefs.get(type_.name)
        if td is not None and td.type is not None:
            return is_json(td.type, interface)

    # - interface types that have a toJSON operation declared on themselves
    #   or one of their inherited interfaces (Types.cpp:258-292).
    def _lookup_iface(name: str):
        if name == interface.name:
            return interface
        if _active_context is not None:
            iface = _active_context.interfaces.get(name)
            if iface is not None:
                return iface
        for imp in interface.imported_interfaces:
            if imp.name == name:
                return imp
        return None

    cur: Interface | None = _lookup_iface(type_.name)
    while cur is not None:
        if any(op.name == "toJSON" for op in cur.operations):
            return True
        if not cur.parent_name:
            break
        cur = _lookup_iface(cur.parent_name)
    return False


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

    _ctx_dicts_wrap = _active_context.dictionaries if _active_context is not None else {}
    _is_dict_type = type_.name in interface.dictionaries or type_.name in _ctx_dicts_wrap

    # IDLGenerators.cpp:2017-2025 + the enum special case at 2244-2245.
    uses_value_access = is_optional and (
        type_.kind == "union"
        or is_string(type_)
        or type_.name in ("sequence", "FrozenArray")
        or is_primitive(type_)
        or is_enum(type_, interface)
        or _is_dict_type
    )
    # Enums also use .value() for *nullable* (not just optional), per
    # IDLGenerators.cpp:2244 (set value to value.value() when nullable too).
    if is_enum(type_, interface) and type_.nullable:
        uses_value_access = True
    value_non_optional_str = f"{value}.value()" if uses_value_access else value
    g.set("value_non_optional", value_non_optional_str)
    g.set("type", _cpp_type_name(type_))

    # Compound wrap: if nullable or optional (except union), open an
    # `if (value.has_value())` / `if (value)` block. IDLGenerators.cpp:2042-2069.
    wrap_in_if = False
    if (is_optional or type_.nullable) and type_.kind != "union":
        if (
            is_string(type_)
            or type_.name in ("sequence", "FrozenArray")
            or is_primitive(type_)
            or is_enum(type_, interface)
            or _is_dict_type
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
    if _find_callback_interface(interface, type_.name) is not None:
        g.append("\n  @result_expression@ @value@->callback().callback;\n")
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    # Resolve typedef before checking callback functions (e.g. EventHandler → EventHandlerNonNull?).
    _resolve_type = type_
    if type_.kind == "plain" and _active_context is not None:
        _td = _active_context.typedefs.get(type_.name) or interface.typedefs.get(type_.name)
        if _td is not None and _td.type is not None:
            _resolve_type = _td.type

    _ctx_cbs = _active_context.callback_functions if _active_context is not None else {}
    _check_cb_name = _resolve_type.name
    _check_cb_nullable = _resolve_type.nullable or type_.nullable
    if _resolve_type.kind == "plain" and (_check_cb_name in interface.callback_functions or _check_cb_name in _ctx_cbs):
        # IDLGenerators.cpp:2249-2268.
        callback = interface.callback_functions.get(_check_cb_name) or _ctx_cbs[_check_cb_name]
        if _check_cb_nullable:
            # Nullable callback: generate an explicit null-check form (IDLGenerators.cpp:2249-2257).
            g.append(
                "\n"
                "  if (!@value_non_optional@) {\n"
                "      @result_expression@ JS::js_null();\n"
                "  } else {\n"
                "      @result_expression@ @value_non_optional@->callback;\n"
                "  }\n"
            )
        elif callback.is_legacy_treat_non_object_as_null:
            # Non-nullable LegacyTreatNonObjectAsNull: still check null (IDLGenerators.cpp:2258-2268).
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

    _ctx_dicts = _active_context.dictionaries if _active_context is not None else {}
    if type_.kind == "plain" and (type_.name in interface.dictionaries or type_.name in _ctx_dicts):
        # IDLGenerators.cpp:2273-2336 — wrap a struct into a JS object by
        # iterating each member and create_data_property'ing it.
        # Use value_non_optional_str so optional outer values are unwrapped
        # before accessing their members (mirrors C++: value_non_optional + "." + member_name).
        _generate_dictionary_wrap(
            g,
            type_,
            value_non_optional_str,
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

    if type_.kind == "parameterized" and type_.name == "record":
        # IDLGenerators.cpp:2133-2165 — record<K, V> to JS object.
        value_type = type_.parameters[1]
        g.append(
            "\n"
            "    {\n"
            "        // An IDL record<…> value D is converted to a JavaScript value as follows:\n"
            "        // 1. Let result be OrdinaryObjectCreate(%Object.prototype%).\n"
            "        auto result = JS::Object::create(realm, realm.intrinsics().object_prototype());\n"
            "\n"
            "        // 2. For each key → value of D:\n"
            "        for (auto const& [key, value] : @value_non_optional@) {\n"
            "            // 1. Let jsKey be key converted to a JavaScript value.\n"
            "            auto js_key = JS::PropertyKey { Utf16FlyString::from_utf8(key) };\n"
            "\n"
            "            // 2. Let jsValue be value converted to a JavaScript value.\n"
        )
        generate_wrap_statement(
            g, "value", value_type, interface, "auto js_value =", recursion_depth=recursion_depth + 1
        )
        g.append(
            "\n"
            "\n"
            "            // 3. Let created be ! CreateDataProperty(result, jsKey, jsValue).\n"
            "            bool created = MUST(result->create_data_property(js_key, js_value));\n"
            "\n"
            "            // 4. Assert: created is true.\n"
            "            VERIFY(created);\n"
            "        }\n"
            "\n"
            "        @result_expression@ result;\n"
            "    }\n"
        )
        _close_wrap_if(g, type_, is_optional, wrap_in_if)
        return

    if type_.kind == "union":
        # IDLGenerators.cpp:2194-2241 — visit lambda per union member.
        flat = flattened_member_types(type_)
        ug = g.fork()
        ug.append("\n    @result_expression@ @value_non_optional@.visit(\n")
        for idx, member_type in enumerate(flat):
            cpp, _ = idl_type_name_to_cpp_type(member_type, interface)
            mg = ug.fork()
            mg.set("current_type", cpp)
            mg.append(
                "\n"
                "        [&vm, &realm]([[maybe_unused]] @current_type@ const& visited_union_value@recursion_depth@) -> JS::Value {\n"
                "            // These may be unused.\n"
                "            (void)vm;\n"
                "            (void)realm;\n"
            )
            generate_wrap_statement(
                mg,
                f"visited_union_value{recursion_depth}",
                member_type,
                interface,
                "return",
                recursion_depth=recursion_depth + 1,
            )
            if idx != len(flat) - 1 or includes_nullable_type(type_):
                mg.append("\n        },\n")
            else:
                mg.append("\n        }\n")
        if includes_nullable_type(type_):
            ug.append("\n        [](Empty) -> JS::Value {\n            return JS::js_null();\n        }\n")
        ug.append("\n    );\n")
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
    """Subset of cpp_type_name (IDLGenerators.cpp:225-234).

    Also mirrors interface_cpp_type_name(Context, Type) which special-cases
    WindowProxy (IDLGenerators.cpp:68-80).
    """
    # WindowProxy is not declared in an IDL file; always emit the hard-coded FQN.
    # Mirrors IDLGenerators.cpp:69-70.
    if type_.name == "WindowProxy":
        return "HTML::WindowProxy"

    # In batch mode, look up the fully-qualified name from the global context.
    if _active_context is not None:
        iface = _active_context.interfaces.get(type_.name)
        if iface is not None and iface.fully_qualified_name:
            return iface.fully_qualified_name
        # Dictionaries: derive FQN from their module_own_path.
        dictionary = _active_context.dictionaries.get(type_.name)
        if dictionary is not None and dictionary.module_own_path:
            ns = cpp_namespace_for_module_path(dictionary.module_own_path)
            if ns:
                return f"{ns}::{type_.name}"
    if type_.name in _JS_BUILTIN_BUFFER_TYPES:
        return f"JS::{type_.name}"
    return type_.name


def _find_callback_interface(interface: Interface, type_name: str):
    """Find a callback interface by name.

    In batch mode, looks up directly in the global context (no imported_interfaces).
    Falls back to BFS over imported_interfaces for old single-file mode.

    Mirrors the semantics of IDL::Interface::referenced_interface +
    callback_interface_for_type from IDLGenerators.cpp.
    """
    if _active_context is not None:
        ctx_iface = _active_context.interfaces.get(type_name)
        if ctx_iface is not None and ctx_iface.is_callback_interface:
            return ctx_iface
        return None

    # Old single-file mode: BFS over imported_interfaces.
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


# Storage-type for sequences in idl_type_name_to_cpp_type. Mirrors
# SequenceStorageType in IDLGenerators.cpp.
_SEQ_STORAGE_VECTOR = "Vector"
_SEQ_STORAGE_ROOT_VECTOR = "RootVector"


def idl_type_name_to_cpp_type(type_: Type, interface: Interface) -> tuple[str, str]:
    """Port of idl_type_name_to_cpp_type (IDLGenerators.cpp:291-407).

    Returns (cpp_type_name, sequence_storage_type).
    """
    name = type_.name
    if type_.kind == "plain" and _is_platform_object_name(name):
        # Use the fully-qualified name when the global context is available.
        # WindowProxy has no IDL file; hard-code its namespace (IDLGenerators.cpp:69-70).
        fqn = _cpp_type_name(type_)
        return (f"GC::Root<{fqn}>", _SEQ_STORAGE_ROOT_VECTOR)
    if type_.kind == "plain" and name in _JS_BUILTIN_BUFFER_TYPES:
        return (f"GC::Root<JS::{name}>", _SEQ_STORAGE_ROOT_VECTOR)
    cb_iface = _find_callback_interface(interface, name)
    if cb_iface is not None:
        cb_cpp_name = cb_iface.fully_qualified_name or cb_iface.implemented_name
        return (f"GC::Root<{cb_cpp_name}>", _SEQ_STORAGE_ROOT_VECTOR)
    ctx_cbs = _active_context.callback_functions if _active_context is not None else {}
    if name in interface.callback_functions or name in ctx_cbs:
        return ("GC::Root<WebIDL::CallbackType>", _SEQ_STORAGE_ROOT_VECTOR)
    if is_string(type_):
        if "Utf16" in name:
            return ("Utf16String", _SEQ_STORAGE_VECTOR)
        return ("String", _SEQ_STORAGE_VECTOR)
    if name in ("double", "unrestricted double") and not type_.nullable:
        return ("double", _SEQ_STORAGE_VECTOR)
    if name in ("float", "unrestricted float") and not type_.nullable:
        return ("float", _SEQ_STORAGE_VECTOR)
    if name == "boolean" and not type_.nullable:
        return ("bool", _SEQ_STORAGE_VECTOR)
    int_map = {
        "byte": "WebIDL::Byte",
        "octet": "WebIDL::Octet",
        "short": "WebIDL::Short",
        "unsigned short": "WebIDL::UnsignedShort",
        "long": "WebIDL::Long",
        "unsigned long": "WebIDL::UnsignedLong",
        "long long": "WebIDL::LongLong",
        "unsigned long long": "WebIDL::UnsignedLongLong",
    }
    if name in int_map and not type_.nullable:
        return (int_map[name], _SEQ_STORAGE_VECTOR)
    if name == "any":
        return ("JS::Value", _SEQ_STORAGE_ROOT_VECTOR)
    if name == "undefined":
        return ("Empty", _SEQ_STORAGE_ROOT_VECTOR)
    if name == "object":
        return ("GC::Root<JS::Object>", _SEQ_STORAGE_VECTOR)
    if name == "BufferSource":
        return ("GC::Root<WebIDL::BufferSource>", _SEQ_STORAGE_ROOT_VECTOR)
    if name == "ArrayBufferView":
        return ("GC::Root<WebIDL::ArrayBufferView>", _SEQ_STORAGE_ROOT_VECTOR)
    if name == "Promise":
        return ("GC::Root<WebIDL::Promise>", _SEQ_STORAGE_ROOT_VECTOR)
    if name in ("sequence", "FrozenArray"):
        elem_cpp, elem_storage = idl_type_name_to_cpp_type(type_.parameters[0], interface)
        storage_name = "GC::RootVector" if elem_storage == _SEQ_STORAGE_ROOT_VECTOR else "Vector"
        if elem_storage == _SEQ_STORAGE_ROOT_VECTOR:
            return (storage_name, _SEQ_STORAGE_VECTOR)
        return (f"{storage_name}<{elem_cpp}>", _SEQ_STORAGE_VECTOR)
    if name == "record":
        k_cpp, _ = idl_type_name_to_cpp_type(type_.parameters[0], interface)
        v_cpp, _ = idl_type_name_to_cpp_type(type_.parameters[1], interface)
        return (f"OrderedHashMap<{k_cpp}, {v_cpp}>", _SEQ_STORAGE_VECTOR)
    if type_.kind == "union":
        return (union_type_to_variant(type_, interface), _SEQ_STORAGE_VECTOR)
    ctx_dicts = _active_context.dictionaries if _active_context is not None else {}
    ctx_enums = _active_context.enumerations if _active_context is not None else {}
    if not type_.nullable and (name in interface.dictionaries or name in ctx_dicts):
        # Use the qualified name from the context if available.
        cpp_name = _cpp_type_name(type_)
        return (cpp_name, _SEQ_STORAGE_VECTOR)
    if name in interface.enumerations or name in ctx_enums:
        return (name, _SEQ_STORAGE_VECTOR)
    raise NotImplementedError(f"idl_type_name_to_cpp_type for {name!r} (kind={type_.kind})")


def flattened_member_types(union_type: Type) -> list[Type]:
    """Port of UnionType::flattened_member_types (Types.h:378-403)."""
    out: list[Type] = []
    for t in union_type.union_member_types or []:
        if t.kind == "union":
            out.extend(flattened_member_types(t))
        else:
            out.append(t)
    return out


def includes_undefined(type_: Type) -> bool:
    if type_.kind == "union":
        return any(includes_undefined(t) for t in type_.union_member_types or [])
    return type_.name == "undefined"


def includes_nullable_type(type_: Type) -> bool:
    # Mirror Type::includes_nullable_type (Types.cpp:34-49): own nullable flag
    # OR any member of a union being nullable.
    if type_.nullable:
        return True
    if type_.kind == "union":
        return any(includes_nullable_type(t) for t in type_.union_member_types or [])
    return False


def union_type_to_variant(union_type: Type, interface: Interface) -> str:
    """Port of union_type_to_variant (IDLGenerators.cpp:268-289)."""
    flat = flattened_member_types(union_type)
    parts: list[str] = []
    for t in flat:
        cpp, _ = idl_type_name_to_cpp_type(t, interface)
        parts.append(cpp)
    if includes_undefined(union_type) or includes_nullable_type(union_type):
        parts.append("Empty")
    return f"Variant<{', '.join(parts)}>"


def attribute_callback_basename(attribute) -> str:
    """Mirror the AttributeCallbackName / snake_case rule used for callback
    names (IDLGenerators.cpp:365-371)."""
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
    name = attribute.extended_attributes.get("ImplementedAs")
    if name:
        return name
    return _make_input_acceptable_cpp(_to_snakecase(attribute.name))


def _generate_dictionary_wrap(g, type_, value, interface: Interface, recursion_depth, iteration_index) -> None:
    """Port of the dictionary branch of generate_wrap_statement
    (IDLGenerators.cpp:2273-2336).
    """
    g.append(
        "\n"
        "    {\n"
        "        auto dictionary_object@recursion_depth@ = JS::Object::create(realm, realm.intrinsics().object_prototype());\n"
    )
    next_iteration = iteration_index + 1
    _ctx_dicts3 = _active_context.dictionaries if _active_context is not None else {}
    current = interface.dictionaries.get(type_.name) or _ctx_dicts3[type_.name]
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
        _ctx_dicts2 = _active_context.dictionaries if _active_context is not None else {}
        if not current.parent_name:
            break
        next_dict = interface.dictionaries.get(current.parent_name) or _ctx_dicts2.get(current.parent_name)
        if next_dict is None:
            break
        current = next_dict
    g.append("\n        @result_expression@ dictionary_object@recursion_depth@;\n    }\n")
