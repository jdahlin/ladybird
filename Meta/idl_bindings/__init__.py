# Web IDL bindings generator (Python port).
#
# Implements the Web IDL grammar from https://webidl.spec.whatwg.org/ — production
# names, terminal names, and algorithm step structure follow the spec text.
# Ladybird-specific extensions (#import, [FIXME], [ImplementedAs=...], etc.) are
# called out individually in their parser methods.
#
# Three modules:
#   lexer.py  — §2.2 Lexical Conventions tokenizer
#   ast.py    — dataclasses corresponding to grammar productions in §2.1
#   parser.py — recursive descent parser, one method per top-level production
