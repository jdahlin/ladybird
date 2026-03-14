/*
 * Copyright (c) 2026-present, the Ladybird developers.
 *
 * SPDX-License-Identifier: BSD-2-Clause
 */

//! Lexer: tokenizes UTF-16 JavaScript source code.
//!
//! The lexer operates directly on a `&[u16]` slice (UTF-16 code units).
//! It produces `Token` structs one at a time via `next()`. Each token
//! stores its type, position (line/column), and the start offset + length
//! into the source buffer for its value.
//!
//! ## Template literals
//!
//! Template literals require stateful lexing because `${...}` expressions
//! can contain arbitrary code (including nested templates). The lexer uses
//! a `template_states` stack to track nesting. When a `${` is encountered
//! inside a template, the lexer pushes state and switches to normal
//! tokenization until the matching `}` is found.
//!
//! ## Regex vs division
//!
//! The lexer must distinguish `/regex/` from `a / b` (division). It uses
//! the preceding token type: if the previous token could end an expression
//! (identifier, literal, `)`, `]`, `++`, `--`), then `/` is division.
//! Otherwise, it's the start of a regex literal.
//!
//! ## Escaped keywords
//!
//! When an identifier contains Unicode escape sequences (e.g., `\u0069f`
//! for `if`), the lexer checks if the decoded value matches a keyword.
//! If so, the token type is `EscapedKeyword` rather than the keyword
//! type, because escaped keywords have different semantics (they can be
//! used as identifiers in some contexts).

use crate::ast::Utf16String;
use crate::token::{Token, TokenType};
use crate::u32_from_usize;

/// State for tracking template literal nesting.
#[derive(Clone)]
struct TemplateState {
    in_expression: bool,
    open_bracket_count: u32,
}

/// Saved lexer state for lookahead and backtracking.
pub(crate) struct SavedLexerState {
    position: usize,
    current_code_unit: u16,
    eof: bool,
    line_number: u32,
    line_column: u32,
    current_token_type: TokenType,
    template_states: Vec<TemplateState>,
}

pub struct Lexer<'a> {
    source: &'a [u16],
    /// 1-based index into `source`: always one past `current_code_unit`.
    /// Subtract 1 to get the 0-based index of the current code unit.
    position: usize,
    current_code_unit: u16,
    eof: bool,
    line_number: u32,
    line_column: u32,
    current_token_type: TokenType,
    regex_is_in_character_class: bool,
    allow_html_comments: bool,
    template_states: Vec<TemplateState>,
    saved_states: Vec<SavedLexerState>,
}

// Unicode constants used by the lexical grammar.
// https://tc39.es/ecma262/#sec-white-space
// https://tc39.es/ecma262/#sec-line-terminators
const NO_BREAK_SPACE: u16 = 0x00A0;
const ZERO_WIDTH_NON_JOINER: u32 = 0x200C;
const ZERO_WIDTH_JOINER: u32 = 0x200D;
const LINE_SEPARATOR: u16 = 0x2028;
const PARAGRAPH_SEPARATOR: u16 = 0x2029;
const ZERO_WIDTH_NO_BREAK_SPACE: u16 = 0xFEFF;

/// Convert an ASCII byte literal to a UTF-16 code unit.
pub(crate) const fn ch(c: u8) -> u16 {
    c as u16
}

/// Compile-time lookup table: ASCII identifier start characters (a-z, A-Z, _, $).
static ASCII_ID_START: [bool; 128] = {
    let mut table = [false; 128];
    let mut i = 0u8;
    while i < 128 {
        table[i as usize] = matches!(i, b'a'..=b'z' | b'A'..=b'Z' | b'_' | b'$');
        i += 1;
    }
    table
};

/// Compile-time lookup table: ASCII identifier continue characters (a-z, A-Z, 0-9, _, $).
static ASCII_ID_CONTINUE: [bool; 128] = {
    let mut table = [false; 128];
    let mut i = 0u8;
    while i < 128 {
        table[i as usize] = matches!(i, b'a'..=b'z' | b'A'..=b'Z' | b'0'..=b'9' | b'_' | b'$');
        i += 1;
    }
    table
};

fn is_ascii(cu: u16) -> bool {
    cu < 128
}

fn is_ascii_alpha(cp: u32) -> bool {
    cp < 128 && (cp as u8).is_ascii_alphabetic()
}

fn is_ascii_digit(cu: u16) -> bool {
    cu >= ch(b'0') && cu <= ch(b'9')
}

fn is_ascii_digit_cp(cp: u32) -> bool {
    cp >= b'0' as u32 && cp <= b'9' as u32
}

fn is_ascii_hex_digit(cu: u16) -> bool {
    is_ascii_digit(cu) || (cu >= ch(b'a') && cu <= ch(b'f')) || (cu >= ch(b'A') && cu <= ch(b'F'))
}

fn is_ascii_alphanumeric(cp: u32) -> bool {
    is_ascii_alpha(cp) || is_ascii_digit_cp(cp)
}

fn is_ascii_space(cu: u16) -> bool {
    matches!(cu, 0x09 | 0x0A | 0x0B | 0x0C | 0x0D | 0x20)
}

fn is_octal_digit(cu: u16) -> bool {
    cu >= ch(b'0') && cu <= ch(b'7')
}

fn is_binary_digit(cu: u16) -> bool {
    cu == ch(b'0') || cu == ch(b'1')
}

fn is_utf16_high_surrogate(cu: u16) -> bool {
    (0xD800..=0xDBFF).contains(&cu)
}

fn is_utf16_low_surrogate(cu: u16) -> bool {
    (0xDC00..=0xDFFF).contains(&cu)
}

/// Decode a code point from a UTF-16 slice starting at `pos`.
/// Returns (code_point, number_of_code_units_consumed).
fn decode_code_point(source: &[u16], pos: usize) -> (u32, usize) {
    if pos >= source.len() {
        return (0xFFFD, 1);
    }
    let cu = source[pos];
    if is_utf16_high_surrogate(cu)
        && pos + 1 < source.len()
        && is_utf16_low_surrogate(source[pos + 1])
    {
        let hi = cu as u32;
        let lo = source[pos + 1] as u32;
        let cp = 0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00);
        (cp, 2)
    } else {
        (cu as u32, 1)
    }
}

// https://tc39.es/ecma262/#sec-line-terminators
// LineTerminator :: <LF> | <CR> | <LS> | <PS>
fn is_line_terminator_cp(cp: u32) -> bool {
    cp == '\n' as u32
        || cp == '\r' as u32
        || cp == LINE_SEPARATOR as u32
        || cp == PARAGRAPH_SEPARATOR as u32
}

