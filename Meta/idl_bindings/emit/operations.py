"""Operation body emitter — port of generate_function
(IDLGenerators.cpp:2386-2501) and generate_arguments
(IDLGenerators.cpp:1922-...).

Operations are the regular methods declared on an interface body. They
become JS_DEFINE_NATIVE_FUNCTION callbacks that unwrap the JS receiver,
coerce each parameter from JS values to C++, call into the impl, and
wrap the return value back to JS.

Currently supports the simple no-arg, no-Promise, no-CEReactions case
covering operations like `undefined addSearchProvider();` (HTML/External.idl).
Parameter coercion, Promise return wrapping, and CEReactions handling
arrive on later concept-ladder rungs.
"""

from __future__ import annotations

from ..ast import Interface
from ..ast import Operation
from .source_generator import SourceGenerator


def generate_function(
    function: Operation,
    interface: Interface,
    class_name: str,
    *,
    static: bool,
    generator: SourceGenerator,
) -> None:
    has_ce = "CEReactions" in function.extended_attributes
    if has_ce and static:
        raise NotImplementedError("[CEReactions] static operations not yet supported")

    from .prototype import _make_input_acceptable_cpp
    from .prototype import _to_snakecase

    g = generator.fork()
    g.set("class_name", class_name)
    g.set("interface_fully_qualified_name", interface.fully_qualified_name)
    g.set("function.name", function.name)
    snake = _make_input_acceptable_cpp(_to_snakecase(function.name))
    g.set("function.name:snakecase", snake)
    g.set(
        "overload_suffix",
        str(getattr(function, "overload_index", 0)) if getattr(function, "is_overloaded", False) else "",
    )

    cpp_name = function.extended_attributes.get("ImplementedAs") or snake
    g.set("function.cpp_name", cpp_name)
    g.set("interface_fully_qualified_name", interface.fully_qualified_name)

    # IDLGenerators.cpp:2402-2407 — opener.
    g.append(
        "\n"
        "JS_DEFINE_NATIVE_FUNCTION(@class_name@::@function.name:snakecase@@overload_suffix@)\n"
        "{\n"
        '    WebIDL::log_trace(vm, "@class_name@::@function.name:snakecase@@overload_suffix@");\n'
        "    [[maybe_unused]] auto& realm = *vm.current_realm();\n"
    )

    is_promise = function.return_type is not None and function.return_type.name == "Promise"
    if is_promise:
        g.append(
            "\n"
            "    auto steps = [&realm, &vm]() -> JS::ThrowCompletionOr<GC::Ref<WebIDL::Promise>> {\n"
            "        (void)realm;\n"
        )

    # impl_from for non-static (IDLGenerators.cpp:2417-2421).
    if not static:
        g.append("\n    auto* impl = TRY(impl_from(vm));\n")

    # IDLGenerators.cpp:2424-2425 — argument count check.
    if not getattr(function, "is_overloaded", False):
        _generate_argument_count_check(function, generator)

    # IDLGenerators.cpp:2427-2429 — coerce each argument.
    from .to_cpp import generate_arguments

    args = generate_arguments(function.parameters, interface, g)

    if static:
        # IDLGenerators.cpp:2466-2473 — first arg is vm.
        full_args = "vm" if not args else f"vm, {args}"
        g.set(".arguments", full_args)
        g.append(
            "\n"
            "    [[maybe_unused]] auto retval = TRY(throw_dom_exception_if_needed(vm, [&] { return @interface_fully_qualified_name@::@function.cpp_name@(@.arguments@); }));\n"
        )
    else:
        g.set(".arguments", args)
        if has_ce:
            g.append(
                "\n"
                "    auto& reactions_stack = HTML::relevant_similar_origin_window_agent(*impl).custom_element_reactions_stack;\n"
                "    reactions_stack.element_queue_stack.append({});\n"
                "\n"
                "    auto retval_or_exception = throw_dom_exception_if_needed(vm, [&] { return impl->@function.cpp_name@(@.arguments@); });\n"
                "\n"
                "    auto queue = reactions_stack.element_queue_stack.take_last();\n"
                "    Bindings::invoke_custom_element_reactions(queue);\n"
                "\n"
                "    if (retval_or_exception.is_error())\n"
                "        return retval_or_exception.release_error();\n"
                "\n"
                "    [[maybe_unused]] auto retval = retval_or_exception.release_value();\n"
            )
        else:
            g.append(
                "\n"
                "    [[maybe_unused]] auto retval = TRY(throw_dom_exception_if_needed(vm, [&] { return impl->@function.cpp_name@(@.arguments@); }));\n"
            )

    if is_promise:
        g.append(
            "\n"
            "        return retval;\n"
            "    };\n"
            "\n"
            "    auto maybe_retval = steps();\n"
            "\n"
            "    // And then, if an exception E was thrown:\n"
            "    // 1. If op has a return type that is a promise type, then return ! Call(%Promise.reject%, %Promise%, «E»).\n"
            "    // 2. Otherwise, end these steps and allow the exception to propagate.\n"
            "    // NOTE: We know that this is a Promise return type statically by the IDL.\n"
            "    if (maybe_retval.is_throw_completion())\n"
            "        return WebIDL::create_rejected_promise(realm, maybe_retval.error_value())->promise();\n"
            "\n"
            "    auto retval = maybe_retval.release_value();\n"
        )

    # IDLGenerators.cpp:2496 — return statement.
    from .types import generate_wrap_statement

    if function.return_type is None:
        raise AssertionError("operation without return type")
    generate_wrap_statement(g, "retval", function.return_type, interface, "return")

    g.append("\n}\n")


def _generate_argument_count_check(function: Operation, generator: SourceGenerator) -> None:
    """Port of generate_argument_count_check (IDLGenerators.cpp:1899-1920)."""
    required = sum(1 for p in function.parameters if not p.optional and not p.variadic)
    if required == 0:
        return
    g = generator.fork()
    g.set("function.name", function.name)
    g.set("function.nargs", str(required))
    if required == 1:
        g.set(".bad_arg_count", "JS::ErrorType::BadArgCountOne")
        g.set(".arg_count_suffix", "")
    else:
        g.set(".bad_arg_count", "JS::ErrorType::BadArgCountMany")
        g.set(".arg_count_suffix", f', "{required}"')
    g.append(
        "\n"
        "    if (vm.argument_count() < @function.nargs@)\n"
        '        return vm.throw_completion<JS::TypeError>(@.bad_arg_count@, "@function.name@"@.arg_count_suffix@);\n'
    )
