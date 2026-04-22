"""Web IDL parser — recursive descent over the grammar in §2.1.

https://webidl.spec.whatwg.org/#idl-grammar

Each top-level method corresponds to one grammar production. The production
text is quoted above the method, with its spec link, and the method body
follows the spec's algorithm structure step by step.

Where Ladybird's behavior diverges from or extends the spec, the divergence
is named explicitly with a reference to the corresponding location in
Libraries/LibIDL/IDLParser.cpp.

The parse() entry point follows the C++ parser's flow at IDLParser.cpp:1255-1490:
  1. Consume any leading #import directives (Ladybird extension).
  2. parse_non_interface_entities(allow_interface=True) — typedefs, enums,
     dictionaries, callbacks, mixins, partials, includes statements, until
     a top-level `interface` or `namespace` keyword.
  3. If `interface` or `namespace` follows: parse the primary definition.
  4. parse_non_interface_entities(allow_interface=False) — more typedefs etc.

Cross-file resolution (typedef substitution into operation parameters,
mixin include flattening, overload set numbering) is *not* performed here.
Those are post-parse computations done by the C++ tool after parsing; the
parity test compares pre-resolution output of both sides so the parser
remains a pure (idl source -> AST) function.
"""

from __future__ import annotations

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
from .lexer import Lexer
from .lexer import Token
from .lexer import TokenKind


class ParseError(Exception):
    def __init__(self, message: str, token: Token, filename: str):
        super().__init__(f"{filename}:{token.line}:{token.column}: {message}")
        self.token = token
        self.filename = filename


# A handful of identifiers from the spec are *contextual* keywords — they are
# only keywords in certain grammar positions and otherwise valid identifiers.
def _is_keyword(tok: Token, keyword: str) -> bool:
    return tok.kind is TokenKind.IDENTIFIER and tok.value == keyword


def _is_punct(tok: Token, value: str) -> bool:
    return tok.kind in (TokenKind.OTHER, TokenKind.PUNCTUATOR) and tok.value == value