// https://tc39.es/ecma262/#sec-white-space
// WhiteSpace :: <TAB> | <VT> | <FF> | <ZWNBSP> | <USP>
// where <USP> is any code point with General Category "Space_Separator" (Zs).
fn is_whitespace_cp(cp: u32) -> bool {
    if cp < 128 {
        return is_ascii_space(cp as u16);
    }
    if cp == NO_BREAK_SPACE as u32 || cp == ZERO_WIDTH_NO_BREAK_SPACE as u32 {
        return true;
    }
    // Unicode General Category "Space_Separator" (Zs)
    matches!(cp, 0x1680 | 0x2000..=0x200A | 0x202F | 0x205F | 0x3000)
}

// https://tc39.es/ecma262/#sec-identifier-names
// IdentifierStartChar :: UnicodeIDStart | $ | _
fn is_identifier_start_cp(cp: u32) -> bool {
    if is_ascii_alpha(cp) || cp == '_' as u32 || cp == '$' as u32 {
        return true;
    }
    if cp < 128 {
        return false;
    }
    unicode_id_start(cp)
}

// https://tc39.es/ecma262/#sec-identifier-names
// IdentifierPartChar :: UnicodeIDContinue | $ | <ZWNJ> | <ZWJ>
fn is_identifier_continue_cp(cp: u32) -> bool {
    if is_ascii_alphanumeric(cp)
        || cp == '$' as u32
        || cp == '_' as u32
        || cp == ZERO_WIDTH_NON_JOINER
        || cp == ZERO_WIDTH_JOINER
    {
        return true;
    }
    if cp < 128 {
        return false;
    }
    unicode_id_continue(cp)
}

fn unicode_id_start(cp: u32) -> bool {
    // NB: The ECMAScript spec requires ID_Start, not XID_Start.
    //     U+309B and U+309C are Other_ID_Start (thus ID_Start) but not XID_Start.
    cp == 0x309B || cp == 0x309C || char::from_u32(cp).is_some_and(unicode_ident::is_xid_start)
}

fn unicode_id_continue(cp: u32) -> bool {
    // NB: The ECMAScript spec requires ID_Continue, not XID_Continue.
    //     U+309B and U+309C are Other_ID_Start (thus ID_Continue) but not XID_Continue.
    cp == 0x309B || cp == 0x309C || char::from_u32(cp).is_some_and(unicode_ident::is_xid_continue)
}

// https://tc39.es/ecma262/#sec-keywords-and-reserved-words
fn keyword_from_str(s: &[u16]) -> Option<TokenType> {
    // Dispatch by length first to minimize comparisons, then compare
    // against compile-time UTF-16 constants (zero allocation).
    match s.len() {
        2 => {
            if s == utf16!("do") {
                return Some(TokenType::Do);
            }
            if s == utf16!("if") {
                return Some(TokenType::If);
            }
            if s == utf16!("in") {
                return Some(TokenType::In);
            }
        }
        3 => {
            if s == utf16!("for") {
                return Some(TokenType::For);
            }
            if s == utf16!("let") {
                return Some(TokenType::Let);
            }
            if s == utf16!("new") {
                return Some(TokenType::New);
            }
            if s == utf16!("try") {
                return Some(TokenType::Try);
            }
            if s == utf16!("var") {
                return Some(TokenType::Var);
            }
        }
        4 => {
            if s == utf16!("case") {
                return Some(TokenType::Case);
            }
            if s == utf16!("else") {
                return Some(TokenType::Else);
            }
            if s == utf16!("enum") {
                return Some(TokenType::Enum);
            }
            if s == utf16!("null") {
                return Some(TokenType::NullLiteral);
            }
            if s == utf16!("this") {
                return Some(TokenType::This);
            }
            if s == utf16!("true") {
                return Some(TokenType::BoolLiteral);
            }
            if s == utf16!("void") {
                return Some(TokenType::Void);
            }
            if s == utf16!("with") {
                return Some(TokenType::With);
            }
        }
        5 => {
            if s == utf16!("async") {
                return Some(TokenType::Async);
            }
            if s == utf16!("await") {
                return Some(TokenType::Await);
            }
            if s == utf16!("break") {
                return Some(TokenType::Break);
            }
            if s == utf16!("catch") {
                return Some(TokenType::Catch);
            }
            if s == utf16!("class") {
                return Some(TokenType::Class);
            }
            if s == utf16!("const") {
                return Some(TokenType::Const);
            }
            if s == utf16!("false") {
                return Some(TokenType::BoolLiteral);
            }
            if s == utf16!("super") {
                return Some(TokenType::Super);
            }
            if s == utf16!("throw") {
                return Some(TokenType::Throw);
            }
            if s == utf16!("while") {
                return Some(TokenType::While);
            }
            if s == utf16!("yield") {
                return Some(TokenType::Yield);
            }
        }
        6 => {
            if s == utf16!("delete") {
                return Some(TokenType::Delete);
            }
            if s == utf16!("export") {
                return Some(TokenType::Export);
            }
            if s == utf16!("import") {
                return Some(TokenType::Import);
            }
            if s == utf16!("return") {
                return Some(TokenType::Return);
            }
            // NB: "static" is intentionally NOT lexed as TokenType::Static.
            // C++ lexes it as Identifier and handles it contextually in class parsing.
            if s == utf16!("switch") {
                return Some(TokenType::Switch);
            }
            if s == utf16!("typeof") {
                return Some(TokenType::Typeof);
            }
        }
        7 => {
            if s == utf16!("default") {
                return Some(TokenType::Default);
            }
            if s == utf16!("extends") {
                return Some(TokenType::Extends);
            }
            if s == utf16!("finally") {
                return Some(TokenType::Finally);
            }
        }
        8 => {
            if s == utf16!("continue") {
                return Some(TokenType::Continue);
            }
            if s == utf16!("debugger") {
                return Some(TokenType::Debugger);
            }
            if s == utf16!("function") {
                return Some(TokenType::Function);
            }
        }
        10 => {
            if s == utf16!("instanceof") {
                return Some(TokenType::Instanceof);
            }
        }
        _ => {}
    }
    None
}


impl<'a> Lexer<'a> {
    pub fn new(source: &'a [u16], line_number: u32, line_column: u32) -> Self {
        let mut lexer = Lexer {
            source,
            position: 0,
            current_code_unit: 0,
            eof: false,
            line_number,
            line_column,
            current_token_type: TokenType::Eof,
            regex_is_in_character_class: false,
            allow_html_comments: true,
            template_states: Vec::new(),
            saved_states: Vec::new(),
        };
        lexer.consume();
        lexer
    }

