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
        # Walk the pattern character by character. The C++ side uses
        # GenericLexer::consume_until, which we mirror with a manual cursor.
        i = 0
        n = len(pattern)
        out = self._builder.append
        while i < n:
            ch = pattern[i]
            if ch == self._escape:
                # `\@` → literal `@`; `\\` → literal `\`. Any other escape
                # is an error per AK::SourceGenerator.
                if i + 1 >= n:
                    raise ValueError("Unexpected EOF while parsing escape sequence")
                next_ch = pattern[i + 1]
                if next_ch != self._opening and next_ch != self._escape:
                    raise ValueError(f"Invalid escape sequence '{self._escape}{next_ch}' on SourceGenerator")
                out(next_ch)
                i += 2
                continue
            if ch == self._opening:
                # Find the closing delimiter.
                close_idx = pattern.find(self._closing, i + 1)
                if close_idx < 0:
                    raise ValueError(f"Unterminated placeholder starting at offset {i} in pattern")
                placeholder = pattern[i + 1 : close_idx]
                out(self.get(placeholder))
                i = close_idx + 1
                continue
            # Plain character — fast-path to the next special character.
            j = i
            while j < n and pattern[j] != self._opening and pattern[j] != self._escape:
                j += 1
            out(pattern[i:j])
            i = j

    def appendln(self, pattern: str) -> None:
        self.append(pattern)
        self._builder.append("\n")
