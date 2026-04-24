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


# Single combined tokenizer regex — matches every token kind and trivia in one
# pass over the source. Alternative groups (named) are tried left-to-right so
# longer patterns must precede shorter ones (decimal before integer, ... before
# single char). Trivia (whitespace, comments) is captured separately and
# filtered out in _tokenize so the caller never sees them.
#
# Named groups used to identify the match kind without an if-chain:
#   trivia      — whitespace or comments (discarded)
#   import_path — path from a #import directive (IMPORT_DIRECTIVE)
#   decimal     — floating-point literal (DECIMAL)
#   integer     — integer literal (INTEGER)
#   ident       — identifier (IDENTIFIER)
#   string      — quoted string, captures interior only (STRING)
#   multi_punct — ... or :: (PUNCTUATOR)
#   other       — any other single character (OTHER / single-char punctuator)
_TOKEN_RE = re.compile(
    r"(?P<trivia>[\t\n\r ]+|//[^\n]*|/\*.*?\*/)"
    r"|#import\s+[<\"](?P<import_path>[^>\"]+)[>\"]"
    r"|(?P<decimal>-?(?:(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[Ee][+-]?[0-9]+)?|[0-9]+[Ee][+-]?[0-9]+))"
    r"|(?P<integer>-?(?:[1-9][0-9]*|0[Xx][0-9A-Fa-f]+|0[0-7]*))"
    r"|(?P<ident>[_\-]?[A-Za-z][0-9A-Z_a-z\-]*)"
    r'|"(?P<string>[^"]*)"'
    r"|(?P<multi_punct>\.\.\.|\:\:)"
    r"|(?P<other>.)",
    re.DOTALL,
)

# Map named group → TokenKind (for non-trivia groups).
_GROUP_TO_KIND: dict[str, TokenKind] = {
    "import_path": TokenKind.IMPORT_DIRECTIVE,
    "decimal": TokenKind.DECIMAL,
    "integer": TokenKind.INTEGER,
    "ident": TokenKind.IDENTIFIER,
    "string": TokenKind.STRING,
    "multi_punct": TokenKind.PUNCTUATOR,
    "other": TokenKind.OTHER,
}


class LexerError(Exception):
    def __init__(self, message: str, line: int, column: int):
        super().__init__(f"{line}:{column}: {message}")
        self.line = line
        self.column = column


def _tokenize(source: str) -> list[Token]:
    """Tokenize the entire source at once using a single combined regex.

    Trivia (whitespace, comments) updates the line/column cursor but is not
    added to the output token list. The final token is always EOF.
    """
    tokens: list[Token] = []
    line = 1
    col = 1

    for m in _TOKEN_RE.finditer(source):
        last_group = m.lastgroup
        if last_group == "trivia":
            # Update line/column and discard.
            chunk = m.group(0)
            newlines = chunk.count("\n")
            if newlines:
                line += newlines
                col = len(chunk) - chunk.rfind("\n")
            else:
                col += len(chunk)
            continue

        kind = _GROUP_TO_KIND[last_group]
        value = m.group(last_group)
        tokens.append(Token(kind, value, line, col))
        # Advance column (tokens never span multiple lines in Web IDL).
        col += len(m.group(0))

    tokens.append(Token(TokenKind.EOF, "", line, col))
    return tokens


class Lexer:
    """Web IDL tokenizer.

    Tokenizes the entire source on construction (single regex pass), then
    exposes a streaming peek/next_token interface for the parser.
    """

    def __init__(self, source: str, filename: str = "<input>"):
        self.source = source
        self.filename = filename
        self._tokens = _tokenize(source)
        self._pos = 0

    # --- public API ---

    def peek(self) -> Token:
        return self._tokens[self._pos]

    def next_token(self) -> Token:
        tok = self._tokens[self._pos]
        if tok.kind is not TokenKind.EOF:
            self._pos += 1
        return tok

    def tokens(self):
        while True:
            tok = self.next_token()
            yield tok
            if tok.kind is TokenKind.EOF:
                return