    pub fn new_at_offset(
        source: &'a [u16],
        offset: usize,
        line_number: u32,
        line_column: u32,
    ) -> Self {
        let mut lexer = Lexer {
            source,
            position: offset,
            current_code_unit: 0,
            eof: false,
            line_number,
            line_column,
            current_token_type: TokenType::Eof,
            regex_is_in_character_class: false,
            allow_html_comments: true,
            template_states: Vec::new(),
            saved_states: Vec::new(),
        };
        lexer.consume();
        lexer
    }

    fn current_template_state(&self) -> &TemplateState {
        self.template_states
            .last()
            .expect("template_states must not be empty")
    }

    fn current_template_state_mut(&mut self) -> &mut TemplateState {
        self.template_states
            .last_mut()
            .expect("template_states must not be empty")
    }

    // https://tc39.es/ecma262/#sec-html-like-comments
    // HTML-like comments are only recognized in Script (not Module) code.
    pub fn disallow_html_comments(&mut self) {
        self.allow_html_comments = false;
    }

    fn source_len(&self) -> usize {
        self.source.len()
    }

    #[inline(always)]
    fn consume(&mut self) {
        if self.position > self.source_len() {
            return;
        }

        if self.position >= self.source_len() {
            self.eof = true;
            self.current_code_unit = 0;
            self.position = self.source_len() + 1;
            self.line_column += 1;
            return;
        }

        if self.is_line_terminator() {
            let second_char_of_crlf = self.position > 1
                && self.source[self.position - 2] == ch(b'\r')
                && self.current_code_unit == ch(b'\n');

            if !second_char_of_crlf {
                self.line_number += 1;
                self.line_column = 1;
            }
        } else {
            if is_utf16_high_surrogate(self.current_code_unit)
                && self.position < self.source_len()
                && is_utf16_low_surrogate(self.source[self.position])
            {
                self.position += 1;
                if self.position >= self.source_len() {
                    self.eof = true;
                    self.current_code_unit = 0;
                    self.position = self.source_len() + 1;
                    self.line_column += 1;
                    return;
                }
            }
            self.line_column += 1;
        }

        self.current_code_unit = self.source[self.position];
        self.position += 1;
    }

    fn current_code_point(&self) -> u32 {
        if self.position == 0 {
            return 0xFFFD;
        }
        let (cp, _) = decode_code_point(self.source, self.position - 1);
        cp
    }

    #[inline(always)]
    fn is_eof(&self) -> bool {
        self.eof
    }

    #[inline(always)]
    fn is_line_terminator(&self) -> bool {
        let cu = self.current_code_unit;
        if cu == ch(b'\n') || cu == ch(b'\r') {
            return true;
        }
        if is_ascii(cu) {
            return false;
        }
        // All line terminators are BMP, so no surrogate pair decoding needed.
        is_line_terminator_cp(self.current_code_unit as u32)
    }

    #[inline(always)]
    fn is_whitespace(&self) -> bool {
        if is_ascii_space(self.current_code_unit) {
            return true;
        }
        if is_ascii(self.current_code_unit) {
            return false;
        }
        // All whitespace characters are BMP, so no surrogate pair decoding needed.
        is_whitespace_cp(self.current_code_unit as u32)
    }

    /// Try to parse a unicode escape sequence at the current position.
    /// Returns (code_point, number_of_code_units_consumed) if successful.
    ///
    /// https://tc39.es/ecma262/#sec-names-and-keywords
    /// UnicodeEscapeSequence :: `u` Hex4Digits | `u{` CodePoint `}`
    fn is_identifier_unicode_escape(&self) -> Option<(u32, usize)> {
        // Current code unit should be '\', and we look ahead from position - 1
        let start = self.position - 1;
        if start >= self.source_len() {
            return None;
        }
        // The current code unit is already consumed (it's '\'), so we look at source[start..]
        // which starts after the backslash. Actually, position-1 points to the current_code_unit.
        // So source[position-1] == '\\'. We need 'u' at source[position].
        let pos = self.position;
        if pos >= self.source_len() {
            return None;
        }
        if self.source[pos] != ch(b'u') {
            return None;
        }
        let pos = pos + 1;
        if pos >= self.source_len() {
            return None;
        }

        if self.source[pos] == ch(b'{') {
            let mut cp: u32 = 0;
            let mut i = pos + 1;
            if i >= self.source_len() {
                return None;
            }
            while i < self.source_len() && self.source[i] != ch(b'}') {
                let cu = self.source[i];
                if !is_ascii_hex_digit(cu) {
                    return None;
                }
                cp = cp * 16 + hex_value(cu);
                if cp > 0x10FFFF {
                    return None;
                }
                i += 1;
            }
            if i >= self.source_len() || self.source[i] != ch(b'}') {
                return None;
            }
            let consumed = i + 1 - (self.position - 1);
            Some((cp, consumed))
        } else {
            if pos + 4 > self.source_len() {
                return None;
            }
            let mut cp: u32 = 0;
            for i in 0..4 {
                let cu = self.source[pos + i];
                if !is_ascii_hex_digit(cu) {
                    return None;
                }
                cp = cp * 16 + hex_value(cu);
            }
            let consumed = pos + 4 - (self.position - 1);
            Some((cp, consumed))
        }
    }

    /// Check if a multi-code-unit identifier character is a surrogate pair
    /// (a real multi-code-unit character) rather than a unicode escape sequence.
    fn is_surrogate_pair(&self, len: usize) -> bool {
        // Surrogate pairs consume exactly 2 code units; escape sequences
        // consume more (e.g., \uXXXX = 6, \u{XXXX} = 4+).
        len == 2
    }

    /// Scan an identifier body (after the start character has been matched).
    /// Consumes identifier-continue characters and detects unicode escapes.
    /// Returns true if the identifier contains escape sequences.
    fn scan_identifier_body(&mut self, initial_len: usize) -> bool {
        // ASCII fast path: when the start character was a plain ASCII char
        // (initial_len == 1), use a tight loop that bypasses consume() overhead.
        if initial_len == 1 {
            // Advance past the start character (simple ASCII, no LT/surrogate checks).
            self.line_column += 1;
            if self.position < self.source.len() {
                self.current_code_unit = self.source[self.position];
                self.position += 1;
            } else {
                self.eof = true;
                self.current_code_unit = 0;
                self.position = self.source.len() + 1;
                self.line_column += 1;
                return false;
            }

            // Tight ASCII identifier loop.
            loop {
                let cu = self.current_code_unit;
                if cu < 128 && ASCII_ID_CONTINUE[cu as usize] {
                    self.line_column += 1;
                    if self.position < self.source.len() {
                        self.current_code_unit = self.source[self.position];
                        self.position += 1;
                    } else {
                        self.eof = true;
                        self.current_code_unit = 0;
                        self.position = self.source.len() + 1;
                        self.line_column += 1;
                        return false;
                    }
                } else if cu == b'\\' as u16 || cu >= 128 {
                    // Non-ASCII or escape — check if it continues the identifier.
                    if let Some((_cp, len)) = self.is_identifier_middle() {
                        return self.scan_identifier_body_slow(len);
                    }
                    break;
                } else {
                    break;
                }
            }
            return false;
        }

        self.scan_identifier_body_slow(initial_len)
    }

