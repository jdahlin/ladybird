"""Python port of AK/SourceGenerator.h.

SourceGenerator is a string-template helper used by every emitter function
in IDLGenerators.cpp. The API surface here mirrors the C++ class line-for-line
so emitter functions transliterate from C++ → Python with minimal change:

    generator.set("name", "Foo")
    generator.append("class @name@ : public Bar {")

The output buffer is shared across forks — `fork()` clones the placeholder
mapping but keeps writing to the same underlying StringBuilder. This is what
lets a function set a new key on a fork without affecting its caller.

Behavior follows AK/SourceGenerator.h exactly:
- Default delimiters are `@` (open + close); escape with `\\@`.
- A missing key is a hard error (mirrors the C++ `VERIFY_NOT_REACHED`).
- A literal `@` in output is written via the escape sequence `\\@`.

There is no equivalent of the C++ String/ByteString overloads — Python `str`
is the only string type used.
"""

from __future__ import annotations

import functools
import re

# Compiled regex for SourceGenerator template substitution.
# Matches: \@ or \\ (escape), @key@ (placeholder). Plain text is consumed via
# slicing between matches — much faster than the character-by-character loop.
_TMPL_RE = re.compile(r"\\([@\\])|@([^@]*)@")


@functools.lru_cache(maxsize=4096)
def _compile_pattern(pattern: str) -> tuple:
    """Parse a template pattern into a tuple of (is_placeholder, content) pairs.

    Cached per unique pattern string — emitter code reuses the same string
    literals on every interface, so the regex runs once per distinct template
    rather than once per interface.

    Returns a tuple of (bool, str) pairs:
      (False, text)        — emit text verbatim
      (True, placeholder)  — look up placeholder in the mapping
    """
    if "@" not in pattern and "\\" not in pattern:
        return ((False, pattern),)
    parts: list[tuple[bool, str]] = []
    pos = 0
    for m in _TMPL_RE.finditer(pattern):
        start = m.start()
        if start > pos:
            parts.append((False, pattern[pos:start]))
        escape, placeholder = m.groups()
        if escape is not None:
            parts.append((False, escape))
        else:
            parts.append((True, placeholder))
        pos = m.end()
    if pos < len(pattern):
        parts.append((False, pattern[pos:]))
    return tuple(parts)


class StringBuilder:
    """Thin wrapper over a list-of-strings sink.

    Mirrors the slice of AK::StringBuilder that SourceGenerator uses:
    `append(str)` and `string_view()` (read-out as `to_string()`).
    """

    def __init__(self) -> None:
        self._parts: list[str] = []

    def append(self, s: str) -> None:
        self._parts.append(s)

    def to_string(self) -> str:
        return "".join(self._parts)

    def __len__(self) -> int:
        return sum(len(p) for p in self._parts)


class SourceGenerator:
    """`@key@` template engine matching AK/SourceGenerator.h.

    Construct one with a shared StringBuilder. `fork()` returns a new
    SourceGenerator with a copied placeholder mapping but the same builder
    underneath, so child generators can override keys without leaking back
    to the parent.

    Default delimiters are `@` (open + close); escape with `\\@`.
    """

    def __init__(
        self,
        builder: StringBuilder,
        mapping: dict[str, str] | None = None,
        opening: str = "@",
        closing: str = "@",
        escape: str = "\\",
    ) -> None:
        self._builder = builder
        self._mapping: dict[str, str] = dict(mapping) if mapping else {}
        self._opening = opening
        self._closing = closing
        self._escape = escape

    def fork(self) -> "SourceGenerator":
        return SourceGenerator(self._builder, dict(self._mapping), self._opening, self._closing, self._escape)

    def set(self, key: str, value: str) -> None:
        if self._opening in key or self._closing in key:
            raise ValueError(
                f"SourceGenerator keys cannot contain the opening/closing delimiters "
                f"'{self._opening}' and '{self._closing}' (got {key!r})."
            )
        self._mapping[key] = value

    def get(self, key: str) -> str:
        if key not in self._mapping:
            raise KeyError(f"No key named {key!r} set on SourceGenerator")
        return self._mapping[key]

    def as_string(self) -> str:
        return self._builder.to_string()

    def append(self, pattern: str) -> None:
        # _compile_pattern is LRU-cached: the regex runs once per distinct
        # template string across all interfaces, not once per call.
        out = self._builder.append
        mapping = self._mapping
        for is_placeholder, content in _compile_pattern(pattern):
            if is_placeholder:
                try:
                    out(mapping[content])
                except KeyError:
                    raise KeyError(f"No key named {content!r} set on SourceGenerator") from None
            else:
                out(content)

    def appendln(self, pattern: str) -> None:
        self.append(pattern)
        self._builder.append("\n")