class Parser:
    """Recursive descent parser producing an `Interface` AST."""

    def __init__(self, source: str, filename: str = "<input>"):
        self.filename = filename
        self.lexer = Lexer(source, filename)
        self.interface = Interface(filename=filename)

    # --- token helpers ---

    def _peek(self) -> Token:
        return self.lexer.peek()

    def _advance(self) -> Token:
        return self.lexer.next_token()

    def _expect_keyword(self, keyword: str) -> Token:
        tok = self._advance()
        if not _is_keyword(tok, keyword):
            raise ParseError(f"expected keyword '{keyword}', got {tok.value!r}", tok, self.filename)
        return tok

    def _consume_keyword(self, keyword: str) -> bool:
        if _is_keyword(self._peek(), keyword):
            self._advance()
            return True
        return False

    def _expect_punct(self, punct: str) -> Token:
        tok = self._advance()
        if not _is_punct(tok, punct):
            raise ParseError(f"expected '{punct}', got {tok.value!r}", tok, self.filename)
        return tok

    def _consume_punct(self, punct: str) -> bool:
        if _is_punct(self._peek(), punct):
            self._advance()
            return True
        return False

    def _expect_identifier(self) -> Token:
        tok = self._advance()
        if tok.kind is not TokenKind.IDENTIFIER:
            raise ParseError(f"expected identifier, got {tok.value!r}", tok, self.filename)
        # Mirror IDLParser.cpp:142 — trim leading underscores. WebIDL allows
        # `_keyword` (e.g. `_callback`, `_default`) to escape contextual
        # keywords without changing the actual identifier name.
        if tok.value.startswith("_"):
            tok = Token(tok.kind, tok.value.lstrip("_"), tok.line, tok.column)
        return tok

    # --- entry point ---

    # https://webidl.spec.whatwg.org/#prod-Definitions
    # Mirrors IDLParser.cpp:1255-1490 (Parser::parse).
    def parse(self) -> Interface:
        # 1. Ladybird extension: consume `#import <path>` directives at file head.
        while self._peek().kind is TokenKind.IMPORT_DIRECTIVE:
            tok = self._advance()
            self.interface.imported_modules.append(tok.value)

        # 2. Non-interface entities (typedefs, enums, dictionaries, callbacks,
        #    mixins, partials, includes statements). Stops on `interface` /
        #    `namespace` so the primary definition can be parsed in step 3.
        self._parse_non_interface_entities(allow_interface=True)

        # 3. Optional primary `interface` or `namespace` declaration.
        tok = self._peek()
        if _is_keyword(tok, "interface"):
            self._advance()
            self._parse_interface(self.interface, extended_attributes=self.interface.extended_attributes)
            self.interface.extended_attributes = self.interface.extended_attributes
        elif _is_keyword(tok, "namespace"):
            self._advance()
            self._parse_namespace(self.interface)

        # 4. More non-interface entities can follow the primary declaration.
        self._parse_non_interface_entities(allow_interface=False)

        return self.interface

    # https://webidl.spec.whatwg.org/#prod-Definitions
    # Definition ::
    #     CallbackOrInterfaceOrMixin | Namespace | Partial | Dictionary
    #     | Enum | Typedef | IncludesStatement
    #
    # Mirrors IDLParser.cpp:1142-1189 (parse_non_interface_entities).
    def _parse_non_interface_entities(self, allow_interface: bool) -> None:
        while True:
            tok = self._peek()
            if tok.kind is TokenKind.EOF:
                return
            if tok.kind is TokenKind.IMPORT_DIRECTIVE:
                # An #import after the file head is unusual but allowed; skip it.
                self._advance()
                self.interface.imported_modules.append(tok.value)
                continue

            extended_attributes = self._parse_extended_attribute_list()

            tok = self._peek()
            if _is_keyword(tok, "dictionary"):
                self._parse_dictionary(extended_attributes, partial=False)
                continue
            if _is_keyword(tok, "partial"):
                # partial dictionary | partial interface | partial interface mixin | partial namespace
                self._advance()
                next_tok = self._peek()
                if _is_keyword(next_tok, "dictionary"):
                    self._parse_dictionary(extended_attributes, partial=True)
                elif _is_keyword(next_tok, "interface"):
                    self._advance()
                    if _is_keyword(self._peek(), "mixin"):
                        self._advance()
                        self._parse_partial_interface_mixin(extended_attributes)
                    else:
                        self._parse_partial_interface(extended_attributes)
                elif _is_keyword(next_tok, "namespace"):
                    self._advance()
                    self._parse_partial_namespace(extended_attributes)
                else:
                    raise ParseError(
                        f"expected 'dictionary', 'interface' or 'namespace' after 'partial', got {next_tok.value!r}",
                        next_tok,
                        self.filename,
                    )
                continue
            if _is_keyword(tok, "enum"):
                self._parse_enum(extended_attributes)
                continue
            if _is_keyword(tok, "typedef"):
                self._parse_typedef(extended_attributes)
                continue
            if _is_keyword(tok, "callback"):
                # callback | callback interface
                self._advance()
                if _is_keyword(self._peek(), "interface"):
                    self._advance()
                    new_iface = Interface(filename=self.filename)
                    new_iface.is_callback_interface = True
                    new_iface.extended_attributes = extended_attributes
                    self._parse_interface(new_iface, extended_attributes=extended_attributes)
                    # Callback interfaces are stored as the primary interface
                    # in the C++ side. Mirror that by promoting if there's no
                    # primary yet; otherwise stash on .mixins for now.
                    if not self.interface.has_primary_interface:
                        self.interface.name = new_iface.name
                        self.interface.is_callback_interface = True
                        self.interface.extended_attributes = new_iface.extended_attributes
                        self.interface.attributes = new_iface.attributes
                        self.interface.constants = new_iface.constants
                        self.interface.operations = new_iface.operations
                        self.interface.static_operations = new_iface.static_operations
                    else:
                        self.interface.mixins[new_iface.name] = new_iface
                else:
                    self._parse_callback_function(extended_attributes)
                continue
            if _is_keyword(tok, "interface"):
                self._advance()
                if _is_keyword(self._peek(), "mixin"):
                    self._advance()
                    self._parse_interface_mixin(extended_attributes)
                    continue
                if allow_interface and not self.interface.has_primary_interface:
                    # The primary `interface` definition belongs to step 3 of parse(),
                    # so put the keyword back conceptually and let the caller handle it.
                    # Easiest: parse it here and mark primary.
                    self._parse_interface(self.interface, extended_attributes=extended_attributes)
                    continue
                # Secondary interface declaration in a single file. Treat as
                # additional Interface stored under `.mixins` so we don't lose it.
                # In practice Ladybird's IDL files have at most one primary.
                new_iface = Interface(filename=self.filename)
                self._parse_interface(new_iface, extended_attributes=extended_attributes)
                self.interface.mixins[new_iface.name] = new_iface
                continue
            if _is_keyword(tok, "namespace") and allow_interface and not self.interface.has_primary_interface:
                self._advance()
                self.interface.extended_attributes = extended_attributes
                self._parse_namespace(self.interface)
                continue

            # IncludesStatement :: identifier includes identifier ;
            # https://webidl.spec.whatwg.org/#prod-IncludesStatement
            # The disambiguator vs. the primary interface: the C++ parser
            # checks for `includes` after the first identifier.
            if tok.kind is TokenKind.IDENTIFIER:
                # Speculatively treat as `Foo includes Bar;`.
                first = self._advance()
                if _is_keyword(self._peek(), "includes"):
                    self._advance()
                    mixin_name = self._expect_identifier().value
                    self._expect_punct(";")
                    self.interface.includes_statements.append((first.value, mixin_name))
                    continue
                raise ParseError(
                    f"unexpected identifier {first.value!r} at top level — expected 'includes', a definition keyword, or end of file",
                    first,
                    self.filename,
                )

            # Otherwise: the caller is expected to stop here (e.g. parse() falls
            # through to step 3 where it dispatches on `interface`/`namespace`).
            return

    # ExtendedAttributeList ::
    #     [ ExtendedAttribute ExtendedAttributes ]
    #     ε
    # https://webidl.spec.whatwg.org/#prod-ExtendedAttributeList
    #
    # Ladybird's C++ parser (IDLParser.cpp:160-188) accepts a simpler superset
    # of the spec's five concrete forms: split on commas, each entry is name
    # or `name=value` where `value` is the literal token sequence up to the
    # next comma or close bracket (parens balanced).
    def _parse_extended_attribute_list(self) -> dict[str, str]:
        if not self._consume_punct("["):
            return {}

        attributes: dict[str, str] = {}
        while True:
            if self._consume_punct("]"):
                return attributes

            name_tok = self._expect_identifier()
            name = name_tok.value
            value = ""

            if self._consume_punct("="):
                # Read value tokens until ',' or ']' at paren depth 0.
                paren_depth = 0
                pieces: list[str] = []
                while True:
                    tok = self._peek()
                    if tok.kind is TokenKind.EOF:
                        raise ParseError("unterminated extended attribute list", tok, self.filename)
                    if _is_punct(tok, "("):
                        paren_depth += 1
                    elif _is_punct(tok, ")"):
                        paren_depth -= 1
                    elif paren_depth == 0 and (_is_punct(tok, ",") or _is_punct(tok, "]")):
                        break
                    pieces.append(tok.value)
                    self._advance()
                value = "".join(pieces)

            attributes[name] = value
            if self._consume_punct(","):
                continue
            self._expect_punct("]")
            return attributes

    # Typedef :: typedef TypeWithExtendedAttributes identifier ;
    # https://webidl.spec.whatwg.org/#prod-Typedef
    #
    # Mirrors IDLParser.cpp:976-993 (Parser::parse_typedef).
    def _parse_typedef(self, extended_attributes: dict[str, str]) -> None:
        self._expect_keyword("typedef")

        # TypeWithExtendedAttributes :: ExtendedAttributeList Type
        # The leading [...] (if any) belongs to the *type*. The C++ parser
        # merges it with the typedef's own extended attributes (982-984).
        type_attrs = self._parse_extended_attribute_list()
        for k, v in type_attrs.items():
            extended_attributes.setdefault(k, v)

        type_ = self._parse_type()
        name = self._expect_identifier().value
        self._expect_punct(";")

        self.interface.typedefs[name] = Typedef(extended_attributes=extended_attributes, type=type_)

    # Enum :: enum identifier { EnumValueList } ;
    # https://webidl.spec.whatwg.org/#prod-Enum
    #
    # Mirrors IDLParser.cpp:929-974 (Parser::parse_enumeration).
    def _parse_enum(self, extended_attributes: dict[str, str]) -> None:
        self._expect_keyword("enum")
        name = self._expect_identifier().value
        self._expect_punct("{")

        enumeration = Enumeration(extended_attributes=extended_attributes)
        names_already_seen: set[str] = set()

        while True:
            tok = self._peek()
            if _is_punct(tok, "}"):
                break
            if tok.kind is not TokenKind.STRING:
                raise ParseError(f"expected a string enum value, got {tok.value!r}", tok, self.filename)
            self._advance()
            value = tok.value

            if value in enumeration.values:
                raise ParseError(
                    f"Enumeration {name} contains duplicate member '{value}'",
                    tok,
                    self.filename,
                )
            enumeration.values.append(value)
            if not enumeration.first_member:
                enumeration.first_member = value

            if self._consume_punct(","):
                continue
            if not _is_punct(self._peek(), "}"):
                raise ParseError(
                    f"expected ',' or '}}' after enum value, got {self._peek().value!r}",
                    self._peek(),
                    self.filename,
                )

        self._expect_punct("}")
        self._expect_punct(";")

        for entry in enumeration.values:
            enumeration.translated_cpp_names[entry] = _translate_enumeration_value_to_cpp_member(
                entry, names_already_seen
            )

        self.interface.enumerations[name] = enumeration

    # Dictionary :: dictionary identifier Inheritance { DictionaryMembers } ;
    # https://webidl.spec.whatwg.org/#prod-Dictionary
    #
    # Mirrors IDLParser.cpp:995-1083 (Parser::parse_dictionary). The C++ side
    # sorts members lexicographically (1068-1074); we reproduce that.
    def _parse_dictionary(self, extended_attributes: dict[str, str], partial: bool) -> None:
        self._expect_keyword("dictionary")
        name = self._expect_identifier().value

        parent_name = ""
        if self._consume_punct(":"):
            parent_name = self._expect_identifier().value

        self._expect_punct("{")

        members: list[DictionaryMember] = []
        while not self._consume_punct("}"):
            member_attrs = self._parse_extended_attribute_list()

            required = False
            if self._consume_keyword("required"):
                required = True

            # `required [EnforceRange] unsigned long initial;` — Ladybird/Web IDL
            # tolerates an extended-attribute list between `required` and the type.
            # Mirrors IDLParser.cpp's parse_parameters argument-attrs handling.
            if _is_punct(self._peek(), "["):
                more = self._parse_extended_attribute_list()
                for k, v in more.items():
                    member_attrs.setdefault(k, v)

            type_ = self._parse_type()
            member_name = self._expect_identifier().value

            default_value: str | None = None
            if self._consume_punct("="):
                default_value = self._parse_default_value_source()

            self._expect_punct(";")
            members.append(
                DictionaryMember(
                    name=member_name,
                    type=type_,
                    required=required,
                    default_value=default_value,
                    extended_attributes=member_attrs,
                )
            )

        self._expect_punct(";")

        members.sort(key=lambda m: m.name)

        dictionary = Dictionary(
            extended_attributes=extended_attributes,
            parent_name=parent_name,
            members=members,
        )
        if partial:
            self.interface.partial_dictionaries.setdefault(name, []).append(dictionary)
        else:
            self.interface.dictionaries[name] = dictionary

    # CallbackRest :: identifier = Type ( ArgumentList ) ;
    # https://webidl.spec.whatwg.org/#prod-CallbackRest
    #
    # Mirrors IDLParser.cpp:1119-1140 (Parser::parse_callback_function).
    # Caller has already consumed the leading `callback` keyword.
    def _parse_callback_function(self, extended_attributes: dict[str, str]) -> None:
        name = self._expect_identifier().value
        self._expect_punct("=")
        return_type = self._parse_type()
        parameters = self._parse_parameter_list()
        self._expect_punct(";")

        self.interface.callback_functions[name] = CallbackFunction(
            return_type=return_type,
            parameters=parameters,
            extended_attributes=extended_attributes,
            is_legacy_treat_non_object_as_null="LegacyTreatNonObjectAsNull" in extended_attributes,
        )

    # InterfaceRest :: identifier Inheritance { InterfaceMembers } ;
    # https://webidl.spec.whatwg.org/#prod-InterfaceRest
    #
    # Mirrors IDLParser.cpp:733-858 (Parser::parse_interface). Caller has
    # already consumed the `interface` keyword (and `callback`/`mixin` for
    # the variants).
    def _parse_interface(self, target: Interface, extended_attributes: dict[str, str]) -> None:
        name = self._expect_identifier().value
        parent_name = ""
        if self._consume_punct(":"):
            parent_name = self._expect_identifier().value

        target.name = name
        target.parent_name = parent_name
        target.extended_attributes = extended_attributes

        self._expect_punct("{")
        self._parse_interface_members(target)
        self._expect_punct(";")

    # Namespace :: namespace identifier { NamespaceMembers } ;
    # https://webidl.spec.whatwg.org/#prod-Namespace
    #
    # Mirrors IDLParser.cpp:872-905 (Parser::parse_namespace). Caller has
    # already consumed the `namespace` keyword.
    def _parse_namespace(self, target: Interface) -> None:
        target.name = self._expect_identifier().value
        target.is_namespace = True

        self._expect_punct("{")
        # Namespace bodies are restricted to readonly attributes, regular
        # operations, and constants per the spec, but the C++ parser is
        # permissive — it just calls parse_function. We use the same
        # interface-member parser; later passes can validate.
        self._parse_interface_members(target)
        self._expect_punct(";")

    # interface mixin identifier { MixinMembers } ;
    # https://webidl.spec.whatwg.org/#prod-MixinRest
    #
    # Caller has consumed `interface mixin`. Mirrors IDLParser.cpp:1085-1104.
    def _parse_interface_mixin(self, extended_attributes: dict[str, str]) -> None:
        new_iface = Interface(filename=self.filename, is_mixin=True)
        new_iface.extended_attributes = extended_attributes
        new_iface.name = self._expect_identifier().value
        # Mixins must not have inheritance (validated post-parse in C++).
        self._expect_punct("{")
        self._parse_interface_members(new_iface)
        self._expect_punct(";")
        self.interface.mixins[new_iface.name] = new_iface

    # partial interface identifier { ... } ;
    # https://webidl.spec.whatwg.org/#prod-Partial
    #
    # Caller has consumed `partial interface`. Mirrors IDLParser.cpp:860-870.
    def _parse_partial_interface(self, extended_attributes: dict[str, str]) -> None:
        partial = Interface(filename=self.filename)
        partial.extended_attributes = extended_attributes
        partial.name = self._expect_identifier().value
        if self._consume_punct(":"):
            partial.parent_name = self._expect_identifier().value
        self._expect_punct("{")
        self._parse_interface_members(partial)
        self._expect_punct(";")
        self.interface.partial_interfaces.append(partial)

    # partial interface mixin identifier { ... } ;
    # Caller has consumed `partial interface mixin`.
    # Mirrors IDLParser.cpp:1106-1117.
    def _parse_partial_interface_mixin(self, extended_attributes: dict[str, str]) -> None:
        partial = Interface(filename=self.filename, is_mixin=True)
        partial.extended_attributes = extended_attributes
        partial.name = self._expect_identifier().value
        self._expect_punct("{")
        self._parse_interface_members(partial)
        self._expect_punct(";")
        self.interface.partial_mixins.append(partial)

    # partial namespace identifier { ... } ;
    # Caller has consumed `partial namespace`. Mirrors IDLParser.cpp:907-916.
    def _parse_partial_namespace(self, extended_attributes: dict[str, str]) -> None:
        partial = Interface(filename=self.filename, is_namespace=True)
        partial.extended_attributes = extended_attributes
        partial.name = self._expect_identifier().value
        self._expect_punct("{")
        self._parse_interface_members(partial)
        self._expect_punct(";")
        self.interface.partial_namespaces.append(partial)

    # InterfaceMembers ::
    #     ExtendedAttributeList InterfaceMember InterfaceMembers
    #     ε
    # InterfaceMember ::
    #     PartialInterfaceMember | Constructor
    # PartialInterfaceMember ::
    #     Const | Operation | Stringifier | StaticMember | Iterable
    #     | AsyncIterable | ReadOnlyMember | ReadWriteAttribute
    #     | ReadWriteMaplike | ReadWriteSetlike | InheritAttribute
    # https://webidl.spec.whatwg.org/#prod-InterfaceMembers
    #
    # Mirrors the for(;;) loop in parse_interface (IDLParser.cpp:746-831).
    def _parse_interface_members(self, target: Interface) -> None:
        while True:
            if self._consume_punct("}"):
                return

            extended_attributes = self._parse_extended_attribute_list()
            tok = self._peek()

            if _is_keyword(tok, "async"):
                self._parse_async_iterable(target)
                continue
            if _is_keyword(tok, "constructor"):
                self._parse_constructor(extended_attributes, target)
                continue
            if _is_keyword(tok, "const"):
                self._parse_constant(target)
                continue
            if _is_keyword(tok, "stringifier"):
                self._parse_stringifier(extended_attributes, target)
                continue
            if _is_keyword(tok, "iterable"):
                self._parse_iterable(target)
                continue
            if _is_keyword(tok, "setlike"):
                self._parse_setlike(target, is_readonly=False)
                continue
            if _is_keyword(tok, "maplike"):
                self._parse_maplike(target, is_readonly=False)
                continue
            if _is_keyword(tok, "inherit") or _is_keyword(tok, "readonly") or _is_keyword(tok, "attribute"):
                self._parse_attribute(extended_attributes, target, static=False)
                continue
            if _is_keyword(tok, "getter"):
                self._advance()
                self._parse_special_operation(extended_attributes, target, kind="getter")
                continue
            if _is_keyword(tok, "setter"):
                self._advance()
                self._parse_special_operation(extended_attributes, target, kind="setter")
                continue
            if _is_keyword(tok, "deleter"):
                self._advance()
                self._parse_special_operation(extended_attributes, target, kind="deleter")
                continue

            if _is_keyword(tok, "static"):
                self._advance()
                next_tok = self._peek()
                if _is_keyword(next_tok, "readonly") or _is_keyword(next_tok, "attribute"):
                    self._parse_attribute(extended_attributes, target, static=True)
                else:
                    op = self._parse_regular_operation(extended_attributes, static=True)
                    target.static_operations.append(op)
                continue

            # Regular operation: starts with a Type (identifier or `(` for
            # union return). _parse_regular_operation handles that.
            op = self._parse_regular_operation(extended_attributes, static=False)
            target.operations.append(op)

    # AttributeRest :: attribute TypeWithExtendedAttributes AttributeName ;
    # https://webidl.spec.whatwg.org/#prod-Attribute
    #
    # Mirrors IDLParser.cpp:338-389 (Parser::parse_attribute). Also handles
    # the `setlike`/`maplike` divergence at IDLParser.cpp:351-354 where the
    # `readonly` keyword is shared between read-only attributes and
    # readonly setlike/maplike declarations.
    def _parse_attribute(self, extended_attributes: dict[str, str], target: Interface, static: bool) -> None:
        inherit = self._consume_keyword("inherit")
        readonly = self._consume_keyword("readonly")

        # `readonly setlike<T>` / `readonly maplike<K,V>` use the same `readonly`
        # keyword as a read-only attribute. Disambiguate before consuming
        # `attribute`. IDLParser.cpp:351-354.
        if not inherit and _is_keyword(self._peek(), "setlike"):
            self._parse_setlike(target, is_readonly=readonly)
            return
        if not inherit and _is_keyword(self._peek(), "maplike"):
            self._parse_maplike(target, is_readonly=readonly)
            return

        self._expect_keyword("attribute")
        type_ = self._parse_type()
        name = self._expect_identifier().value
        self._expect_punct(";")

        attribute = Attribute(
            name=name,
            type=type_,
            readonly=readonly,
            inherit=inherit,
            extended_attributes=extended_attributes,
        )
        if static:
            target.static_attributes.append(attribute)
        else:
            target.attributes.append(attribute)

    # Const :: const ConstType identifier = ConstValue ;
    # https://webidl.spec.whatwg.org/#prod-Const
    #
    # Mirrors IDLParser.cpp:391-412 (Parser::parse_constant).
    def _parse_constant(self, target: Interface) -> None:
        self._expect_keyword("const")
        type_ = self._parse_type()
        name = self._expect_identifier().value
        self._expect_punct("=")
        # Consume tokens up to the terminating `;`. The C++ side reads a
        # whitespace-or-`;`-delimited token; we reassemble token text.
        pieces: list[str] = []
        while True:
            tok = self._peek()
            if _is_punct(tok, ";"):
                break
            if tok.kind is TokenKind.EOF:
                raise ParseError("unterminated constant declaration", tok, self.filename)
            pieces.append(tok.value)
            self._advance()
        self._expect_punct(";")
        target.constants.append(Constant(type=type_, name=name, value="".join(pieces)))

    # Constructor :: constructor ( ArgumentList ) ;
    # https://webidl.spec.whatwg.org/#prod-Constructor
    #
    # Mirrors IDLParser.cpp:490-501 (Parser::parse_constructor).
    def _parse_constructor(self, extended_attributes: dict[str, str], target: Interface) -> None:
        self._expect_keyword("constructor")
        parameters = self._parse_parameter_list()
        self._expect_punct(";")
        target.constructors.append(Constructor(parameters=parameters, extended_attributes=extended_attributes))

    # Stringifier :: stringifier StringifierRest
    # StringifierRest :: AttributeRest | ;
    # https://webidl.spec.whatwg.org/#prod-Stringifier
    #
    # Mirrors IDLParser.cpp:503-516 (Parser::parse_stringifier).
    def _parse_stringifier(self, extended_attributes: dict[str, str], target: Interface) -> None:
        self._expect_keyword("stringifier")
        target.has_stringifier = True
        if (
            _is_keyword(self._peek(), "attribute")
            or _is_keyword(self._peek(), "inherit")
            or _is_keyword(self._peek(), "readonly")
        ):
            self._parse_attribute(extended_attributes, target, static=False)
            target.stringifier_attribute = target.attributes[-1]
            target.stringifier_extended_attributes = target.stringifier_attribute.extended_attributes
        else:
            target.stringifier_extended_attributes = extended_attributes
            self._expect_punct(";")

    # Iterable :: iterable < Type OptionalType > ;
    # OptionalType :: , Type | ε
    # https://webidl.spec.whatwg.org/#prod-Iterable
    #
    # Mirrors IDLParser.cpp:518-541 (Parser::parse_iterable).
    def _parse_iterable(self, target: Interface) -> None:
        self._expect_keyword("iterable")
        self._expect_punct("<")
        first_type = self._parse_type()
        if self._consume_punct(","):
            second_type = self._parse_type()
            target.pair_iterator_types = (first_type, second_type)
        else:
            target.value_iterator_type = first_type
        self._expect_punct(">")
        self._expect_punct(";")

    # AsyncIterable :: async iterable < Type OptionalType > OptionalArgumentList ;
    # https://webidl.spec.whatwg.org/#idl-async-iterable-declaration
    #
    # Mirrors IDLParser.cpp:543-579 (Parser::parse_async_iterable).
    def _parse_async_iterable(self, target: Interface) -> None:
        self._expect_keyword("async")
        self._expect_keyword("iterable")
        self._expect_punct("<")
        first_type = self._parse_type()
        if self._consume_punct(","):
            second_type = self._parse_type()
            target.async_pair_iterator_types = (first_type, second_type)
        else:
            target.async_value_iterator_type = first_type
        self._expect_punct(">")
        if _is_punct(self._peek(), "("):
            target.async_iterator_parameters = self._parse_parameter_list()
        self._expect_punct(";")

    # Setlike :: readonly? setlike < Type > ;
    # https://webidl.spec.whatwg.org/#prod-ReadWriteSetlike
    #
    # Mirrors IDLParser.cpp:581-600 (Parser::parse_setlike).
    def _parse_setlike(self, target: Interface, is_readonly: bool) -> None:
        self._expect_keyword("setlike")
        self._expect_punct("<")
        target.set_entry_type = self._parse_type()
        target.is_set_readonly = is_readonly
        self._expect_punct(">")
        self._expect_punct(";")

    # Maplike :: readonly? maplike < Type , Type > ;
    # https://webidl.spec.whatwg.org/#prod-ReadWriteMaplike
    #
    # Mirrors IDLParser.cpp:602-625 (Parser::parse_maplike).
    def _parse_maplike(self, target: Interface, is_readonly: bool) -> None:
        self._expect_keyword("maplike")
        self._expect_punct("<")
        target.map_key_type = self._parse_type()
        self._expect_punct(",")
        target.map_value_type = self._parse_type()
        target.is_map_readonly = is_readonly
        self._expect_punct(">")
        self._expect_punct(";")

    # SpecialOperation :: getter | setter | deleter (each followed by a
    # RegularOperation form). https://webidl.spec.whatwg.org/#prod-SpecialOperation
    #
    # Caller has already consumed the keyword (`getter`/`setter`/`deleter`).
    # Routes to the correct named/indexed slot on Interface based on the
    # parameter type; mirrors parse_getter / parse_setter / parse_deleter
    # in IDLParser.cpp:627-731.
    def _parse_special_operation(
        self,
        extended_attributes: dict[str, str],
        target: Interface,
        kind: str,
    ) -> None:
        op = self._parse_regular_operation(extended_attributes, static=False)
        if not op.parameters:
            raise ParseError(f"{kind} must have at least one parameter", self._peek(), self.filename)
        identifier_param_type = op.parameters[0].type
        assert identifier_param_type is not None
        type_name = identifier_param_type.name
        if kind == "getter":
            if type_name == "DOMString":
                target.named_property_getter = op
            elif type_name == "unsigned long":
                target.indexed_property_getter = op
            else:
                raise ParseError(
                    f"named/indexed property getter parameter must be DOMString or unsigned long, got {type_name!r}",
                    self._peek(),
                    self.filename,
                )
        elif kind == "setter":
            if type_name == "DOMString":
                target.named_property_setter = op
            elif type_name == "unsigned long":
                target.indexed_property_setter = op
            else:
                raise ParseError(
                    f"named/indexed property setter parameter must be DOMString or unsigned long, got {type_name!r}",
                    self._peek(),
                    self.filename,
                )
        elif kind == "deleter":
            if type_name == "DOMString":
                target.named_property_deleter = op
            else:
                raise ParseError(
                    f"named property deleter parameter must be DOMString, got {type_name!r}",
                    self._peek(),
                    self.filename,
                )
        else:
            raise AssertionError(f"unknown special-operation kind {kind!r}")

    # RegularOperation :: Type OperationRest
    # OperationRest :: OptionalOperationName ( ArgumentList ) ;
    # OptionalOperationName :: identifier | ε
    # https://webidl.spec.whatwg.org/#prod-RegularOperation
    #
    # Mirrors IDLParser.cpp:463-488 (Parser::parse_function). Anonymous form
    # (no identifier — used by special operations) is supported via the
    # caller passing an extended_attributes map.
    def _parse_regular_operation(self, extended_attributes: dict[str, str], static: bool) -> Operation:
        return_type = self._parse_type()
        # OptionalOperationName: an identifier *or* nothing (anonymous, e.g.
        # for special operations). If `(` is next we have an anonymous form.
        name = ""
        if self._peek().kind is TokenKind.IDENTIFIER:
            # Mirror IDLParser.cpp:142 — strip leading underscores (WebIDL allows
            # `_keyword` to escape contextual keywords like `_any`).
            raw = self._advance().value
            name = raw.lstrip("_") if raw.startswith("_") else raw
        parameters = self._parse_parameter_list()
        self._expect_punct(";")
        return Operation(
            name=name,
            return_type=return_type,
            parameters=parameters,
            extended_attributes=extended_attributes,
            static=static,
        )

    # ArgumentList :: Argument Arguments | ε
    # Argument :: ExtendedAttributeList ArgumentRest
    # ArgumentRest :: optional TypeWithExtendedAttributes ArgumentName Default
    #               | Type Ellipsis ArgumentName
    # https://webidl.spec.whatwg.org/#prod-ArgumentList
    #
    # Mirrors IDLParser.cpp:414-461 (Parser::parse_parameters).
    def _parse_parameter_list(self) -> list[Parameter]:
        self._expect_punct("(")
        parameters: list[Parameter] = []
        # Top-of-loop check matches IDLParser.cpp:418-420; this also tolerates
        # a trailing comma in argument lists (e.g. Geolocation.idl:6-8).
        while True:
            if self._consume_punct(")"):
                return parameters
            attrs = self._parse_extended_attribute_list()

            optional = False
            if self._consume_keyword("optional"):
                optional = True
                # IDLParser.cpp:427-432 supports a second [...] block after
                # `optional`; we mirror it.
                more_attrs = self._parse_extended_attribute_list()
                for k, v in more_attrs.items():
                    attrs.setdefault(k, v)

            type_ = self._parse_type()

            variadic = False
            if _is_punct(self._peek(), "..."):
                self._advance()
                variadic = True

            name = self._expect_identifier().value

            default_value: str | None = None
            if optional and self._consume_punct("="):
                default_value = self._parse_default_value_source()

            parameters.append(
                Parameter(
                    name=name,
                    type=type_,
                    optional=optional,
                    variadic=variadic,
                    default_value=default_value,
                    extended_attributes=attrs,
                )
            )

            if variadic:
                # Variadic must be last.
                self._expect_punct(")")
                return parameters
            if self._consume_punct(","):
                continue
            self._expect_punct(")")
            return parameters

    # Type :: SingleType | UnionType Null
    # https://webidl.spec.whatwg.org/#prod-Type
    #
    # Mirrors IDLParser.cpp:236-336 (Parser::parse_type). Supports:
    #   - UnionType `(A or B or ...)` with optional trailing `?`
    #   - PrimitiveType (with `unsigned` / `unrestricted` / `long long`)
    #   - identifier-named types
    #   - ParameterizedType `Name<T, ...>`
    #   - trailing `?` for nullability
    def _parse_type(self) -> Type:
        # UnionType :: ( UnionMemberType or UnionMemberType UnionMemberTypes )
        # https://webidl.spec.whatwg.org/#prod-UnionType
        if self._consume_punct("("):
            members = [self._parse_type()]
            self._expect_keyword("or")
            members.append(self._parse_type())
            while self._consume_keyword("or"):
                members.append(self._parse_type())
            self._expect_punct(")")
            nullable = self._consume_punct("?")
            return Type(name="", nullable=nullable, kind="union", union_member_types=members)

        unsigned = self._consume_keyword("unsigned")
        unrestricted = False
        if not unsigned:
            unrestricted = self._consume_keyword("unrestricted")

        name_tok = self._expect_identifier()
        name = name_tok.value

        # IntegerType :: short | long | long long
        # https://webidl.spec.whatwg.org/#prod-IntegerType
        if name == "long" and _is_keyword(self._peek(), "long"):
            self._advance()
            name = "long long"

        # ParameterizedType :: sequence<T>, Promise<T>, FrozenArray<T>,
        # ObservableArray<T>, record<K, V>.
        # The C++ parser accepts <...> after any identifier (IDLParser.cpp:288-296).
        parameters: list[Type] = []
        is_parameterized = False
        if self._consume_punct("<"):
            is_parameterized = True
            parameters.append(self._parse_type())
            while self._consume_punct(","):
                parameters.append(self._parse_type())
            self._expect_punct(">")

        nullable = self._consume_punct("?")

        full_name_parts: list[str] = []
        if unsigned:
            full_name_parts.append("unsigned ")
        if unrestricted:
            full_name_parts.append("unrestricted ")
        full_name_parts.append(name)
        full_name = "".join(full_name_parts)

        if is_parameterized:
            return Type(
                name=full_name,
                nullable=nullable,
                kind="parameterized",
                parameters=parameters,
            )
        return Type(name=full_name, nullable=nullable)

    # DefaultValue :: ConstValue | string | [] | {} | null | undefined
    # https://webidl.spec.whatwg.org/#prod-DefaultValue
    #
    # Returns the default value as the literal source text (e.g. "0", "true",
    # "\"\"", "[]"). Codegen needs the literal form, not a parsed value.
    def _parse_default_value_source(self) -> str:
        tok = self._peek()
        if tok.kind in (TokenKind.INTEGER, TokenKind.DECIMAL, TokenKind.IDENTIFIER):
            self._advance()
            return tok.value
        if tok.kind is TokenKind.STRING:
            self._advance()
            return f'"{tok.value}"'
        if _is_punct(tok, "["):
            self._advance()
            self._expect_punct("]")
            return "[]"
        if _is_punct(tok, "{"):
            self._advance()
            self._expect_punct("}")
            return "{}"
        if _is_punct(tok, "-"):
            self._advance()
            inner = self._peek()
            if inner.kind in (TokenKind.INTEGER, TokenKind.DECIMAL, TokenKind.IDENTIFIER):
                self._advance()
                return f"-{inner.value}"
        raise ParseError(f"expected a default value, got {tok.value!r}", tok, self.filename)