    /// Slow path for identifier scanning: handles unicode escapes, surrogate pairs,
    /// and non-ASCII identifier characters.
    fn scan_identifier_body_slow(&mut self, initial_len: usize) -> bool {
        let mut has_escape = false;
        let mut ident_len = initial_len;
        loop {
            let is_pair = self.is_surrogate_pair(ident_len);
            has_escape |= ident_len > 1 && !is_pair;
            let consume_count = if is_pair { 1 } else { ident_len };
            for _ in 0..consume_count {
                self.consume();
            }
            if let Some((_next_cp, next_len)) = self.is_identifier_middle() {
                ident_len = next_len;
            } else {
                break;
            }
        }
        has_escape
    }

    /// Re-scan the source from `scan_start` (1-based position) to `self.position`
    /// and build a decoded identifier value. Only called when escapes are present.
    fn build_identifier_value(&self, scan_start: usize) -> Utf16String {
        let raw = &self.source[scan_start - 1..self.position - 1];
        let mut result = Utf16String(Vec::with_capacity(raw.len()));
        let mut i = 0;
        while i < raw.len() {
            if raw[i] == ch(b'\\') {
                i += 1; // skip '\'
                if i < raw.len() && raw[i] == ch(b'u') {
                    i += 1; // skip 'u'
                    if i < raw.len() && raw[i] == ch(b'{') {
                        i += 1; // skip '{'
                        let mut cp: u32 = 0;
                        while i < raw.len() && raw[i] != ch(b'}') {
                            cp = cp * 16 + hex_value(raw[i]);
                            i += 1;
                        }
                        if i < raw.len() {
                            i += 1; // skip '}'
                        }
                        encode_utf16(cp, &mut result.0);
                    } else {
                        let mut cp: u32 = 0;
                        for _ in 0..4 {
                            if i < raw.len() {
                                cp = cp * 16 + hex_value(raw[i]);
                                i += 1;
                            }
                        }
                        encode_utf16(cp, &mut result.0);
                    }
                }
            } else {
                result.0.push(raw[i]);
                i += 1;
            }
        }
        result
    }

    /// Check if the current position starts an identifier start character.
    /// Returns (code_point, number_of_code_units_to_consume).
    fn is_identifier_start(&self) -> Option<(u32, usize)> {
        let cp = self.current_code_point();
        if cp == '\\' as u32 {
            if let Some((escaped_cp, len)) = self.is_identifier_unicode_escape()
                && is_identifier_start_cp(escaped_cp)
            {
                return Some((escaped_cp, len));
            }
            return None;
        }

        if is_identifier_start_cp(cp) {
            let len = if cp > 0xFFFF { 2 } else { 1 };
            return Some((cp, len));
        }

        None
    }

    /// Check if the current position continues an identifier.
    /// Returns (code_point, number_of_code_units_to_consume).
    fn is_identifier_middle(&self) -> Option<(u32, usize)> {
        let cp = self.current_code_point();
        if cp == '\\' as u32 {
            if let Some((escaped_cp, len)) = self.is_identifier_unicode_escape()
                && is_identifier_continue_cp(escaped_cp)
            {
                return Some((escaped_cp, len));
            }
            return None;
        }

        if is_identifier_continue_cp(cp) {
            let len = if cp > 0xFFFF { 2 } else { 1 };
            return Some((cp, len));
        }

        None
    }

    /// Classify an identifier token: check for keywords and escaped keywords.
    fn classify_identifier(
        &self,
        has_escape: bool,
        value_start: usize,
        token_type: &mut TokenType,
        identifier_value: &mut Option<Utf16String>,
    ) {
        if has_escape {
            let decoded = self.build_identifier_value(value_start);
            if keyword_from_str(&decoded).is_some() {
                *token_type = TokenType::EscapedKeyword;
            } else {
                *token_type = TokenType::Identifier;
            }
            *identifier_value = Some(decoded);
        } else {
            let source_slice = &self.source[value_start - 1..self.position - 1];
            if let Some(kw) = keyword_from_str(source_slice) {
                *token_type = kw;
            } else {
                *token_type = TokenType::Identifier;
            }
        }
    }

    fn match2(&self, a: u16, b: u16) -> bool {
        if self.position >= self.source_len() {
            return false;
        }
        self.current_code_unit == a && self.source[self.position] == b
    }

    fn match3(&self, a: u16, b: u16, c: u16) -> bool {
        if self.position + 1 >= self.source_len() {
            return false;
        }
        self.current_code_unit == a
            && self.source[self.position] == b
            && self.source[self.position + 1] == c
    }

    fn match4(&self, a: u16, b: u16, c: u16, d: u16) -> bool {
        if self.position + 2 >= self.source_len() {
            return false;
        }
        self.current_code_unit == a
            && self.source[self.position] == b
            && self.source[self.position + 1] == c
            && self.source[self.position + 2] == d
    }

    fn match_numeric_literal_separator_followed_by(&self, check: fn(u16) -> bool) -> bool {
        if self.position >= self.source_len() {
            return false;
        }
        self.current_code_unit == ch(b'_') && check(self.source[self.position])
    }

    // https://tc39.es/ecma262/#sec-comments
    // SingleLineComment :: `//` SingleLineCommentChars?
    //
    // https://tc39.es/ecma262/#sec-html-like-comments
    // SingleLineHTMLOpenComment :: `<!--` ...
    // SingleLineHTMLCloseComment :: [LT] WhiteSpace? `-->` ...
    // (HTML-like comments are only allowed in scripts, not modules.)
    //
    // https://tc39.es/ecma262/#sec-hashbang
    // HashbangComment :: `#!` SingleLineCommentChars?
    fn is_line_comment_start(&self, line_has_token_yet: bool) -> bool {
        self.match2(ch(b'/'), ch(b'/'))
            || (self.allow_html_comments && self.match4(ch(b'<'), ch(b'!'), ch(b'-'), ch(b'-')))
            || (self.allow_html_comments
                && !line_has_token_yet
                && self.match3(ch(b'-'), ch(b'-'), ch(b'>')))
            || (self.match2(ch(b'#'), ch(b'!')) && self.position == 1)
    }

