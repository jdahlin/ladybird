# Bindings code emitter — Python port of
# Meta/Lagom/Tools/CodeGenerators/LibWeb/BindingsGenerator/IDLGenerators.cpp.
#
# Each emitter function consumes the resolved Interface AST (see ../resolver.py)
# and writes C++ source through SourceGenerator's @key@ template substitution.
# The output must be byte-identical to what the C++ tool produces — that's the
# parity contract enforced by Meta/idl_parity.py.
#
# Generators are added concept-by-concept following the ladder in
# /home/jdahlin/.claude/plans/how-can-we-rewrite-snoopy-pebble.md.
