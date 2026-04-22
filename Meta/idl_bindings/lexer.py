"""Web IDL lexer — §2.2 Lexical Conventions.

https://webidl.spec.whatwg.org/#idl-grammar

The spec defines tokenization via these regular expressions (§2.2):

    integer     = -?([1-9][0-9]*|0[Xx][0-9A-Fa-f]+|0[0-7]*)
    decimal     = -?(([0-9]+\\.[0-9]*|[0-9]*\\.[0-9]+)([Ee][+-]?[0-9]+)?|[0-9]+[Ee][+-]?[0-9]+)
    identifier  = [_-]?[A-Za-z][0-9A-Z_a-z-]*
    string      = \"[^\"]*\"
    whitespace  = [\\t\\n\\r ]+
    comment     = //.*|/\\*(.|\\n)*?\\*/
    other       = [^\\t\\n\\r 0-9A-Za-z]

Punctuators is a closed set: ( ) [ ] { } , ; : ? = < > ...

Each Token records its kind, its source value, and its (line, column) origin
so error messages can match the C++ tool's quality. Whitespace and comments
are not emitted as tokens — they are consumed silently between meaningful
tokens — but the lexer still tracks their effect on line/column counters.

Ladybird-specific extension: a `#import "..."` directive is emitted as a
distinct token so the preprocessor pass can resolve imports before grammar
parsing proper begins. This is not part of the Web IDL spec; it mirrors
IDLParser.cpp:1270.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from enum import Enum
from enum import auto


class TokenKind(Enum):
    # §2.2 token classes
    INTEGER = auto()
    DECIMAL = auto()
    IDENTIFIER = auto()
    STRING = auto()
    OTHER = auto()  # single character not in any other class
    PUNCTUATOR = auto()  # multi-character punctuators like ... and ::
    EOF = auto()
    # Ladybird extension
    IMPORT_DIRECTIVE = auto()  # `#import <path>` or `#import "path"`


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    value: str
    line: int  # 1-based
    column: int  # 1-based


# Spec regexes from §2.2, transcribed verbatim.
_INTEGER_RE = re.compile(r"-?([1-9][0-9]*|0[Xx][0-9A-Fa-f]+|0[0-7]*)")
_DECIMAL_RE = re.compile(r"-?(([0-9]+\.[0-9]*|[0-9]*\.[0-9]+)([Ee][+-]?[0-9]+)?|[0-9]+[Ee][+-]?[0-9]+)")
_IDENTIFIER_RE = re.compile(r"[_-]?[A-Za-z][0-9A-Z_a-z-]*")
_STRING_RE = re.compile(r'"[^"]*"')
_WHITESPACE_RE = re.compile(r"[\t\n\r ]+")
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

# Multi-character punctuators must be tried before single-character OTHER.
# The Web IDL grammar uses these compound forms; longest-match wins.
_MULTI_PUNCTUATORS = ("...", "::")

# Ladybird extension: #import preprocessor directive.
# Mirrors IDLParser.cpp:1270 (`lexer.consume_specific("#import"sv)`). The form is
#   #import <Some/Path.idl>     or
#   #import "Some/Path.idl"
# (The C++ parser accepts the `<...>` form; both styles are tolerated here.)
_IMPORT_RE = re.compile(r"#import\s+([<\"])([^>\"]+)[>\"]")


class LexerError(Exception):
    def __init__(self, message: str, line: int, column: int):
        super().__init__(f"{line}:{column}: {message}")
        self.line = line
        self.column = column


class Lexer:
    """Streaming Web IDL tokenizer with line/column tracking.

    Tokens are produced lazily via next_token(); peek() looks ahead by one without
    consuming. Whitespace and comments are skipped between tokens but still
    update the (line, column) cursor.
    """

    def __init__(self, source: str, filename: str = "<input>"):
        self.source = source
        self.filename = filename
        self.pos = 0  # byte offset into source
        self.line = 1  # 1-based current line
        self.column = 1  # 1-based current column
        self._lookahead: Token | None = None

    # --- public API ---

    def peek(self) -> Token:
        if self._lookahead is None:
            self._lookahead = self._read_token()
        return self._lookahead

    def next_token(self) -> Token:
        if self._lookahead is not None:
            tok = self._lookahead
            self._lookahead = None
            return tok
        return self._read_token()

    def tokens(self):
        while True:
            tok = self.next_token()
            yield tok
            if tok.kind is TokenKind.EOF:
                return

    # --- internals ---

    def _advance(self, n: int) -> str:
        # Consume n characters, updating line/column counters.
        chunk = self.source[self.pos : self.pos + n]
        for ch in chunk:
            if ch == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
        self.pos += n
        return chunk

    def _skip_trivia(self) -> None:
        # Consume whitespace and comments. The spec does not emit either as tokens.
        while self.pos < len(self.source):
            m = _WHITESPACE_RE.match(self.source, self.pos)
            if m:
                self._advance(m.end() - m.start())
                continue
            m = _LINE_COMMENT_RE.match(self.source, self.pos)
            if m:
                self._advance(m.end() - m.start())
                continue
            m = _BLOCK_COMMENT_RE.match(self.source, self.pos)
            if m:
                self._advance(m.end() - m.start())
                continue
            return

    def _read_token(self) -> Token:
        self._skip_trivia()
        if self.pos >= len(self.source):
            return Token(TokenKind.EOF, "", self.line, self.column)

        line, column = self.line, self.column
        rest = self.source

        # Ladybird extension: #import directive must be tried before any other token
        # because `#` is not a Web IDL punctuator and would otherwise be OTHER.
        m = _IMPORT_RE.match(rest, self.pos)
        if m:
            path = m.group(2)
            self._advance(m.end() - m.start())
            return Token(TokenKind.IMPORT_DIRECTIVE, path, line, column)

        # decimal must be tried before integer (longest-match: `1.5` is a decimal,
        # not an integer followed by `.5`).
        m = _DECIMAL_RE.match(rest, self.pos)
        if m:
            value = m.group(0)
            self._advance(len(value))
            return Token(TokenKind.DECIMAL, value, line, column)

        m = _INTEGER_RE.match(rest, self.pos)
        if m:
            value = m.group(0)
            self._advance(len(value))
            return Token(TokenKind.INTEGER, value, line, column)

        m = _IDENTIFIER_RE.match(rest, self.pos)
        if m:
            value = m.group(0)
            self._advance(len(value))
            return Token(TokenKind.IDENTIFIER, value, line, column)

        m = _STRING_RE.match(rest, self.pos)
        if m:
            # Strip the surrounding quotes; preserve the inner contents verbatim.
            value = m.group(0)[1:-1]
            self._advance(m.end() - m.start())
            return Token(TokenKind.STRING, value, line, column)

        # Multi-character punctuators (`...`, `::`) — longest match.
        for punct in _MULTI_PUNCTUATORS:
            if rest.startswith(punct, self.pos):
                self._advance(len(punct))
                return Token(TokenKind.PUNCTUATOR, punct, line, column)

        # Otherwise: a single non-alphanumeric character ("other" per §2.2).
        ch = rest[self.pos]
        self._advance(1)
        return Token(TokenKind.OTHER, ch, line, column)