    fn is_block_comment_start(&self) -> bool {
        self.match2(ch(b'/'), ch(b'*'))
    }

    fn is_block_comment_end(&self) -> bool {
        self.match2(ch(b'*'), ch(b'/'))
    }

    fn is_numeric_literal_start(&self) -> bool {
        is_ascii_digit(self.current_code_unit)
            || (self.current_code_unit == ch(b'.')
                && self.position < self.source_len()
                && is_ascii_digit(self.source[self.position]))
    }

    // https://tc39.es/ecma262/#sec-lexical-and-regexp-grammars
    // The InputElementRegExp and InputElementRegExpOrTemplateTail goals are
    // used in syntactic contexts where a RegularExpressionLiteral is permitted.
    // The slash is a division operator when the preceding token could end an
    // expression; otherwise it starts a regex literal.
    fn slash_means_division(&self) -> bool {
        let tt = self.current_token_type;
        tt.is_identifier_name()
            || tt == TokenType::BigIntLiteral
            || tt == TokenType::BracketClose
            || tt == TokenType::CurlyClose
            || tt == TokenType::MinusMinus
            || tt == TokenType::NumericLiteral
            || tt == TokenType::ParenClose
            || tt == TokenType::PlusPlus
            || tt == TokenType::PrivateIdentifier
            || tt == TokenType::RegexLiteral
            || tt == TokenType::StringLiteral
            || tt == TokenType::TemplateLiteralEnd
    }

    fn consume_decimal_number(&mut self) -> bool {
        if !is_ascii_digit(self.current_code_unit) {
            return false;
        }
        while is_ascii_digit(self.current_code_unit)
            || self.match_numeric_literal_separator_followed_by(is_ascii_digit)
        {
            self.consume();
        }
        true
    }

    fn consume_exponent(&mut self) -> bool {
        self.consume();
        if self.current_code_unit == ch(b'-') || self.current_code_unit == ch(b'+') {
            self.consume();
        }
        if !is_ascii_digit(self.current_code_unit) {
            return false;
        }
        self.consume_decimal_number()
    }

    fn consume_octal_number(&mut self) -> bool {
        self.consume();
        if !is_octal_digit(self.current_code_unit) {
            return false;
        }
        while is_octal_digit(self.current_code_unit)
            || self.match_numeric_literal_separator_followed_by(is_octal_digit)
        {
            self.consume();
        }
        true
    }

    fn consume_hexadecimal_number(&mut self) -> bool {
        self.consume();
        if !is_ascii_hex_digit(self.current_code_unit) {
            return false;
        }
        while is_ascii_hex_digit(self.current_code_unit)
            || self.match_numeric_literal_separator_followed_by(is_ascii_hex_digit)
        {
            self.consume();
        }
        true
    }

    fn try_consume_bigint_suffix(&mut self, token_type: &mut TokenType) {
        if self.current_code_unit == ch(b'n') {
            self.consume();
            *token_type = TokenType::BigIntLiteral;
        }
    }

    fn consume_binary_number(&mut self) -> bool {
        self.consume();
        if !is_binary_digit(self.current_code_unit) {
            return false;
        }
        while is_binary_digit(self.current_code_unit)
            || self.match_numeric_literal_separator_followed_by(is_binary_digit)
        {
            self.consume();
        }
        true
    }

    fn consume_regex_literal(&mut self) -> TokenType {
        self.regex_is_in_character_class = false;
        while !self.is_eof() {
            if self.is_line_terminator()
                || (!self.regex_is_in_character_class && self.current_code_unit == ch(b'/'))
            {
                break;
            }

            if self.current_code_unit == ch(b'[') {
                self.regex_is_in_character_class = true;
            } else if self.current_code_unit == ch(b']') {
                self.regex_is_in_character_class = false;
            }

            if self.match2(ch(b'\\'), ch(b'/'))
                || self.match2(ch(b'\\'), ch(b'['))
                || self.match2(ch(b'\\'), ch(b'\\'))
                || (self.regex_is_in_character_class && self.match2(ch(b'\\'), ch(b']')))
            {
                self.consume();
            }
            self.consume();
        }

        if self.current_code_unit == ch(b'/') {
            self.consume();
            TokenType::RegexLiteral
        } else {
            TokenType::UnterminatedRegexLiteral
        }
    }