# IDLParser.cpp:76-108 (convert_enumeration_value_to_cpp_enum_member).
#
# Rules:
#   - Split on whitespace, '-', '_'.
#   - Title-case each word and concatenate.
#   - If the first character is not [A-Za-z_], prepend '_'.
#   - If a non-alphanumeric run lies between words, emit a single '_'.
#   - If the result is empty, use "Empty".
#   - If the result collides with a previously emitted name, append '_' until unique.
def _translate_enumeration_value_to_cpp_member(value: str, names_already_seen: set[str]) -> str:
    pieces: list[str] = []
    pos = 0
    first_word = True

    def is_separator(ch: str) -> bool:
        return ch.isspace() or ch in ("-", "_")

    def is_alnum(ch: str) -> bool:
        return ch.isalnum() and ord(ch) < 128

    def is_valid_cpp_identifier_start(ch: str) -> bool:
        return ch == "_" or (ch.isalpha() and ord(ch) < 128)

    while pos < len(value):
        while pos < len(value) and is_separator(value[pos]):
            pos += 1
        if pos >= len(value):
            break

        if first_word:
            if not is_valid_cpp_identifier_start(value[pos]):
                pieces.append("_")
            first_word = False

        word_start = pos
        while pos < len(value) and is_alnum(value[pos]):
            pos += 1
        word = value[word_start:pos]
        if word:
            pieces.append(word[:1].upper() + word[1:].lower())
            continue

        non_alnum_start = pos
        while pos < len(value) and not is_alnum(value[pos]):
            pos += 1
        if pos > non_alnum_start:
            pieces.append("_")

    result = "".join(pieces) or "Empty"
    while result in names_already_seen:
        result += "_"
    names_already_seen.add(result)
    return result
