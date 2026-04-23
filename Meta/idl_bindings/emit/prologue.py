"""Implementation file prologue — port of generate_implementation_prologue
(IDLGenerators.cpp:6122-6195) and emit_includes_for_all_imports
(IDLGenerators.cpp:465-497) and generate_using_namespace_definitions
(IDLGenerators.cpp:5418-5475).

The prologue is the giant fixed include block + the per-interface include
+ the using-namespace block + the `namespace Web::Bindings {` opener that
sits at the top of every generated .cpp.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from ..ast import Interface
from .source_generator import SourceGenerator

# IDLGenerators.cpp:5418-5475 (generate_using_namespace_definitions).
# Order is significant — must match the C++ exactly to keep parity.
_USING_NAMESPACES = (
    "Web::Animations",
    "Web::Clipboard",
    "Web::ContentSecurityPolicy",
    "Web::CookieStore",
    "Web::CredentialManagement",
    "Web::Crypto",
    "Web::CSS",
    "Web::DOM",
    "Web::DOMURL",
    "Web::Encoding",
    "Web::EncryptedMediaExtensions",
    "Web::EntriesAPI",
    "Web::EventTiming",
    "Web::Fetch",
    "Web::FileAPI",
    "Web::Gamepad",
    "Web::Geolocation",
    "Web::Geometry",
    "Web::HighResolutionTime",
    "Web::HTML",
    "Web::IndexedDB",
    "Web::Internals",
    "Web::IntersectionObserver",
    "Web::MediaCapabilitiesAPI",
    "Web::MediaCapture",
    "Web::MediaSourceExtensions",
    "Web::NavigationTiming",
    "Web::NotificationsAPI",
    "Web::PerformanceTimeline",
    "Web::RequestIdleCallback",
    "Web::ResizeObserver",
    "Web::ResourceTiming",
    "Web::Selection",
    "Web::Serial",
    "Web::ServiceWorker",
    "Web::Speech",
    "Web::StorageAPI",
    "Web::Streams",
    "Web::SVG",
    "Web::TrustedTypes",
    "Web::UIEvents",
    "Web::URLPattern",
    "Web::UserTiming",
    "Web::WebAssembly",
    "Web::WebAudio",
    "Web::WebGL",
    "Web::WebGL::Extensions",
    "Web::WebIDL",
    "Web::WebVTT",
    "Web::WebXR",
    "Web::XHR",
    "Web::XPath",
)


def _will_generate_code(interface: Interface) -> bool:
    """Mirror IDL::Interface::will_generate_code (Types.h:356-359)."""
    if interface.name:
        return True
    if any(d.is_original_definition for d in interface.dictionaries.values()):
        return True
    if any(e.is_original_definition for e in interface.enumerations.values()):
        return True
    return False


def _generate_include_for_interface(generator: SourceGenerator, interface: Interface) -> None:
    """Mirror IDLGenerators.cpp:445-463 (generate_include_for_interface).

    Computes a header path relative to known search paths. Without a runtime
    `g_header_search_paths`, we just emit the absolute path the same way the
    C++ tool does when it finds no match — that's still byte-identical for
    the corpus we care about.
    """
    g = generator.fork()
    path_string = interface.filename or ""
    include_title = interface.implemented_name if interface.implemented_name else Path(path_string).stem
    include_path = f"{Path(path_string).parent}/{include_title}.h"
    g.set("include.path", include_path)
    g.append("\n#include <@include.path@>\n")


def emit_includes_for_all_imports(
    interface: Interface,
    generator: SourceGenerator,
    is_iterator: bool = False,
    is_async_iterator: bool = False,
) -> None:
    # IDLGenerators.cpp:465-497. Mirrors the post-f25bfd747a structure:
    # - The main interface is emitted BEFORE the BFS (if will_generate_code()).
    # - Imported modules are enqueued in BFS order, but an include is only
    #   generated for modules that have a primary interface (non-empty name).
    #   This mirrors the C++ check `module->interface.has_value()`, which is
    #   only set when `!interface.name.is_empty()` (IDLParser.cpp:1487-1488).
    #   Dict/enum-only files (no primary interface) are traversed for their
    #   sub-imports but do NOT themselves generate an #include via the BFS.
    # Mirrors the post-f25bfd747a C++ structure:
    # 1. Mark main interface as visited, emit its include if will_generate_code().
    # 2. BFS over imports. For imported modules, only emit an include if the
    #    file has a primary interface (non-empty name). Dict/enum-only files
    #    (like QueuingStrategy.idl) are traversed for sub-imports but do not
    #    themselves generate an #include. This mirrors the C++ check
    #    `module->interface.has_value()`, which is only set for non-empty-name
    #    interfaces (IDLParser.cpp:1487-1488).
    seen: set[str] = {interface.filename}
    queue: deque[Interface] = deque(interface.imported_interfaces)

    if _will_generate_code(interface):
        _generate_include_for_interface(generator, interface)

    while queue:
        i = queue.popleft()
        if i.filename in seen:
            continue
        seen.add(i.filename)
        for imp in i.imported_interfaces:
            if imp.filename not in seen:
                queue.append(imp)
        if not i.name:
            continue
        _generate_include_for_interface(generator, i)
    if is_iterator:
        iterator_path = interface.fully_qualified_name.replace("::", "/") + "Iterator"
        ig = generator.fork()
        ig.set("iterator_class.path", iterator_path)
        ig.append("\n#   include <LibWeb/@iterator_class.path@.h>\n")
    if is_async_iterator:
        iterator_path = interface.fully_qualified_name.replace("::", "/") + "AsyncIterator"
        ig = generator.fork()
        ig.set("iterator_class.path", iterator_path)
        ig.append("\n#   include <LibWeb/@iterator_class.path@.h>\n")


def generate_implementation_prologue(interface: Interface, generator: SourceGenerator) -> None:
    g = generator.fork()
    g.set("bindings_name", interface.implemented_name)
    if interface.parent_name:
        g.set("parent_bindings_name", interface.parent_name)

    # IDLGenerators.cpp:6130-6177 — fixed include block. The leading "\n"
    # mirrors the C++ raw-string literal which starts with a newline after
    # the opening paren.
    g.append(
        "\n"
        "#include <AK/Function.h>\n"
        "#include <AK/TypeCasts.h>\n"
        "#include <LibGC/Heap.h>\n"
        "#include <LibIDL/Types.h>\n"
        "#include <LibJS/Runtime/AbstractOperations.h>\n"
        "#include <LibJS/Runtime/Array.h>\n"
        "#include <LibJS/Runtime/ArrayBuffer.h>\n"
        "#include <LibJS/Runtime/DataView.h>\n"
        "#include <LibJS/Runtime/Error.h>\n"
        "#include <LibJS/Runtime/FunctionObject.h>\n"
        "#include <LibJS/Runtime/GlobalObject.h>\n"
        "#include <LibJS/Runtime/Iterator.h>\n"
        "#include <LibJS/Runtime/PrimitiveString.h>\n"
        "#include <LibJS/Runtime/PromiseConstructor.h>\n"
        "#include <LibJS/Runtime/TypedArray.h>\n"
        "#include <LibJS/Runtime/Value.h>\n"
        "#include <LibJS/Runtime/ValueInlines.h>\n"
        "#include <LibWeb/Bindings/@bindings_name@.h>\n"
        "#include <LibWeb/Bindings/ExceptionOrUtils.h>\n"
        "#include <LibWeb/Bindings/Intrinsics.h>\n"
        "#include <LibWeb/Bindings/MainThreadVM.h>\n"
        "#include <LibWeb/Bindings/PrincipalHostDefined.h>\n"
        "#include <LibWeb/DOM/Element.h>\n"
        "#include <LibWeb/DOM/ElementFactory.h>\n"
        "#include <LibWeb/DOM/Event.h>\n"
        "#include <LibWeb/DOM/IDLEventListener.h>\n"
        "#include <LibWeb/DOM/NodeFilter.h>\n"
        "#include <LibWeb/DOM/Range.h>\n"
        "#include <LibWeb/HTML/CustomElements/CustomElementDefinition.h>\n"
        "#include <LibWeb/HTML/CustomElements/CustomElementRegistry.h>\n"
        "#include <LibWeb/HTML/Numbers.h>\n"
        "#include <LibWeb/HTML/Scripting/Environments.h>\n"
        "#include <LibWeb/HTML/Scripting/SimilarOriginWindowAgent.h>\n"
        "#include <LibWeb/HTML/Window.h>\n"
        "#include <LibWeb/HTML/WindowProxy.h>\n"
        "#include <LibWeb/Infra/Strings.h>\n"
        "#include <LibWeb/Namespace.h>\n"
        "#include <LibWeb/WebIDL/AbstractOperations.h>\n"
        "#include <LibWeb/WebIDL/AsyncIterator.h>\n"
        "#include <LibWeb/WebIDL/Buffers.h>\n"
        "#include <LibWeb/WebIDL/CallbackType.h>\n"
        "#include <LibWeb/WebIDL/OverloadResolution.h>\n"
        "#include <LibWeb/WebIDL/Promise.h>\n"
        "#include <LibWeb/WebIDL/Tracing.h>\n"
        "#include <LibWeb/WebIDL/Types.h>\n"
        "\n"
    )

    if interface.parent_name:
        # IDLGenerators.cpp:6181-6185.
        g.append(
            "\n"
            "#if __has_include(<LibWeb/Bindings/@parent_bindings_name@.h>)\n"
            "#    include <LibWeb/Bindings/@parent_bindings_name@.h>\n"
            "#endif\n"
            "\n"
        )

    emit_includes_for_all_imports(
        interface,
        g,
        is_iterator=interface.pair_iterator_types is not None,
        is_async_iterator=interface.async_value_iterator_type is not None,
    )

    # IDLGenerators.cpp:5418-5475 (generate_using_namespace_definitions).
    g.append("\n// FIXME: This is a total hack until we can figure out the namespace for a given type somehow.\n")
    for ns in _USING_NAMESPACES:
        g.append(f"using namespace {ns};\n")

    # IDLGenerators.cpp:6191-6193 — namespace open.
    g.append("\nnamespace Web::Bindings {\n\n")