    #[allow(clippy::should_implement_trait)]
    pub fn next(&mut self) -> Token {
        let trivia_start = self.position;
        let in_template = !self.template_states.is_empty();
        let mut line_has_token_yet = self.line_column > 1;
        let mut unterminated_comment = false;
        let mut token_message: Option<String> = None;

        if !in_template || self.current_template_state().in_expression {
            // Fast skip: if current char is ASCII and not whitespace/LT/slash/hash/dash/lt,
            // there's no trivia to consume. This avoids the is_line_terminator() and
            // is_whitespace() calls for the common case in minified JS.
            let cu = self.current_code_unit;
            if cu >= 128
                || matches!(
                    cu as u8,
                    b'\t' | b'\n' | 0x0B | 0x0C | b'\r' | b' ' | b'/' | b'<' | b'-' | b'#'
                )
            {
                loop {
                    if self.is_line_terminator() {
                        line_has_token_yet = false;
                        loop {
                            self.consume();
                            if !self.is_line_terminator() {
                                break;
                            }
                        }
                    } else if self.is_whitespace() {
                        // ASCII non-LT whitespace fast path (space, tab, VT, FF).
                        while !self.eof
                            && self.current_code_unit < 128
                            && matches!(self.current_code_unit as u8, b' ' | b'\t' | 0x0B | 0x0C)
                        {
                            self.line_column += 1;
                            if self.position < self.source.len() {
                                self.current_code_unit = self.source[self.position];
                                self.position += 1;
                            } else {
                                self.eof = true;
                                self.current_code_unit = 0;
                                self.position = self.source.len() + 1;
                                self.line_column += 1;
                                break;
                            }
                        }
                        // Fall back to general loop for remaining non-ASCII whitespace.
                        while self.is_whitespace() {
                            self.consume();
                        }
                    } else if self.is_line_comment_start(line_has_token_yet) {
                        self.consume();
                        // ASCII line comment fast path: skip non-LT ASCII chars directly.
                        while !self.eof
                            && self.current_code_unit < 128
                            && self.current_code_unit != b'\n' as u16
                            && self.current_code_unit != b'\r' as u16
                        {
                            self.line_column += 1;
                            if self.position < self.source.len() {
                                self.current_code_unit = self.source[self.position];
                                self.position += 1;
                            } else {
                                self.eof = true;
                                self.current_code_unit = 0;
                                self.position = self.source.len() + 1;
                                self.line_column += 1;
                                break;
                            }
                        }
                        // Fall back for non-ASCII (LS/PS line terminators).
                        loop {
                            if self.is_eof() || self.is_line_terminator() {
                                break;
                            }
                            self.consume();
                        }
                    } else if self.is_block_comment_start() {
                        let start_line_number = self.line_number;
                        self.consume();
                        loop {
                            self.consume();
                            if self.is_eof() || self.is_block_comment_end() {
                                break;
                            }
                        }
                        if self.is_eof() {
                            unterminated_comment = true;
                        }
                        self.consume(); // consume *
                        if self.is_eof() {
                            unterminated_comment = true;
                        }
                        self.consume(); // consume /

                        if start_line_number != self.line_number {
                            line_has_token_yet = false;
                        }
                    } else {
                        break;
                    }
                }
            } // end fast-skip if
        }

        let value_start = self.position;
        let value_start_line_number = self.line_number;
        let value_start_column_number = self.line_column;
        let mut token_type = TokenType::Invalid;
        let did_consume_whitespace_or_comments = trivia_start != value_start;

        let mut identifier_value: Option<Utf16String> = None;

        if self.current_token_type == TokenType::RegexLiteral
            && !self.is_eof()
            && (self.current_code_unit < 128
                && (self.current_code_unit as u8 as char).is_ascii_alphabetic())
            && !did_consume_whitespace_or_comments
        {
            token_type = TokenType::RegexFlags;
            while !self.is_eof()
                && self.current_code_unit < 128
                && (self.current_code_unit as u8 as char).is_ascii_alphabetic()
            {
                self.consume();
            }
        } else if self.current_code_unit == ch(b'`') {
            self.consume();
            if !in_template {
                token_type = TokenType::TemplateLiteralStart;
                self.template_states.push(TemplateState {
                    in_expression: false,
                    open_bracket_count: 0,
                });
            } else if self.current_template_state().in_expression {
                self.template_states.push(TemplateState {
                    in_expression: false,
                    open_bracket_count: 0,
                });
                token_type = TokenType::TemplateLiteralStart;
            } else {
                self.template_states.pop();
                token_type = TokenType::TemplateLiteralEnd;
            }
        } else if in_template
            && self.current_template_state().in_expression
            && self.current_template_state().open_bracket_count == 0
            && self.current_code_unit == ch(b'}')
        {
            self.consume();
            token_type = TokenType::TemplateLiteralExprEnd;
            self.current_template_state_mut().in_expression = false;
        } else if in_template && !self.current_template_state().in_expression {
            if self.is_eof() {
                token_type = TokenType::UnterminatedTemplateLiteral;
                self.template_states.pop();
            } else if self.match2(ch(b'$'), ch(b'{')) {
                token_type = TokenType::TemplateLiteralExprStart;
                self.consume();
                self.consume();
                self.current_template_state_mut().in_expression = true;
            } else {
                while !self.match2(ch(b'$'), ch(b'{'))
                    && self.current_code_unit != ch(b'`')
                    && !self.is_eof()
                {
                    if self.match2(ch(b'\\'), ch(b'$'))
                        || self.match2(ch(b'\\'), ch(b'`'))
                        || self.match2(ch(b'\\'), ch(b'\\'))
                    {
                        self.consume();
                    }
                    self.consume();
                }
                if self.is_eof() && !self.template_states.is_empty() {
                    token_type = TokenType::UnterminatedTemplateLiteral;
                } else {
                    token_type = TokenType::TemplateLiteralString;
                }
            }
        } else if self.current_code_unit == ch(b'#') {
            self.consume();
            if let Some((_cp, len)) = self.is_identifier_start() {
                let has_escape = self.scan_identifier_body(len);
                if has_escape {
                    identifier_value = Some(self.build_identifier_value(value_start));
                }
                token_type = TokenType::PrivateIdentifier;
            } else {
                token_type = TokenType::Invalid;
                token_message = Some(
                    "Start of private name '#' but not followed by valid identifier".to_string(),
                );
            }
        } else if self.current_code_unit < 128
            && ASCII_ID_START[self.current_code_unit as usize]
        {
            // ASCII identifier fast path — skip is_identifier_start/current_code_point overhead.
            let has_escape = self.scan_identifier_body(1);
            self.classify_identifier(has_escape, value_start, &mut token_type, &mut identifier_value);
        } else if let Some((_cp, len)) = self.is_identifier_start() {
            let has_escape = self.scan_identifier_body(len);
            self.classify_identifier(has_escape, value_start, &mut token_type, &mut identifier_value);
        } else if self.is_numeric_literal_start() {
            token_type = TokenType::NumericLiteral;
            let mut is_invalid = false;
            if self.current_code_unit == ch(b'0') {
                self.consume();
                if self.current_code_unit == ch(b'.') {
                    self.consume();
                    while is_ascii_digit(self.current_code_unit) {
                        self.consume();
                    }
                    if self.current_code_unit == ch(b'e') || self.current_code_unit == ch(b'E') {
                        is_invalid = !self.consume_exponent();
                    }
                } else if self.current_code_unit == ch(b'e') || self.current_code_unit == ch(b'E') {
                    is_invalid = !self.consume_exponent();
                } else if self.current_code_unit == ch(b'o') || self.current_code_unit == ch(b'O') {
                    is_invalid = !self.consume_octal_number();
                    self.try_consume_bigint_suffix(&mut token_type);
                } else if self.current_code_unit == ch(b'b') || self.current_code_unit == ch(b'B') {
                    is_invalid = !self.consume_binary_number();
                    self.try_consume_bigint_suffix(&mut token_type);
                } else if self.current_code_unit == ch(b'x') || self.current_code_unit == ch(b'X') {
                    is_invalid = !self.consume_hexadecimal_number();
                    self.try_consume_bigint_suffix(&mut token_type);
                } else if self.current_code_unit == ch(b'n') {
                    self.try_consume_bigint_suffix(&mut token_type);
                } else if is_ascii_digit(self.current_code_unit) {
                    // Legacy octal without 0o prefix
                    loop {
                        self.consume();
                        if !is_ascii_digit(self.current_code_unit) {
                            break;
                        }
                    }
                }
            } else {
                while is_ascii_digit(self.current_code_unit)
                    || self.match_numeric_literal_separator_followed_by(is_ascii_digit)
                {
                    self.consume();
                }
                if self.current_code_unit == ch(b'n') {
                    self.consume();
                    token_type = TokenType::BigIntLiteral;
                } else {
                    if self.current_code_unit == ch(b'.') {
                        self.consume();
                        if self.current_code_unit == ch(b'_') {
                            is_invalid = true;
                        }
                        while is_ascii_digit(self.current_code_unit)
                            || self.match_numeric_literal_separator_followed_by(is_ascii_digit)
                        {
                            self.consume();
                        }
                    }
                    if (self.current_code_unit == ch(b'e') || self.current_code_unit == ch(b'E'))
                        && !self.consume_exponent()
                    {
                        is_invalid = true;
                    }
                }
            }
            if is_invalid {
                token_type = TokenType::Invalid;
                token_message = Some("Invalid numeric literal".to_string());
            }
        } else if self.current_code_unit == ch(b'"') || self.current_code_unit == ch(b'\'') {
            let stop_char = self.current_code_unit;
            self.consume();
            // ASCII string literal fast path: skip plain ASCII chars that aren't
            // the stop char, backslash, CR, or LF.
            while !self.eof
                && self.current_code_unit < 128
                && self.current_code_unit != stop_char
                && self.current_code_unit != b'\\' as u16
                && self.current_code_unit != b'\r' as u16
                && self.current_code_unit != b'\n' as u16
            {
                self.line_column += 1;
                if self.position < self.source.len() {
                    self.current_code_unit = self.source[self.position];
                    self.position += 1;
                } else {
                    self.eof = true;
                    self.current_code_unit = 0;
                    self.position = self.source.len() + 1;
                    self.line_column += 1;
                    break;
                }
            }
            // Fall back to general loop for escapes, non-ASCII, and terminators.
            while self.current_code_unit != stop_char
                && self.current_code_unit != ch(b'\r')
                && self.current_code_unit != ch(b'\n')
                && !self.is_eof()
            {
                if self.current_code_unit == ch(b'\\') {
                    self.consume();
                    if self.current_code_unit == ch(b'\r')
                        && self.position < self.source_len()
                        && self.source[self.position] == ch(b'\n')
                    {
                        self.consume();
                    }
                }
                self.consume();
                // Re-enter ASCII fast path after escape.
                while !self.eof
                    && self.current_code_unit < 128
                    && self.current_code_unit != stop_char
                    && self.current_code_unit != b'\\' as u16
                    && self.current_code_unit != b'\r' as u16
                    && self.current_code_unit != b'\n' as u16
                {
                    self.line_column += 1;
                    if self.position < self.source.len() {
                        self.current_code_unit = self.source[self.position];
                        self.position += 1;
                    } else {
                        self.eof = true;
                        self.current_code_unit = 0;
                        self.position = self.source.len() + 1;
                        self.line_column += 1;
                        break;
                    }
                }
            }
            if self.current_code_unit != stop_char {
                token_type = TokenType::UnterminatedStringLiteral;
            } else {
                self.consume();
                token_type = TokenType::StringLiteral;
            }
        } else if self.current_code_unit == ch(b'/') && !self.slash_means_division() {
            self.consume();
            token_type = self.consume_regex_literal();
        } else if self.eof {
            if unterminated_comment {
                token_type = TokenType::Invalid;
                token_message = Some("Unterminated multi-line comment".to_string());
            } else {
                token_type = TokenType::Eof;
            }
        } else if is_ascii(self.current_code_unit) {
            // Inline operator dispatch: handle each ASCII operator character directly
            // instead of cascading through match4/parse_three_char/parse_two_char/single_char.
            let next = if self.position < self.source.len() { self.source[self.position] } else { 0 };
            let next2 = if self.position + 1 < self.source.len() { self.source[self.position + 1] } else { 0 };
            match self.current_code_unit as u8 {
                b';' => { token_type = TokenType::Semicolon; self.consume(); }
                b',' => { token_type = TokenType::Comma; self.consume(); }
                b'(' => { token_type = TokenType::ParenOpen; self.consume(); }
                b')' => { token_type = TokenType::ParenClose; self.consume(); }
                b'[' => { token_type = TokenType::BracketOpen; self.consume(); }
                b']' => { token_type = TokenType::BracketClose; self.consume(); }
                b'{' => { token_type = TokenType::CurlyOpen; self.consume(); }
                b'}' => { token_type = TokenType::CurlyClose; self.consume(); }
                b':' => { token_type = TokenType::Colon; self.consume(); }
                b'~' => { token_type = TokenType::Tilde; self.consume(); }
                b'=' => {
                    if next == b'=' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::EqualsEqualsEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::EqualsEquals;
                        self.consume(); self.consume();
                    } else if next == b'>' as u16 {
                        token_type = TokenType::Arrow;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Equals;
                        self.consume();
                    }
                }
                b'!' => {
                    if next == b'=' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::ExclamationMarkEqualsEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::ExclamationMarkEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::ExclamationMark;
                        self.consume();
                    }
                }
                b'+' => {
                    if next == b'+' as u16 {
                        token_type = TokenType::PlusPlus;
                        self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::PlusEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Plus;
                        self.consume();
                    }
                }
                b'-' => {
                    if next == b'-' as u16 {
                        token_type = TokenType::MinusMinus;
                        self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::MinusEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Minus;
                        self.consume();
                    }
                }
                b'*' => {
                    if next == b'*' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::DoubleAsteriskEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'*' as u16 {
                        token_type = TokenType::DoubleAsterisk;
                        self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::AsteriskEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Asterisk;
                        self.consume();
                    }
                }
                b'/' => {
                    if next == b'=' as u16 {
                        token_type = TokenType::SlashEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Slash;
                        self.consume();
                    }
                }
                b'%' => {
                    if next == b'=' as u16 {
                        token_type = TokenType::PercentEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Percent;
                        self.consume();
                    }
                }
                b'&' => {
                    if next == b'&' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::DoubleAmpersandEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'&' as u16 {
                        token_type = TokenType::DoubleAmpersand;
                        self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::AmpersandEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Ampersand;
                        self.consume();
                    }
                }
                b'|' => {
                    if next == b'|' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::DoublePipeEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'|' as u16 {
                        token_type = TokenType::DoublePipe;
                        self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::PipeEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Pipe;
                        self.consume();
                    }
                }
                b'^' => {
                    if next == b'=' as u16 {
                        token_type = TokenType::CaretEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Caret;
                        self.consume();
                    }
                }
                b'<' => {
                    if next == b'<' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::ShiftLeftEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'<' as u16 {
                        token_type = TokenType::ShiftLeft;
                        self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::LessThanEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::LessThan;
                        self.consume();
                    }
                }
                b'>' => {
                    if next == b'>' as u16 && next2 == b'>' as u16 {
                        // Check for >>>= (4-char token)
                        let next3 = if self.position + 2 < self.source.len() { self.source[self.position + 2] } else { 0 };
                        if next3 == b'=' as u16 {
                            token_type = TokenType::UnsignedShiftRightEquals;
                            self.consume(); self.consume(); self.consume(); self.consume();
                        } else {
                            token_type = TokenType::UnsignedShiftRight;
                            self.consume(); self.consume(); self.consume();
                        }
                    } else if next == b'>' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::ShiftRightEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'>' as u16 {
                        token_type = TokenType::ShiftRight;
                        self.consume(); self.consume();
                    } else if next == b'=' as u16 {
                        token_type = TokenType::GreaterThanEquals;
                        self.consume(); self.consume();
                    } else {
                        token_type = TokenType::GreaterThan;
                        self.consume();
                    }
                }
                b'?' => {
                    if next == b'?' as u16 && next2 == b'=' as u16 {
                        token_type = TokenType::DoubleQuestionMarkEquals;
                        self.consume(); self.consume(); self.consume();
                    } else if next == b'?' as u16 {
                        token_type = TokenType::DoubleQuestionMark;
                        self.consume(); self.consume();
                    } else if next == b'.' as u16 {
                        // https://tc39.es/ecma262/#sec-punctuators
                        // OptionalChainingPunctuator :: `?.` [lookahead ∉ DecimalDigit]
                        if !is_ascii_digit(next2) {
                            token_type = TokenType::QuestionMarkPeriod;
                            self.consume(); self.consume();
                        } else {
                            token_type = TokenType::QuestionMark;
                            self.consume();
                        }
                    } else {
                        token_type = TokenType::QuestionMark;
                        self.consume();
                    }
                }
                b'.' => {
                    if next == b'.' as u16 && next2 == b'.' as u16 {
                        token_type = TokenType::TripleDot;
                        self.consume(); self.consume(); self.consume();
                    } else {
                        token_type = TokenType::Period;
                        self.consume();
                    }
                }
                _ => {
                    token_type = TokenType::Invalid;
                    self.consume();
                }
            }
        } else {
            token_type = TokenType::Invalid;
            self.consume();
        }

        if !self.template_states.is_empty() && self.current_template_state().in_expression {
            if token_type == TokenType::CurlyOpen {
                self.current_template_state_mut().open_bracket_count += 1;
            } else if token_type == TokenType::CurlyClose {
                self.current_template_state_mut().open_bracket_count = self
                    .current_template_state()
                    .open_bracket_count
                    .saturating_sub(1);
            }
        }

        self.current_token_type = token_type;

        let trivia_has_line_terminator = if trivia_start > 0 && value_start > trivia_start {
            self.source[trivia_start - 1..value_start - 1]
                .iter()
                .any(|&cu| {
                    cu == ch(b'\n')
                        || cu == ch(b'\r')
                        || cu == LINE_SEPARATOR
                        || cu == PARAGRAPH_SEPARATOR
                })
        } else {
            false
        };

        Token {
            token_type,
            trivia_start: u32_from_usize(trivia_start.saturating_sub(1)),
            trivia_len: u32_from_usize(value_start - trivia_start),
            value_start: u32_from_usize(value_start.saturating_sub(1)),
            value_len: u32_from_usize(self.position - value_start),
            line_number: value_start_line_number,
            line_column: value_start_column_number,
            offset: u32_from_usize(value_start.saturating_sub(1)),
            trivia_has_line_terminator,
            identifier_value,
            message: token_message,
        }
    }

    // === State accessors for parser save/restore ===

    pub fn position(&self) -> usize {
        self.position
    }

    pub fn line_number(&self) -> u32 {
        self.line_number
    }

    pub fn line_column(&self) -> u32 {
        self.line_column
    }

    pub fn restore(&mut self, position: usize, line_number: u32, line_column: u32) {
        self.position = position;
        self.line_number = line_number;
        self.line_column = line_column;
        self.eof = position > self.source_len();
        // position is one past current_code_unit, so restore from position - 1
        if position > 0 && position <= self.source_len() {
            self.current_code_unit = self.source[position - 1];
        } else {
            self.current_code_unit = 0;
        }
    }

    pub fn save_state(&mut self) {
        self.saved_states.push(SavedLexerState {
            position: self.position,
            current_code_unit: self.current_code_unit,
            eof: self.eof,
            line_number: self.line_number,
            line_column: self.line_column,
            current_token_type: self.current_token_type,
            template_states: self.template_states.clone(),
        });
    }

    pub fn load_state(&mut self) {
        let state = self.saved_states.pop().expect("No saved lexer state");
        self.position = state.position;
        self.current_code_unit = state.current_code_unit;
        self.eof = state.eof;
        self.line_number = state.line_number;
        self.line_column = state.line_column;
        self.current_token_type = state.current_token_type;
        self.template_states = state.template_states;
    }

    pub fn discard_saved_state(&mut self) {
        self.saved_states.pop();
    }

    /// Re-lex the current Slash or SlashEquals token as a regex literal.
    pub fn force_slash_as_regex(&mut self) -> Token {
        let has_equals = self.current_token_type == TokenType::SlashEquals;

        assert!(self.position > 0);
        let mut value_start = self.position - 1;

        // Capture line position at the start of the '/' token, before consuming the regex body.
        let token_line_number = self.line_number;
        let mut token_line_column = self.line_column.saturating_sub(1);

        if has_equals {
            value_start -= 1;
            self.position -= 1;
            self.current_code_unit = ch(b'=');
            token_line_column = token_line_column.saturating_sub(1);
        }

        let token_type = self.consume_regex_literal();
        self.current_token_type = token_type;

        Token {
            token_type,
            trivia_start: 0,
            trivia_len: 0,
            value_start: u32_from_usize(value_start.saturating_sub(1)),
            value_len: u32_from_usize(self.position - value_start),
            line_number: token_line_number,
            line_column: token_line_column,
            offset: u32_from_usize(value_start.saturating_sub(1)),
            trivia_has_line_terminator: false,
            identifier_value: None,
            message: None,
        }
    }
}

fn hex_value(cu: u16) -> u32 {
    match cu {
        0x30..=0x39 => (cu - 0x30) as u32,
        0x41..=0x46 => (cu - 0x41 + 10) as u32,
        0x61..=0x66 => (cu - 0x61 + 10) as u32,
        _ => unreachable!("hex_value is only called for validated hex digits"),
    }
}

fn encode_utf16(cp: u32, buffer: &mut Vec<u16>) {
    if cp <= 0xFFFF {
        buffer.push(cp as u16);
    } else {
        let cp = cp - 0x10000;
        buffer.push((0xD800 + (cp >> 10)) as u16);
        buffer.push((0xDC00 + (cp & 0x3FF)) as u16);
    }
}
