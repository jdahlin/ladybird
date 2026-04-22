"""Web IDL AST — dataclasses corresponding to grammar productions in §2.1.

https://webidl.spec.whatwg.org/#idl-grammar

Production names follow the spec exactly. Field names follow the spec where
the spec names them; otherwise they follow Ladybird's existing C++ AST
(Libraries/LibIDL/Types.h) so the JSON shape produced by the C++ tool's
--emit-ir-json flag is reproducible from these dataclasses without translation.

Ladybird-specific extensions (`#import`, `[FIXME]`, `[ImplementedAs]`, …) live
on the relevant container as named fields, each annotated with the C++ origin.

Only the productions needed for current ladder rungs are populated below.
New productions are appended as concept-ladder rungs introduce them.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Optional


# §2.5 / §3.10 — Type is the central AST node. The C++ side splits this into
# Type / ParameterizedType / UnionType subclasses; we mirror that with a
# `kind` discriminator and optional fields.
@dataclass
class Type:
    # Resolved canonical name as it would appear in an IDL fragment, e.g.
    # "DOMString", "long long", "sequence<DOMString>", "(DOMString or long)".
    # Mirrors IDL::Type::name() in Libraries/LibIDL/Types.h.
    name: str
    nullable: bool = False
    # "plain" | "parameterized" | "union" — matches IDL::Type::Kind.
    kind: str = "plain"
    # Populated for kind == "parameterized" (e.g. sequence<T>, Promise<T>).
    parameters: list["Type"] = field(default_factory=list)
    # Populated for kind == "union".
    union_member_types: list["Type"] = field(default_factory=list)


# §2.4 Typedefs — `Typedef :: typedef TypeWithExtendedAttributes identifier ;`
# https://webidl.spec.whatwg.org/#idl-typedefs
@dataclass
class Typedef:
    # `extended_attributes` mirrors the C++ HashMap<ByteString, ByteString>:
    # an ordered map of attribute name → value (empty string for boolean form).
    extended_attributes: dict[str, str] = field(default_factory=dict)
    type: Optional[Type] = None


# §2.13 Enumerations — `Enum :: enum identifier { EnumValueList } ;`
# https://webidl.spec.whatwg.org/#idl-enums
@dataclass
class Enumeration:
    extended_attributes: dict[str, str] = field(default_factory=dict)
    # `values` is the ordered list of string literals declared in the enum.
    values: list[str] = field(default_factory=list)
    # `first_member` mirrors IDL::Enumeration::first_member; used by codegen
    # for the default value when none is specified.
    first_member: str = ""
    # IDLParser.cpp:968-970 maps each enum value to a C++-safe identifier via
    # convert_enumeration_value_to_cpp_enum_member (kebab/space → PascalCase,
    # leading-digit → `_` prefix, dedup with trailing `_`).
    translated_cpp_names: dict[str, str] = field(default_factory=dict)
    # `is_original_definition` mirrors IDL::Enumeration::is_original_definition;
    # set False when an enum is re-declared (Ladybird allows this for the
    # purpose of populating a partial-spec stub).
    is_original_definition: bool = True


# §2.5.4 Operations / §2.5.6 Callback functions — Parameter is shared between
# operations, constructors, and callback functions.
#
# ArgumentList :: Argument Arguments | ε
# Argument :: ExtendedAttributeList ArgumentRest
# ArgumentRest :: optional TypeWithExtendedAttributes ArgumentName Default
#               | Type Ellipsis ArgumentName
# Ellipsis :: ... | ε
# https://webidl.spec.whatwg.org/#prod-ArgumentList
#
# Mirrors IDL::Parameter in Libraries/LibIDL/Types.h.
@dataclass
class Parameter:
    name: str = ""
    type: Optional[Type] = None
    optional: bool = False
    variadic: bool = False
    default_value: Optional[str] = None
    extended_attributes: dict[str, str] = field(default_factory=dict)


# §2.5.4 Operations — RegularOperation produces an IDL::Function in C++.
# https://webidl.spec.whatwg.org/#prod-Operation
#
# Both regular and special operations (getter/setter/deleter/stringifier)
# share this structure; specials are routed via dedicated fields on Interface.
@dataclass
class Operation:
    name: str = ""
    return_type: Optional[Type] = None
    parameters: list[Parameter] = field(default_factory=list)
    extended_attributes: dict[str, str] = field(default_factory=dict)
    static: bool = False


# §2.5.1 Constants — `Const :: const ConstType identifier = ConstValue ;`
# https://webidl.spec.whatwg.org/#prod-Const
#
# Mirrors IDL::Constant in Libraries/LibIDL/Types.h.
@dataclass
class Constant:
    type: Optional[Type] = None
    name: str = ""
    # Raw source text of the constant value (e.g. "0", "true", "0xFF").
    value: str = ""


# §2.5.2 Attributes — `Attribute :: AttributeRest`
# AttributeRest :: attribute TypeWithExtendedAttributes AttributeName ;
# https://webidl.spec.whatwg.org/#prod-Attribute
#
# Mirrors IDL::Attribute in Libraries/LibIDL/Types.h.
@dataclass
class Attribute:
    name: str = ""
    type: Optional[Type] = None
    readonly: bool = False
    inherit: bool = False
    extended_attributes: dict[str, str] = field(default_factory=dict)


# §2.5.3 Constructors — `Constructor :: constructor ( ArgumentList ) ;`
# https://webidl.spec.whatwg.org/#prod-Constructor
@dataclass
class Constructor:
    parameters: list[Parameter] = field(default_factory=list)
    extended_attributes: dict[str, str] = field(default_factory=dict)


# §2.5.6 Callback functions
# CallbackRest :: identifier = Type ( ArgumentList ) ;
# https://webidl.spec.whatwg.org/#prod-CallbackRest
#
# Mirrors IDL::CallbackFunction in Libraries/LibIDL/Types.h.
@dataclass
class CallbackFunction:
    return_type: Optional[Type] = None
    parameters: list[Parameter] = field(default_factory=list)
    extended_attributes: dict[str, str] = field(default_factory=dict)
    # Reflects `[LegacyTreatNonObjectAsNull]` (Web IDL legacy extended attribute).
    is_legacy_treat_non_object_as_null: bool = False


# §3.2 Dictionary type
# `Dictionary :: dictionary identifier Inheritance { DictionaryMembers } ;`
# https://webidl.spec.whatwg.org/#idl-dictionaries
#
# Mirrors IDL::DictionaryMember and IDL::Dictionary in Libraries/LibIDL/Types.h
# and IDLParser.cpp:995-1075 (Parser::parse_dictionary).
@dataclass
class DictionaryMember:
    name: str = ""
    type: Optional[Type] = None
    required: bool = False
    default_value: Optional[str] = None
    extended_attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class Dictionary:
    extended_attributes: dict[str, str] = field(default_factory=dict)
    parent_name: str = ""  # §3.2.1 dictionary inheritance
    # IDLParser.cpp:1068-1074 sorts members lexicographically by name.
    members: list[DictionaryMember] = field(default_factory=list)
    # `is_original_definition` mirrors IDL::Dictionary::is_original_definition.
    is_original_definition: bool = True


# §2.5 InterfaceLike — top-level container that aggregates everything parsed
# from a single .idl file (and its #imports). Mirrors IDL::Interface in
# Libraries/LibIDL/Types.h.
#
# An Interface is also reused to hold parsed mixin/namespace/partial
# declarations: each gets its own Interface with the appropriate flags
# (is_mixin / is_namespace / etc.) set.
@dataclass
class Interface:
    # Source filename (absolute path), preserved for diagnostics and depfiles.
    filename: str = ""
    # Primary interface/namespace/dictionary/etc. name declared in the file.
    # Empty for files containing only typedefs/enums.
    name: str = ""
    # §2.5 Interface inheritance: `interface identifier Inheritance { ... }`,
    # where Inheritance is `: identifier` or empty. Empty string when absent.
    parent_name: str = ""

    # Kind flags — at most one may be true for the *primary* interface in a file.
    is_namespace: bool = False
    is_mixin: bool = False
    is_callback_interface: bool = False

    # ExtendedAttributeList attached to the primary interface declaration.
    extended_attributes: dict[str, str] = field(default_factory=dict)

    # §2.5.2 Attributes / §2.5.4 Operations / §2.5.1 Constants /
    # §2.5.3 Constructors / §2.5.6 Stringifier — primary interface members.
    attributes: list[Attribute] = field(default_factory=list)
    static_attributes: list[Attribute] = field(default_factory=list)
    constants: list[Constant] = field(default_factory=list)
    constructors: list[Constructor] = field(default_factory=list)
    operations: list[Operation] = field(default_factory=list)
    static_operations: list[Operation] = field(default_factory=list)

    has_stringifier: bool = False
    has_unscopable_member: bool = False
    stringifier_attribute: Optional[Attribute] = None
    stringifier_extended_attributes: Optional[dict[str, str]] = None

    # §2.5.7 Iterable declarations.
    value_iterator_type: Optional[Type] = None
    pair_iterator_types: Optional[tuple[Type, Type]] = None
    async_value_iterator_type: Optional[Type] = None
    async_pair_iterator_types: Optional[tuple[Type, Type]] = None
    async_iterator_parameters: list[Parameter] = field(default_factory=list)

    # §2.5.8 Setlike / §2.5.9 Maplike.
    set_entry_type: Optional[Type] = None
    is_set_readonly: bool = False
    map_key_type: Optional[Type] = None
    map_value_type: Optional[Type] = None
    is_map_readonly: bool = False

    # Special operations (named/indexed property handlers + deleter).
    named_property_getter: Optional[Operation] = None
    named_property_setter: Optional[Operation] = None
    named_property_deleter: Optional[Operation] = None
    indexed_property_getter: Optional[Operation] = None
    indexed_property_setter: Optional[Operation] = None

    # §2.4 Typedefs declared at file scope. Insertion-ordered.
    typedefs: dict[str, Typedef] = field(default_factory=dict)
    # §2.13 Enumerations declared at file scope. Insertion-ordered.
    enumerations: dict[str, Enumeration] = field(default_factory=dict)
    # §3.2 Dictionaries declared at file scope. Insertion-ordered.
    dictionaries: dict[str, Dictionary] = field(default_factory=dict)
    # §2.5.6 Callback functions declared at file scope. Insertion-ordered.
    callback_functions: dict[str, CallbackFunction] = field(default_factory=dict)

    # Mixins (`interface mixin Foo { ... }`) declared in this file or its imports.
    mixins: dict[str, "Interface"] = field(default_factory=dict)

    # §2.5.10 IncludesStatement: `Foo includes Bar;` — Vector of (target, mixin).
    # Stored as parsed (the C++ side resolves these into included_mixins later).
    includes_statements: list[tuple[str, str]] = field(default_factory=list)

    # Partial declarations seen in this file or its imports. Each is its own
    # parsed Interface that should later be merged into the primary.
    partial_interfaces: list["Interface"] = field(default_factory=list)
    partial_mixins: list["Interface"] = field(default_factory=list)
    partial_namespaces: list["Interface"] = field(default_factory=list)
    partial_dictionaries: dict[str, list[Dictionary]] = field(default_factory=dict)

    # `imported_modules` mirrors IDL::Interface::imported_modules. Populated
    # by the preprocessor pass for each `#import` directive that resolves.
    imported_modules: list[str] = field(default_factory=list)
    # The actual parsed Interface objects for each resolved import. Mirrors
    # IDL::Interface::imported_modules in Libraries/LibIDL/Types.h:332
    # (the C++ field holds Interface& references; we hold full objects).
    # Populated by the ImportResolver alongside imported_modules. Walked by
    # emit_includes_for_all_imports for the BFS that emits #include lines.
    imported_interfaces: list["Interface"] = field(default_factory=list)

    # --- post-parse computed fields ---
    # IDLParser.cpp:833-856 sets these at the end of parse_interface;
    # IDL::Interface declares them in Libraries/LibIDL/Types.h:319-326.
    # main.cpp:60-69 sets fully_qualified_name. We compute them via
    # resolver.compute_post_parse_names().
    implemented_name: str = ""  # `[ImplementedAs=...]` value or `name`
    namespaced_name: str = ""  # `<LegacyNamespace>.<name>` or `name`
    fully_qualified_name: str = ""  # `<libweb_namespace>::<implemented_name>` or `implemented_name`
    constructor_class: str = ""  # `<implemented_name>Constructor`
    prototype_class: str = ""  # `<implemented_name>Prototype`
    prototype_base_class: str = ""  # `<parent_name or "Object">Prototype`
    namespace_class: str = ""  # `<name>Namespace`
    global_mixin_class: str = ""  # `<name>GlobalMixin`

    # Marker for whether this Interface object holds *only* secondary
    # declarations (typedefs/enums/dicts/etc.) without a primary interface —
    # mirrors IDL::Interface::will_generate_code() reasoning at the parser layer.
    @property
    def has_primary_interface(self) -> bool:
        return bool(self.name)
