"""Deterministic JSON serializer for the IR.

Turns a resolved `Interface` AST into a stable, sorted-key-free JSON
document. The same shape will be emitted by `Lagom::BindingsGenerator
--emit-ir-json` (see Phase 1 of the rewrite plan) so the two parsers
can be compared byte-for-byte.

Field order in the output mirrors the field order on the dataclasses
in ast.py — insertion-ordered, matching the C++ side's OrderedHashMap
iteration.

Empty collections and zero/None defaults are emitted explicitly. The
goal is a representation where any structural difference produces a
visible diff rather than being silently equivalent. (That said,
`extended_attributes` is emitted as a JSON object whose keys preserve
insertion order, since both sides use ordered maps.)
"""

from __future__ import annotations

import json

from dataclasses import fields
from dataclasses import is_dataclass
from typing import Any

from .ast import Attribute
from .ast import CallbackFunction
from .ast import Constant
from .ast import Constructor
from .ast import Dictionary
from .ast import DictionaryMember
from .ast import Enumeration
from .ast import Interface
from .ast import Operation
from .ast import Parameter
from .ast import Type
from .ast import Typedef

# Some dataclasses have post-parse fields that the resolver writes
# (overload_index, is_overloaded). Those are not declared on the
# @dataclass so they don't show up via `fields()`. Listed here so the
# serializer can pick them up if the resolver has run.
_POST_PARSE_FIELDS = {
    Operation: ("overload_index", "is_overloaded"),
    Constructor: ("overload_index", "is_overloaded"),
}


def to_dict(node: Any) -> Any:
    """Recursively convert an AST node (or scalar) to JSON-friendly types."""
    if node is None:
        return None
    if isinstance(node, (str, int, float, bool)):
        return node
    if isinstance(node, (list, tuple)):
        return [to_dict(x) for x in node]
    if isinstance(node, dict):
        # Preserve insertion order; do not sort.
        return {k: to_dict(v) for k, v in node.items()}
    if is_dataclass(node):
        out: dict[str, Any] = {}
        # Tag with the AST kind so the JSON is self-describing and easy to
        # diff. Using the simple class name keeps the output compact.
        out["_kind"] = type(node).__name__
        for f in fields(node):
            out[f.name] = to_dict(getattr(node, f.name))
        for extra in _POST_PARSE_FIELDS.get(type(node), ()):
            if hasattr(node, extra):
                out[extra] = to_dict(getattr(node, extra))
        return out
    raise TypeError(f"don't know how to serialize {type(node).__name__}: {node!r}")


def dumps(interface: Interface, *, indent: int | None = 2) -> str:
    """Serialize an Interface to JSON text."""
    return json.dumps(to_dict(interface), indent=indent, ensure_ascii=False)


__all__ = ["to_dict", "dumps"]


# Re-export the AST node names so callers don't need to import them
# separately when they only want to round-trip JSON.
_AST_NODES = (
    Type,
    Typedef,
    Enumeration,
    Parameter,
    Operation,
    Constant,
    Attribute,
    Constructor,
    CallbackFunction,
    DictionaryMember,
    Dictionary,
    Interface,
)
