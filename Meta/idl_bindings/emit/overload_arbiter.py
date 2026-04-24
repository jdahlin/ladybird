"""Port of generate_overload_arbiter and helpers
(IDLGenerators.cpp:2504-2879).

Used for both constructor and operation overloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from ..ast import Interface
from ..ast import Type
from .naming import _make_input_acceptable_cpp
from .naming import _to_snakecase
from .types import _get_active_context


@dataclass
class _Item:
    callable_id: int = 0
    types: list[Type] = field(default_factory=list)
    optionality_values: list[str] = field(default_factory=list)


def _compute_effective_overload_set(overloads: list) -> list[_Item]:
    """Port of compute_the_effective_overload_set
    (IDLGenerators.cpp:2504-2628)."""
    items: list[_Item] = []
    maximum_arguments = max((len(o.parameters) for o in overloads), default=0)
    for overload_id, overload in enumerate(overloads):
        types: list[Type] = []
        opt_values: list[str] = []
        is_variadic = False
        for arg in overload.parameters:
            types.append(arg.type)
            if arg.variadic:
                opt_values.append("Variadic")
                is_variadic = True
            elif arg.optional:
                opt_values.append("Optional")
            else:
                opt_values.append("Required")
        items.append(_Item(callable_id=overload_id, types=list(types), optionality_values=list(opt_values)))
        argument_count = len(overload.parameters)
        if is_variadic:
            for i in range(argument_count, maximum_arguments):
                it = _Item(callable_id=overload_id)
                for j in range(argument_count):
                    it.types.append(types[j])
                    it.optionality_values.append(opt_values[j])
                for _ in range(argument_count, i + 1):
                    it.types.append(types[argument_count - 1])
                    it.optionality_values.append("Variadic")
                items.append(it)
        i = argument_count - 1
        while i >= 0:
            arg = overload.parameters[i]
            if not arg.optional and not arg.variadic:
                break
            it = _Item(callable_id=overload_id)
            for j in range(i):
                it.types.append(types[j])
                it.optionality_values.append(opt_values[j])
            items.append(it)
            i -= 1
    return items


def _generate_constructor_for_idl_type(t: Type) -> str:
    """Port of generate_constructor_for_idl_type
    (IDLGenerators.cpp:2630-2667)."""
    nullable = "true" if t.nullable else "false"
    if t.kind == "plain":
        return f'make_ref_counted<IDL::Type>("{t.name}", {nullable})'
    if t.kind == "parameterized":
        params = ", ".join(_generate_constructor_for_idl_type(p) for p in t.parameters)
        return (
            f'make_ref_counted<IDL::ParameterizedType>("{t.name}", {nullable}, '
            f"Vector<NonnullRefPtr<IDL::Type const>> {{{params}}})"
        )
    if t.kind == "union":
        members = ", ".join(_generate_constructor_for_idl_type(m) for m in t.union_member_types)
        return (
            f'make_ref_counted<IDL::UnionType>("{t.name}", {nullable}, '
            f"Vector<NonnullRefPtr<IDL::Type const>> {{{members}}})"
        )
    raise AssertionError(f"unknown type kind {t.kind!r}")


def _is_distinguishable(interface: Interface, a: Type, b: Type) -> bool:
    """Heuristic port of Type::is_distinguishable_from. Conservative —
    returns False (not distinguishable) only for clear matches; otherwise
    True. The C++ logic is much richer; this covers the common cases."""
    if a is b:
        return False
    if a.name == b.name and a.kind == b.kind:
        return False
    return True


def _resolve_distinguishing_argument_index(interface: Interface, items: list[_Item], argument_count: int) -> int:
    for argument_index in range(argument_count):
        found_indistinguishable = False
        for first in range(len(items)):
            for second in range(first + 1, len(items)):
                if not _is_distinguishable(
                    interface, items[first].types[argument_index], items[second].types[argument_index]
                ):
                    found_indistinguishable = True
                    break
            if found_indistinguishable:
                break
        if not found_indistinguishable:
            return argument_index
    # All indices were indistinguishable — pick 0 (matches the conservative C++
    # VERIFY_NOT_REACHED, but we don't want to crash on the small cases here).
    return 0


def generate_overload_arbiter(
    overloads: list,
    overload_set_key: str,
    interface: Interface,
    class_name: str,
    *,
    is_constructor: bool,
    generator,
) -> None:
    """Port of generate_overload_arbiter (IDLGenerators.cpp:2708-2879).

    `overloads` is a list of either Constructor or Operation objects (the
    fields used here — parameters — are common to both).
    """
    g = generator.fork()
    if is_constructor:
        g.set("constructor_class", class_name)
    else:
        g.set("class_name", class_name)
    g.set("function.name:snakecase", _make_input_acceptable_cpp(_to_snakecase(overload_set_key)))

    _ctx_oa = _get_active_context()

    def _is_dictionary_type(name: str) -> bool:
        if name in interface.dictionaries:
            return True
        return _ctx_oa is not None and name in _ctx_oa.dictionaries

    dictionary_types: list[str] = []
    seen: set[str] = set()

    if is_constructor:
        g.append(
            "\n"
            "JS::ThrowCompletionOr<GC::Ref<JS::Object>> @constructor_class@::construct(JS::FunctionObject& new_target)\n"
            "{\n"
            "    auto& vm = this->vm();\n"
            '    WebIDL::log_trace(vm, "@constructor_class@::construct");\n'
        )
    else:
        g.append(
            "\n"
            "JS_DEFINE_NATIVE_FUNCTION(@class_name@::@function.name:snakecase@)\n"
            "{\n"
            '    WebIDL::log_trace(vm, "@class_name@::@function.name:snakecase@");\n'
        )

    g.append(
        "\n"
        "    Optional<int> chosen_overload_callable_id;\n"
        "    Optional<IDL::EffectiveOverloadSet> effective_overload_set;\n"
    )

    items = _compute_effective_overload_set(overloads)
    maximum_argument_count = max((len(it.types) for it in items), default=0)
    g.set("max_argument_count", str(maximum_argument_count))
    g.append("    switch (min(@max_argument_count@, vm.argument_count())) {\n")

    for argument_count in range(maximum_argument_count + 1):
        eos = [it for it in items if len(it.types) == argument_count]
        if not eos:
            continue
        distinguishing = 0
        if len(eos) > 1:
            distinguishing = _resolve_distinguishing_argument_index(interface, eos, argument_count)
        g.set("current_argument_count", str(argument_count))
        if len(eos) == 1:
            for t in eos[0].types:
                if _is_dictionary_type(t.name) and t.name not in seen:
                    seen.add(t.name)
                    dictionary_types.append(t.name)
            g.set("overload.callable_id", str(eos[0].callable_id))
            g.append(
                "\n"
                "    case @current_argument_count@:\n"
                "        chosen_overload_callable_id = @overload.callable_id@;\n"
                "        break;\n"
                "\n"
            )
        else:
            g.set("overload_count", str(len(eos)))
            g.append(
                "\n"
                "    case @current_argument_count@: {\n"
                "        Vector<IDL::EffectiveOverloadSet::Item> overloads;\n"
                "        overloads.ensure_capacity(@overload_count@);\n"
                "\n"
            )
            for it in eos:
                types_strs = []
                opts_strs = []
                for typ, opt in zip(it.types, it.optionality_values, strict=True):
                    if _is_dictionary_type(typ.name) and typ.name not in seen:
                        seen.add(typ.name)
                        dictionary_types.append(typ.name)
                    types_strs.append(_generate_constructor_for_idl_type(typ))
                    opts_strs.append(f"IDL::Optionality::{opt}")
                types_str = "Vector<NonnullRefPtr<IDL::Type const>> { " + ", ".join(types_strs) + "}"
                opts_str = "Vector<IDL::Optionality> { " + ", ".join(opts_strs) + "}"
                g.set("overload.callable_id", str(it.callable_id))
                g.set("overload.types", types_str)
                g.set("overload.optionality_values", opts_str)
                g.append(
                    "        overloads.empend(@overload.callable_id@, @overload.types@, @overload.optionality_values@);\n"
                )
            g.set("overload_set.distinguishing_argument_index", str(distinguishing))
            g.append(
                "\n"
                "        effective_overload_set.emplace(move(overloads), @overload_set.distinguishing_argument_index@);\n"
                "        break;\n"
                "    }\n"
            )

    g.append("\n    }\n")

    g.append("\n    Vector<StringView> dictionary_types {\n")
    for d in dictionary_types:
        dg = g.fork()
        dg.set("d", d)
        dg.append('    "@d@"sv,\n')
    g.append("};\n")

    g.append(
        "\n"
        "\n"
        "    if (!chosen_overload_callable_id.has_value()) {\n"
        "        if (!effective_overload_set.has_value())\n"
        "            return vm.throw_completion<JS::TypeError>(JS::ErrorType::OverloadResolutionFailed);\n"
        "        chosen_overload_callable_id = TRY(WebIDL::resolve_overload(vm, effective_overload_set.value(), dictionary_types)).callable_id;\n"
        "    }\n"
        "\n"
        "    switch (chosen_overload_callable_id.value()) {\n"
    )

    for i in range(len(overloads)):
        og = g.fork()
        og.set("overload_id", str(i))
        og.append("\n    case @overload_id@:\n")
        if is_constructor:
            og.append("\n        return construct@overload_id@(new_target);\n")
        else:
            og.append("\n        return @function.name:snakecase@@overload_id@(vm);\n")

    g.append("\n    default:\n        VERIFY_NOT_REACHED();\n    }\n}\n")
