"""Naming helpers shared across emit modules.

Extracted to break circular import cycles between prototype.py, types.py,
to_cpp.py, operations.py, and overload_arbiter.py.
"""

from __future__ import annotations

import functools

# C++ keywords (and a few Serenity/LibJS names) that collide with IDL
# identifiers.  Mirrors _CPP_KEYWORD_RESERVED from prototype.py.
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
    """Mirror make_input_acceptable_cpp from IDLGenerators.cpp.

    Appends `_` to C++ keywords and replaces `-` with `_`.
    """
    if s in _CPP_KEYWORD_RESERVED:
        return s + "_"
    return s.replace("-", "_")


@functools.lru_cache(maxsize=None)
def _to_snakecase(s: str) -> str:
    """Mirror AK::String::to_snakecase.

    Inserts `_` before each uppercase letter (except at the start), then
    lowercases. Adjacent uppercases stay together until the *last* one in
    a run, so "URLSearchParams" → "url_search_params" and "HTMLElement"
    → "html_element".
    """
    # Mirror AK::StringUtils::to_snakecase (StringUtils.cpp:306-330) exactly.
    if not s:
        return s
    out: list[str] = []
    for i, ch in enumerate(s):
        insert = False
        if i > 0:
            prev = s[i - 1]
            if prev.isascii() and prev.islower() and prev.isalpha() and ch.isascii() and ch.isupper() and ch.isalpha():
                insert = True
            elif i < len(s) - 1:
                nxt = s[i + 1]
                if ch.isascii() and ch.isupper() and ch.isalpha() and nxt.isascii() and nxt.islower() and nxt.isalpha():
                    insert = True
        if insert:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)
