#!/usr/bin/env python3
"""
t900cc.py — C to TLCS-900 Assembly Compiler v1
NGPCraft Toolchain, Jalon 3

Compiles a subset of C (C89/C99 subset) to .asm files in t900as.py syntax.
ABI v1 (__cdecl): args pushed right-to-left, return in WA(16-bit)/XWA(32-bit)/A(8-bit).
Frame pointer: XIY (via LINK XIY, N / UNLK XIY). Callee-saved: XIZ, XWA, XBC, XDE, XHL.

Usage:
    python3 t900cc.py input.c -o output.asm

Supported C subset v1:
- Types: void, char, unsigned char, short, unsigned short, int, unsigned int,
         long, unsigned long, u8, u16, u32 (via typedef)
- Functions: definitions and extern declarations
- Statements: if/else, while, for, return, expression statements
- Expressions: arithmetic, bitwise, comparison, assignment, function calls,
               array subscript, pointer dereference, address-of, cast
- Globals: static (BSS or initialized)
- #define (simple value substitutes), #include (skipped)
"""

import sys
import os
import re
import argparse
from dataclasses import dataclass, field, fields
from typing import List, Optional, Dict, Tuple, Any

# Chantier 4 Phase P-1: IR buffer for codegen (binary-identical no-op
# wrapper around the existing emit_* pipeline). See BACKEND_DESIGN.md.
# Chantier 5 Phase P-5.1 (2026-05-20): replaced flat IRBuffer with
# block-level IRFunction. See CHANTIER_5_PLAN.md §P-5.1.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t900cc_ir import (  # noqa: E402
    EmitRaw, IRBuffer, IRFunction, BasicBlock, lower_to_asm,
    LoadImm, LoadLocal, LoadGlobal, StoreLocal, BinOp as IRBinOp,
)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class Type:
    pass

@dataclass
class VoidType(Type):
    def __repr__(self): return "void"
    def size(self): return 0

@dataclass
class IntType(Type):
    nbytes: int    # 1, 2, 4
    signed: bool = False
    def __repr__(self):
        sign = "" if self.signed else "u"
        return f"{sign}int{self.nbytes*8}"
    def size(self): return self.nbytes

@dataclass
class PtrType(Type):
    base: Type
    far: bool = False
    def __repr__(self): return f"{'far_' if self.far else ''}ptr({self.base})"
    def size(self): return 4  # all pointers are 32-bit in v1 (NGPC ABI)

@dataclass
class ArrayType(Type):
    elem: Type
    count: int
    def __repr__(self): return f"array[{self.count}]({self.elem})"
    def size(self): return self.elem.size() * self.count

@dataclass
class StructField:
    name: str
    type_: 'Type'
    offset: int

@dataclass
class StructType(Type):
    tag: str
    fields: List['StructField']
    _size: int
    def __repr__(self): return f"struct_{self.tag}"
    def size(self): return self._size

# Canonical type singletons
VOID    = VoidType()
U8      = IntType(1, False)
U16     = IntType(2, False)
U32     = IntType(4, False)
I8      = IntType(1, True)
I16     = IntType(2, True)
I32     = IntType(4, True)

# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

TK_EOF       = 'EOF'
TK_IDENT     = 'IDENT'
TK_NUMBER    = 'NUMBER'
TK_STRING    = 'STRING'
TK_CHAR      = 'CHAR'
TK_PUNCT     = 'PUNCT'
TK_KEYWORD   = 'KEYWORD'

KEYWORDS = {
    'void', 'char', 'short', 'int', 'long',
    'unsigned', 'signed', 'const', 'volatile', 'static', 'extern', 'register',
    'typedef', 'return', 'if', 'else', 'while', 'for', 'do',
    'break', 'continue', 'sizeof', 'goto',
    'struct', 'union', 'enum',
    'switch', 'case', 'default',
    '__interrupt',
}

@dataclass
class Token:
    kind: str
    value: Any
    line: int

class LexError(Exception):
    def __init__(self, msg, line):
        super().__init__(f"Lex error line {line}: {msg}")

def lex(source: str, filename: str = '<stdin>') -> List[Token]:
    """Tokenize C source, handling comments and preprocessor directives."""
    tokens = []
    i = 0
    line = 1
    defines: Dict[str, str] = {}  # simple #define name value

    n = len(source)

    def peek(offset=0):
        pos = i + offset
        return source[pos] if pos < n else ''

    def advance():
        nonlocal i, line
        c = source[i]
        i += 1
        if c == '\n':
            line += 1
        return c

    while i < n:
        c = source[i]

        # Newline / whitespace
        if c in ' \t\r\n':
            advance()
            continue

        # Line comment
        if c == '/' and peek(1) == '/':
            while i < n and source[i] != '\n':
                i += 1
            continue

        # Block comment
        if c == '/' and peek(1) == '*':
            start_line = line
            i += 2
            while i < n:
                if source[i] == '*' and i+1 < n and source[i+1] == '/':
                    i += 2
                    break
                if source[i] == '\n':
                    line += 1
                i += 1
            else:
                raise LexError("Unterminated block comment", start_line)
            continue

        # Preprocessor directives
        if c == '#':
            # Read until end of logical line (handle line continuation)
            directive_line = line
            i += 1
            # skip whitespace
            while i < n and source[i] in ' \t':
                i += 1
            # read directive name
            dir_start = i
            while i < n and source[i].isalpha():
                i += 1
            directive = source[dir_start:i]
            # read rest of line
            while i < n and source[i] in ' \t':
                i += 1
            rest_start = i
            while i < n and source[i] != '\n':
                i += 1
            rest = source[rest_start:i].strip()

            if directive == 'define':
                # Simple: #define NAME VALUE
                parts = rest.split(None, 1)
                if parts and '(' not in parts[0]:
                    name = parts[0]
                    val = parts[1] if len(parts) > 1 else ''
                    defines[name] = val
            # #include, #ifndef, #ifdef, #endif, #pragma: skip
            continue

        # String literal
        if c == '"':
            start_line = line
            i += 1
            s = []
            while i < n and source[i] != '"':
                ch = source[i]
                if ch == '\\':
                    i += 1
                    esc = source[i] if i < n else ''
                    escmap = {'n':'\n','t':'\t','r':'\r','\\':'\\','"':'"',"'":'\'','0':'\0'}
                    s.append(escmap.get(esc, esc))
                else:
                    if ch == '\n': line += 1
                    s.append(ch)
                i += 1
            if i >= n:
                raise LexError("Unterminated string", start_line)
            i += 1  # closing "
            tokens.append(Token(TK_STRING, ''.join(s), start_line))
            continue

        # Char literal
        if c == "'":
            i += 1
            if source[i] == '\\':
                i += 1
                esc = source[i]
                escmap = {'n':'\n','t':'\t','r':'\r','\\':'\\','"':'"',"'":'\'','0':'\0'}
                ch = ord(escmap.get(esc, esc))
            else:
                ch = ord(source[i])
            i += 1
            if i < n and source[i] == "'":
                i += 1
            tokens.append(Token(TK_NUMBER, ch, line))
            continue

        # Number
        if c.isdigit() or (c == '0' and peek(1) in 'xX'):
            start = i
            if c == '0' and peek(1) in 'xX':
                i += 2
                while i < n and source[i] in '0123456789abcdefABCDEF':
                    i += 1
                val = int(source[start:i], 16)
            else:
                while i < n and source[i].isdigit():
                    i += 1
                val = int(source[start:i])
            # skip type suffixes: u U l L
            while i < n and source[i] in 'uUlL':
                i += 1
            tokens.append(Token(TK_NUMBER, val, line))
            continue

        # Identifier or keyword
        if c.isalpha() or c == '_':
            start = i
            while i < n and (source[i].isalnum() or source[i] == '_'):
                i += 1
            word = source[start:i]
            # Expand defines
            if word in defines:
                expanded = defines[word]
                if expanded:
                    try:
                        val = int(expanded, 0)
                        tokens.append(Token(TK_NUMBER, val, line))
                        continue
                    except ValueError:
                        # Non-integer expansion (e.g. (*(volatile u8*)0x6F82)):
                        # splice expanded text at current position for inline re-parsing.
                        source = source[:start] + expanded + source[i:]
                        n = len(source)
                        i = start
                        continue
            if word in KEYWORDS:
                tokens.append(Token(TK_KEYWORD, word, line))
            else:
                tokens.append(Token(TK_IDENT, word, line))
            continue

        # Multi-character punctuation
        two = source[i:i+2]
        if two in ('++', '--', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=',
                   '<<=', '>>=', '==', '!=', '<=', '>=', '&&', '||', '<<', '>>',
                   '->', '...'):
            # check 3-char first
            three = source[i:i+3]
            if three in ('<<=', '>>='):
                tokens.append(Token(TK_PUNCT, three, line))
                i += 3
            else:
                tokens.append(Token(TK_PUNCT, two, line))
                i += 2
            continue

        # Single char punctuation
        if c in '()[]{};,.*&~!+-/%^|<>=?:':
            tokens.append(Token(TK_PUNCT, c, line))
            i += 1
            continue

        raise LexError(f"Unexpected character: {repr(c)}", line)

    tokens.append(Token(TK_EOF, None, line))
    return tokens

# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------

@dataclass
class FuncDecl:
    name: str
    params: List[Tuple[str, Type]]  # (name, type)
    ret_type: Type
    body: Optional[Any] = None  # Block or None
    is_interrupt: bool = False
    is_static: bool = False
    line: int = 0

@dataclass
class VarDecl:
    name: str
    type_: Type
    init_expr: Optional[Any] = None
    is_static: bool = False
    is_extern: bool = False
    is_const: bool = False
    is_register: bool = False
    line: int = 0

@dataclass
class Block:
    stmts: List[Any]
    line: int = 0

@dataclass
class IfStmt:
    cond: Any
    then: Any
    else_: Optional[Any] = None
    line: int = 0

@dataclass
class WhileStmt:
    cond: Any
    body: Any
    line: int = 0

@dataclass
class ForStmt:
    init: Optional[Any]
    cond: Optional[Any]
    step: Optional[Any]
    body: Any
    line: int = 0

@dataclass
class DoWhileStmt:
    body: Any
    cond: Any
    line: int = 0

@dataclass
class ReturnStmt:
    expr: Optional[Any] = None
    line: int = 0

@dataclass
class BreakStmt:
    line: int = 0

@dataclass
class ContinueStmt:
    line: int = 0

@dataclass
class GotoStmt:
    label: str
    line: int = 0

@dataclass
class LabelStmt:
    label: str
    stmt: Any   # statement following the label (may be None)
    line: int = 0

@dataclass
class CaseClause:
    value: Any        # integer constant (int), or None for default
    stmts: list       # list of statements
    line: int = 0

@dataclass
class SwitchStmt:
    expr: Any         # switch expression
    clauses: list     # list of CaseClause (default clause has value=None)
    line: int = 0

@dataclass
class ExprStmt:
    expr: Any
    line: int = 0

@dataclass
class IndirectCall:
    """Call through a function pointer expression (not a plain Ident)."""
    callee: Any   # expression that evaluates to a u16 function address
    args: list
    line: int = 0

@dataclass
class BinOp:
    op: str
    left: Any
    right: Any
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class UnaryOp:
    op: str
    expr: Any
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class Assign:
    op: str     # '=', '+=', etc.
    target: Any
    value: Any
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class FuncCall:
    name: str
    args: List[Any]
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class Subscript:
    base: Any
    index: Any
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class Deref:
    expr: Any
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class AddrOf:
    expr: Any
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class Ident:
    name: str
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class Const:
    value: int
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class InitList:
    """Array/struct initializer: = { v0, v1, ... }"""
    values: List[Any]   # flattened initializer expressions / scalar literals

@dataclass
class Cast:
    type_: Type
    expr: Any
    line: int = 0

@dataclass
class FieldAccess:
    """Struct field access: expr.field (is_arrow=False) or expr->field (is_arrow=True)."""
    expr: Any
    field: str
    is_arrow: bool
    type_: Optional[Type] = None
    line: int = 0

@dataclass
class Ternary:
    """cond ? then : else_"""
    cond: Any
    then: Any
    else_: Any
    line: int = 0

# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class ParseError(Exception):
    def __init__(self, msg, line):
        super().__init__(f"Parse error line {line}: {msg}")

class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self.typedefs: Dict[str, Type] = {
            'u8':  U8,  'u16': U16, 'u32': U32,
            'i8':  I8,  'i16': I16, 'i32': I32,
            's8':  I8,  's16': I16, 's32': I32,
        }
        self.struct_defs: Dict[str, StructType] = {}
        self.enum_vals: Dict[str, int] = {}
        self._is_interrupt: bool = False
        self._is_const: bool = False
        self._is_register: bool = False

    def _parse_init_list(self) -> 'List[Any]':
        """Parse { v0, v1, ... } or { {a,b}, {c,d}, ... } — flatten to flat int list."""
        self.advance()  # consume '{'
        values = []
        while not self.match(TK_PUNCT, '}') and not self.match(TK_EOF):
            if self.match(TK_PUNCT, '{'):
                values.extend(self._parse_init_list())  # nested struct element
            else:
                expr = self.parse_expr()
                values.append(expr)
            if not self.consume(TK_PUNCT, ','):
                break
        self.expect_punct('}')
        return values

    def _eval_const(self, expr) -> int:
        """Constant-fold an expression to an integer for initializer lists."""
        if isinstance(expr, int):
            return expr
        if isinstance(expr, Const):
            return expr.value
        if isinstance(expr, UnaryOp) and expr.op == '-':
            return -self._eval_const(expr.expr)
        if isinstance(expr, UnaryOp) and expr.op == '~':
            return ~self._eval_const(expr.expr)
        if isinstance(expr, Cast):
            return self._eval_const(expr.expr)
        if isinstance(expr, BinOp):
            l = self._eval_const(expr.left)
            r = self._eval_const(expr.right)
            if expr.op == '+':  return l + r
            if expr.op == '-':  return l - r
            if expr.op == '*':  return l * r
            if expr.op == '|':  return l | r
            if expr.op == '&':  return l & r
            if expr.op == '<<': return l << r
            if expr.op == '>>': return l >> r
        return 0  # fallback for unsupported patterns

    def peek(self, offset=0) -> Token:
        pos = self.pos + offset
        if pos >= len(self.tokens):
            return self.tokens[-1]  # EOF
        return self.tokens[pos]

    def cur(self) -> Token:
        return self.peek(0)

    def advance(self) -> Token:
        t = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return t

    def expect(self, kind, value=None) -> Token:
        t = self.cur()
        if t.kind != kind:
            raise ParseError(f"Expected {kind!r} but got {t.kind!r} ({t.value!r})", t.line)
        if value is not None and t.value != value:
            raise ParseError(f"Expected {value!r} but got {t.value!r}", t.line)
        return self.advance()

    def expect_punct(self, value) -> Token:
        return self.expect(TK_PUNCT, value)

    def expect_keyword(self, value) -> Token:
        return self.expect(TK_KEYWORD, value)

    def match(self, kind, value=None) -> bool:
        t = self.cur()
        if t.kind != kind:
            return False
        if value is not None and t.value != value:
            return False
        return True

    def consume(self, kind, value=None) -> Optional[Token]:
        if self.match(kind, value):
            return self.advance()
        return None

    # -- Type parsing --

    def is_type_start(self) -> bool:
        t = self.cur()
        if t.kind == TK_KEYWORD and t.value in (
            'void','char','short','int','long','unsigned','signed',
            'const','volatile','static','extern','register','typedef',
            'struct','union','enum',
        ):
            return True
        if t.kind == TK_IDENT and t.value in self.typedefs:
            return True
        return False

    def parse_base_type(self) -> Tuple[Type, bool, bool]:
        """Returns (base_type, is_static, is_extern)."""
        is_static = False
        is_extern = False
        is_typedef = False

        # Consume qualifiers
        while True:
            if self.consume(TK_KEYWORD, 'static'):
                is_static = True
            elif self.consume(TK_KEYWORD, 'extern'):
                is_extern = True
            elif self.consume(TK_KEYWORD, 'register'):
                self._is_register = True
            elif self.consume(TK_KEYWORD, 'typedef'):
                is_typedef = True
            elif self.consume(TK_KEYWORD, 'const'):
                self._is_const = True
            elif self.consume(TK_KEYWORD, 'volatile'):
                pass
            elif self.consume(TK_KEYWORD, '__interrupt'):
                self._is_interrupt = True
            else:
                break

        t = self.cur()
        # typedef name
        if t.kind == TK_IDENT and t.value in self.typedefs:
            self.advance()
            ty = self.typedefs[t.value]
        elif t.kind == TK_KEYWORD and t.value == 'void':
            self.advance()
            ty = VOID
        elif t.kind == TK_KEYWORD and t.value in ('struct', 'union'):
            # struct/union: parse definition or reference
            is_union = (t.value == 'union')
            self.advance()
            tag = ''
            if self.match(TK_IDENT):
                tag = self.advance().value
            if self.match(TK_PUNCT, '{'):
                self.advance()
                fields: List[StructField] = []
                offset = 0
                max_field_sz = 0
                while not self.match(TK_PUNCT, '}'):
                    if self.match(TK_EOF):
                        break
                    fty, _, _ = self.parse_base_type()
                    # Support multiple names: u8 a, b, c; — collect all names
                    fnames = []
                    if self.match(TK_IDENT):
                        fnames.append(self.advance().value)
                    while self.consume(TK_PUNCT, ','):
                        if self.match(TK_IDENT):
                            fnames.append(self.advance().value)
                    if not fnames:
                        fnames = ['']
                    for fname in fnames:
                        cur_fty = fty
                        # Array field
                        if self.consume(TK_PUNCT, '['):
                            count = 0
                            if not self.match(TK_PUNCT, ']'):
                                ce = self.parse_expr()
                                count = ce.value if isinstance(ce, Const) else 0
                            self.expect_punct(']')
                            cur_fty = ArrayType(fty, count)
                        fsz = cur_fty.size() if cur_fty.size() > 0 else 1
                        if is_union:
                            # union: all fields at offset 0, size = max of all fields
                            fields.append(StructField(fname, cur_fty, 0))
                            max_field_sz = max(max_field_sz, fsz)
                        else:
                            align = min(fsz, 2)
                            if align > 1 and offset % align != 0:
                                offset += align - (offset % align)
                            fields.append(StructField(fname, cur_fty, offset))
                            offset += fsz
                    self.expect_punct(';')
                self.expect_punct('}')
                if is_union:
                    # Round union size to 2-byte boundary
                    if max_field_sz % 2 != 0:
                        max_field_sz += 1
                    ty = StructType(tag, fields, max_field_sz)
                else:
                    # Round struct total size to 2-byte boundary
                    if offset % 2 != 0:
                        offset += 1
                    ty = StructType(tag, fields, offset)
                if tag:
                    self.struct_defs[tag] = ty
            elif tag in self.struct_defs:
                ty = self.struct_defs[tag]
            else:
                # Forward reference: assume 2-byte placeholder
                ty = StructType(tag, [], 2)
        elif t.kind == TK_KEYWORD and t.value == 'enum':
            self.advance()
            tag = ''
            if self.match(TK_IDENT):
                tag = self.advance().value
            if self.match(TK_PUNCT, '{'):
                self.advance()
                val = 0
                while not self.match(TK_PUNCT, '}'):
                    if self.match(TK_EOF):
                        break
                    ename = self.expect(TK_IDENT).value
                    if self.consume(TK_PUNCT, '='):
                        ce = self.parse_expr()
                        if isinstance(ce, Const):
                            val = ce.value
                    self.enum_vals[ename] = val
                    val += 1
                    self.consume(TK_PUNCT, ',')
                self.expect_punct('}')
            ty = U16   # enums are treated as u16
        else:
            # Parse int type
            signed = True
            if self.consume(TK_KEYWORD, 'unsigned'):
                signed = False
            elif self.consume(TK_KEYWORD, 'signed'):
                signed = True

            size = 2  # default int
            if self.consume(TK_KEYWORD, 'char'):
                size = 1
            elif self.consume(TK_KEYWORD, 'short'):
                size = 2
                self.consume(TK_KEYWORD, 'int')
            elif self.consume(TK_KEYWORD, 'long'):
                size = 4
                self.consume(TK_KEYWORD, 'int')
            elif self.consume(TK_KEYWORD, 'int'):
                size = 2
            else:
                if not self.consume(TK_KEYWORD, 'long'):
                    pass  # will default to int
            ty = IntType(size, signed)

        # NGP_FAR qualifier: appears between base type and '*'
        # e.g. "const u16 NGP_FAR *p" — consumed here, propagated to PtrType.far
        far_qual = False
        if self.match(TK_IDENT, 'NGP_FAR'):
            self.advance()
            far_qual = True

        # Pointer suffixes
        while self.consume(TK_PUNCT, '*'):
            self.consume(TK_KEYWORD, 'const')  # skip const qualifier on pointer
            ty = PtrType(ty, far=far_qual)
            far_qual = False  # far only applies to the outermost pointer

        return ty, is_static, is_extern

    # -- Top-level parse --

    def parse_program(self):
        decls = []
        while not self.match(TK_EOF):
            d = self.parse_top_level()
            if d is not None:
                decls.append(d)
        return decls

    def parse_top_level(self):
        line = self.cur().line

        # typedef
        if self.match(TK_KEYWORD, 'typedef'):
            self.advance()
            self._is_interrupt = False
            ty, _, _ = self.parse_base_type()
            # typedef struct { } Name; or typedef enum { } Name;
            # After parsing the struct/enum body, the typedef name follows.
            # But if the struct itself consumed nothing further, parse the name.
            if self.match(TK_PUNCT, ';'):
                # anonymous typedef with no name (e.g. typedef struct { } ;) — skip
                self.advance()
                return None
            name = self.expect(TK_IDENT).value
            self.typedefs[name] = ty
            # If it was typedef struct Tag { } Name, also register Name as struct alias
            if isinstance(ty, StructType) and not ty.tag:
                ty.tag = name
                self.struct_defs[name] = ty
            self.expect_punct(';')
            return None

        self._is_interrupt = False
        self._is_const = False
        self._is_register = False
        ty, is_static, is_extern = self.parse_base_type()
        # __interrupt may appear after the return type: "void __interrupt foo()"
        if self.consume(TK_KEYWORD, '__interrupt'):
            self._is_interrupt = True
        is_interrupt = self._is_interrupt
        self._is_interrupt = False
        self._is_register = False

        # After a struct/enum definition, may be just `struct Tag { }; ` with no variable
        if isinstance(ty, (StructType,)) and self.match(TK_PUNCT, ';'):
            self.advance()
            return None

        # Handle case where struct/enum definition is followed by variable name
        name = ''
        if self.match(TK_IDENT):
            name = self.advance().value
        elif self.match(TK_PUNCT, ';'):
            self.advance()
            return None
        else:
            # Skip unexpected token
            t = self.cur()
            if not self.match(TK_EOF):
                self.advance()
            return None

        # Function declaration or definition
        if self.match(TK_PUNCT, '('):
            params = self.parse_param_list()
            if self.match(TK_PUNCT, '{'):
                # Function definition
                body = self.parse_block()
                return FuncDecl(name, params, ty, body=body, is_interrupt=is_interrupt, is_static=is_static, line=line)
            else:
                # Forward declaration
                self.expect_punct(';')
                return FuncDecl(name, params, ty, body=None, is_interrupt=is_interrupt, is_static=is_static, line=line)

        # Global variable — may be an array (incl. multi-dim): T name[M][N]...;
        # Multi-dim arrays are flattened: total count = M*N*...
        if self.consume(TK_PUNCT, '['):
            total = 1
            first = True
            while True:
                if not self.match(TK_PUNCT, ']'):
                    count_expr = self.parse_expr()
                    dim = count_expr.value if isinstance(count_expr, Const) else 0
                else:
                    dim = 0
                self.expect_punct(']')
                total = (total * dim) if dim else 0
                first = False
                if not self.consume(TK_PUNCT, '['):
                    break
            ty = ArrayType(ty, total)
        init_expr = None
        if self.consume(TK_PUNCT, '='):
            if self.match(TK_PUNCT, '{'):
                init_expr = InitList(self._parse_init_list())
            else:
                init_expr = self.parse_expr()
        is_const = self._is_const
        self._is_const = False
        self.expect_punct(';')
        return VarDecl(name, ty, init_expr=init_expr, is_static=is_static, is_extern=is_extern, is_const=is_const, line=line)

    def parse_param_list(self) -> List[Tuple[str, Type]]:
        self.expect_punct('(')
        params = []
        if self.consume(TK_KEYWORD, 'void') and self.match(TK_PUNCT, ')'):
            self.expect_punct(')')
            return params
        while not self.match(TK_PUNCT, ')'):
            if self.match(TK_PUNCT, '.'):  # varargs, skip
                break
            self._is_const = False
            self._is_register = False
            ty, _, _ = self.parse_base_type()
            self._is_register = False
            # Optional parameter name
            pname = ''
            if self.match(TK_IDENT):
                pname = self.advance().value
            # T name[N] param: array decays to pointer — consume [N], keep type as-is
            if self.consume(TK_PUNCT, '['):
                if not self.match(TK_PUNCT, ']'):
                    self.parse_expr()  # consume size expression
                self.expect_punct(']')
                ty = U16  # treat as pointer (16-bit address)
            params.append((pname, ty))
            if not self.consume(TK_PUNCT, ','):
                break
        self.expect_punct(')')
        return params

    def parse_block(self) -> Block:
        line = self.cur().line
        self.expect_punct('{')
        stmts = []
        while not self.match(TK_PUNCT, '}'):
            if self.match(TK_EOF):
                raise ParseError("Unterminated block", line)
            s = self.parse_stmt()
            if s is not None:
                stmts.append(s)
        self.expect_punct('}')
        return Block(stmts, line=line)

    def parse_stmt(self):
        t = self.cur()

        if self.match(TK_PUNCT, '{'):
            return self.parse_block()

        if self.match(TK_KEYWORD, 'if'):
            return self.parse_if()

        if self.match(TK_KEYWORD, 'while'):
            return self.parse_while()

        if self.match(TK_KEYWORD, 'for'):
            return self.parse_for()

        if self.match(TK_KEYWORD, 'do'):
            return self.parse_do_while()

        if self.match(TK_KEYWORD, 'switch'):
            return self.parse_switch()

        if self.match(TK_KEYWORD, 'return'):
            line = self.cur().line
            self.advance()
            expr = None
            if not self.match(TK_PUNCT, ';'):
                expr = self.parse_expr()
            self.expect_punct(';')
            return ReturnStmt(expr, line=line)

        if self.match(TK_KEYWORD, 'break'):
            line = self.cur().line
            self.advance()
            self.expect_punct(';')
            return BreakStmt(line=line)

        if self.match(TK_KEYWORD, 'continue'):
            line = self.cur().line
            self.advance()
            self.expect_punct(';')
            return ContinueStmt(line=line)

        if self.match(TK_KEYWORD, 'goto'):
            line = self.cur().line
            self.advance()
            lname = self.expect(TK_IDENT).value
            self.expect_punct(';')
            return GotoStmt(label=lname, line=line)

        # Local variable declaration
        if self.is_type_start():
            return self.parse_local_decl()

        # Labeled statement: IDENT ':'
        if self.match(TK_IDENT) and self.peek(1).kind == TK_PUNCT and self.peek(1).value == ':':
            line = self.cur().line
            lname = self.advance().value
            self.advance()  # consume ':'
            inner = self.parse_stmt()
            return LabelStmt(label=lname, stmt=inner, line=line)

        # Expression statement or empty
        if self.consume(TK_PUNCT, ';'):
            return None

        line = t.line
        expr = self.parse_expr()
        self.expect_punct(';')
        return ExprStmt(expr, line=line)

    def parse_local_decl(self):
        """Parse one or more comma-separated local variable declarations sharing the same base type.
        Returns a single VarDecl or a Block of VarDecls (for 'u8 a, b, c;' patterns).
        """
        line = self.cur().line
        self._is_const = False
        self._is_register = False
        base_ty, is_static, _ = self.parse_base_type()
        is_const = self._is_const
        is_register = self._is_register
        self._is_const = False
        self._is_register = False
        decls = []
        while True:
            vname = self.expect(TK_IDENT).value
            # Per-variable pointer depth (e.g. u8 *p, q;)
            pty = base_ty
            # Array (incl. multi-dim T name[M][N]... → flattened to T[M*N*...])
            vty = pty
            if self.consume(TK_PUNCT, '['):
                total = 1
                while True:
                    if not self.match(TK_PUNCT, ']'):
                        count_expr = self.parse_expr()
                        dim = count_expr.value if isinstance(count_expr, Const) else 0
                    else:
                        dim = 0
                    self.expect_punct(']')
                    total = (total * dim) if dim else 0
                    if not self.consume(TK_PUNCT, '['):
                        break
                vty = ArrayType(pty, total)
            init_expr = None
            if self.consume(TK_PUNCT, '='):
                if self.match(TK_PUNCT, '{'):
                    init_expr = InitList(self._parse_init_list())
                else:
                    init_expr = self.parse_expr()
            decls.append(VarDecl(vname, vty, init_expr=init_expr,
                                  is_static=is_static, is_const=is_const,
                                  is_register=is_register, line=line))
            if not self.consume(TK_PUNCT, ','):
                break
        self.expect_punct(';')
        if len(decls) == 1:
            return decls[0]
        return Block(decls, line=line)

    def parse_if(self) -> IfStmt:
        line = self.cur().line
        self.expect_keyword('if')
        self.expect_punct('(')
        cond = self.parse_expr()
        self.expect_punct(')')
        then = self.parse_stmt()
        else_ = None
        if self.consume(TK_KEYWORD, 'else'):
            else_ = self.parse_stmt()
        return IfStmt(cond, then, else_, line=line)

    def parse_while(self) -> WhileStmt:
        line = self.cur().line
        self.expect_keyword('while')
        self.expect_punct('(')
        cond = self.parse_expr()
        self.expect_punct(')')
        body = self.parse_stmt()
        return WhileStmt(cond, body, line=line)

    def parse_do_while(self) -> DoWhileStmt:
        line = self.cur().line
        self.expect_keyword('do')
        body = self.parse_stmt()
        self.expect_keyword('while')
        self.expect_punct('(')
        cond = self.parse_expr()
        self.expect_punct(')')
        self.expect_punct(';')
        return DoWhileStmt(body, cond, line=line)

    def parse_for(self) -> ForStmt:
        line = self.cur().line
        self.expect_keyword('for')
        self.expect_punct('(')
        init = None
        if not self.match(TK_PUNCT, ';'):
            if self.is_type_start():
                init = self.parse_local_decl()
            else:
                init = ExprStmt(self.parse_expr(), line=line)
                self.expect_punct(';')
        else:
            self.advance()
        cond = None
        if not self.match(TK_PUNCT, ';'):
            cond = self.parse_expr()
        self.expect_punct(';')
        step = None
        if not self.match(TK_PUNCT, ')'):
            step = self.parse_expr()
        self.expect_punct(')')
        body = self.parse_stmt()
        return ForStmt(init, cond, step, body, line=line)

    def parse_switch(self) -> SwitchStmt:
        line = self.cur().line
        self.expect_keyword('switch')
        self.expect_punct('(')
        expr = self.parse_expr()
        self.expect_punct(')')
        self.expect_punct('{')

        clauses = []
        current_stmts = None
        current_value = None  # None = default, int = case value
        current_line = line

        while not self.consume(TK_PUNCT, '}'):
            if self.match(TK_EOF):
                raise ParseError("Unterminated switch body", line)
            if self.match(TK_KEYWORD, 'case'):
                # Save previous clause if any
                if current_stmts is not None:
                    clauses.append(CaseClause(current_value, current_stmts, current_line))
                # Start new clause
                current_line = self.cur().line
                self.advance()  # consume 'case'
                val_expr = self.parse_expr()
                val = self._eval_const(val_expr)
                self.expect_punct(':')
                current_stmts = []
                current_value = val
            elif self.match(TK_KEYWORD, 'default'):
                if current_stmts is not None:
                    clauses.append(CaseClause(current_value, current_stmts, current_line))
                current_line = self.cur().line
                self.advance()  # consume 'default'
                self.expect_punct(':')
                current_stmts = []
                current_value = None
            else:
                if current_stmts is None:
                    raise ParseError("statement before first case in switch", self.cur().line)
                current_stmts.append(self.parse_stmt())

        # Save last clause
        if current_stmts is not None:
            clauses.append(CaseClause(current_value, current_stmts, current_line))

        return SwitchStmt(expr, clauses, line)

    # -- Expression parsing (precedence climbing) --

    def parse_expr(self):
        return self.parse_assign()

    def parse_assign(self):
        left = self.parse_ternary()
        line = self.cur().line
        op = self.cur().value
        assign_ops = {'=', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>='}
        if self.cur().kind == TK_PUNCT and op in assign_ops:
            self.advance()
            right = self.parse_assign()
            return Assign(op, left, right, line=line)
        return left

    def parse_ternary(self):
        cond = self.parse_lor()
        if self.consume(TK_PUNCT, '?'):
            then = self.parse_expr()
            self.expect_punct(':')
            else_ = self.parse_ternary()
            return Ternary(cond, then, else_, line=self.cur().line)
        return cond

    def parse_lor(self):
        left = self.parse_land()
        while self.consume(TK_PUNCT, '||'):
            right = self.parse_land()
            left = BinOp('||', left, right)
        return left

    def parse_land(self):
        left = self.parse_bor()
        while self.consume(TK_PUNCT, '&&'):
            right = self.parse_bor()
            left = BinOp('&&', left, right)
        return left

    def parse_bor(self):
        left = self.parse_bxor()
        while self.consume(TK_PUNCT, '|'):
            right = self.parse_bxor()
            left = self._const_fold('|', left, right)  # Phase 5 PoC: fold const|const
        return left

    def parse_bxor(self):
        left = self.parse_band()
        while self.consume(TK_PUNCT, '^'):
            right = self.parse_band()
            left = self._const_fold('^', left, right)  # Phase 5 PoC: fold const^const
        return left

    def parse_band(self):
        left = self.parse_eq()
        while self.match(TK_PUNCT, '&') and self.peek(1).value != '&':
            self.advance()
            right = self.parse_eq()
            left = self._const_fold('&', left, right)  # Phase 5 PoC: fold const&const
        return left

    def parse_eq(self):
        left = self.parse_rel()
        while self.cur().kind == TK_PUNCT and self.cur().value in ('==', '!='):
            op = self.advance().value
            right = self.parse_rel()
            left = BinOp(op, left, right)
        return left

    def parse_rel(self):
        left = self.parse_shift()
        while self.cur().kind == TK_PUNCT and self.cur().value in ('<', '<=', '>', '>='):
            op = self.advance().value
            right = self.parse_shift()
            left = BinOp(op, left, right)
        return left

    def parse_shift(self):
        left = self.parse_add()
        while self.cur().kind == TK_PUNCT and self.cur().value in ('<<', '>>'):
            op = self.advance().value
            right = self.parse_add()
            # Phase 5 PoC (2026-06-22): fold `<<`/`>>` of two compile-time
            # constants like parse_add/parse_mul already do. _const_fold
            # already handles these ops; parse_shift was the only arith
            # level not wired to it, so `1u << 3` emitted a runtime
            # `ld WA,1; extz; sll 3` instead of the constant 8 (cf
            # NgpCraft_rebuild FINDINGS §0quater). Semantics-preserving:
            # only fires when BOTH operands are compile-time Const.
            # Measured: StarGunner j16 body 146112 -> 145945 B (-167).
            left = self._const_fold(op, left, right)
        return left

    @staticmethod
    def _const_fold(op, left, right):
        """Fold binary op to Const when both operands are compile-time constants.
        Used in array size expressions like (SCREEN_H / BG_DMA_LINE_STEP) = (152 / 2)."""
        if isinstance(left, Const) and isinstance(right, Const):
            lv, rv = left.value, right.value
            if op == '+': return Const(lv + rv)
            if op == '-': return Const(lv - rv)
            if op == '*': return Const(lv * rv)
            if op == '/' and rv != 0: return Const(lv // rv)
            if op == '%' and rv != 0: return Const(lv % rv)
            if op == '|': return Const(lv | rv)
            if op == '&': return Const(lv & rv)
            if op == '^': return Const(lv ^ rv)
            if op == '<<': return Const(lv << rv)
            if op == '>>': return Const(lv >> rv)
        return BinOp(op, left, right)

    def parse_add(self):
        left = self.parse_mul()
        while self.cur().kind == TK_PUNCT and self.cur().value in ('+', '-'):
            op = self.advance().value
            right = self.parse_mul()
            left = self._const_fold(op, left, right)
        return left

    def parse_mul(self):
        left = self.parse_unary()
        while self.cur().kind == TK_PUNCT and self.cur().value in ('*', '/', '%'):
            op = self.advance().value
            right = self.parse_unary()
            left = self._const_fold(op, left, right)
        return left

    def parse_unary(self):
        line = self.cur().line
        t = self.cur()
        if t.kind == TK_PUNCT:
            if t.value == '-':
                self.advance()
                expr = self.parse_unary()
                return UnaryOp('-', expr, line=line)
            if t.value == '!':
                self.advance()
                expr = self.parse_unary()
                return UnaryOp('!', expr, line=line)
            if t.value == '~':
                self.advance()
                expr = self.parse_unary()
                return UnaryOp('~', expr, line=line)
            if t.value == '*':
                self.advance()
                expr = self.parse_unary()
                return Deref(expr, line=line)
            if t.value == '&':
                self.advance()
                expr = self.parse_unary()
                return AddrOf(expr, line=line)
            if t.value == '++':
                self.advance()
                expr = self.parse_unary()
                return UnaryOp('pre++', expr, line=line)
            if t.value == '--':
                self.advance()
                expr = self.parse_unary()
                return UnaryOp('pre--', expr, line=line)
        # Cast: (type) expr
        if t.kind == TK_PUNCT and t.value == '(':
            # Look ahead: is it a cast?
            saved = self.pos
            try:
                self.advance()  # consume '('
                if self.is_type_start():
                    ty, _, _ = self.parse_base_type()
                    if self.match(TK_PUNCT, ')'):
                        self.advance()
                        expr = self.parse_unary()
                        return Cast(ty, expr, line=line)
                # Not a cast, restore
                self.pos = saved
            except (ParseError, Exception):
                self.pos = saved
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while True:
            t = self.cur()
            if t.kind == TK_PUNCT and t.value == '[':
                self.advance()
                idx = self.parse_expr()
                self.expect_punct(']')
                expr = Subscript(expr, idx, line=t.line)
            elif t.kind == TK_PUNCT and t.value == '(':
                # Function call via function pointer — not supported in v1
                self.advance()
                args = []
                while not self.match(TK_PUNCT, ')'):
                    args.append(self.parse_assign())
                    if not self.consume(TK_PUNCT, ','):
                        break
                self.expect_punct(')')
                if isinstance(expr, Ident):
                    expr = FuncCall(expr.name, args, line=t.line)
                else:
                    expr = IndirectCall(expr, args, line=t.line)
            elif t.kind == TK_PUNCT and t.value == '++':
                self.advance()
                expr = UnaryOp('post++', expr, line=t.line)
            elif t.kind == TK_PUNCT and t.value == '--':
                self.advance()
                expr = UnaryOp('post--', expr, line=t.line)
            elif t.kind == TK_PUNCT and t.value == '.':
                self.advance()
                member = self.expect(TK_IDENT).value
                expr = FieldAccess(expr, member, is_arrow=False, line=t.line)
            elif t.kind == TK_PUNCT and t.value == '->':
                self.advance()
                member = self.expect(TK_IDENT).value
                expr = FieldAccess(expr, member, is_arrow=True, line=t.line)
            else:
                break
        return expr

    def parse_primary(self):
        t = self.cur()
        line = t.line

        if t.kind == TK_NUMBER:
            self.advance()
            return Const(t.value, line=line)

        if t.kind == TK_STRING:
            self.advance()
            # Adjacent string literal concatenation: "a" "b" -> "ab" (C standard)
            s = t.value
            while self.pos < len(self.tokens) and self.tokens[self.pos].kind == TK_STRING:
                s += self.tokens[self.pos].value
                self.advance()
            return Const(s, type_=PtrType(U8), line=line)

        if t.kind == TK_IDENT:
            self.advance()
            name = t.value
            # Enum constant expansion
            if name in self.enum_vals and not self.match(TK_PUNCT, '('):
                return Const(self.enum_vals[name], type_=U16, line=line)
            # Function call
            if self.match(TK_PUNCT, '('):
                self.advance()
                args = []
                while not self.match(TK_PUNCT, ')'):
                    args.append(self.parse_assign())
                    if not self.consume(TK_PUNCT, ','):
                        break
                self.expect_punct(')')
                return FuncCall(name, args, line=line)
            return Ident(name, line=line)

        if t.kind == TK_KEYWORD and t.value == 'sizeof':
            self.advance()
            self.expect_punct('(')
            if self.is_type_start():
                ty, _, _ = self.parse_base_type()
                self.expect_punct(')')
                return Const(ty.size(), line=line)
            else:
                expr = self.parse_expr()
                self.expect_punct(')')
                # Try to resolve size at compile time for known types
                return UnaryOp('sizeof', expr, line=line)

        if t.kind == TK_PUNCT and t.value == '(':
            self.advance()
            expr = self.parse_expr()
            self.expect_punct(')')
            return expr

        raise ParseError(f"Unexpected token {t.kind!r} {t.value!r}", line)

# ---------------------------------------------------------------------------
# Semantic pass — type resolution and symbol table
# ---------------------------------------------------------------------------

class SemanticError(Exception):
    def __init__(self, msg, line=0):
        super().__init__(f"Semantic error line {line}: {msg}")

@dataclass
class Symbol:
    name: str
    type_: Type
    scope: str     # 'global', 'local', 'param'
    offset: int = 0    # stack offset for locals (negative from XHL)
    label: str = ''    # asm label for globals
    is_far: bool = False  # True for const array/struct globals in f_const (ROM 0x200000+)
    reg_name: str = ''    # non-empty for locals kept in an XIZ-bank slot (PERF-LAG-5)
    # P2 no-frame leaf: non-empty when an adecl param stays live in its
    # incoming reg (XWA/XBC/XDE) for the whole function body. Distinct from
    # reg_name to avoid conflicting with PERF-LAG-5 bank logic.
    adecl_live_reg: str = ''

class SemanticPass:
    def __init__(self, decls):
        self.decls = decls
        self.globals: Dict[str, Symbol] = {}
        self.func_decls: Dict[str, FuncDecl] = {}
        self.errors: List[str] = []

    def run(self):
        for d in self.decls:
            if isinstance(d, FuncDecl):
                sym = Symbol(d.name, d.ret_type, 'global', label=f'_{d.name}')
                self.func_decls[d.name] = d
                self.globals[d.name] = sym
            elif isinstance(d, VarDecl):
                # All const globals go to f_const (ROM 0x200000+) → far address.
                # Non-const globals go to f_data/f_area (RAM) → near address.
                # const T *ptr = pointer-to-const: pointer itself is mutable → RAM.
                # Only non-pointer const values (scalars, arrays, structs) go to ROM.
                is_far = d.is_const and not isinstance(d.type_, PtrType)
                sym = Symbol(d.name, d.type_, 'global', label=f'_{d.name}', is_far=is_far)
                self.globals[d.name] = sym

# ---------------------------------------------------------------------------
# Code Generator
# ---------------------------------------------------------------------------

class CodeGenError(Exception):
    def __init__(self, msg, line=0):
        super().__init__(f"CodeGen error line {line}: {msg}")

class CodeGen:
    """
    Generates TLCS-900 assembly from AST.
    Strategy: simple, correct, not optimized.
    Primary accumulator: WA (16-bit) or XWA (32-bit).
    Secondary: BC, DE (scratch).
    XHL = frame pointer when function has locals.
    """

    def __init__(self, source_name: str, decls, semantics: SemanticPass,
                 struct_defs: Optional[Dict[str, 'StructType']] = None):
        self.source_name = source_name
        self.decls = decls
        self.sem = semantics
        self.struct_defs: Dict[str, 'StructType'] = struct_defs or {}
        self.local_func_defs = {
            d.name for d in decls
            if isinstance(d, FuncDecl) and d.body is not None
        }
        # Regression bisect toggles for hardware validation.
        # Default = current optimized codegen; set env var to 0 to disable a group.
        self.opt_perf_lag_1 = os.environ.get('T900CC_PERF_LAG_1', '1') != '0'
        self.opt_perf_lag_2 = os.environ.get('T900CC_PERF_LAG_2', '1') != '0'
        self.opt_perf_lag_4 = os.environ.get('T900CC_PERF_LAG_4', '1') != '0'
        self.opt_perf_lag_5 = os.environ.get('T900CC_PERF_LAG_5', '1') != '0'
        self.opt_perf_lag_6 = os.environ.get('T900CC_PERF_LAG_6', '1') != '0'
        self.opt_perf_lag_7 = os.environ.get('T900CC_PERF_LAG_7', '1') != '0'
        self.opt_perf_lag_8 = os.environ.get('T900CC_PERF_LAG_8', '1') != '0'
        # P1 — ABI v2 __adecl (args in registers: 1st→XWA, 2nd→XBC, 3rd→XDE, rest pile).
        # DECISIONS.md §ABI v1 lines 42-48 document this as "reserved for later".
        # ON by default since 2026-05-19 (HW-validated in session 2026-04-20, body
        # 159 089 B / ratio ×2.006 on stargunner_j16 vs 176 697 B / ×2.23 baseline).
        # Set T900CC_ABI_ADECL=0 to fall back to cdecl (also: --cdecl-legacy CLI flag).
        # When OFF: legacy cdecl behavior (all args pushed right-to-left).
        self.opt_abi_adecl = os.environ.get('T900CC_ABI_ADECL', '1') != '0'
        self.lines: List[str] = []           # output lines
        self.label_counter = 0
        self.externs: List[str] = []         # extern names to declare
        self.bss_vars: List[VarDecl] = []    # uninitialized globals
        self.data_vars: List[VarDecl] = []   # initialized globals
        self.const_vars: List[VarDecl] = []  # const array/struct data → f_const ROM section
        self.string_consts: List[Tuple[str, str]] = []  # (label, string)
        self.str_counter = 0

        # Per-function state
        self.local_vars: Dict[str, Symbol] = {}
        self.local_offset = 0   # grows negative (next slot = -2, -4, ...)
        self.has_locals = False
        self.ret_type: Type = VOID
        self.is_interrupt: bool = False
        self.loop_break_label: Optional[str] = None
        self.loop_cont_label: Optional[str] = None
        self.param_syms: Dict[str, Symbol] = {}
        # Extra XDE field offset for struct->field access via (XDE+d8).
        # Set by gen_lvalue_addr(FieldAccess), consumed by _emit_load/store_from_de.
        self._xde_field_offset: int = 0
        self._mem_base_reg: str = 'XDE'
        # Static local variables: maps local name → Symbol(mangled_name, type_, 'global').
        # These are allocated in BSS/DATA section rather than on the frame.
        self.static_local_globals: Dict[str, 'Symbol'] = {}
        self.current_func_name: str = ''
        self._xbc_cached_elem_key = None
        self._xbc_cached_elem_offset: int = 0  # idx*esz saved when cache is populated
        self._frame_reg: str = 'XIY'
        self._frame_reg_saved: bool = False
        self._need_frame: bool = False
        self._func_exit_label: Optional[str] = None
        self._addr_taken_local_names = set()
        self._has_inline_asm: bool = False
        self._reg_bank_slots_free: List[str] = []
        self._has_reg_bank_locals: bool = False
        self._save_xiz_regbank: bool = False
        self._save_xix_scratch: bool = False
        self._prelink_saved_bytes: int = 0
        self._scratch_addr_reg: str = 'XIZ'
        self._addr_scratch_saved_xiz: bool = False

        # Chantier 4 Phase P-1 / Chantier 5 Phase P-5.1 — IR container
        # active during gen_function. From P-5.1 onwards the container
        # is block-level (`IRFunction` with a list of `BasicBlock`s).
        # When set (non-None), every emit() call appends an EmitRaw
        # op to the current block; emit_label() starts a new block.
        # At function end the IR is round-trip validated against the
        # actually-emitted lines to catch lowering bugs. Disabled
        # (None) outside of function scope so headers / extern decls /
        # global emission stay unchanged.
        self.ir_function: Optional[IRFunction] = None
        # Legacy attribute kept for any external reader. Always None
        # in P-5.1+; the active container is ir_function.
        self.ir_buffer: Optional[IRBuffer] = None
        # Index into self.lines marking where the active function's
        # emission started; used to compare against lower_to_asm output.
        self._ir_func_start_idx: int = 0
        # Skip the round-trip check (CLI flag --no-ir-check) for perf
        # or for diagnosing weird divergences in isolation.
        self._ir_check_enabled = (
            os.environ.get('T900CC_IR_CHECK', '1') != '0'
        )

        # Chantier 4 Phase P-3 — register tracker for 32-bit address
        # materialization (CODEGEN_NOTES.md pattern P-04). Maps each
        # tracked register to the symbol whose 32-bit address it
        # currently holds (None = unknown / clobbered).
        # Tracked registers: XBC, XDE, XIZ, XIX, XHL, XWA (NOT XIY which
        # is the frame pointer and never participates).
        # The cache is reset at function entry, on every emit_label
        # (basic block boundary), on every call/calr, and invalidated
        # per-reg whenever an emit_instr is detected to write to that reg.
        self._reg_holds_sym: Dict[str, Optional[str]] = {}
        # Push/pop stack model for symbol tracking through `push X; ...;
        # pop X` patterns. We push the tracked-symbol-or-None value for
        # the source reg at each `push`, and on `pop` we either consume
        # this saved symbol (so the popped reg gets its old binding back)
        # or invalidate (if the saved entry came from an untracked source).
        self._c4_p3_push_stack: List[Optional[str]] = []
        # Gate via env var so we can bisect P-3 vs pre-P-3 builds without
        # editing code. Default ON for production.
        self._opt_c4_p3_reg_tracker = (
            os.environ.get('T900CC_C4_P3', '1') != '0'
        )
        # Stats: how many `ld XR32, sym` were elided by the tracker.
        self._c4_p3_elision_count = 0

        # Chantier 5 P-5.6 — Allocator-driven codegen, gated.
        # Modes:
        #   '0' / unset     : allocator pipeline disabled (default). Legacy
        #                     codegen path is the only one. Binary-identical
        #                     to P-4' baseline. SAFE.
        #   'shadow'        : pipeline runs in parallel (liveness +
        #                     allocator + lower_ir_with_allocation) but
        #                     its output is COMPARED with the legacy
        #                     emission; on mismatch raises a CodeGenError.
        #                     Legacy output ships. Validates the plumbing
        #                     without committing to new asm. SAFE.
        #   '1' / 'on'      : pipeline output REPLACES legacy emission.
        #                     Body delta possible. RISKY — used only
        #                     for explicitly migrated codegen sites.
        self._opt_c5_regalloc = os.environ.get('T900CC_C5_REGALLOC', '0')
        # P-5.6.1 wiring: gate the emission of structured IR ops by
        # individual gen_assign sub-paths. When OFF (default), gen_assign
        # uses only the legacy EmitRaw path. When ON, gen_assign emits
        # structured ops (LoadImm/StoreLocal) IN ADDITION to the legacy
        # text — the structured ops feed the C5 pipeline (liveness +
        # allocator + lower_ir_with_allocation) while the legacy text
        # keeps self.lines populated for the round-trip / shadow check.
        # Only effective when `_opt_c5_regalloc != '0'`.
        self._opt_c5_use_structured = (
            os.environ.get('T900CC_C5_USE_STRUCTURED', '0') != '0'
        )
        # Per-function counter for naming virtual registers emitted by
        # migrated codegen paths. Reset in `gen_function`.
        self._c5_vreg_counter = 0
        # P-5.6.3: per-vreg class hint (string name like 'WA_ONLY',
        # 'HL_ONLY', 'WORD_DATA') consumed by _c5_run_pipeline when
        # building LiveInterval.cls. Default behavior (vreg absent from
        # dict): WORD_DATA + pref='XWA' as before. Helpers that need
        # tighter constraints (e.g. BinOp byte-split ALU which needs
        # src_a=XWA and src_b=XHL) populate this dict at emission time.
        # Reset per function.
        self._c5_vreg_cls: Dict[str, str] = {}
        # Stats for C5 pipeline (populated when shadow / on).
        self._c5_stats = {
            'functions_processed': 0,
            'intervals_total': 0,
            'spills_total': 0,
            'shadow_mismatches': 0,
            'structured_emits': 0,
            'structured_emits_this_function': 0,
            'functions_with_structured_emits': 0,
            'shadow_skipped_structured': 0,
        }

        # Phase 5 (2026-06-22) — NATIVE 32-bit reg-reg ALU for sz==4 binops.
        # The legacy sz==4 path byte-splits (`add A,L; adc W,H`) which only
        # touches the LOW 16 bits → 32-bit `long`/pointer arithmetic that
        # crosses a 64 KB boundary is COMPUTED WRONG (verified: 0x20FFFF+1
        # → 0x200000 instead of 0x210000). The native op `add XWA, XHL` is
        # both correct (full 32-bit) and shorter. It needs the E8..EF
        # encoding (silicon-safe, CC900-proven) which t900as now emits for
        # 32-bit r+r (was D8..DF = HW-broken). Emulator-verified:
        # add/sub/and/or/xor XWA,XHL execute and cross bit-16 correctly.
        # GATED OFF by default (HW-unvalidated on our corpus for the full
        # E8..EF ALU family; CC900 proves E8/E9/EA, EB is emulator-clean).
        # Set T900CC_C5_ALU32=1 to opt in (produces a HW-test ROM).
        self._opt_c5_alu32 = os.environ.get('T900CC_C5_ALU32', '0') != '0'

        # Chantier 4 Phase P-4' (post P-4 rollback) — safe peephole that
        # removes consecutive `push X; pop X` (same register) no-op
        # sequences. These have ZERO side effects (no flag change, no
        # memory write, XSP unchanged round-trip) and arise from
        # expression-evaluation idioms where t900cc bounces a value
        # through the stack to keep a temp value alive. Saves 2 bytes
        # per occurrence. Uses no D0..D7 opcodes — fully HW-safe.
        self._opt_c4_p4p_push_pop_self = (
            os.environ.get('T900CC_C4_P4P', '1') != '0'
        )
        self._c4_p4p_elision_count = 0
        # Chantier 4 Phase P-4'b WIDE-WINDOW extension of P-4'.
        # **DEFAULT OFF (2026-05-20 pass 21)** after HW black-screen
        # regression on real NGPC. Bug : the safety check only looks at
        # `parent(X) in defs(intermediate)`, but does NOT check if
        # intermediates READ (XSP+N) — which depends on the pushed value
        # being on the stack. Inline `__asm("ld XDE, (XSP+N)")` blocks in
        # core/ngpc_dma.c, ngpc_rtc.c, ngpc_sys.c, ngpc_timing.c read
        # their args via (XSP+N) — eliding the surrounding push/pop
        # shifts XSP and breaks those reads. The same root cause that
        # killed the cross-TU adécl v1 attempt 2026-05-20 pass 1.
        # The peephole code is preserved for a future fix iteration
        # (need to bail when intermediate ops have XSP in uses, or
        # any (XSP+...) memory operand). Until then, `T900CC_C4_P4PB=1`
        # to opt back in.
        self._opt_c4_p4pb_push_pop_wide = (
            os.environ.get('T900CC_C4_P4PB', '0') != '0'
        )
        self._c4_p4pb_elision_count = 0

        # Chantier 5 Phase P-5.6.5 (2026-05-20) — STORE-LOAD FORWARDING
        # peephole. Eliminates redundant `LDW WA, (XIY+off)` that
        # immediately follows `LDW (XIY+off), WA` (same cell) when no
        # intermediate op clobbers WA OR aliases (XIY+off).
        #
        # Multi-statement value tracking — MVP form. Empirical audit
        # (`audit_store_load_fwd.py`) shows 61 candidate sites in j16
        # (~−183 B body potential). Window max = 3 lines.
        #
        # Safety analysis (= correct redundant-load elimination):
        #   - The store wrote WA to (XIY+off). WA at the store point
        #     holds exactly the value at (XIY+off) post-store.
        #   - If intermediate ops don't write WA, WA stays at that value.
        #   - If intermediate ops don't write (XIY+off), the cell value
        #     stays at WA's value.
        #   - Therefore the subsequent LDW WA, (XIY+off) reads what's
        #     already in WA — it's redundant.
        #   - For "no other write aliases (XIY+off)" : conservative —
        #     bail on ANY memory write that's not via the SAME stack
        #     base at a DIFFERENT offset (= safe non-aliasing).
        #     This catches pointer stores (e.g. via XDE) that COULD
        #     alias the slot (& addresses-taken locals).
        #
        # Estimated body delta: −183 B if all 61 sites elide.
        self._opt_c5_ms_fwd = os.environ.get('T900CC_C5_MS_FWD', '1') != '0'
        self._c5_ms_fwd_count = 0

        # Chantier 5 Phase P-5.6.6 (2026-05-20) — LIVE VALUE TRACKER (LVT).
        # In-codegen value tracking: maintain which local/global/const
        # currently holds in WA across statements, so redundant loads
        # are NEVER EMITTED (vs post-emit elimination by P-5.6.5 peephole).
        #
        # Cache signature `('local', off)` / `('global', label)` /
        # `('const', value)` / None (unknown).
        #
        # Hooks :
        #   - `emit_instr(line)` parses the line for known WA-affecting
        #     patterns and updates `_lvt_wa` automatically.
        #   - `_load_local(sym)` checks cache BEFORE emit and skips if
        #     the value is already in WA. Same for `_load_param`.
        #   - `emit_label` calls `_lvt_reset_all()` (control flow entry).
        #   - Branch / call / ret emissions clear via use-def parser
        #     (all caller-saved regs in defs → invalidates cache).
        #
        # Default ON via T900CC_C5_LVT. Disable for bisect via
        # `T900CC_C5_LVT=0`. Coexists with P-5.6.5 peephole as
        # belt-and-suspenders ; when LVT mature the peephole will
        # have 0 sites to catch.
        self._opt_c5_lvt = os.environ.get('T900CC_C5_LVT', '1') != '0'
        # P-5.8 v7 Axe A (pass 37) : mem-form ALU INC/DEC fast path
        # for u16 XIY-rel locals. Encoding `0x9D <d8> 0x60+n` (INCW)
        # / `0x9D <d8> 0x68+n` (DECW). NOT in quirks_db → presumed HW
        # safe but unconfirmed on our own corpus → gated by env var.
        # Default ON because the audit showed ~10 B/site savings and
        # the encoding is heavily used in commercial NGPC ROMs (per
        # NgpCraft_emulator opcode-coverage analysis). Roll back to
        # '0' if HW regression detected.
        self._opt_c5_memform_alu = (
            os.environ.get('T900CC_C5_MEMFORM_ALU', '1') != '0'
        )
        # P-5.8 v7.2 (pass 38) : dead `ld W, 0` post-emit elision.
        # Conservative DCE — only removes `ld W, 0` when W is provably
        # written before being read within the same straight-line
        # sequence (bails on labels/branches/calls/uncertain ops).
        # Default ON ; gate via T900CC_C5_DEAD_LD_W=0 to roll back.
        self._opt_c5_dead_ld_w = (
            os.environ.get('T900CC_C5_DEAD_LD_W', '1') != '0'
        )
        # P-5.8 v7.3 (pass 39) : byte-narrow load hint. When set by a
        # caller that only needs A (not full WA — e.g., `cp A, imm` /
        # `or A, A` / byte store sites), `_load_local` / `_load_param`
        # / `gen_ident` for u8 sources SKIP the `ld W, 0` zero-extend.
        # Source-side complement to the v7.2 dead-W elision peephole :
        # killing the `ld W, 0` BEFORE emission (vs after) avoids the
        # post-pass complexity and catches cases the peephole's cross-
        # branch analysis can't safely prove dead.
        self._byte_narrow_load = False
        self._opt_c5_byte_narrow = (
            os.environ.get('T900CC_C5_BYTE_NARROW', '1') != '0'
        )
        # P-5.8 v7.4 (pass 40) : byte-narrow ALU hint. Set by callers
        # that consume the alu result as u8 only (= LDB store, byte
        # cmp, etc.). `_emit_alu16` for arith/bitwise ('+','-','&',
        # '|','^') SKIPS the high-byte half (`adc W, H`, `sbc W, H`,
        # `and W, H`, etc.) when the flag is set — the H half is dead
        # for u8 consumers. Saves 2 B/site on byte arith.
        # Comparisons ('==','!=','<','<=','>','>=') still need both
        # halves to set flags correctly — flag is ignored for cmp ops.
        self._byte_narrow_alu = False
        self._opt_c5_byte_narrow_alu = (
            os.environ.get('T900CC_C5_BYTE_NARROW_ALU', '1') != '0'
        )
        # P-5.8 v7.4 (pass 40) : post-emit dead byte-split alu high-half
        # peephole. Drops `adc W, H` / `sbc W, H` / `and W, H` /
        # `or W, H` / `xor W, H` when followed by a byte store of A.
        # **HW REGRESSION pass 41 (2026-05-21)** : user reported
        # enemy patterns broken on HW v7.4 ROM. Suspected cause :
        # the peephole declares the high-half dead when it sees a
        # byte store of A WITHIN THE WINDOW but doesn't scan past
        # the store to verify no LATER consumer reads WA (e.g.
        # `push WA` further out for arg passing, `LDW (mem), WA`
        # for word store reusing same WA). The high-half had set
        # W to the correct 16-bit result ; dropping it leaves W
        # with stale data if any consumer reads it later.
        # **Disabled by default** until safer scan is implemented
        # (must walk full window AFTER the byte store to verify
        # no WA-reader). User can opt in via T900CC_C5_DEAD_ALU_HI=1.
        self._opt_c5_dead_alu_hi = (
            os.environ.get('T900CC_C5_DEAD_ALU_HI', '0') == '1'
        )
        # What WA / HL currently holds: ('local', base_nib, off_int)
        # | ('global', label) | ('const', value) | None
        # P-5.6.8 (pass 28): HL tracking added in parallel to WA.
        # HL is the RHS register for byte-split ALU (`add A, L; adc W, H`)
        # so caching loads-into-HL eliminates many redundant `ld HL, imm`
        # and `ld HL, (XIY+d)` between byte-split sequences. Audit pré
        # showed 94 sites = ~−282 B body delta on j16.
        self._lvt_wa: Optional[Tuple] = None
        self._lvt_hl: Optional[Tuple] = None
        # P-5.6.9 (pass 29): track if W (high byte of WA) is known = 0.
        # Audit comparatif disasm home vs CC900 montre 3179 `ld W, 0`
        # en home vs 9 en CC900 = +3170 sites redondants = ~−6 KB body
        # potentiel. Pattern : u8 loads via `ld W, 0 ; LDB A, mem` —
        # tous les u8 reloads ré-émettent `ld W, 0` sans tracker que
        # W est déjà 0 depuis le précédent.
        # Quand True : W est garanti à 0 → `ld W, 0` peut être skip.
        # Set par : `ld W, 0` (trivial), `ld WA, imm<256` (high byte = 0).
        # Clear par : `ld W, <non-zero>`, alu/pop/call qui touche W,
        # `ld WA, imm>=256`, etc.
        self._lvt_w_zero: bool = False
        self._c5_lvt_hits = 0  # per-function elided loads

    # Set of tracked 32-bit registers (P-3). XIY excluded (frame).
    _C4_P3_TRACKED_REGS = ('XBC', 'XDE', 'XIZ', 'XIX', 'XHL', 'XWA')
    # Set of caller-clobbered regs reset at every call site.
    _C4_P3_CALL_CLOBBERED = ('XBC', 'XDE', 'XIZ', 'XIX', 'XHL', 'XWA')

    def _c4_p3_reset_all_regs(self) -> None:
        """Invalidate every tracked register (call site, basic block).

        Also clears the push/pop stack model — across a basic-block
        boundary or call we cannot reason about pushed values.
        """
        if self._reg_holds_sym:
            self._reg_holds_sym = {}
        self._c4_p3_push_stack = []

    def _c4_p3_invalidate_reg(self, reg: str) -> None:
        """Invalidate a single tracked register's cache entry."""
        if reg in self._reg_holds_sym:
            del self._reg_holds_sym[reg]

    # Map any register name (32/16/8-bit alias) to its parent tracked
    # 32-bit register. Writing to a sub-register clobbers the parent's
    # cached symbol address (the parent is no longer a valid 32-bit
    # pointer to that symbol).
    _C4_P3_PARENT_X32 = {
        # 32-bit
        'XWA': 'XWA', 'XBC': 'XBC', 'XDE': 'XDE', 'XHL': 'XHL',
        'XIX': 'XIX', 'XIY': 'XIY', 'XIZ': 'XIZ', 'XSP': 'XSP',
        # 16-bit
        'WA': 'XWA', 'BC': 'XBC', 'DE': 'XDE', 'HL': 'XHL',
        'IX': 'XIX', 'IY': 'XIY', 'IZ': 'XIZ', 'SP': 'XSP',
        # 8-bit
        'A': 'XWA', 'W': 'XWA', 'B': 'XBC', 'C': 'XBC',
        'D': 'XDE', 'E': 'XDE', 'H': 'XHL', 'L': 'XHL',
        # XIZ byte-aliases (used by reg-bank locals)
        'QIZH': 'XIZ', 'QIZL': 'XIZ', 'IZH': 'XIZ', 'IZL': 'XIZ',
        'QIXH': 'XIX', 'QIXL': 'XIX', 'IXH': 'XIX', 'IXL': 'XIX',
        'QIYH': 'XIY', 'QIYL': 'XIY', 'IYH': 'XIY', 'IYL': 'XIY',
    }

    # Mnemonics whose first operand is the destination written register.
    # `push` and `pop` are handled separately by the push/pop stack model.
    _C4_P3_WRITE_MNEMONICS = {
        'ld', 'ldw', 'ldb', 'lda',
        'inc', 'dec', 'add', 'sub', 'adc', 'sbc',
        'and', 'or', 'xor', 'sll', 'sra', 'srl',
        'exts', 'extz', 'mul', 'div', 'mulu', 'divu',
        'rl', 'rr', 'rlc', 'rrc',
    }

    def _c4_p3_check_emit_invalidation(self, line: str) -> None:
        """After emitting a line, update the reg-holds cache.

        Rules:
        - Empty line / pure comment: no effect.
        - `call ...` or `calr ...`: invalidate all caller-clobbered regs.
        - Recognized write to a tracked reg (`ld R, ...`, `pop R`, etc.):
          invalidate that reg.
        - `db 0xNN, …  ; <comment>` raw-byte emit: parse the comment to
          find the mnemonic's destination. If it writes a tracked reg,
          invalidate. If we can't parse → conservative full-reset.
        - Anything else: no effect (the cache stays).
        """
        if not self._opt_c4_p3_reg_tracker or not self._reg_holds_sym:
            return
        stripped = line.strip()
        if not stripped or stripped.startswith(';'):
            return
        # Strip inline comment to isolate the instruction.
        instr_part = stripped.split(';', 1)[0].strip()
        if not instr_part:
            return

        # Function calls clobber caller-saved regs.
        first_tok = instr_part.split(None, 1)[0].lower()
        if first_tok in ('call', 'calr'):
            for r in self._C4_P3_CALL_CLOBBERED:
                self._c4_p3_invalidate_reg(r)
            return

        # Unconditional branches (jp/jrl/jr without cond) are also
        # block-end events; the cache resets at next label anyway, but
        # we conservatively reset here too to handle fall-throughs.
        if first_tok in ('jp', 'jrl', 'jr'):
            # Could be conditional (`jrl Z, label`) — be precise.
            # Operand part: if starts with a cond mnemonic, it's conditional.
            tail = instr_part.split(None, 1)[1] if ' ' in instr_part else ''
            first_arg = tail.split(',', 1)[0].strip()
            CONDS = {'Z', 'NZ', 'C', 'NC', 'PL', 'MI', 'OV', 'NOV',
                     'LT', 'LE', 'GT', 'GE', 'ULE', 'UGT', 'EQ', 'NE',
                     'F', 'T'}
            if first_arg.upper() not in CONDS:
                # Unconditional flow change — reset.
                self._c4_p3_reset_all_regs()
            return

        # `ret` / `reti` end of basic block (rare to find more code
        # immediately after, but be safe).
        if first_tok in ('ret', 'reti', 'retd'):
            self._c4_p3_reset_all_regs()
            return

        # `db 0xXX, ...; <mnemonic> dest, src` raw-byte: parse comment.
        if first_tok == 'db':
            # Comment after ';' should hint at the instruction.
            cmt = stripped.split(';', 1)
            if len(cmt) < 2:
                # No comment → cannot tell what this writes.
                self._c4_p3_reset_all_regs()
                return
            hint = cmt[1].strip()
            # Common shapes:
            #   "LDW WA, (XIY-2)"   → writes WA
            #   "LDB A, (XDE+0)"    → writes A (part of WA, invalidate WA)
            #   "LDB (XIY-2), A"    → writes memory, not reg
            #   "LDW (XIY-2), WA"   → writes memory
            #   "LD QIZH, A"        → writes XIZ low-byte alias
            #   "LD A, QIZH"        → reads QIZH, writes A
            #   "LDA XSP, ..."      → writes XSP
            #   anything starting with paren → memory dest
            hint_up = hint.upper()
            mnemonic_match = re.match(r'\s*([A-Z]+)\s+(.+)', hint_up)
            if not mnemonic_match:
                self._c4_p3_reset_all_regs()
                return
            mn = mnemonic_match.group(1)
            rest = mnemonic_match.group(2)
            # Destination is the first operand of the mnemonic. Split
            # on comma first, then on whitespace to peel off any
            # trailing descriptive prose (e.g. `XHL  HL->XHL …`).
            dest = rest.split(',', 1)[0].strip().split(None, 1)[0]
            if dest.startswith('('):
                # Memory destination — no reg write.
                return
            # Use the same parent-map as plain text emissions.
            parent = self._C4_P3_PARENT_X32.get(dest.upper())
            if parent is not None:
                self._c4_p3_invalidate_reg(parent)
                return
            # Unknown dest — conservative reset.
            self._c4_p3_reset_all_regs()
            return

        # Parse: <mnemonic> [<dest>[, <src>...]]
        # If the mnemonic writes its first operand, identify the dest's
        # parent 32-bit reg and invalidate that.
        parts = instr_part.split(None, 1)
        mnem = parts[0].lower()
        operand_part = parts[1] if len(parts) > 1 else ''
        first_operand = operand_part.split(',', 1)[0].strip()

        # Push/pop pair tracking: a `push R` saves R's current cached
        # symbol on a model stack; the matching `pop R'` restores it
        # (so `push XDE; …; pop XDE` keeps XDE's binding live across
        # the bracketed code as long as the stack hasn't been imbalanced).
        # If `pop` lands on a different reg than the corresponding push,
        # that reg gets the saved symbol (e.g. `push XWA; pop XHL`
        # transit: XHL ends up holding whatever XWA held).
        if mnem == 'push':
            parent = self._C4_P3_PARENT_X32.get(first_operand.upper())
            if parent is not None:
                # Save the current symbol (may be None if not tracked).
                self._c4_p3_push_stack.append(self._reg_holds_sym.get(parent))
            else:
                # Pushed something we can't map (e.g. `push 0x20` immediate).
                # The stack frame still has an entry — push None to keep
                # alignment with pops.
                self._c4_p3_push_stack.append(None)
            return  # push does NOT write any tracked reg

        if mnem == 'pop':
            parent = self._C4_P3_PARENT_X32.get(first_operand.upper())
            if not self._c4_p3_push_stack:
                # Unbalanced pop (stack went below our model's depth).
                # Conservative: invalidate the popped reg.
                if parent is not None:
                    self._c4_p3_invalidate_reg(parent)
                return
            saved = self._c4_p3_push_stack.pop()
            if parent is not None:
                if saved is not None:
                    # Re-bind the popped reg to the saved symbol.
                    self._reg_holds_sym[parent] = saved
                else:
                    # The pushed source wasn't tracked → popped reg holds
                    # an unknown value, invalidate any prior binding.
                    self._c4_p3_invalidate_reg(parent)
            return

        if mnem in self._C4_P3_WRITE_MNEMONICS:
            # Memory dests start with `(` — no register write.
            if first_operand.startswith('('):
                return
            # Look up the parent 32-bit reg of the dest.
            parent = self._C4_P3_PARENT_X32.get(first_operand.upper())
            if parent is not None:
                # Invalidate the parent reg if cached.
                self._c4_p3_invalidate_reg(parent)
            # If parent is None (unknown dest like a label) we conservatively
            # do nothing — labels aren't reg writes.
            return

        # Mnemonics that don't write any reg (or write only flags / memory).
        SAFE_NONWRITE = {'push', 'jp', 'jrl', 'jr', 'nop', 'halt', 'ei', 'di',
                         'swi', 'rcf', 'scf', 'ccf', 'zcf', 'reti', 'ret',
                         'retd', 'cp', 'cpb', 'cpw', 'cpd', 'cpdr', 'cpi',
                         'cpir', 'bit', 'tst', 'link', 'unlk'}
        if mnem in SAFE_NONWRITE:
            # `link` writes XIY (frame setup) — but only at function
            # prologue; we reset at function entry anyway, so OK.
            # `unlk` restores XIY — also prologue/epilogue boundary.
            return

        # Unknown mnemonic — conservative reset (rare in practice).
        self._c4_p3_reset_all_regs()

    def fresh_label(self, hint='') -> str:
        self.label_counter += 1
        return f'.Lcc_{self.label_counter}'

    def emit(self, line: str):
        """Emit a single line to self.lines AND record it in the active
        IR container (if any).

        From C5 P-5.1 the IR container is `IRFunction` (block-level);
        ops go into `current_block.ops`. The fallback to the legacy
        `IRBuffer` remains for safety but isn't expected to fire."""
        self.lines.append(line)
        if self.ir_function is not None:
            self.ir_function.append(EmitRaw(line))
        elif self.ir_buffer is not None:
            self.ir_buffer.append(EmitRaw(line))

    def emit_comment(self, s: str):
        self.emit(f'    ; {s}')

    def emit_label(self, label: str):
        """Emit a label and start a new IR basic block.

        Chantier 4 P-3: a label = block boundary → invalidate all
        tracked address registers (conservative).
        Chantier 5 P-5.1: a label = explicit new IR basic block. The
        label is stored as block METADATA (not as an op inside the
        block) — `lower_to_asm` emits `<label>:` automatically when
        walking each block.
        Chantier 5 P-5.6.6: a label = LVT cache reset (control flow
        could enter from anywhere, WA contents unknown)."""
        self._c4_p3_reset_all_regs()
        self._lvt_reset_all()
        if self.ir_function is not None:
            # Block-level model: the label is the new block's metadata.
            # Emit the label text directly to self.lines (no need to
            # also append it as an op — lower_to_asm will reconstruct
            # the same line from block.label).
            self.ir_function.start_block(label)
            self.lines.append(f'{label}:')
        else:
            # Outside of function context (headers, externs, …) — fall
            # back to plain emit so output is unchanged.
            self.emit(f'{label}:')

    def emit_instr(self, instr: str):
        line = f'    {instr}'
        # Chantier 5 P-5.6.6: LVT skip-check. If this is a `LDW WA,
        # (BASE+off)` and `_lvt_wa` already says WA holds the value at
        # that (BASE+off) cell, SKIP emission entirely. Universal:
        # catches loads emitted via _load_local, _load_param, helpers,
        # or any other path that goes through emit_instr.
        # Generalized to all stack bases (XIY most common, XIX for
        # large-frame fns, others rare but supported).
        if self._opt_c5_lvt:
            # Skip-check 1 : LDW WA, (BASE+off) — local/param load
            m = self._LVT_LDW_WA_RE.match(line)
            if m:
                base_nib = m.group(1).upper()
                d = int(m.group(2), 16)
                if d >= 0x80:
                    d -= 0x100
                if self._lvt_wa == ('local', base_nib, d):
                    # Already in WA — skip emit, count as elided.
                    self._c5_lvt_hits += 1
                    return
            # Skip-check 2 (P-5.6.7) : ld WA, imm — const load.
            # Audit showed 280 redundant `ld WA, imm` sites in j16
            # (= ~−840 B body potential). Same const loaded multiple
            # times in sequence (typical struct field zero-init pattern).
            m = self._LVT_LD_WA_IMM_RE.match(line)
            if m:
                try:
                    v = int(m.group(1), 0) & 0xFFFF
                    if self._lvt_wa == ('const', v):
                        self._c5_lvt_hits += 1
                        return
                except ValueError:
                    pass  # fall through, label form handled by global pattern
            # Skip-check 3 (P-5.6.7) : ld WA, (_label) — global load.
            # Audit showed only 2 sites in j16 but cost is identical so
            # we wire it for completeness.
            m = self._LVT_LD_WA_GLOBAL_RE.match(line)
            if m:
                if self._lvt_wa == ('global', m.group(1)):
                    self._c5_lvt_hits += 1
                    return
            # P-5.6.8 (pass 28) HL skip-checks — parallel à WA pour les
            # 4 patterns que track le _lvt_hl. ~94 sites in j16 = ~−282 B.
            m = self._LVT_LDW_HL_RE.match(line)
            if m:
                base_nib = m.group(1).upper()
                d = int(m.group(2), 16)
                if d >= 0x80:
                    d -= 0x100
                if self._lvt_hl == ('local', base_nib, d):
                    self._c5_lvt_hits += 1
                    return
            m = self._LVT_LD_HL_IMM_RE.match(line)
            if m:
                try:
                    v = int(m.group(1), 0) & 0xFFFF
                    if self._lvt_hl == ('const', v):
                        self._c5_lvt_hits += 1
                        return
                except ValueError:
                    pass
            m = self._LVT_LD_HL_GLOBAL_RE.match(line)
            if m:
                if self._lvt_hl == ('global', m.group(1)):
                    self._c5_lvt_hits += 1
                    return
            # P-5.6.9 (pass 29) : skip `ld W, 0` if W already known 0.
            # Audit comparatif montre ~3170 sites redondants en j16.
            m = self._LVT_LD_W_VAL_RE.match(line)
            if m:
                try:
                    v = int(m.group(1), 0) & 0xFF
                    if v == 0 and self._lvt_w_zero:
                        # W already 0, skip the redundant load
                        self._c5_lvt_hits += 1
                        return
                except ValueError:
                    pass
        self.emit(line)
        # Chantier 4 P-3: update reg tracker AFTER emit so the cache
        # reflects post-instruction state for subsequent _emit_label_addr_to.
        self._c4_p3_check_emit_invalidation(line)
        # Chantier 5 P-5.6.6: LVT update — parse line for known
        # WA-affecting patterns and update _lvt_wa accordingly.
        if self._opt_c5_lvt:
            self._lvt_update_from_line(line)

    # ----- Chantier 5 P-5.6.6 Live Value Tracker helpers -----

    # Parsing regexes for emit_instr's LVT auto-update.
    # Generalized to all stack bases (XWA..XSP via 0x98..0x9F load and
    # 0xB8..0xBF store prefixes). Capture group 1 = base hex nibble
    # (e.g. 'D' for XIY = 0x9D / 0xBD), group 2 = offset hex byte.
    _LVT_LDW_WA_RE = re.compile(
        r'^\s+db\s+0x9([89A-F]),\s+0x([0-9A-F]{2}),\s+0x20\b',
        re.IGNORECASE,
    )
    _LVT_LDW_STORE_WA_RE = re.compile(
        r'^\s+db\s+0xB([89A-F]),\s+0x([0-9A-F]{2}),\s+0x50\b',
        re.IGNORECASE,
    )
    # P-5.6.6 v2 — invalidation regexes for memory writes that DON'T
    # update WA but DO modify a memory cell that might be the cached
    # cell.
    # - WORD STORE via non-WA r16 source (sub-op 0x51/52/53 = BC/DE/HL src)
    _LVT_WORD_STORE_NONWA_RE = re.compile(
        r'^\s+db\s+0xB([89A-F]),\s+0x([0-9A-F]{2}),\s+0x5[1-3]\b',
        re.IGNORECASE,
    )
    # - BYTE STORE via any r8 source (sub-op 0x40..0x47)
    _LVT_BYTE_STORE_RE = re.compile(
        r'^\s+db\s+0xB([89A-F]),\s+0x([0-9A-F]{2}),\s+0x4[0-7]\b',
        re.IGNORECASE,
    )
    # Set of "frame pointer" base nibbles: XIY = D, XIZ = E. Other
    # bases (XWA=8, XBC=9, XDE=A, XHL=B, XIX=C, XSP=F) can be POINTER
    # registers used to alias a frame slot.
    _LVT_FRAME_BASE_NIBS = frozenset({'D', 'E'})
    # Mapping cache-key base_nib → physical reg name (for use-def
    # comparison when checking if the cached base reg gets written).
    _LVT_NIB_TO_REG = {
        '8': 'XWA', '9': 'XBC', 'A': 'XDE', 'B': 'XHL',
        'C': 'XIX', 'D': 'XIY', 'E': 'XIZ', 'F': 'XSP',
    }
    _LVT_LD_WA_IMM_RE = re.compile(
        r'^\s+ld\s+WA,\s+(-?\d+|0x[0-9A-Fa-f]+)\s*(?:;|$)',
        re.IGNORECASE,
    )
    _LVT_LD_WA_GLOBAL_RE = re.compile(
        r'^\s+ld\s+WA,\s+\(([A-Za-z_][A-Za-z_0-9]*)\)\s*(?:;|$)',
        re.IGNORECASE,
    )
    _LVT_LD_W_IMM_RE = re.compile(r'^\s+ld\s+W,\s+', re.IGNORECASE)
    # P-5.6.9 (pass 29) — `ld W, 0` specific match (dominant u8 zero-ext
    # pattern). Captures group(1) = the immediate value.
    _LVT_LD_W_VAL_RE = re.compile(
        r'^\s+ld\s+W,\s+(-?\d+|0x[0-9A-Fa-f]+)\s*(?:;|$)',
        re.IGNORECASE,
    )
    _LVT_LD_A_RE = re.compile(r'^\s+ld\s+A,\s+', re.IGNORECASE)
    # P-5.6.9 — ops that write A only (not W). Used to refine the
    # use-def fallback's W=0 invalidation decision.
    # Covers :
    #   - alu mnemonic form : `add A, L`, `sub A, L`, ..., `inc A`, `dec A`
    #   - byte load mnemonic : `ld A, ...`, `ldb A, ...`
    #   - byte load `db` form with hint : `db 0xXX, ..., 0x21  ; LDB A, ...`
    _LVT_A_ONLY_WRITE_RE = re.compile(
        r'^\s+(?:add|sub|adc|sbc|and|or|xor|inc|dec|sll|sra|srl|rl|rr|rlc|rrc|ld|ldb)\s+A\b'
        r'|^\s+db\s+[^;]*;\s*ldb?\s+A\b',
        re.IGNORECASE,
    )
    # P-5.6.8 (pass 28) HL tracking regexes — mirror of WA versions.
    # `LDW HL, (BASE+off)` = `db 0x9{N}, d, 0x23` (sub-op 0x23 = HL dest).
    _LVT_LDW_HL_RE = re.compile(
        r'^\s+db\s+0x9([89A-F]),\s+0x([0-9A-F]{2}),\s+0x23\b',
        re.IGNORECASE,
    )
    # `LDW (BASE+off), HL` = `db 0xB{N}, d, 0x53` (sub-op 0x53 = HL src).
    _LVT_LDW_STORE_HL_RE = re.compile(
        r'^\s+db\s+0xB([89A-F]),\s+0x([0-9A-F]{2}),\s+0x53\b',
        re.IGNORECASE,
    )
    # `ld HL, imm` mnemonic form
    _LVT_LD_HL_IMM_RE = re.compile(
        r'^\s+ld\s+HL,\s+(-?\d+|0x[0-9A-Fa-f]+)\s*(?:;|$)',
        re.IGNORECASE,
    )
    # `ld HL, (_label)` mnemonic form
    _LVT_LD_HL_GLOBAL_RE = re.compile(
        r'^\s+ld\s+HL,\s+\(([A-Za-z_][A-Za-z_0-9]*)\)\s*(?:;|$)',
        re.IGNORECASE,
    )
    # Partial HL writes: `ld H, ...` or `ld L, ...` invalidate HL cache.
    _LVT_LD_H_RE = re.compile(r'^\s+ld\s+H,\s+', re.IGNORECASE)
    _LVT_LD_L_RE = re.compile(r'^\s+ld\s+L,\s+', re.IGNORECASE)

    def _lvt_reset_all(self) -> None:
        """Clear all tracked-value caches. Called at function entry,
        labels, calls, and any other point where WA / HL contents
        become uncertain.
        """
        self._lvt_wa = None
        self._lvt_hl = None
        self._lvt_w_zero = False

    def _lvt_invalidate_word_write(self, base_nib: str, off: int) -> None:
        """Memory at (BASE+off) was written. Check both WA and HL caches.
        Invalidate any that points to the same cell or possible alias.

        P-5.6.8 pass 28: refactored to handle both _lvt_wa and _lvt_hl.

        - Same base + same offset = same cell → invalidate.
        - Both frame-based (XIY/XIZ) different offsets = different locals
          (no alias) → keep cache.
        - Either side is a pointer base (XDE / XBC / XHL / XIX) →
          POTENTIAL ALIAS via address-taken local → conservatively
          invalidate.
        """
        for attr in ('_lvt_wa', '_lvt_hl'):
            cache = getattr(self, attr)
            if not isinstance(cache, tuple) or cache[0] != 'local':
                continue
            _, cached_base, cached_off = cache
            if base_nib == cached_base and off == cached_off:
                setattr(self, attr, None)
                continue
            if (base_nib not in self._LVT_FRAME_BASE_NIBS
                    or cached_base not in self._LVT_FRAME_BASE_NIBS):
                setattr(self, attr, None)

    def _lvt_invalidate_byte_write(self, base_nib: str, off: int) -> None:
        """Memory byte at (BASE+off) was written. Word cell at cached
        offset spans cached_off..cached_off+1 — a byte store at either
        cached_off or cached_off+1 (same base) modifies the cell.

        Conservative cross-base aliasing same as `_lvt_invalidate_word_write`.
        Checks both WA and HL caches (P-5.6.8).
        """
        for attr in ('_lvt_wa', '_lvt_hl'):
            cache = getattr(self, attr)
            if not isinstance(cache, tuple) or cache[0] != 'local':
                continue
            _, cached_base, cached_off = cache
            if base_nib == cached_base:
                if off == cached_off or off == (cached_off + 1) & 0xFF:
                    setattr(self, attr, None)
                # else: same base, non-overlapping byte → safe
                continue
            if (base_nib not in self._LVT_FRAME_BASE_NIBS
                    or cached_base not in self._LVT_FRAME_BASE_NIBS):
                setattr(self, attr, None)

    def _lvt_update_from_line(self, line: str) -> None:
        """Inspect a JUST-EMITTED asm line and update `_lvt_wa` / `_lvt_hl`
        to reflect post-instruction state.

        Recognized patterns set the cache; any other XWA/XHL-writing line
        clears it (conservative). P-5.6.8 pass 28 : HL tracking added
        in parallel to WA.
        """
        # ----- WA load patterns -----
        # Pattern: `db 0x9{N}, d, 0x20  ; LDW WA, (BASE+d)` → WA holds local
        m = self._LVT_LDW_WA_RE.match(line)
        if m:
            base_nib = m.group(1).upper()
            d = int(m.group(2), 16)
            if d >= 0x80:
                d -= 0x100
            self._lvt_wa = ('local', base_nib, d)
            return
        # Pattern: `db 0xB{N}, d, 0x50  ; LDW (BASE+d), WA` → after store,
        # WA still holds the value at mem[BASE+d]. Also INVALIDATE HL
        # cache if it pointed to the same cell (cross-cache invariant).
        m = self._LVT_LDW_STORE_WA_RE.match(line)
        if m:
            base_nib = m.group(1).upper()
            d = int(m.group(2), 16)
            if d >= 0x80:
                d -= 0x100
            self._lvt_invalidate_word_write(base_nib, d)  # invalidate HL if aliased
            self._lvt_wa = ('local', base_nib, d)  # then SET wa
            return
        # ----- HL load patterns (P-5.6.8 pass 28) -----
        # Pattern: `db 0x9{N}, d, 0x23  ; LDW HL, (BASE+d)` → HL holds local
        m = self._LVT_LDW_HL_RE.match(line)
        if m:
            base_nib = m.group(1).upper()
            d = int(m.group(2), 16)
            if d >= 0x80:
                d -= 0x100
            self._lvt_hl = ('local', base_nib, d)
            return
        # Pattern: `db 0xB{N}, d, 0x53  ; LDW (BASE+d), HL` → post-store,
        # HL holds mem[BASE+d]. Invalidate WA cache if aliased.
        m = self._LVT_LDW_STORE_HL_RE.match(line)
        if m:
            base_nib = m.group(1).upper()
            d = int(m.group(2), 16)
            if d >= 0x80:
                d -= 0x100
            self._lvt_invalidate_word_write(base_nib, d)  # invalidate WA if aliased
            self._lvt_hl = ('local', base_nib, d)
            return
        # ----- Memory store invalidations (no WA/HL write) -----
        # word store via non-WA / non-HL r16 source (sub-op 0x51 BC or 0x52 DE)
        m = self._LVT_WORD_STORE_NONWA_RE.match(line)
        if m:
            base_nib = m.group(1).upper()
            d = int(m.group(2), 16)
            if d >= 0x80:
                d -= 0x100
            self._lvt_invalidate_word_write(base_nib, d)
            return
        # byte store via any r8 source
        m = self._LVT_BYTE_STORE_RE.match(line)
        if m:
            base_nib = m.group(1).upper()
            d = int(m.group(2), 16)
            if d >= 0x80:
                d -= 0x100
            self._lvt_invalidate_byte_write(base_nib, d)
            return
        # ----- WA immediate / global load -----
        m = self._LVT_LD_WA_IMM_RE.match(line)
        if m:
            try:
                v = int(m.group(1), 0) & 0xFFFF
                self._lvt_wa = ('const', v)
                # P-5.6.9 : ld WA, imm — high byte of imm is W.
                # If high byte is 0, W is now known = 0.
                self._lvt_w_zero = ((v >> 8) & 0xFF) == 0
                return
            except ValueError:
                pass
        m = self._LVT_LD_WA_GLOBAL_RE.match(line)
        if m:
            self._lvt_wa = ('global', m.group(1))
            # WA loaded from memory — unknown high byte, clear W=0 cache
            self._lvt_w_zero = False
            return
        # ----- HL immediate / global load (P-5.6.8) -----
        m = self._LVT_LD_HL_IMM_RE.match(line)
        if m:
            try:
                v = int(m.group(1), 0)
                self._lvt_hl = ('const', v & 0xFFFF)
                return
            except ValueError:
                pass
        m = self._LVT_LD_HL_GLOBAL_RE.match(line)
        if m:
            self._lvt_hl = ('global', m.group(1))
            return
        # ----- Partial WA / HL writes (invalidate) -----
        m_w = self._LVT_LD_W_VAL_RE.match(line)
        if m_w:
            # `ld W, imm` — sets W partially. wa cache invalidated.
            # Update W=0 cache : True iff imm == 0.
            self._lvt_wa = None
            try:
                v = int(m_w.group(1), 0) & 0xFF
                self._lvt_w_zero = (v == 0)
            except ValueError:
                self._lvt_w_zero = False
            return
        if self._LVT_LD_W_IMM_RE.match(line) or self._LVT_LD_A_RE.match(line):
            # `ld W, <not-imm>` (e.g. `ld W, H` reg-to-reg) or `ld A, ...`
            # Both write part of WA. wa cache invalidated.
            # `ld W, <reg>` makes W unknown → clear W=0 cache.
            # `ld A, ...` doesn't touch W → keep W=0 cache as-is.
            self._lvt_wa = None
            if self._LVT_LD_W_IMM_RE.match(line):
                self._lvt_w_zero = False
            return
        if self._LVT_LD_H_RE.match(line) or self._LVT_LD_L_RE.match(line):
            self._lvt_hl = None
            return
        # ----- Generic use-def fallback -----
        from t900cc_liveness import _extract_uses_defs_from_text
        _uses, defs = _extract_uses_defs_from_text(line)
        if 'XWA' in defs:
            self._lvt_wa = None
            # P-5.6.9 : XWA write may or may not touch W. Use-def parser
            # is too coarse (it maps `A` to XWA parent). Distinguish :
            # if the op is an A-only alu (add A, sub A, ...), W is NOT
            # modified → keep W=0 cache. Otherwise clear conservatively.
            if not self._LVT_A_ONLY_WRITE_RE.match(line):
                self._lvt_w_zero = False
        if 'XHL' in defs:
            self._lvt_hl = None
        # P-5.6.6 v3 — cached BASE register write invalidates the cache
        # (for both WA and HL caches).
        for attr in ('_lvt_wa', '_lvt_hl'):
            cache = getattr(self, attr)
            if not isinstance(cache, tuple) or cache[0] != 'local':
                continue
            cached_base_reg = self._LVT_NIB_TO_REG.get(cache[1])
            if cached_base_reg is not None and cached_base_reg in defs:
                setattr(self, attr, None)

    def _emit_stack_alloc_lda(self, n_bytes: int):
        """Allocate local stack space with hardware-safe LDA XSP,(XSP+s8) chunks."""
        remaining = n_bytes
        while remaining > 0:
            # LDA uses a signed 8-bit displacement. Keep chunks even and comfortably in range.
            chunk = 126 if remaining > 126 else remaining
            if chunk <= 0:
                break
            self.emit_instr(
                f'lda  XSP, (XSP-{chunk})   ; alloc {chunk}B locals via hw-safe stack adjust'
            )
            remaining -= chunk

    def _stack_base_reg(self, sym: Symbol) -> str:
        if sym.scope == 'param':
            return 'XIY'
        return self._frame_reg

    def _stack_base_idx(self, base_reg: str) -> int:
        return {'XWA': 0, 'XBC': 1, 'XDE': 2, 'XHL': 3,
                'XIX': 4, 'XIY': 5, 'XIZ': 6, 'XSP': 7}[base_reg]

    def _emit_stack_base_low16_to_wa(self, base_reg: str):
        self.emit_instr(f'push {base_reg}')
        self.emit_instr('pop  WA')
        self.emit_instr('add  XSP, 2')

    def _emit_stack_sym_addr_to_xwa(self, sym: Symbol):
        if sym.reg_name:
            self.emit_comment(f'ERROR: cannot take address of register local {sym.name!r}')
            self.emit_instr('ld   XWA, 0')
            return
        offset_u16 = sym.offset & 0xFFFF
        self._emit_stack_base_low16_to_wa(self._stack_base_reg(sym))
        self.emit_instr(f'ld   HL, {offset_u16}')
        self.emit_instr('add  A,  L')
        self.emit_instr('adc  W,  H')
        self.emit_instr('extz XWA')

    def _emit_addr_scratch_load(self, label: str, comment: str = ''):
        if self._save_xiz_regbank and self._scratch_addr_reg == 'XIZ' and not self._addr_scratch_saved_xiz:
            self.emit_instr('push XIZ')
            self._addr_scratch_saved_xiz = True
        suffix = f'   ; {comment}' if comment else ''
        self.emit_instr(f'ld   {self._scratch_addr_reg}, {label}{suffix}')

    def _emit_addr_scratch_to(self, dst_reg: str):
        if dst_reg == self._scratch_addr_reg:
            return
        self.emit_instr(f'push {self._scratch_addr_reg}')
        self.emit_instr(f'pop  {dst_reg}')
        if self._addr_scratch_saved_xiz:
            self.emit_instr('pop  XIZ')
            self._addr_scratch_saved_xiz = False

    def _emit_label_addr_to(self, dst_reg: str, label: str, comment: str = ''):
        """Chantier A (2026-04-20): emit `ld <dst_reg>, label` directly instead
        of the 7-byte pattern `ld XIZ, label; push XIZ; pop <dst_reg>`.

        Saves 2 B per site (4 sites × 2 B = 8 B minimum per shmup function,
        multiplied across all hot paths → several KB total). Encoding
        `ld R32, imm32` = `0x40+r_code, lo, mid, hi, hi` (5 B) confirmed
        safe hardware (J8-1 bisect 2026-03-23, Ganbare uses 0x45=XIY).

        The caller is assumed to want `dst_reg` clobbered; this matches every
        previous `_emit_addr_scratch_load + _emit_addr_scratch_to(dst_reg)` site.

        Chantier 4 Phase P-3 (2026-05-20): consult the intra-function
        register tracker (`_reg_holds_sym`). If `dst_reg` already holds
        the address of `label` (set by a previous call in this basic
        block and not invalidated since), skip emission — saves 5 bytes
        per elided site. Tracks symbol→register state through
        `_c4_p3_check_emit_invalidation`.
        """
        if self._opt_c4_p3_reg_tracker:
            current = self._reg_holds_sym.get(dst_reg)
            if current is not None and current == label:
                # Already in place — skip emission.
                self._c4_p3_elision_count += 1
                return
        suffix = f'   ; {comment}' if comment else ''
        self.emit_instr(f'ld   {dst_reg}, {label}{suffix}')
        # Record the new state (overwrites any prior tracked symbol).
        if self._opt_c4_p3_reg_tracker:
            self._reg_holds_sym[dst_reg] = label

    def _emit_xiz_bank_load_a(self, bank_name: str):
        bank_code = {'IZL': 0xF8, 'IZH': 0xF9, 'QIZL': 0xFA, 'QIZH': 0xFB}[bank_name]
        self.emit_instr(f'db 0xC7, 0x{bank_code:02X}, 0x99  ; LD A, {bank_name}')

    def _emit_xiz_bank_store_a(self, bank_name: str):
        bank_code = {'IZL': 0xF8, 'IZH': 0xF9, 'QIZL': 0xFA, 'QIZH': 0xFB}[bank_name]
        self.emit_instr(f'db 0xC7, 0x{bank_code:02X}, 0x89  ; LD {bank_name}, A')

    def _contains_addr_taken_local(self, node) -> set:
        names = set()
        if node is None:
            return names
        if isinstance(node, (Type, StructField)):
            return names
        if isinstance(node, AddrOf):
            if isinstance(node.expr, Ident):
                names.add(node.expr.name)
            names.update(self._contains_addr_taken_local(node.expr))
            return names
        if isinstance(node, InitList):
            for value in node.values:
                names.update(self._contains_addr_taken_local(value))
            return names
        if hasattr(node, '__dataclass_fields__'):
            for field_info in fields(node):
                value = getattr(node, field_info.name)
                if isinstance(value, list):
                    for item in value:
                        names.update(self._contains_addr_taken_local(item))
                else:
                    names.update(self._contains_addr_taken_local(value))
        return names

    def _direct_abs_scalar_symbol(self, node):
        if not self.opt_perf_lag_7 or not isinstance(node, Ident):
            return None
        name = node.name
        if name in self.static_local_globals:
            sym = self.static_local_globals[name]
            label = f'_{sym.name}'
        elif name in self.sem.globals:
            sym = self.sem.globals[name]
            label = f'_{name}'
        else:
            return None
        if sym.is_far or isinstance(sym.type_, ArrayType):
            return None
        if self.type_size(sym.type_) > 2:
            return None
        return sym, label

    def _emit_direct_abs_scalar_store(self, label: str, sz: int):
        if sz == 1:
            self.emit_instr(f'ld   ({label}), A')
        else:
            self.emit_instr(f'ldw  ({label}), WA')

    @staticmethod
    def _mem_size_suffix(sz: int) -> str:
        return {1: 'b', 2: 'w', 4: 'l'}[sz]

    def _direct_abs_int_symbol(self, node, max_nbytes: int = 2):
        direct = self._direct_abs_scalar_symbol(node)
        if direct is None:
            return None
        sym, label = direct
        if not isinstance(sym.type_, IntType):
            return None
        if self.type_size(sym.type_) > max_nbytes:
            return None
        return sym, label

    def _emit_direct_abs_mem_inc_dec(self, label: str, sz: int, delta: int):
        suffix = self._mem_size_suffix(sz)
        if delta > 0:
            self.emit_instr(f'inc{suffix} 1, ({label})')
        else:
            self.emit_instr(f'dec{suffix} 1, ({label})')

    def _emit_direct_abs_mem_alu_const(self, op: str, label: str, sz: int, imm: int):
        suffix = self._mem_size_suffix(sz)
        mnem = {
            '+': 'add',
            '-': 'sub',
            '&': 'and',
            '|': 'or',
            '^': 'xor',
        }[op]
        mask = (1 << (sz * 8)) - 1
        self.emit_instr(f'{mnem}{suffix} ({label}), {imm & mask}')

    def _emit_direct_abs_mem_alu_reg(self, op: str, label: str, sz: int, reg: str):
        suffix = self._mem_size_suffix(sz)
        mnem = {
            '+': 'add',
            '-': 'sub',
            '&': 'and',
            '|': 'or',
            '^': 'xor',
        }[op]
        self.emit_instr(f'{mnem}{suffix} ({label}), {reg}')

    def _emit_cmp_after_cp_branch(self, op: str, label_target: str):
        if op == '==':
            self.emit_instr(f'jrl  Z,  {label_target}')
        elif op == '!=':
            self.emit_instr(f'jrl  NZ, {label_target}')
        elif op == '<':
            self.emit_instr(f'jrl  C,  {label_target}')
        elif op == '>=':
            self.emit_instr(f'jrl  NC, {label_target}')
        elif op == '>':
            label_done = self.fresh_label('cmp_done')
            self.emit_instr(f'jrl  Z,  {label_done}')
            self.emit_instr(f'jrl  NC, {label_target}')
            self.emit_label(label_done)
        elif op == '<=':
            self.emit_instr(f'jrl  C,  {label_target}')
            self.emit_instr(f'jrl  Z,  {label_target}')
        else:
            raise ValueError(f"Unsupported compare op {op}")

    def _contains_any_call(self, node) -> bool:
        """True if the subtree contains any FuncCall (user function) or
        IndirectCall. Used by P2 leaf detection. Does NOT include __asm
        pseudo-calls (those are inline asm — separate check)."""
        if node is None:
            return False
        if isinstance(node, FuncCall):
            if node.name in ('__asm', '__asm__'):
                return any(self._contains_any_call(a) for a in node.args)
            return True
        if isinstance(node, IndirectCall):
            return True
        # Structural recurse — mirror _contains_inline_asm shape
        if isinstance(node, Block):
            return any(self._contains_any_call(s) for s in node.stmts)
        if isinstance(node, VarDecl):
            return self._contains_any_call(node.init_expr)
        if isinstance(node, IfStmt):
            return (self._contains_any_call(node.cond) or
                    self._contains_any_call(node.then) or
                    self._contains_any_call(node.else_))
        if isinstance(node, WhileStmt):
            return self._contains_any_call(node.cond) or self._contains_any_call(node.body)
        if isinstance(node, ForStmt):
            return (self._contains_any_call(node.init) or
                    self._contains_any_call(node.cond) or
                    self._contains_any_call(node.step) or
                    self._contains_any_call(node.body))
        if isinstance(node, DoWhileStmt):
            return self._contains_any_call(node.body) or self._contains_any_call(node.cond)
        if isinstance(node, ReturnStmt):
            return self._contains_any_call(node.expr)
        if isinstance(node, LabelStmt):
            return self._contains_any_call(node.stmt)
        if isinstance(node, SwitchStmt):
            if self._contains_any_call(node.expr):
                return True
            for clause in node.clauses:
                if self._contains_any_call(clause.value):
                    return True
                if any(self._contains_any_call(s) for s in clause.stmts):
                    return True
            return False
        if isinstance(node, ExprStmt):
            return self._contains_any_call(node.expr)
        if isinstance(node, BinOp):
            return self._contains_any_call(node.left) or self._contains_any_call(node.right)
        if isinstance(node, UnaryOp):
            return self._contains_any_call(node.expr)
        if isinstance(node, Assign):
            return self._contains_any_call(node.target) or self._contains_any_call(node.value)
        if isinstance(node, Subscript):
            return self._contains_any_call(node.base) or self._contains_any_call(node.index)
        if isinstance(node, Deref):
            return self._contains_any_call(node.expr)
        if isinstance(node, AddrOf):
            return self._contains_any_call(node.expr)
        if isinstance(node, Cast):
            return self._contains_any_call(node.expr)
        if isinstance(node, FieldAccess):
            return self._contains_any_call(node.expr)
        if isinstance(node, Ternary):
            return (self._contains_any_call(node.cond) or
                    self._contains_any_call(node.then) or
                    self._contains_any_call(node.else_))
        if isinstance(node, InitList):
            return any(self._contains_any_call(v) for v in node.values)
        return False

    def _contains_param_assign(self, node, param_names) -> bool:
        """True if any `pname = …` assignment OR `pname++`/`pname--` OR
        compound assign mutates a param from the given set. Leaf adecl
        path requires params to stay read-only in their live reg."""
        if node is None:
            return False
        if isinstance(node, Assign):
            t = node.target
            if isinstance(t, Ident) and t.name in param_names:
                return True
            return (self._contains_param_assign(node.target, param_names) or
                    self._contains_param_assign(node.value, param_names))
        if isinstance(node, UnaryOp):
            # ++/-- mutate the operand
            if node.op in ('++', '--', 'pre++', 'pre--', 'post++', 'post--'):
                if isinstance(node.expr, Ident) and node.expr.name in param_names:
                    return True
            return self._contains_param_assign(node.expr, param_names)
        # Structural recurse
        if isinstance(node, Block):
            return any(self._contains_param_assign(s, param_names) for s in node.stmts)
        if isinstance(node, VarDecl):
            return self._contains_param_assign(node.init_expr, param_names)
        if isinstance(node, IfStmt):
            return (self._contains_param_assign(node.cond, param_names) or
                    self._contains_param_assign(node.then, param_names) or
                    self._contains_param_assign(node.else_, param_names))
        if isinstance(node, WhileStmt):
            return (self._contains_param_assign(node.cond, param_names) or
                    self._contains_param_assign(node.body, param_names))
        if isinstance(node, ForStmt):
            return (self._contains_param_assign(node.init, param_names) or
                    self._contains_param_assign(node.cond, param_names) or
                    self._contains_param_assign(node.step, param_names) or
                    self._contains_param_assign(node.body, param_names))
        if isinstance(node, DoWhileStmt):
            return (self._contains_param_assign(node.body, param_names) or
                    self._contains_param_assign(node.cond, param_names))
        if isinstance(node, ReturnStmt):
            return self._contains_param_assign(node.expr, param_names)
        if isinstance(node, LabelStmt):
            return self._contains_param_assign(node.stmt, param_names)
        if isinstance(node, SwitchStmt):
            if self._contains_param_assign(node.expr, param_names):
                return True
            for clause in node.clauses:
                if self._contains_param_assign(clause.value, param_names):
                    return True
                if any(self._contains_param_assign(s, param_names) for s in clause.stmts):
                    return True
            return False
        if isinstance(node, ExprStmt):
            return self._contains_param_assign(node.expr, param_names)
        if isinstance(node, BinOp):
            return (self._contains_param_assign(node.left, param_names) or
                    self._contains_param_assign(node.right, param_names))
        if isinstance(node, FuncCall):
            return any(self._contains_param_assign(a, param_names) for a in node.args)
        if isinstance(node, IndirectCall):
            return (self._contains_param_assign(node.callee, param_names) or
                    any(self._contains_param_assign(a, param_names) for a in node.args))
        if isinstance(node, Subscript):
            return (self._contains_param_assign(node.base, param_names) or
                    self._contains_param_assign(node.index, param_names))
        if isinstance(node, Deref):
            return self._contains_param_assign(node.expr, param_names)
        if isinstance(node, AddrOf):
            return self._contains_param_assign(node.expr, param_names)
        if isinstance(node, Cast):
            return self._contains_param_assign(node.expr, param_names)
        if isinstance(node, FieldAccess):
            return self._contains_param_assign(node.expr, param_names)
        if isinstance(node, Ternary):
            return (self._contains_param_assign(node.cond, param_names) or
                    self._contains_param_assign(node.then, param_names) or
                    self._contains_param_assign(node.else_, param_names))
        if isinstance(node, InitList):
            return any(self._contains_param_assign(v, param_names) for v in node.values)
        return False

    def _contains_inline_asm(self, node) -> bool:
        if node is None:
            return False
        if isinstance(node, Block):
            return any(self._contains_inline_asm(s) for s in node.stmts)
        if isinstance(node, VarDecl):
            return self._contains_inline_asm(node.init_expr)
        if isinstance(node, IfStmt):
            return (self._contains_inline_asm(node.cond) or
                    self._contains_inline_asm(node.then) or
                    self._contains_inline_asm(node.else_))
        if isinstance(node, WhileStmt):
            return self._contains_inline_asm(node.cond) or self._contains_inline_asm(node.body)
        if isinstance(node, ForStmt):
            return (self._contains_inline_asm(node.init) or
                    self._contains_inline_asm(node.cond) or
                    self._contains_inline_asm(node.step) or
                    self._contains_inline_asm(node.body))
        if isinstance(node, DoWhileStmt):
            return self._contains_inline_asm(node.body) or self._contains_inline_asm(node.cond)
        if isinstance(node, ReturnStmt):
            return self._contains_inline_asm(node.expr)
        if isinstance(node, LabelStmt):
            return self._contains_inline_asm(node.stmt)
        if isinstance(node, SwitchStmt):
            if self._contains_inline_asm(node.expr):
                return True
            for clause in node.clauses:
                if self._contains_inline_asm(clause.value):
                    return True
                if any(self._contains_inline_asm(s) for s in clause.stmts):
                    return True
            return False
        if isinstance(node, ExprStmt):
            return self._contains_inline_asm(node.expr)
        if isinstance(node, IndirectCall):
            return (self._contains_inline_asm(node.callee) or
                    any(self._contains_inline_asm(a) for a in node.args))
        if isinstance(node, BinOp):
            return self._contains_inline_asm(node.left) or self._contains_inline_asm(node.right)
        if isinstance(node, UnaryOp):
            return self._contains_inline_asm(node.expr)
        if isinstance(node, Assign):
            return self._contains_inline_asm(node.target) or self._contains_inline_asm(node.value)
        if isinstance(node, FuncCall):
            if node.name in ('__asm', '__asm__'):
                return True
            return any(self._contains_inline_asm(a) for a in node.args)
        if isinstance(node, Subscript):
            return self._contains_inline_asm(node.base) or self._contains_inline_asm(node.index)
        if isinstance(node, Deref):
            return self._contains_inline_asm(node.expr)
        if isinstance(node, AddrOf):
            return self._contains_inline_asm(node.expr)
        if isinstance(node, Cast):
            return self._contains_inline_asm(node.expr)
        if isinstance(node, FieldAccess):
            return self._contains_inline_asm(node.expr)
        if isinstance(node, Ternary):
            return (self._contains_inline_asm(node.cond) or
                    self._contains_inline_asm(node.then) or
                    self._contains_inline_asm(node.else_))
        if isinstance(node, InitList):
            return any(self._contains_inline_asm(v) for v in node.values)
        return False

    # -- Type helpers --

    def type_size(self, ty: Type) -> int:
        if ty is None:
            return 2
        return ty.size()

    def _init_scalar_slots(self, ty: Type) -> int:
        """Number of scalar initializer expressions needed for one value of type ty."""
        if ty is None:
            return 1
        if isinstance(ty, (IntType, PtrType)):
            return 1
        if isinstance(ty, ArrayType):
            if ty.count <= 0:
                return 0
            elem_slots = self._init_scalar_slots(ty.elem)
            return elem_slots * ty.count if elem_slots > 0 else 0
        if isinstance(ty, StructType):
            total = 0
            for field in ty.fields:
                total += self._init_scalar_slots(field.type_)
            return total
        return 1

    def _infer_unsized_array_type(self, ty: Type, init_expr) -> Type:
        """Infer [] count from a flat initializer list when possible."""
        if not isinstance(ty, ArrayType) or ty.count != 0 or not isinstance(init_expr, InitList):
            return ty
        elem_slots = self._init_scalar_slots(ty.elem)
        count = len(init_expr.values) if elem_slots <= 0 else (len(init_expr.values) + elem_slots - 1) // elem_slots
        return ArrayType(ty.elem, count)

    def _sync_global_decl_symbol(self, decl: 'VarDecl'):
        """Keep semantic global symbol metadata aligned with declaration rewrites."""
        sym = self.sem.globals.get(decl.name)
        if sym is None:
            return
        sym.type_ = decl.type_
        sym.is_far = decl.is_const and not isinstance(decl.type_, PtrType)  # pointer-to-const stays in RAM

    def _eval_init_const(self, expr) -> int:
        """Constant-fold a scalar initializer expression."""
        if isinstance(expr, int):
            return expr
        if isinstance(expr, Const):
            return expr.value
        if isinstance(expr, UnaryOp) and expr.op == 'sizeof':
            return self.type_size(self.typeof_expr(expr.expr))
        if isinstance(expr, UnaryOp) and expr.op == '-':
            return -self._eval_init_const(expr.expr)
        if isinstance(expr, UnaryOp) and expr.op == '~':
            return ~self._eval_init_const(expr.expr)
        if isinstance(expr, Cast):
            return self._eval_init_const(expr.expr)
        if isinstance(expr, BinOp):
            l = self._eval_init_const(expr.left)
            r = self._eval_init_const(expr.right)
            if expr.op == '+':  return l + r
            if expr.op == '-':  return l - r
            if expr.op == '*':  return l * r
            if expr.op == '/':
                if r == 0:
                    return 0
                q = abs(l) // abs(r)
                return -q if ((l < 0) ^ (r < 0)) else q
            if expr.op == '%':
                if r == 0:
                    return 0
                q = abs(l) // abs(r)
                q = -q if ((l < 0) ^ (r < 0)) else q
                return l - (q * r)
            if expr.op == '|':  return l | r
            if expr.op == '&':  return l & r
            if expr.op == '<<': return l << r
            if expr.op == '>>': return l >> r
        return 0

    def _const_ptr_operand(self, expr):
        """Return an assembler operand for a constant pointer initializer."""
        if isinstance(expr, int):
            return expr
        if isinstance(expr, Cast):
            return self._const_ptr_operand(expr.expr)
        if isinstance(expr, Ident):
            if expr.name in self.sem.globals:
                return f'_{expr.name}'
        if isinstance(expr, AddrOf):
            inner = expr.expr
            if isinstance(inner, Ident):
                return f'_{inner.name}'
        if isinstance(expr, Const):
            return expr.value
        return self._eval_init_const(expr)

    def _const_init_operand(self, expr, ty: Type):
        if isinstance(ty, PtrType):
            return self._const_ptr_operand(expr)
        return self._eval_init_const(expr)

    def _serialize_init_pieces(self, ty: Type, values: List[Any], idx: List[int],
                               pieces: List[Tuple[int, Any]]):
        """Serialize an initializer into exact-size DB/DW/DL pieces."""
        if isinstance(ty, ArrayType):
            for _ in range(ty.count):
                self._serialize_init_pieces(ty.elem, values, idx, pieces)
            return

        if isinstance(ty, StructType):
            cur_off = 0
            for field in ty.fields:
                while cur_off < field.offset:
                    pieces.append((1, 0))
                    cur_off += 1
                self._serialize_init_pieces(field.type_, values, idx, pieces)
                cur_off = field.offset + self.type_size(field.type_)
            while cur_off < self.type_size(ty):
                pieces.append((1, 0))
                cur_off += 1
            return

        sz = self.type_size(ty)
        if sz <= 0:
            return
        expr = values[idx[0]] if idx[0] < len(values) else 0
        if idx[0] < len(values):
            idx[0] += 1
        pieces.append((sz, self._const_init_operand(expr, ty)))

    def _build_init_pieces(self, ty: Type, init_expr: InitList) -> List[Tuple[int, Any]]:
        pieces: List[Tuple[int, Any]] = []
        idx = [0]
        self._serialize_init_pieces(ty, init_expr.values, idx, pieces)
        return pieces

    def _emit_init_pieces(self, out: List[str], pieces: List[Tuple[int, Any]]):
        """Emit grouped DB/DW/DL lines from typed initializer pieces."""
        dir_map = {1: 'db', 2: 'dw', 4: 'dl'}
        cur_sz = 0
        cur_ops: List[str] = []

        def flush():
            nonlocal cur_sz, cur_ops
            if not cur_ops:
                return
            out.append(f'    {dir_map[cur_sz]} {", ".join(cur_ops)}')
            cur_sz = 0
            cur_ops = []

        for sz, operand in pieces:
            if sz not in dir_map:
                raise ValueError(f"Unsupported initializer chunk size {sz}")
            op_str = operand if isinstance(operand, str) else str(operand)
            if cur_ops and (cur_sz != sz or len(cur_ops) >= 16):
                flush()
            cur_sz = sz
            cur_ops.append(op_str)
        flush()

    def stack_size_for(self, ty: Type) -> int:
        """Size to push on stack for an argument of this type."""
        sz = self.type_size(ty)
        if sz == 1:
            return 2   # u8 extended to 16-bit on stack
        return max(sz, 2)

    def acc_reg(self, sz: int) -> str:
        """Primary accumulator register for given size."""
        if sz == 4:
            return 'XWA'
        elif sz == 2:
            return 'WA'
        else:
            return 'A'   # 8-bit

    def sec_reg(self, sz: int) -> str:
        """Secondary register for given size."""
        if sz == 4:
            return 'XHL'
        elif sz == 2:
            return 'HL'
        else:
            return 'H'

    # NGPC hardware bug: word-size r+r ops (D0..D7 prefix + 80..FF sub-op) hang the CPU.
    # Byte-size r+r (C8..CF prefix) and word/long immediate ops work fine.
    # All 16-bit r+r operations must be emitted via byte-split or push/pop.

    # ----- C5 P-5.8 full-function IR migration (pass 31) -----

    def _gen_expr_to_ir(self, node) -> Optional[Tuple[str, Type]]:
        """P-5.8 (pass 31) : evaluate `node` ENTIRELY in IR (no asm
        emit to self.lines).

        Returns (vreg, type) on success, None if the node type isn't
        yet supported by the IR migration. Caller (`_migrate_*`)
        propagates None up — function migration fails and falls back
        to legacy gen_block.

        Currently supported (pass 31 v1) :
          - Const(int) → LoadImm  (width u8/u16/u32 depending on type)
          - Ident(local/param u16 at XIY-rel) → LoadLocal
          - Ident(near global u16 scalar) → LoadGlobal

        Vregs allocated via `_c5_vreg_counter`. Caller sets cls hints
        via `self._c5_vreg_cls[vreg]` if needed (e.g., WA_ONLY for
        return values, HL_ONLY for binop RHS).
        """
        # Const integer
        if isinstance(node, Const) and isinstance(node.value, int):
            v = node.value
            if -32768 <= v <= 65535:
                vreg = f'%t{self._c5_vreg_counter}'
                self._c5_vreg_counter += 1
                width = 'u16'
                self.ir_function.append(LoadImm(dest=vreg, value=v & 0xFFFF, width=width))
                return vreg, U16
            return None
        # Ident — resolve to local/param u16 XIY-rel or global u16 near
        if isinstance(node, Ident):
            name = node.name
            sym = self.local_vars.get(name) or self.param_syms.get(name)
            if sym is not None:
                if sym.reg_name or sym.adecl_live_reg:
                    return None
                if isinstance(sym.type_, ArrayType):
                    return None
                if self.type_size(sym.type_) != 2:
                    return None
                if self._stack_base_reg(sym) != 'XIY':
                    return None
                vreg = f'%t{self._c5_vreg_counter}'
                self._c5_vreg_counter += 1
                self.ir_function.append(LoadLocal(dest=vreg, offset=sym.offset, width='u16'))
                return vreg, sym.type_
            # Near global u16 scalar
            if self.opt_perf_lag_7 and name in self.sem.globals:
                gsym = self.sem.globals[name]
                if gsym.is_far or isinstance(gsym.type_, ArrayType):
                    return None
                if self.type_size(gsym.type_) != 2:
                    return None
                vreg = f'%t{self._c5_vreg_counter}'
                self._c5_vreg_counter += 1
                label = f'_{name}'
                self.ir_function.append(LoadGlobal(dest=vreg, sym=label, width='u16'))
                return vreg, gsym.type_
        # P-5.8 v6 (pass 36) : structured BinOp for u16 add/sub/and/or/xor.
        # HW reality: TLCS-900 byte-split ALU on NGPC (the silicon-broken
        # D0..D7 r+r forms force us into `add A, L; adc W, H` style) is
        # DESTRUCTIVE on src_a — the result OVERWRITES src_a's register.
        # Model this faithfully in IR: `dest = src_a` (same vreg name).
        # Liveness then sees src_a's interval continuing through the
        # BinOp op (re-defined to itself), with no spurious overlap
        # between a "fresh dest" vreg and src_a at the BinOp position.
        # Lowering constraints (`_lower_binop`, t900cc_alloc.py L648) :
        #   src_a → XWA, src_b → XHL, dest → XWA (same as src_a). ✓
        if isinstance(node, BinOp):
            ast_to_ir_op = {'+': 'add', '-': 'sub',
                            '&': 'and', '|': 'or', '^': 'xor'}
            ir_op = ast_to_ir_op.get(node.op)
            if ir_op is None:
                return None
            # Only allow leaf-expr children (Const + Ident u16) — both
            # sides must resolve cleanly without nested allocations
            # competing for XWA/XHL.
            if not (self._can_eval_expr_into_hl(node.left)
                    and self._can_eval_expr_into_hl(node.right)):
                return None
            # Mirror legacy gen_binop pass-30 order : evaluate RIGHT
            # (→ XHL via `_eval_expr_into_hl`) FIRST, then LEFT (→ XWA).
            # This avoids the WA→HL push/pop transit pattern. To stay
            # byte-identical to pass 30 / v5, our structured emission
            # must emit `LDW HL, ...` before `LDW WA, ...`.
            right_result = self._gen_expr_to_ir(node.right)
            if right_result is None:
                return None
            left_result = self._gen_expr_to_ir(node.left)
            if left_result is None:
                return None
            vreg_a, _lty = left_result
            vreg_b, _rty = right_result
            self._c5_vreg_cls[vreg_a] = 'WA_ONLY'
            self._c5_vreg_cls[vreg_b] = 'HL_ONLY'
            # dest IS src_a (in-place overwrite — HW reality).
            self.ir_function.append(IRBinOp(
                dest=vreg_a, src_a=vreg_a, src_b=vreg_b,
                op=ir_op, width='u16',
            ))
            return vreg_a, U16
        return None

    def _migrate_fail(self, reason):
        """Emit a one-line stderr trace when migration bails on a node.
        Gated by T900CC_C5_TRACE_MIGRATION_FAIL=1 — separate from the
        success trace because the volume is much higher (every fn that
        legacy emits walks through here)."""
        if os.environ.get('T900CC_C5_TRACE_MIGRATION_FAIL'):
            import sys as _sys
            _sys.stderr.write(f'[C5-migrate-stop] {reason}\n')
        return False

    def _gen_stmt_to_ir(self, stmt) -> bool:
        """P-5.8 (pass 31) : migrate a statement to IR. Returns True on
        success, False if the stmt type isn't yet supported.
        """
        if isinstance(stmt, Block):
            for s in stmt.stmts:
                if not self._gen_stmt_to_ir(s):
                    return False
            return True
        if isinstance(stmt, ReturnStmt):
            if stmt.expr is None:
                # `return;` — void, no value. Emit jump to exit label.
                self.ir_function.append(EmitRaw(f'    jp   {self._func_exit_label}'))
                return True
            # Try structured path : the expr is simple enough for IR ops.
            ir_result = self._gen_expr_to_ir(stmt.expr)
            if ir_result is not None:
                vreg, _ty = ir_result
                # ABI v1 : u16/u32 return value in WA. Constrain vreg to
                # WA_ONLY so the allocator places it there ; legacy `ret`
                # reads WA.
                self._c5_vreg_cls[vreg] = 'WA_ONLY'
                self.ir_function.append(EmitRaw(f'    jp   {self._func_exit_label}'))
                return True
            # P-5.8 v5 (pass 35) : Return passthrough — `return BinOp(...)`,
            # `return FuncCall(...)`, `return Subscript(...)`, etc. Legacy
            # `gen_return` emits the expression eval + the exit jump.
            lines_before = len(self.lines)
            try:
                self.gen_return(stmt)
            except Exception as exc:
                return self._migrate_fail(
                    f'Return(expr={type(stmt.expr).__name__}) raised: {exc!r}'
                )
            del self.lines[lines_before:]
            return True
        # P-5.8 v2 (pass 32) : VarDecl — declaration of a local. The slot
        # is reserved during semantic analysis ; runtime emission needed
        # ONLY when there's an init_expr. No-init declarations are pure
        # type/symbol entries, zero asm.
        if isinstance(stmt, VarDecl):
            if stmt.is_static:
                # P-5.8 v5 (pass 35) : Static locals need `gen_local_decl`
                # to register them in `bss_vars` / `data_vars` / `const_vars`.
                # Passthrough — gen_local_decl emits zero runtime code for
                # statics (storage is in BSS/DATA, init at link time) so
                # the line-dedup capture-and-del catches anything that
                # leaks into self.lines (typically nothing).
                lines_before = len(self.lines)
                try:
                    self.gen_local_decl(stmt)
                except Exception as exc:
                    return self._migrate_fail(f'VarDecl(static) raised: {exc!r}')
                del self.lines[lines_before:]
                return True
            if stmt.init_expr is None:
                # no initializer → no asm to emit. Slot was reserved
                # during semantic pass.
                return True
            # init_expr present : must be _gen_expr_to_ir-compatible AND
            # the local must be a u16 XIY-relative slot (where StoreLocal
            # is wired in the allocator lowering).
            sym = self.local_vars.get(stmt.name)
            if sym is None:
                return False
            if sym.reg_name or sym.adecl_live_reg:
                return False
            if isinstance(sym.type_, ArrayType):
                return False
            if self.type_size(sym.type_) != 2:
                return False
            if self._stack_base_reg(sym) != 'XIY':
                return False
            ir_result = self._gen_expr_to_ir(stmt.init_expr)
            if ir_result is None:
                return False
            vreg, _ty = ir_result
            self.ir_function.append(
                StoreLocal(offset=sym.offset, src=vreg, width='u16')
            )
            return True
        # P-5.8 v4 (pass 34) : control-flow passthrough (IfStmt, WhileStmt,
        # ForStmt, DoWhileStmt, SwitchStmt). Same capture+splice trick as
        # FuncCall — legacy `gen_if` / `gen_while` / etc. handle the asm
        # emission. `emit_label` calls inside them populate IR basic
        # blocks naturally (start_block per label), so the IR ends up
        # multi-block. `lower_ir_with_allocation` already iterates
        # `ir_function.blocks` (t900cc_alloc.py:769). The mirror
        # cache-invalidation calls that gen_stmt normally performs
        # after each control-flow stmt are reproduced here verbatim.
        if isinstance(stmt, (IfStmt, WhileStmt, ForStmt, DoWhileStmt, SwitchStmt)):
            lines_before = len(self.lines)
            try:
                if isinstance(stmt, IfStmt):
                    self.gen_if(stmt)
                    if not (stmt.else_ is None
                            and self._stmt_never_falls_through(stmt.then)):
                        self._invalidate_elem_base_cache()
                elif isinstance(stmt, WhileStmt):
                    self.gen_while(stmt)
                    self._invalidate_elem_base_cache()
                elif isinstance(stmt, ForStmt):
                    self.gen_for(stmt)
                    self._invalidate_elem_base_cache()
                elif isinstance(stmt, DoWhileStmt):
                    self.gen_do_while(stmt)
                    self._invalidate_elem_base_cache()
                else:  # SwitchStmt
                    self.gen_switch(stmt)
                    self._invalidate_elem_base_cache()
            except Exception as exc:
                return self._migrate_fail(
                    f'{type(stmt).__name__} legacy passthrough raised: {exc!r}'
                )
            del self.lines[lines_before:]
            return True
        # P-5.8 v2 (pass 32) : ExprStmt — a statement that is a bare
        # expression. C's `tmp = 0x1234;` parses as ExprStmt(Assign(...)).
        # We dispatch to the assign handler when the inner expr is one.
        # Other ExprStmt forms (call, post++, etc.) not yet migratable.
        if isinstance(stmt, ExprStmt):
            inner = stmt.expr
            # `(void)foo();` parses as ExprStmt(Cast(FuncCall)). Unwrap
            # the Cast — for ExprStmt the cast result is discarded, so
            # the cast itself is a no-op codegen-wise.
            if isinstance(inner, Cast):
                inner = inner.expr
            # P-5.8 v5 (pass 35) : bare-value ExprStmt (`0;`, `42;`,
            # `x;`). Legacy `gen_expr` emits a load-then-discard. To
            # keep byte-identity we passthrough rather than no-op
            # (a no-op would optimize but change the binary).
            if isinstance(inner, (Const, Ident)):
                lines_before = len(self.lines)
                try:
                    self.gen_expr(inner)
                except Exception as exc:
                    return self._migrate_fail(f'ExprStmt({type(inner).__name__}) raised: {exc!r}')
                del self.lines[lines_before:]
                return True
            # P-5.8 v5 (pass 35) : ExprStmt(UnaryOp) — postfix `x++;` /
            # `x--;` (and prefix). Legacy gen_stmt at line 4609-4612
            # special-cases post++/post-- to call `gen_inc_dec` directly
            # (saves the intermediate value materialization). Mirror
            # that here, otherwise the inc value would be loaded into
            # WA uselessly.
            if isinstance(inner, UnaryOp):
                lines_before = len(self.lines)
                try:
                    if inner.op in ('post++', 'post--'):
                        delta = 1 if inner.op == 'post++' else -1
                        self.gen_inc_dec(inner.expr, delta=delta, post=False)
                    else:
                        self.gen_expr(inner)
                except Exception as exc:
                    return self._migrate_fail(f'ExprStmt(UnaryOp {inner.op}) raised: {exc!r}')
                del self.lines[lines_before:]
                return True
            if isinstance(inner, Assign):
                stmt = inner  # fall through to the Assign block below
            elif isinstance(inner, FuncCall):
                # P-5.8 v3 (pass 33) : FuncCall passthrough — emit the
                # call via legacy gen_expr. The base `emit()` helper
                # ALREADY appends each line to both `self.lines` AND
                # `self.ir_function` (see emit() docstring at L1969),
                # so the IR is populated for free. We only need to
                # remove the duplicate append from self.lines — the
                # pipeline's `lower_ir_with_allocation → self.lines =
                # prefix + new_lines` would otherwise produce a body
                # with double-emitted calls (IR copy + leftover legacy
                # copy of self.lines additions).
                #
                # Rollback safety : `_try_migrate_function_to_ir`
                # snapshots self.lines length AND all legacy cache
                # state before invoking us. If migration fails later,
                # both are restored so legacy `gen_block` runs on a
                # clean slate (cf. _snapshot_legacy_caches docstring).
                lines_before = len(self.lines)
                try:
                    self.gen_expr(inner)
                except Exception as exc:
                    return self._migrate_fail(
                        f'ExprStmt(FuncCall {getattr(inner, "name", "?")!r}) '
                        f'gen_expr raised: {exc!r}'
                    )
                del self.lines[lines_before:]
                return True
            else:
                return self._migrate_fail(f'ExprStmt({type(inner).__name__})')
        # P-5.8 v2 (pass 32) : Assign — assignment `local = expr` for u16
        # XIY-relative locals/params. Both sides must be migration-safe.
        # Op must be '=' (compound +=/-= not yet handled).
        if isinstance(stmt, Assign):
            target = stmt.target
            # Fast structured path : simple `local_u16 = simple_expr;`
            # uses LoadX + StoreLocal IR ops (zero passthrough).
            if (stmt.op == '=' and isinstance(target, Ident)):
                name = target.name
                sym = self.local_vars.get(name) or self.param_syms.get(name)
                if (sym is not None
                        and not sym.reg_name and not sym.adecl_live_reg
                        and not isinstance(sym.type_, ArrayType)
                        and self.type_size(sym.type_) == 2
                        and self._stack_base_reg(sym) == 'XIY'):
                    ir_result = self._gen_expr_to_ir(stmt.value)
                    if ir_result is not None:
                        vreg, _ty = ir_result
                        self.ir_function.append(
                            StoreLocal(offset=sym.offset, src=vreg, width='u16')
                        )
                        return True
            # P-5.8 v5 (pass 35) : Assign passthrough — for FieldAccess /
            # Subscript / Deref targets, compound ops (+=, -=, &=), static
            # global targets, or any non-Ident target. Legacy gen_assign
            # handles all of these correctly ; we capture its emissions.
            # Same emit-and-dedup pattern as FuncCall passthrough.
            lines_before = len(self.lines)
            try:
                self.gen_assign(stmt)
            except Exception as exc:
                return self._migrate_fail(
                    f'Assign(op={stmt.op}, target={type(target).__name__}) raised: {exc!r}'
                )
            del self.lines[lines_before:]
            return True
        return self._migrate_fail(f'stmt={type(stmt).__name__}')

    def _try_migrate_function_to_ir(self, func_body) -> bool:
        """P-5.8 (pass 31) : attempt to migrate the WHOLE function body
        to IR. Returns True on success (IR built, ready for allocator
        lowering). On failure, the IR may be partially populated — caller
        must reset before falling back to legacy.

        Strategy : record IR state before attempting, then try
        `_gen_stmt_to_ir(body)`. On failure, rewind by truncating IR
        ops back to the saved length.
        """
        if self.ir_function is None:
            return False
        # Save state for rollback.
        # P-5.8 v3 (pass 33) : the FuncCall passthrough branch calls legacy
        # `gen_expr` which mutates self.lines AND a dozen legacy cache fields
        # (LVT, XDE/XBC ptr caches, reg_holds_sym, etc.). On rollback we MUST
        # restore everything so the fallback legacy `gen_block` works on the
        # same clean state it would have seen with no migration attempt.
        # P-5.8 v4 (pass 34) : control-flow passthrough (gen_if/while/etc.)
        # may emit labels, which call `start_block(label)` and add NEW
        # blocks to `ir_function.blocks`. Truncating only `current_block.ops`
        # would leak orphan blocks past saved_ops_len → `lower_to_asm`
        # walks them and produces malformed output (= the round-trip
        # divergence we hit in pass 34 dev). Snapshot the whole blocks
        # array + the original entry block's ops length, restore both
        # on rollback.
        saved_blocks_len = len(self.ir_function.blocks)
        entry_block = self.ir_function.blocks[0]
        saved_entry_ops_len = len(entry_block.ops)
        saved_vreg_counter = self._c5_vreg_counter
        saved_cls_dict = dict(self._c5_vreg_cls)
        saved_lines_len = len(self.lines)
        saved_caches = self._snapshot_legacy_caches()
        # P-5.8 v3 (pass 33) : string-pool snapshot. `gen_const` (called
        # transitively via gen_expr for string-literal args) appends to
        # `string_consts` and bumps `str_counter`. If migration later
        # rolls back, the orphan string remains in the pool, the legacy
        # fallback emits ANOTHER copy with a new label, and we leak +N
        # bytes of duplicate string per failed migration. Snapshot
        # length + counter ; restore on rollback.
        saved_str_consts_len = len(self.string_consts)
        saved_str_counter = self.str_counter
        try:
            ok = self._gen_stmt_to_ir(func_body)
        except Exception as exc:
            if os.environ.get('T900CC_C5_TRACE_MIGRATION'):
                import sys as _sys
                _sys.stderr.write(f'[C5-migrate-fail-exc] {exc!r}\n')
            ok = False
        if not ok:
            # Rollback IR blocks + entry ops + vreg counter/cls + legacy
            # lines + caches + string pool. Order matters: blocks first
            # so `current_block` reverts to entry block before we touch
            # its ops.
            del self.ir_function.blocks[saved_blocks_len:]
            entry_block.ops[:] = entry_block.ops[:saved_entry_ops_len]
            self._c5_vreg_counter = saved_vreg_counter
            self._c5_vreg_cls = saved_cls_dict
            del self.lines[saved_lines_len:]
            self._restore_legacy_caches(saved_caches)
            del self.string_consts[saved_str_consts_len:]
            self.str_counter = saved_str_counter
            return False
        return True

    def _snapshot_legacy_caches(self) -> tuple:
        """P-5.8 v3 (pass 33) : capture all legacy-emit cache state that
        `gen_expr` (called via FuncCall passthrough) might mutate. Returned
        opaque tuple is restored via `_restore_legacy_caches` on rollback.

        State tracked (all reset by `gen_function`'s prologue, so this
        snapshot at function entry == clean state) :
          - LVT triple (_lvt_wa, _lvt_hl, _lvt_w_zero)
          - Address-reg tracker (_reg_holds_sym dict)
          - XIY pending sym (_xiy_sym_pending)
          - XDE field/decay/far flags + cached ptr key
          - XBC cached elem key + offset
          - _mem_base_reg
        """
        return (
            self._lvt_wa, self._lvt_hl, self._lvt_w_zero,
            dict(self._reg_holds_sym),
            self._xiy_sym_pending,
            self._xde_field_offset, self._xde_ptr_is_array_decay,
            self._xde_addr_is_far, self._mem_base_reg,
            self._xde_cached_ptr_key,
            self._xbc_cached_elem_key, self._xbc_cached_elem_offset,
        )

    def _restore_legacy_caches(self, snap: tuple) -> None:
        """Inverse of `_snapshot_legacy_caches`."""
        (self._lvt_wa, self._lvt_hl, self._lvt_w_zero,
         reg_holds, self._xiy_sym_pending,
         self._xde_field_offset, self._xde_ptr_is_array_decay,
         self._xde_addr_is_far, self._mem_base_reg,
         self._xde_cached_ptr_key,
         self._xbc_cached_elem_key, self._xbc_cached_elem_offset) = snap
        self._reg_holds_sym = reg_holds

    def _can_eval_expr_into_hl(self, node) -> bool:
        """P-5.7 (pass 30) — predicate : would `_eval_expr_into_hl(node)`
        succeed (= emit direct without WA clobber) ? Side-effect-free
        version used by gen_binop to decide path BEFORE emitting.
        """
        if isinstance(node, Const) and isinstance(node.value, int):
            return -32768 <= node.value <= 65535
        if isinstance(node, Ident):
            name = node.name
            sym = self.local_vars.get(name) or self.param_syms.get(name)
            if sym is not None:
                if sym.reg_name or sym.adecl_live_reg:
                    return False
                if self.type_size(sym.type_) != 2:
                    return False
                if self._stack_base_reg(sym) != 'XIY':
                    return False
                if isinstance(sym.type_, ArrayType):
                    return False
                return True
            if self.opt_perf_lag_7 and name in self.sem.globals:
                gsym = self.sem.globals[name]
                if gsym.is_far or isinstance(gsym.type_, ArrayType):
                    return False
                if self.type_size(gsym.type_) != 2:
                    return False
                return True
        return False

    def _eval_expr_into_hl(self, node) -> Optional[Type]:
        """P-5.7 (pass 30) : try to evaluate `node` DIRECTLY into HL,
        bypassing the WA→HL transit (`push WA; pop HL` = 2 B).

        Audit pré : 2174 sites de transit dans j16 = ~4348 B potentiel.
        Many come from `gen_binop`'s RHS evaluation which always goes
        through WA before being moved to HL.

        Returns the resulting Type (HL contains the value) on success,
        or None if the expression can't be evaluated directly to HL
        (caller falls back to `gen_expr` + `_emit_copy_wa_to_hl`).

        Contract : on success, the emission TOUCHES HL ONLY (does not
        clobber WA). Caller may assume WA is preserved across this call.

        Handled cases (HL-direct, ≤ 4 B per site savings vs transit) :
        - `Const` integer : `ld HL, imm` (3-4 B)
        - `Ident(local_u16_XIY)` : `db 0x9D d 0x23  ; LDW HL, (XIY+d)`
          (3 B, sub-op 0x23 HW-validated pass 22)
        - `Ident(global_u16_near)` : `ld HL, (_label)` (4 B, sub-op 0x23
          HW-shipped via existing legacy load patterns)

        Excluded (returns None) :
        - Pointers, struct field access, array subscript, deref
        - Function calls (clobbers caller-saved including HL)
        - BinOp (chained — would recursive cost)
        - Locals via XIZ-bank (different load path)
        - far globals
        - sz != 2 (u8, u32 not wired here yet)
        """
        # Const integer (u8/u16)
        if isinstance(node, Const) and isinstance(node.value, int):
            v = node.value
            if -32768 <= v <= 65535:
                self.emit_instr(f'ld   HL, {v}')
                return U16
            return None
        # Ident — resolve to local/param or global
        if isinstance(node, Ident):
            name = node.name
            # Local u16 at XIY+off
            sym = self.local_vars.get(name) or self.param_syms.get(name)
            if sym is not None:
                if sym.reg_name:  # banked
                    return None
                if sym.adecl_live_reg:  # leaf adécl param
                    return None
                if self.type_size(sym.type_) != 2:
                    return None
                base_reg = self._stack_base_reg(sym)
                if base_reg != 'XIY':
                    return None
                if isinstance(sym.type_, ArrayType):
                    return None
                off = sym.offset
                d = off & 0xFF
                self.emit_instr(
                    f'db 0x9D, 0x{d:02X}, 0x23  ; LDW HL, (XIY{off:+d})'
                )
                return sym.type_
            # Near global u16 scalar (matches _direct_abs_scalar_symbol shape)
            if self.opt_perf_lag_7 and name in self.sem.globals:
                gsym = self.sem.globals[name]
                if gsym.is_far or isinstance(gsym.type_, ArrayType):
                    return None
                if self.type_size(gsym.type_) != 2:
                    return None
                label = f'_{name}'
                self.emit_instr(f'ld   HL, ({label})')
                return gsym.type_
        # Anything else : caller must use gen_expr + copy_wa_to_hl
        return None

    def _emit_copy_wa_to_hl(self):
        """Copy WA → HL via stack (avoids broken word r+r LD D0 8B)."""
        self.emit_instr('push WA')   # 0x28 — known safe
        self.emit_instr('pop  HL')   # 0x4B — known safe

    def _emit_copy_xwa_to_xhl(self):
        """Copy XWA → XHL via stack (avoids broken long r+r LD D8 8B).

        Same hardware issue as `_emit_copy_wa_to_hl` but on the long-form
        family D8..DF r+r. The direct `ld XHL, XWA` (D8 8B) is in the
        silicon-broken D8..DF r+r family confirmed by NGPC silicon and
        documented in NgpCraft_emulator's `quirks_db.json` v2026-04-22.v3
        (`cpu.d8_df_register_to_register`). Emulator HW-faithful blocks
        on this opcode, real silicon misbehaves silently.
        """
        self.emit_instr('push XWA')  # 0x38 — known safe
        self.emit_instr('pop  XHL')  # 0x5B — known safe

    def _emit_double_wa_u16(self):
        """WA = WA * 2 via safe byte-split ops."""
        self.emit_instr('push WA')
        self.emit_instr('pop  HL')
        self.emit_instr('add  A,  L')
        self.emit_instr('adc  W,  H')

    def _emit_mul_wa_by_const_shiftadd(self, val: int) -> bool:
        """Multiply WA by a small constant using only safe shift/add patterns.

        Bounded on purpose: only handles factors whose popcount is 1 or 2.
        This is mainly used for hot array/struct index scaling (e.g. *6, *10, *24)
        to avoid the slower mem-form MUL in the hottest loops.
        """
        if val <= 0:
            return False
        bits = [i for i in range(16) if val & (1 << i)]
        if len(bits) == 1:
            for _ in range(bits[0]):
                self._emit_double_wa_u16()
            return True
        if len(bits) == 2:
            lo, hi = bits
            for _ in range(lo):
                self._emit_double_wa_u16()
            self.emit_instr('push WA')   # save x<<lo
            for _ in range(hi - lo):
                self._emit_double_wa_u16()
            self.emit_instr('pop  HL')   # restore x<<lo
            self.emit_instr('add  A,  L')
            self.emit_instr('adc  W,  H')
            return True
        return False

    def _emit_alu16(self, op: str):
        """16-bit ALU op WA OP= HL via byte-split byte r+r (C8..CF prefix, safe).
        Pre: WA=left, HL=right. For arithmetic/bitwise: result in WA.
        For comparison (==, !=, <, <=, >, >=): sets flags, WA may be destroyed.

        P-5.8 v7.4 (pass 40) : when `_byte_narrow_alu` is set AND op is
        arithmetic/bitwise (NOT comparison), skip the high-byte half.
        Caller asserts the result is consumed as u8 (LDB store, byte
        cmp), so W register's post-alu value is dead. Saves 2 B/site.
        For carry-propagating ops (+, -) the byte form correctly handles
        the low half ; high-byte carry is dropped (= correct for u8).
        For bitwise ops (&, |, ^) per-byte is exactly the same.
        """
        narrow = self._byte_narrow_alu and self._opt_c5_byte_narrow_alu
        if op == '+':
            self.emit_instr('add  A,  L')   # CF 81
            if not narrow:
                self.emit_instr('adc  W,  H')   # CE 90
        elif op == '-':
            self.emit_instr('sub  A,  L')   # CF A1
            if not narrow:
                self.emit_instr('sbc  W,  H')   # CE B0
        elif op == '&':
            self.emit_instr('and  A,  L')   # CF C1
            if not narrow:
                self.emit_instr('and  W,  H')   # CE C0
        elif op == '|':
            self.emit_instr('or   A,  L')   # CF E1
            if not narrow:
                self.emit_instr('or   W,  H')   # CE E0
        elif op == '^':
            self.emit_instr('xor  A,  L')   # CF D1
            if not narrow:
                self.emit_instr('xor  W,  H')   # CE D0
        elif op in ('==', '!='):
            # xor+or: Z=1 iff WA==HL. WA is destroyed (OK: overwritten with 0/1 immediately).
            self.emit_instr('xor  A,  L')   # CF D1: A = A^L
            self.emit_instr('xor  W,  H')   # CE D0: W = W^H
            self.emit_instr('or   A,  W')   # C8 E1: A=(A^L)|(W^H), Z=1 iff WA==HL
        elif op in ('<', '>='):
            # sub A,L + sbc W,H -> C=1 if WA < HL (unsigned borrow)
            self.emit_instr('sub  A,  L')   # CF A1
            self.emit_instr('sbc  W,  H')   # CE B0
        elif op in ('>', '<='):
            # reversed: sub L,A + sbc H,W -> C=1 if HL < WA (= WA > HL)
            self.emit_instr('sub  L,  A')   # C9 A7: L=L-A
            self.emit_instr('sbc  H,  W')   # C8 B6: H=H-W-C

    def _emit_cmp16_signed_branch(self, op: str, label_true: str):
        """Branch to label_true for a signed 16-bit WA op HL compare.
        Uses a byte-wise signed compare on the high byte, then an unsigned
        compare on the low byte when the high bytes are equal.
        """
        label_done = self.fresh_label('cmp16s_done')

        # Compare signed high bytes first. If they differ, that decides.
        self.emit_instr('sub  W,  H')
        if op == '<':
            self.emit_instr(f'jrl  LT, {label_true}')
            self.emit_instr(f'jrl  NZ, {label_done}')
            self.emit_instr('sub  A,  L')
            self.emit_instr(f'jrl  C,  {label_true}')
        elif op == '<=':
            self.emit_instr(f'jrl  LT, {label_true}')
            self.emit_instr(f'jrl  NZ, {label_done}')
            self.emit_instr('sub  A,  L')
            self.emit_instr(f'jrl  C,  {label_true}')
            self.emit_instr(f'jrl  Z,  {label_true}')
        elif op == '>':
            self.emit_instr(f'jrl  LT, {label_done}')
            self.emit_instr(f'jrl  NZ, {label_true}')
            self.emit_instr('sub  A,  L')
            self.emit_instr(f'jrl  Z,  {label_done}')
            self.emit_instr(f'jrl  NC, {label_true}')
        elif op == '>=':
            self.emit_instr(f'jrl  LT, {label_done}')
            self.emit_instr(f'jrl  NZ, {label_true}')
            self.emit_instr('sub  A,  L')
            self.emit_instr(f'jrl  NC, {label_true}')
        else:
            raise ValueError(f"Unsupported signed 16-bit compare op {op}")
        self.emit_label(label_done)

    _CMP16_CC = {
        '==': 'Z',  '!=': 'NZ',
        '<':  'C',  '>=': 'NC',
        '>':  'C',  '<=': 'NC',
    }

    def _emit_cmp_u8_const_branch(self, op: str, imm8: int, label_target: str,
                                  signed: bool = False):
        """Branch on an 8-bit compare after `cp A, imm8`.

        Chantier 4 Phase P-2 — subsumes CODEGEN_NOTES.md pattern P-09
        (cp A, imm excess). When the constant is 0:

        - `cp A, 0` (3 bytes) is replaced by `or A, A` (2 bytes), which
          sets the Z flag the same way. Saves 1 byte per site.
        - For unsigned A (`signed=False`), the carry flag from `cp A, 0`
          is always 0 (A is non-negative). Branches on C (`<` for
          unsigned) are dead; branches on NC (`>=` for unsigned) are
          always taken. The post-branch lowering elides the dead branch
          and emits the always-taken case as an unconditional jp/jrl.

        Saves an additional 1-3 bytes per site (depending on which
        condition was dead). Net realistic gain on StarGunner J16:
        −600 B to −1 KB body.

        For non-zero imm8, falls back to the legacy `cp A, imm8` path.
        """
        imm8 &= 0xFF
        if imm8 == 0:
            self.emit_instr('or   A,  A')
            self._emit_cmp_after_zero_branch(op, label_target, signed=signed)
            return
        self.emit_instr(f'cp   A,  {imm8}')
        self._emit_cmp_after_cp_branch(op, label_target)

    def _emit_cmp_after_zero_branch(self, op: str, label_target: str,
                                    signed: bool):
        """Lower a post-zero-compare branch using flags from `or A, A`.

        Phase P-2 helper for the imm==0 special case in
        `_emit_cmp_u8_const_branch`. After `or A, A`:
          - Z is set ⟺ A == 0
          - C is always 0 (or A, A never sets carry)
          - For unsigned A: A < 0 is impossible, A >= 0 is always true.

        This means several branches are statically determined and can
        be elided or converted to unconditional jumps. The compares
        below mirror `_emit_cmp_after_cp_branch` but with this static
        knowledge applied.
        """
        if op == '==':
            self.emit_instr(f'jrl  Z,  {label_target}')
        elif op == '!=':
            self.emit_instr(f'jrl  NZ, {label_target}')
        elif op == '<':
            if not signed:
                # Unsigned A < 0 is always false → branch is dead, omit.
                return
            self.emit_instr(f'jrl  LT, {label_target}')
        elif op == '>=':
            if not signed:
                # Unsigned A >= 0 is always true → unconditional branch.
                self.emit_instr(f'jp   {label_target}')
                return
            self.emit_instr(f'jrl  GE, {label_target}')
        elif op == '>':
            if not signed:
                # Unsigned A > 0 ⟺ A != 0 ⟺ NZ
                self.emit_instr(f'jrl  NZ, {label_target}')
                return
            label_done = self.fresh_label('cmp_done')
            self.emit_instr(f'jrl  Z,  {label_done}')
            self.emit_instr(f'jrl  GE, {label_target}')
            self.emit_label(label_done)
        elif op == '<=':
            if not signed:
                # Unsigned A <= 0 ⟺ A == 0 ⟺ Z
                self.emit_instr(f'jrl  Z,  {label_target}')
                return
            self.emit_instr(f'jrl  LT, {label_target}')
            self.emit_instr(f'jrl  Z,  {label_target}')
        else:
            raise ValueError(f"Unsupported compare op {op}")

    # -- Generating functions --

    def generate(self):
        # Collect names of functions defined (with body) in this module — used to
        # suppress duplicate extern/public for the same symbol (e.g. forward decls).
        defined_funcs = {d.name for d in self.decls if isinstance(d, FuncDecl) and d.body is not None}
        # Same issue for globals: headers may contribute an `extern foo;` declaration
        # before the real definition in the same translation unit.
        defined_vars = {d.name for d in self.decls if isinstance(d, VarDecl) and not d.is_extern}

        # Collect externs (function declarations without body, extern variables)
        for d in self.decls:
            if isinstance(d, FuncDecl) and d.body is None:
                # Skip if this module also defines the function — would generate
                # both 'extern _sym' and 'public _sym', confusing the linker.
                if d.name in defined_funcs:
                    continue
                if d.name not in self.externs:
                    self.externs.append(d.name)
            elif isinstance(d, VarDecl):
                if d.is_extern:
                    if d.name in defined_vars:
                        continue
                    # extern var declaration: emit as extern, not as BSS/DATA
                    if d.name not in self.externs:
                        self.externs.append(d.name)
                elif (isinstance(d.init_expr, Const) and isinstance(d.init_expr.value, str)
                        and isinstance(d.type_, ArrayType)):
                    # Global const/non-const char[] initialized with a string literal.
                    # Convert to InitList of bytes; respect declared array size (no null if full).
                    s = d.init_expr.value
                    count = d.type_.count
                    if count == 0:
                        bytes_list = [ord(c) for c in s] + [0]
                        d.type_ = ArrayType(d.type_.elem, len(bytes_list))
                    else:
                        bytes_list = [ord(c) for c in s[:count]]
                        while len(bytes_list) < count:
                            bytes_list.append(0)
                    d.init_expr = InitList(bytes_list)
                    self._sync_global_decl_symbol(d)
                    if d.is_const and not isinstance(d.type_, PtrType):
                        self.const_vars.append(d)
                    else:
                        self.data_vars.append(d)
                elif isinstance(d.init_expr, InitList):
                    d.type_ = self._infer_unsized_array_type(d.type_, d.init_expr)
                    self._sync_global_decl_symbol(d)
                    # Array/struct initializer: const → ROM (f_const), non-const → DATA (f_data)
                    # PtrType with const = pointer-to-const; pointer itself is mutable → DATA.
                    if d.is_const and not isinstance(d.type_, PtrType):
                        self.const_vars.append(d)
                    else:
                        self.data_vars.append(d)
                elif d.init_expr is None:
                    self.bss_vars.append(d)
                elif isinstance(d.init_expr, Const) and d.init_expr.value == 0:
                    # Zero-init is equivalent to BSS; crt0 zero-inits BSS to RAM addresses.
                    # DATA section symbols get ROM addresses → wrong for runtime access.
                    self.bss_vars.append(d)
                else:
                    # Non-zero, non-list init: const scalars/arrays go to f_const (ROM).
                    # const T *ptr = pointer-to-const: pointer is mutable → f_data (RAM).
                    if d.is_const and not isinstance(d.type_, PtrType):
                        self.const_vars.append(d)
                    else:
                        self.data_vars.append(d)

        # Emit header
        base = os.path.basename(self.source_name)
        module = os.path.splitext(base)[0]
        self.emit(f'; Generated by t900cc.py from {base}')
        self.emit(f'    module  {module}')
        self.emit('')
        self.emit('    f_code section code large')
        self.emit('')

        # Emit extern declarations
        if self.externs:
            for name in self.externs:
                self.emit(f'    extern  _{name}')
            self.emit('')

        # Emit function definitions
        for d in self.decls:
            if isinstance(d, FuncDecl) and d.body is not None:
                self.gen_function(d)

    # ------------------------------------------------------------------
    # P1 — ABI v2 __adecl helpers (scaffolding, 2026-04-20).
    # These helpers are safe to call regardless of opt_abi_adecl:
    #   - OFF: _func_uses_adecl → False, _assign_param_registers → {}
    #     → callers go through the unchanged cdecl path.
    #   - ON:  adecl only applies to functions defined in this TU
    #     (local_func_defs). Externs (e.g. HAL ASM symbols) stay cdecl
    #     so the ASM boundary is not broken.
    # ------------------------------------------------------------------

    def _func_uses_adecl(self, func_name: str) -> bool:
        """Return True when func_name should be called with the adecl
        convention (1st arg XWA, 2nd XBC, 3rd XDE, rest stack).

        Conservative policy (Phase 1):
          - function must be defined in this TU (local_func_defs)
          - function must be `static` — public functions may be called from
            ASM (HAL stubs) and must keep cdecl
          - `main` and __interrupt handlers are invoked from crt0/vector
            tables with cdecl semantics, never adecl
        """
        if not self.opt_abi_adecl:
            return False
        if func_name not in self.local_func_defs:
            return False
        if func_name == 'main':
            return False
        fd = self.sem.func_decls.get(func_name)
        if fd is None:
            return False
        if getattr(fd, 'is_interrupt', False):
            return False
        if not getattr(fd, 'is_static', False):
            return False
        return True

    def _assign_param_registers(self, params):
        """For a function whose signature is (params), return a dict
        {param_name: reg_name} listing params that arrive in registers.

        Rule (DECISIONS.md lines 42-48): fill XWA, XBC, XDE in order for
        scalars/pointers of size ≤ 4. A param > 4 bytes (struct-by-value,
        exotic) forces that param AND all following ones onto the stack.

        Returns {} when flag is off OR no param fits — callers then use
        the unchanged stack-based layout.
        """
        if not self.opt_abi_adecl:
            return {}
        regs = ['XWA', 'XBC', 'XDE']
        reg_map = {}
        for (pname, ptype) in params:
            if not regs:
                break
            psz = self.stack_size_for(ptype)
            if psz > 4:
                break
            reg_map[pname] = regs.pop(0)
        return reg_map

    def gen_function(self, func: FuncDecl):
        # Chantier 4 P-1 / Chantier 5 P-5.1: activate the block-level
        # IR container for this function. Every emit() call appends an
        # EmitRaw op to the current block; emit_label() opens a new
        # block. At function end the IR is round-tripped through
        # lower_to_asm and diffed vs self.lines[start:end] to catch
        # lowering bugs.
        self.ir_function = IRFunction(name=func.name)
        self.ir_buffer = None  # legacy; not used in P-5.1+
        self._ir_func_start_idx = len(self.lines)
        # P-5.6.1: vreg names are per-function (`%t0`, `%t1`, …).
        self._c5_vreg_counter = 0
        self._c5_vreg_cls = {}
        self._c5_stats['structured_emits_this_function'] = 0
        # P-5.8 (pass 31) : per-function flag — True quand le body a été
        # FULLY migré en IR structuré. Force le pipeline allocator à
        # tourner en 'on' mode pour cette fn (replace lines body
        # via allocator output) au lieu de shadow/off.
        self._fn_fully_migrated_to_ir = False

        # Chantier 4 Phase P-3: reset the address-register tracker at
        # function entry. Each function starts with no known register
        # state — params arrive via ABI but their content is not a
        # symbol we'd cache (P-3 caches `ld XR32, &sym` materializations
        # only).
        self._reg_holds_sym = {}

        # Chantier 5 P-5.6.6: reset Live Value Tracker. At function
        # entry, WA holds either arg0 (adécl) or whatever the caller
        # left (cdecl) — neither is a sig we'd cache. Reset to None.
        self._lvt_reset_all()
        self._c5_lvt_hits = 0

        self.emit(f'; Function: {func.name}')
        if not func.is_static:
            self.emit(f'    public  _{func.name}')
        self.emit(f'_{func.name}:')

        # Reset per-function state
        self.local_vars = {}
        self.local_offset = 0
        self.has_locals = False
        self.current_func_name = func.name
        self.static_local_globals = {}
        self.ret_type = func.ret_type
        self.is_interrupt = func.is_interrupt
        self.loop_break_label = None
        self.loop_cont_label = None
        self.param_syms = {}
        self._xiy_sym_pending = None  # set by gen_lvalue_addr for locals/params
        self._xde_field_offset = 0    # extra XDE byte offset for struct field access
        self._xde_ptr_is_array_decay = False  # True when XDE = array base (no deref for subscript)
        self._xde_addr_is_far = False  # True when current XDE address must preserve hi16
        self._mem_base_reg = 'XDE'
        self._xde_cached_ptr_key = None
        self._xbc_cached_elem_key = None
        self._xbc_cached_elem_offset = 0
        self._frame_size = 0          # bytes allocated for locals (for epilogue)
        self._frame_reg = 'XIY'
        self._frame_reg_saved = False
        self._need_frame = False
        self._func_exit_label = f'.L_return_{func.name}'
        self._addr_taken_local_names = self._contains_addr_taken_local(func.body)
        self._has_inline_asm = self._contains_inline_asm(func.body)
        # Documented byte-addressable XIZ spill slots seen in CC900 output.
        # Keep IZL out until halfword bank moves are characterized cleanly.
        self._reg_bank_slots_free = ['QIZH', 'IZH', 'QIZL']
        self._has_reg_bank_locals = False
        self._save_xiz_regbank = False
        self._save_xix_scratch = False
        self._prelink_saved_bytes = 0
        self._scratch_addr_reg = 'XIZ'
        self._addr_scratch_saved_xiz = False

        # Pre-scan locals to assign stack offsets / register-bank slots.
        self._assign_locals(func.body)

        # P1 — adecl: pre-allocate negative frame slots for params that will
        # arrive in registers (XWA/XBC/XDE). Offsets are reserved BEFORE the
        # prologue so total_locals accounts for the spill storage.
        # Symmetric rule with caller-side: only static, non-interrupt,
        # non-main local funcs use adecl — everything else stays cdecl.
        adecl_reg_map = (self._assign_param_registers(func.params)
                         if self._func_uses_adecl(func.name) else {})

        # P2 — no-frame leaf (2026-04-20). Detect if this function can live
        # entirely in registers without a frame:
        #   - adecl eligible (all reg-passed params)
        #   - no body call (direct or indirect)
        #   - no inline asm
        #   - no address-taken param (would need a memory slot)
        #   - no param mutation (`p = ...`, `p++`, etc.) — params stay
        #     read-only in their live reg for the whole body
        #   - no body local var
        #   - no ISR (those save/restore regs)
        param_names = {p[0] for p in func.params}
        body_has_locals = self._count_locals(func.body) > 0
        self._leaf_adecl = (
            bool(adecl_reg_map) and
            len(adecl_reg_map) == len(func.params) and   # all params reg-passed
            not body_has_locals and
            not self._has_inline_asm and
            not func.is_interrupt and
            not self._contains_any_call(func.body) and
            not (param_names & self._addr_taken_local_names) and
            not self._contains_param_assign(func.body, param_names)
        )

        self._adecl_spills = []  # [(reg_name, psz, frame_off), ...] consumed after prologue
        if not self._leaf_adecl:
            for pname, pty in func.params:
                rn = adecl_reg_map.get(pname)
                if not rn:
                    continue
                psz = self.stack_size_for(pty)
                slot_sz = psz if psz >= 2 else 2
                self.local_offset -= slot_sz
                self._adecl_spills.append((pname, rn, psz, self.local_offset))

        total_locals = -self.local_offset
        self.has_locals = total_locals > 0
        if self._has_reg_bank_locals:
            self._save_xiz_regbank = True
            self._prelink_saved_bytes = 4

        # Assign parameter symbols (params are above the frame pointer)
        # Stack layout after LINK XIY, N (cdecl, args pushed right-to-left):
        #   XIY-N..XIY-1  = local variables (N bytes)
        #   XIY+0..XIY+3  = saved XIY (pushed by LINK)
        #   XIY+4..XIY+7  = return address (pushed by CALL)
        #   XIY+8..       = first param (leftmost, pushed last by caller)
        has_params = len(func.params) > 0
        need_frame = self.has_locals or has_params
        # P2 leaf: params stay in regs, nothing on the frame.
        if self._leaf_adecl:
            need_frame = False
        self._need_frame = need_frame

        if self._save_xiz_regbank:
            self.emit_instr('push XIZ')
        if self._save_xix_scratch:
            self.emit_instr('push XIX')

        if need_frame:
            n_bytes = total_locals
            if n_bytes % 2 != 0:
                n_bytes += 1
            self._frame_size = n_bytes
            use_alt_frame = (
                self.opt_perf_lag_5 and
                not has_params and
                not self.is_interrupt and
                16 <= n_bytes <= 126 and
                not self._has_inline_asm and
                not self._save_xix_scratch
            )
            if use_alt_frame:
                self._frame_reg = 'XIX'
                self._frame_reg_saved = True
                self.emit_instr('push XIX')          # save alternate frame register
                self.emit_instr('lda  XIX, XSP')     # XIX = stack anchor after save
            else:
                # link XIY, 0 — confirmed hardware safe (N=0, bug only affects N>=5).
                # Atomically: push old XIY; XIY = XSP; XSP -= 0.
                # Stack layout after prologue:
                #   XIY-N..XIY-1  = local variables  (allocated below)
                #   XIY+0..XIY+3  = saved XIY  (from link)
                #   XIY+4..XIY+7  = return address (from CALL)
                #   XIY+8..       = first param
                self.emit_instr('link XIY, 0')      # push old XIY + XIY=XSP (atomic, hw confirmed)
            if n_bytes > 0:
                if self.opt_perf_lag_2:
                    self._emit_stack_alloc_lda(n_bytes)
                else:
                    for _ in range(n_bytes // 2):
                        self.emit_instr('push WA')

        # Assign param symbols.
        # cdecl (default):
        #   [XIY+0..3] = saved XIY (4 bytes, from link)
        #   [XIY+4..7] = return address (4 bytes, from CALL)
        #   [XIY+8..]  = first param (left-to-right order, pushed by caller)
        # adecl: params in self._adecl_spills already have their negative
        # frame slot pre-reserved above; the rest go on the stack as usual.
        adecl_slot_by_name = {spill[0]: spill[3] for spill in self._adecl_spills}
        param_base = 8 + self._prelink_saved_bytes  # offset from XIY to first stack param
        offset = param_base
        for pname, pty in func.params:
            psz = self.stack_size_for(pty)
            if self._leaf_adecl and pname in adecl_reg_map:
                # P2 leaf: param stays live in its incoming reg.
                sym = Symbol(pname, pty, 'param', offset=0,
                             adecl_live_reg=adecl_reg_map[pname])
            elif pname in adecl_slot_by_name:
                # adecl with spill: frame slot via negative offset.
                sym = Symbol(pname, pty, 'param', offset=adecl_slot_by_name[pname])
            else:
                sym = Symbol(pname, pty, 'param', offset=offset)
                offset += psz
            self.param_syms[pname] = sym

        # Spill adecl reg-params into their pre-reserved frame slots.
        # Phase 1b step 1 (2026-04-20): store directly from XBC/XDE without
        # push/pop XWA transit. Saves 2 B per non-XWA spill for 8/16-bit.
        #
        # Store opcodes (confirmed via ngpc_disasm MCP tool):
        #   LDW (XIY+d), WA  →  db 0xBD, d, 0x50
        #   LDW (XIY+d), BC  →  db 0xBD, d, 0x51
        #   LDW (XIY+d), DE  →  db 0xBD, d, 0x52
        #   LDB (XIY+d), A   →  db 0xBD, d, 0x41 (low byte of XWA)
        #   LDB (XIY+d), C   →  db 0xBD, d, 0x43 (low byte of XBC)
        #   LDB (XIY+d), E   →  db 0xBD, d, 0x45 (low byte of XDE)
        # 32-bit keeps transit (rare case; split via WA as before).
        LDW_SUFFIX = {'XWA': 0x50, 'XBC': 0x51, 'XDE': 0x52}
        LDB_SUFFIX = {'XWA': 0x41, 'XBC': 0x43, 'XDE': 0x45}
        for pname, reg_name, psz, slot_off in self._adecl_spills:
            d = slot_off & 0xFF
            if psz == 4:
                # 32-bit: transit via XWA (split into 2 LDW).
                # Direct BC/DE word stores would need QBC/QDE (bank-3 halves)
                # which aren't in scope here — defer to future optimization.
                if reg_name != 'XWA':
                    self.emit_instr(f'push {reg_name}                 ; adecl spill: {pname} (32-bit transit)')
                    self.emit_instr('pop  XWA')
                d_hi = (slot_off + 2) & 0xFF
                self.emit_instr('push XWA')
                self.emit_instr('pop  WA')
                self.emit_instr(f'db 0xBD, 0x{d:02X}, 0x50  ; LDW (XIY{slot_off:+d}), WA [adecl {pname} lo16]')
                self.emit_instr('pop  WA')
                self.emit_instr(f'db 0xBD, 0x{d_hi:02X}, 0x50  ; LDW (XIY{slot_off+2:+d}), WA [adecl {pname} hi16]')
            elif psz == 2:
                suf = LDW_SUFFIX[reg_name]
                reg16 = reg_name[1:]  # XBC -> BC, XDE -> DE, XWA -> WA
                self.emit_instr(f'db 0xBD, 0x{d:02X}, 0x{suf:02X}  ; LDW (XIY{slot_off:+d}), {reg16} [adecl param {pname}]')
            else:
                suf = LDB_SUFFIX[reg_name]
                # byte alias: XWA→A, XBC→C, XDE→E
                byte_name = {'XWA': 'A', 'XBC': 'C', 'XDE': 'E'}[reg_name]
                self.emit_instr(f'db 0xBD, 0x{d:02X}, 0x{suf:02X}  ; LDB (XIY{slot_off:+d}), {byte_name} [adecl param {pname}]')

        # ISR: save clobbered registers (CPU only auto-saves SR+PC)
        if self.is_interrupt:
            self.emit_instr('push WA')   # accumulator
            self.emit_instr('push BC')   # used in inc/dec pattern
            self.emit_instr('push HL')   # used in inc/dec (ld HL,1; add A,L; adc W,H) — MUST save!
            self.emit_instr('push XDE')  # used for memory addressing

        # Generate body
        # P-5.8 (pass 31) : try full-function IR migration first. If the
        # body is composed only of AST nodes supported by `_gen_stmt_to_ir`
        # / `_gen_expr_to_ir`, build the IR directly without calling
        # gen_block. The allocator + lowering pipeline (forced 'on'
        # mode below) will emit the body asm from the IR.
        migrated = False
        if (self._opt_c5_lvt  # gate on the C5 toolchain being active
                and func.body is not None
                and self._try_migrate_function_to_ir(func.body)):
            self._fn_fully_migrated_to_ir = True
            self._c5_stats['fns_fully_migrated'] = (
                self._c5_stats.get('fns_fully_migrated', 0) + 1
            )
            migrated = True
            if os.environ.get('T900CC_C5_TRACE_MIGRATION'):
                import sys as _sys
                _sys.stderr.write(f'[C5-migrate] {func.name}\n')
        if not migrated:
            self.gen_block(func.body)

        # Epilogue
        self.emit_label(self._func_exit_label)
        self._emit_epilogue(need_frame)
        self.emit('')

        # Chantier 4 P-1 / Chantier 5 P-5.1: round-trip the IR
        # container (IRFunction or legacy IRBuffer) and verify the
        # lowering produces the same lines that were emitted to
        # self.lines for this function. Any divergence indicates a
        # lowering bug — fail loud during dev rather than silently
        # producing wrong asm.
        active_ir = self.ir_function if self.ir_function is not None else self.ir_buffer
        # P-5.8 (pass 31) : skip round-trip when the function was FULLY
        # migrated to structured IR ops. The legacy `self.lines[start:]`
        # is empty/minimal (only prologue+epilogue, since `gen_block`
        # was skipped). The IR contains LoadImm/LoadLocal/etc. that
        # `lower_to_asm` (the legacy raw lowering) doesn't model. The
        # allocator pipeline below produces the real body lines.
        if (self._ir_check_enabled and active_ir is not None
                and not self._fn_fully_migrated_to_ir):
            recomputed = lower_to_asm(active_ir)
            actual = self.lines[self._ir_func_start_idx:]
            if recomputed != actual:
                # Find first divergence for the error message
                lim = min(len(recomputed), len(actual))
                first_diff = next(
                    (i for i in range(lim) if recomputed[i] != actual[i]),
                    lim,
                )
                raise CodeGenError(
                    f"IR round-trip diverged at line index {first_diff} "
                    f"in function {func.name}. "
                    f"actual={actual[first_diff] if first_diff < len(actual) else '<eof>'!r} "
                    f"recomputed={recomputed[first_diff] if first_diff < len(recomputed) else '<eof>'!r}",
                    func.line,
                )

        # Chantier 5 P-5.6: allocator-driven codegen pipeline (gated).
        # Runs liveness + allocator + lower_ir_with_allocation on the
        # IRFunction built during emission. In 'shadow' mode the output
        # is COMPARED with the legacy lines (binary-identical expected
        # because the IR currently contains only EmitRaw ops with their
        # physical registers baked in — the allocator has no degrees of
        # freedom). Any divergence indicates a plumbing bug. In '1'/'on'
        # mode the pipeline output REPLACES the legacy lines (body
        # delta possible — only safe for migrated codegen sites).
        # P-5.8 (pass 31) : pipeline trigger is also FORCED ON when the
        # function body was fully migrated to structured IR. In that
        # case the asm body lives ONLY in IR (gen_block was skipped),
        # so the allocator MUST run to produce the body lines — there
        # is no env-var opt-out for migrated fns.
        if ((self._opt_c5_regalloc in ('shadow', '1', 'on')
                or self._fn_fully_migrated_to_ir)
                and self.ir_function is not None):
            self._c5_run_pipeline(func)

        # Chantier 4 Phase P-4' peephole: remove consecutive
        # `push X; pop X` (same register) no-op sequences. See
        # `_c4_p4p_elide_push_pop_self` for full safety analysis.
        if self._opt_c4_p4p_push_pop_self:
            self._c4_p4p_elide_push_pop_self()

        # Chantier 4 Phase P-4'b WIDE-WINDOW extension: catches
        # `push X; ...non-clobbering ops...; pop X` pairs that survived
        # P-4' because of intervening text. Safety reasoning below.
        if self._opt_c4_p4pb_push_pop_wide:
            self._c4_p4pb_elide_push_pop_wide()

        # Chantier 5 Phase P-5.6.5 STORE-LOAD FORWARDING peephole.
        # Eliminates redundant `LDW WA, (XIY+off)` immediately after
        # `LDW (XIY+off), WA`. Multi-statement value tracking MVP.
        if self._opt_c5_ms_fwd:
            self._c5_ms_elide_store_load_fwd()

        # P-5.8 v7.2 (pass 38) : dead `ld W, 0` elision peephole.
        # Comparative disasm vs CC900 showed ~2700 sites of `ld W, 0
        # ; u8 zero-extend (P4)` emitted by our codegen vs ZERO in
        # CC900. ~41% of these have W subsequently overwritten before
        # any read → the `ld W, 0` is dead and can be elided. Targets
        # the +10492 `ld` instruction gap that dominates the body
        # delta vs CC900 (×2.50 ratio).
        if self._opt_c5_dead_ld_w:
            self._c5_elide_dead_ld_w_zero()

        # P-5.8 v7.4 (pass 40) : dead byte-split alu high-half peephole.
        # Eliminates `adc W, H` (etc.) when followed by a byte store
        # `LDB (mem), A` (high-byte result is dead). Audit shows
        # ~2500 sites of this pattern in j16 → potential −5 KB.
        # Conservative bails on intermediate flag-readers, branches,
        # cross-block, etc.
        if self._opt_c5_dead_alu_hi:
            self._c5_elide_dead_alu_hi()

        self.ir_function = None
        self.ir_buffer = None

    def _c5_run_pipeline(self, func) -> None:
        """Chantier 5 P-5.6 pipeline: liveness + allocator + lower.

        In 'shadow' mode: compares lowered output with self.lines[start:]
        and raises on mismatch.
        In '1'/'on' mode: replaces self.lines[start:] with lowered output.

        With the current IR (only EmitRaw ops, no structured ops with
        vregs), this pipeline is effectively a no-op confirmation:
        - Liveness analyzes physical register usage from EmitRaw text.
        - Allocator sees no virtual registers → trivial result.
        - lower_ir_with_allocation walks blocks and emits EmitRaw text
          verbatim → same output as the round-trip check from P-1.

        When P-5.6.1+ migrates specific codegen sites to use structured
        ops (BinOp, LoadLocal, etc.) with virtual registers, the
        pipeline will exercise the real allocator. For P-5.6 minimum-
        viable, this is plumbing validation only."""
        import importlib
        try:
            liveness_mod = importlib.import_module('t900cc_liveness')
            alloc_mod = importlib.import_module('t900cc_alloc')
        except ImportError as exc:
            raise CodeGenError(
                f'C5 pipeline failed to import dependencies: {exc}',
                func.line,
            )

        # Step 1: liveness analysis on the IR.
        liveness = liveness_mod.compute_liveness(self.ir_function)
        self._c5_stats['functions_processed'] += 1

        # Step 2: build LiveInterval list from the PER-OP disjoint
        # intervals (P-5.6.1 per-op refinement).
        # - Physical regs: one `LiveInterval(forced=R)` PER disjoint run.
        #   When the legacy text uses a phys reg in several non-adjacent
        #   places with dead gaps in between (typical pattern: WA used
        #   here, dead through structured-op gap, used again later),
        #   we feed the allocator multiple short intervals instead of
        #   one convex-hull span. The allocator's `expire_old(start)`
        #   then releases the phys reg in each gap, freeing it for
        #   `pref`-hinted vregs that fit cleanly.
        # - Virtual regs: one `LiveInterval(cls=WORD_DATA, pref='XWA')`
        #   per disjoint run. Our SSA-style structured emission gives
        #   each vreg a single tight run (one def + one use), so almost
        #   always len == 1 here.
        # - RESERVED regs (XIY, XSP): skipped — frame and stack pointer
        #   are out of the allocator pool.
        try:
            regclass_mod = importlib.import_module('t900cc_regclass')
            reserved = regclass_mod.RESERVED_REGS
            # WORD_DATA = {XWA, XBC, XDE, XHL} — only regs that admit
            # the `LDW (mem+disp), R16` 3-byte encoding wired in
            # `lower_ir_with_allocation`. Excludes XIX/XIZ which would
            # need a different store encoding family.
            vreg_cls = regclass_mod.RegClass.WORD_DATA
        except ImportError:
            reserved = frozenset({'XIY', 'XSP'})
            vreg_cls = None
        intervals = []
        phys_runs_total = 0
        # P-5.6.3: per-vreg class lookup. Helpers that need tighter
        # constraints than the WORD_DATA default populate
        # self._c5_vreg_cls. Map cls name → RegClass enum value.
        cls_name_to_enum = {}
        try:
            for member in regclass_mod.RegClass:
                cls_name_to_enum[member.name] = member
        except Exception:
            cls_name_to_enum = {}
        for reg_name, runs in liveness.live_intervals.items():
            if reg_name.startswith('%'):
                # Virtual register from a structured op.
                # Resolve per-vreg cls hint if present.
                hinted_cls_name = self._c5_vreg_cls.get(reg_name)
                if hinted_cls_name is not None and hinted_cls_name in cls_name_to_enum:
                    iv_cls = cls_name_to_enum[hinted_cls_name]
                    # Pref aligns with the cls when single-reg
                    # (WA_ONLY → XWA, HL_ONLY → XHL); otherwise stay
                    # on XWA as the legacy default.
                    if hinted_cls_name == 'HL_ONLY':
                        iv_pref = 'XHL'
                    else:
                        iv_pref = 'XWA'
                else:
                    iv_cls = vreg_cls
                    iv_pref = 'XWA'
                for lo, hi in runs:
                    intervals.append(alloc_mod.LiveInterval(
                        vreg=reg_name,
                        start=lo, end=hi,
                        cls=iv_cls,
                        pref=iv_pref,
                    ))
            else:
                if reg_name in reserved:
                    continue
                for idx, (lo, hi) in enumerate(runs):
                    intervals.append(alloc_mod.LiveInterval(
                        vreg=f'%phys_{reg_name}_{idx}',
                        start=lo, end=hi,
                        forced=reg_name,
                    ))
                phys_runs_total += len(runs)
        self._c5_stats['intervals_total'] += len(intervals)
        # Track how many per-op splits we created vs naive 1-per-reg.
        # Useful in stats output to gauge how often the convex hull
        # was pessimistic.
        self._c5_stats['phys_disjoint_runs'] = (
            self._c5_stats.get('phys_disjoint_runs', 0) + phys_runs_total
        )

        # Step 3: allocate.
        result = alloc_mod.allocate(intervals)
        self._c5_stats['spills_total'] += len(result.spilled)

        # P-5.6.1 wiring: vreg spill materialization is NOT yet wired
        # (P-5.5 only built the SpillSlotManager + insert_spill_code
        # skeleton — real Load/Store insertion is deferred). If any
        # vreg from a structured op got spilled, the lowering would
        # KeyError on the missing allocation. Skip pipeline lowering
        # gracefully and let the legacy text in self.lines ship.
        # The convex-hull liveness for phys regs makes XWA appear
        # "live across the whole function", so the allocator spills
        # the structured-op vreg → expected limitation until per-op
        # liveness lands in a later phase.
        vreg_spilled = any(v.startswith('%t') for v in result.spilled)
        if vreg_spilled:
            self._c5_stats['shadow_skipped_vreg_spilled'] = (
                self._c5_stats.get('shadow_skipped_vreg_spilled', 0) + 1
            )
            # P-5.8 (pass 31) : for migrated fns the legacy body is
            # empty, so silently bailing would ship broken asm
            # (label + epilogue, no body). Fail loud — better than
            # corrupt ROM. If this fires the migration predicate let
            # through a pattern the allocator can't handle yet.
            if self._fn_fully_migrated_to_ir:
                raise CodeGenError(
                    f'C5 pipeline: vreg spill in fully-migrated fn '
                    f'{func.name} — allocator could not place vreg(s) '
                    f'{sorted(v for v in result.spilled if v.startswith("%t"))!r}. '
                    f'Either widen the migration predicate guard or '
                    f'extend allocator coverage.',
                    func.line,
                )
            return

        # Step 4: insert spill code + lower with allocation.
        spill_mgr = alloc_mod.SpillSlotManager(base_offset=-self._frame_size)
        alloc_mod.insert_spill_code(self.ir_function, result, spill_mgr)
        new_lines = alloc_mod.lower_ir_with_allocation(
            self.ir_function, result, spill_mgr,
        )

        # Step 5: shadow compare or replace.
        # P-5.6.1 per-op refinement: with per-op disjoint intervals the
        # allocator can grant `pref='XWA'` to short-lived structured
        # vregs that fit in dead gaps of XWA. So fns with structured
        # emits should now match legacy byte-for-byte too — we no
        # longer skip the comparison. If a mismatch still fires it is
        # a real bug to investigate (e.g. a new structured pattern
        # whose vreg overlaps phys XWA at the migration site).
        actual = self.lines[self._ir_func_start_idx:]
        had_structured = self._c5_stats['structured_emits_this_function'] > 0
        if had_structured:
            self._c5_stats['functions_with_structured_emits'] += 1
        # P-5.8 (pass 31) : for fns FULLY migrated to IR, the body in
        # self.lines is empty (gen_block was skipped). The allocator
        # output IS the canonical body — force 'on' (replace) mode.
        effective_mode = (
            'on' if self._fn_fully_migrated_to_ir else self._opt_c5_regalloc
        )
        if effective_mode == 'shadow':
            if new_lines != actual:
                self._c5_stats['shadow_mismatches'] += 1
                lim = min(len(new_lines), len(actual))
                first_diff = next(
                    (i for i in range(lim) if new_lines[i] != actual[i]),
                    lim,
                )
                raise CodeGenError(
                    f'C5 shadow mode: pipeline output diverges at line {first_diff} '
                    f'in function {func.name}. '
                    f'legacy={actual[first_diff] if first_diff < len(actual) else "<eof>"!r} '
                    f'pipeline={new_lines[first_diff] if first_diff < len(new_lines) else "<eof>"!r}',
                    func.line,
                )
        else:
            # 'on'/'1' — replace.
            self.lines = self.lines[:self._ir_func_start_idx] + new_lines

    # ----- Chantier 4 Phase P-4' peephole helper -----

    _C4_P4P_PUSH_RE = re.compile(r'^\s+push\s+(\S+)(?:\s|$)')
    _C4_P4P_POP_RE = re.compile(r'^\s+pop\s+(\S+)(?:\s|$)')

    def _c4_p4p_elide_push_pop_self(self) -> None:
        """Walk self.lines[func_start:end] and delete every consecutive
        `push X; pop X` (same register name) pair.

        Safety analysis:
          - `push R` reads R, writes XSP and memory[XSP-sz]. No flag change.
          - `pop R` writes R from memory[XSP], writes XSP. No flag change.
          - Net effect of `push R; pop R`: XSP unchanged (decrement then
            increment by sz), memory unchanged (push wrote the value
            that pop then reads — same value), R unchanged (read then
            written back as same value). No flag side effects.
          - Therefore the pair is a fully observable no-op and can be
            removed without any semantic change.

        Origin of the pattern: t900cc's expression evaluator emits this
        in certain compound-expression sequences where it bounces WA
        through the stack to ensure a temp value remains live across
        an intermediate operation, but the intermediate happens to be
        non-clobbering for WA. The compiler doesn't know that at
        generation time, so a post-emit peephole catches the leftovers.

        Gain: 2 bytes per elision. Observed ~84 sites in StarGunner J16.
        """
        i = self._ir_func_start_idx
        end = len(self.lines)
        out: list = []
        i_local = i
        while i_local < end:
            line = self.lines[i_local]
            if i_local + 1 < end:
                m1 = self._C4_P4P_PUSH_RE.match(line)
                m2 = self._C4_P4P_POP_RE.match(self.lines[i_local + 1])
                if m1 and m2 and m1.group(1) == m2.group(1):
                    # Skip both lines.
                    self._c4_p4p_elision_count += 1
                    i_local += 2
                    continue
            out.append(line)
            i_local += 1
        self.lines = self.lines[:i] + out

    # ----- Chantier 5 Phase P-5.6.5 store-load forwarding peephole -----

    # `LDW (BASE+d), WA` = `db 0xB{base_idx}, d, 0x50` (store WA to mem).
    # BASE prefixes : 0xB8 XWA, 0xB9 XBC, 0xBA XDE, 0xBB XHL, 0xBC XIX,
    # 0xBD XIY, 0xBE XIZ, 0xBF XSP. We accept all 8 (the legacy can
    # store via any stack-base reg).
    _C5_MS_STORE_WA_RE = re.compile(
        r'^\s+db 0x(B[89A-F]), 0x([0-9A-F]{2}), 0x50\b',
        re.IGNORECASE,
    )
    # `LDW WA, (BASE+d)` = `db 0x9{base_idx}, d, 0x20`.
    _C5_MS_LOAD_WA_RE = re.compile(
        r'^\s+db 0x(9[89A-F]), 0x([0-9A-F]{2}), 0x20\b',
        re.IGNORECASE,
    )
    # Generic word store via stack base (any r16 src) : 0xB8..0xBF + d
    # + 0x50..0x53. Used to detect cell-overwrite at SAME (base+off).
    _C5_MS_ANY_WORD_STORE_RE = re.compile(
        r'^\s+db 0x(B[89A-F]), 0x([0-9A-F]{2}), 0x5[0-3]\b',
        re.IGNORECASE,
    )
    # Generic byte store via stack base : sub-op 0x40..0x47 (LDB
    # (base+d), R8). May overwrite half a cached word.
    _C5_MS_ANY_BYTE_STORE_RE = re.compile(
        r'^\s+db 0x(B[89A-F]), 0x([0-9A-F]{2}), 0x4[0-7]\b',
        re.IGNORECASE,
    )
    # P-5.8 v7 Axe A (pass 37) : mem-form ALU on word ARID +d8 cells.
    # Prefix `0x98..0x9F` (word ARID) + sub-op in the write-to-mem set :
    #   0x30+R EX, 0x60..0x6F INC/DEC #n, 0x88+R ADD, 0x98+R ADC,
    #   0xA8+R SUB, 0xB8+R SBC, 0xC8+R AND, 0xD8+R XOR, 0xE8+R OR
    #   0x38..0x3E ADD/.../OR imm16, 0x78..0x7F shifts
    # All of these MODIFY the word cell at (BASE+d). The store-load
    # forwarding peephole must treat them as cell overwrites (else it
    # would elide a subsequent LDW WA reload after INCW etc., shipping
    # stale WA values — caught during pass 37 smoke).
    _C5_MS_ANY_WORD_MEM_ALU_RE = re.compile(
        r'^\s+db 0x(9[89A-F]), 0x([0-9A-F]{2}), '
        r'0x(30|31|32|33|34|35|36|37'             # EX (mem), R16 (8 dst)
        r'|38|39|3[A-E]'                          # ALU (mem), imm16
        r'|6[0-9A-F]'                             # INC/DEC #n, (mem)
        r'|78|79|7[A-F]'                          # shifts (mem)
        r'|8[89A-F]|9[89A-F]|A[89A-F]|B[89A-F]'   # ADD/ADC/SUB/SBC (mem), R16
        r'|C[89A-F]|D[89A-F]|E[89A-F])'           # AND/XOR/OR (mem), R16
        r'\b',
        re.IGNORECASE,
    )
    # Byte version (0x88..0x8F prefix). Same sub-op layout. May overwrite
    # half a cached word, so byte mem-form ALU on (BASE+off) or
    # (BASE+off-1) invalidates the WORD cache at (BASE+off).
    _C5_MS_ANY_BYTE_MEM_ALU_RE = re.compile(
        r'^\s+db 0x(8[89A-F]), 0x([0-9A-F]{2}), '
        r'0x(30|31|32|33|34|35|36|37'
        r'|38|39|3[A-E]'
        r'|6[0-9A-F]'
        r'|78|79|7[A-F]'
        r'|8[89A-F]|9[89A-F]|A[89A-F]|B[89A-F]'
        r'|C[89A-F]|D[89A-F]|E[89A-F])'
        r'\b',
        re.IGNORECASE,
    )
    _C5_MS_LABEL_RE = re.compile(r'^\S+:\s*$')

    # Mapping store-prefix → matching load-prefix (same stack base reg).
    _C5_MS_STORE_TO_LOAD_PREFIX = {
        'B8': '98', 'B9': '99', 'BA': '9A', 'BB': '9B',
        'BC': '9C', 'BD': '9D', 'BE': '9E', 'BF': '9F',
    }
    # Window cap : the audit showed max distance = 3. Cap at 10 to be
    # safe — typical sites are immediate (distance 1).
    _C5_MS_WINDOW_CAP = 10

    def _c5_ms_elide_store_load_fwd(self) -> None:
        """Store-load forwarding peephole (C5 P-5.6.5).

        Eliminates `LDW WA, (BASE+off)` immediately following
        `LDW (BASE+off), WA` (same stack base, same offset) when no
        intermediate op invalidates the assumption that WA still holds
        the stored value.

        Safety bails on :
          - Any label (control flow could enter the window)
          - Any branch / call / ret (control flow could exit / clobber WA)
          - Any byte/word store via the SAME stack base at the SAME
            offset (cell overwritten — value would change)
          - Any byte store via the SAME stack base at off OR off+1
            (cached cell's bytes overlap — value changed)
          - Any word/byte store via a DIFFERENT stack base prefix
            (CONSERVATIVE — could alias the cached cell if & address
            taken and addressed via XDE etc.)
          - Any op whose use-def parser reports XWA in `defs`
            (= WA clobbered, cached value lost)
          - Distance > `_C5_MS_WINDOW_CAP` (capped to keep the peephole
            local and fast)

        Note : the peephole runs AFTER the C5 pipeline + C4 P-4' /
        P-4'b peepholes. So the asm text it operates on is the final
        emission (legacy + structured replaced + push/pop adjacent
        eliminations).

        Saves 3 bytes per elision (one `db 0x9{X}, d, 0x20` = 3 B).
        Audit shows ~60 sites on j16 → estim. −180 B.
        """
        # Local import to avoid cycle at module-load time.
        from t900cc_liveness import _extract_uses_defs_from_text

        start = self._ir_func_start_idx
        n = len(self.lines)
        to_delete: set = set()

        for i in range(start, n):
            if i in to_delete:
                continue
            line = self.lines[i]
            m_store = self._C5_MS_STORE_WA_RE.match(line)
            if not m_store:
                continue
            store_prefix = m_store.group(1).upper()
            store_off = m_store.group(2).upper()
            expected_load_prefix = self._C5_MS_STORE_TO_LOAD_PREFIX.get(store_prefix)
            if expected_load_prefix is None:
                continue
            store_off_int = int(store_off, 16)

            # Walk forward looking for the matching LDW WA at the same cell.
            window = 0
            j = i + 1
            safe = True
            while j < n and safe and window < self._C5_MS_WINDOW_CAP:
                if j in to_delete:
                    j += 1
                    continue
                nxt = self.lines[j]
                stripped = nxt.strip()
                if not stripped or stripped.startswith(';'):
                    j += 1
                    continue
                window += 1
                # Control-flow terminators
                if self._C5_MS_LABEL_RE.match(stripped):
                    safe = False
                    break
                if (self._C5_P4PB_OR_BRANCH_TEST(nxt)):
                    safe = False
                    break
                # Check if it's the matching load
                m_load = self._C5_MS_LOAD_WA_RE.match(nxt)
                if m_load:
                    if (m_load.group(1).upper() == expected_load_prefix
                            and m_load.group(2).upper() == store_off):
                        # MATCH — elide the load
                        to_delete.add(j)
                        self._c5_ms_fwd_count += 1
                    # Whether match or not, the load wrote to WA so any
                    # subsequent search must restart from a fresh store.
                    break
                # Check word-store aliasing
                m_word_st = self._C5_MS_ANY_WORD_STORE_RE.match(nxt)
                if m_word_st:
                    if (m_word_st.group(1).upper() == store_prefix
                            and m_word_st.group(2).upper() == store_off):
                        # Same cell rewritten — invalidate
                        safe = False
                        break
                    if m_word_st.group(1).upper() != store_prefix:
                        # Different base → conservative bail (potential alias)
                        safe = False
                        break
                    # Same base, different offset → safe, continue
                    j += 1
                    continue
                # Check byte-store aliasing
                m_byte_st = self._C5_MS_ANY_BYTE_STORE_RE.match(nxt)
                if m_byte_st:
                    base = m_byte_st.group(1).upper()
                    off_b = int(m_byte_st.group(2), 16)
                    if base == store_prefix and (off_b == store_off_int
                                                 or off_b == (store_off_int + 1) & 0xFF):
                        safe = False
                        break
                    if base != store_prefix:
                        safe = False
                        break
                    # Same base, non-overlapping byte → safe
                    j += 1
                    continue
                # P-5.8 v7 (pass 37) : check mem-form ALU on word ARID
                # +d8 (INCW/DECW/ADDW/etc. — pattern `db 0x9{N}, d, sub-op`
                # where sub-op is in the write-to-mem set). Modifies the
                # cell at (BASE+d) BUT does NOT update WA — eliding the
                # subsequent reload would ship stale data.
                m_mem_alu = self._C5_MS_ANY_WORD_MEM_ALU_RE.match(nxt)
                if m_mem_alu:
                    # Mem-form word ALU modifies cell — conservative bail
                    # whether same / different cell (alias risk).
                    safe = False
                    break
                # Byte mem-form ALU also modifies bytes — may overlap
                # the cached word cell. Same conservative bail.
                m_byte_alu = self._C5_MS_ANY_BYTE_MEM_ALU_RE.match(nxt)
                if m_byte_alu:
                    safe = False
                    break
                # WA-clobber check
                _uses, defs = _extract_uses_defs_from_text(nxt)
                if 'XWA' in defs:
                    safe = False
                    break
                j += 1

        if to_delete:
            self.lines = [
                ln for k, ln in enumerate(self.lines) if k not in to_delete
            ]

    # ----- P-5.8 v7.2 (pass 38) : dead `ld W, 0` elision peephole -----

    # Source emission pattern : `    ld   W, 0                 ; u8 zero-extend (P4)`
    # or `    ld   W, 0                 ; zero-extend u8 param <name>`
    # We match the strict `ld W, 0` line (any tail comment ok).
    _C5_LD_W_ZERO_RE = re.compile(r'^\s+ld\s+W,\s+0\b', re.IGNORECASE)

    # Forward-scan patterns that READ W (any read defeats DCE) :
    #   - byte-split alu : `adc W, H`, `add W, H`, `sub W, H`, `sbc W, H`,
    #     `and W, H`, `or W, H`, `xor W, H`, `cp W, H` (read W as lhs)
    #   - `or A, W` (Z flag test on whole WA — reads W)
    #   - any `<op> ..., W` (W as src) — covered by use-def fallback
    # Patterns that WRITE W (kill the prior `ld W, 0`, validating elision) :
    #   - `ld W, ...` (any imm or reg src)
    #   - `ld WA, ...` (overwrites both A and W)
    #   - `db 0x9{N}, d, 0x20  ; LDW WA, ...` (overwrites WA)
    #   - `db 0x{D}1, ... ; ld WA, (label)`  (LD WA, abs16)
    #   - `pop WA` (pops into WA)
    #   - `pop XWA` (pops into XWA)
    _C5_W_WRITE_LD_W_RE = re.compile(r'^\s+ld\s+W,', re.IGNORECASE)
    _C5_W_WRITE_LD_WA_RE = re.compile(r'^\s+ld\s+WA,', re.IGNORECASE)
    _C5_W_WRITE_LDW_WA_MEM_RE = re.compile(
        r'^\s+db 0x(9[89A-F]), 0x([0-9A-F]{2}), 0x20\b', re.IGNORECASE
    )
    _C5_W_WRITE_LD_WA_ABS_RE = re.compile(
        r'^\s+db 0xD1,', re.IGNORECASE  # word LD WA, (abs16)
    )
    _C5_W_WRITE_POP_WA_RE = re.compile(
        r'^\s+pop\s+(WA|XWA)\b', re.IGNORECASE
    )

    # Branch line that takes a label as target (jr / jrl / jp X). The
    # target label is in the operand portion. Used to walk across
    # branches in the dead-W peephole.
    _C5_BRANCH_TARGET_RE = re.compile(
        r'^\s+(?:jp|jr|jrl|calr)\s+(?:[A-Z]+,\s*)?([.\w]+)', re.IGNORECASE
    )
    # Label line : `.Lxxx:` or `_xxx:`.
    _C5_LABEL_DEFN_RE = re.compile(r'^([.\w]+):\s*$')

    def _c5_w_reads_in_window(self, start_idx: int, depth: int) -> bool:
        """Returns True if W is provably READ somewhere reachable from
        line index `start_idx` within `depth` lines, before any
        instruction that writes W. Used by `_c5_elide_dead_ld_w_zero`
        to look across branches : if neither the branch target nor the
        fall-through path reads W before writing it, the dead-W ld can
        be elided even across the branch.

        Conservative : returns True (= read) on any unrecognized
        pattern, label outside the function, depth exhaustion."""
        from t900cc_liveness import _extract_uses_defs_from_text
        n = len(self.lines)
        fn_start = self._ir_func_start_idx
        # Visit set keyed by line index to avoid loops.
        visited: set = set()
        stack: list = [(start_idx, depth)]
        while stack:
            idx, remaining = stack.pop()
            if remaining <= 0:
                return True  # too deep — conservative read
            if idx < fn_start or idx >= n:
                return True  # outside fn — conservative read
            if idx in visited:
                continue
            visited.add(idx)
            j = idx
            while j < n and remaining > 0:
                ln = self.lines[j]
                stripped = ln.strip()
                if not stripped or stripped.startswith(';'):
                    j += 1
                    continue
                remaining -= 1
                # Label : new BB, but the label itself is not a read
                if self._C5_LABEL_DEFN_RE.match(stripped):
                    j += 1
                    continue
                # Branch to target — fork
                m_br = self._C5_BRANCH_TARGET_RE.match(ln)
                if m_br:
                    target = m_br.group(1)
                    # Locate target label
                    target_idx = self._c5_find_label_idx(target)
                    if target_idx is None:
                        return True  # extern call or unknown — conservative
                    is_conditional = bool(
                        re.match(r'^\s+(?:jr|jrl)\s+\w+,', ln)
                    )
                    if is_conditional:
                        # Push fall-through + target
                        stack.append((j + 1, remaining))
                        stack.append((target_idx + 1, remaining))
                    else:
                        # Unconditional jp/jrl — only target path
                        stack.append((target_idx + 1, remaining))
                    break  # don't continue past this branch in this iter
                # Call / ret / unknown CF
                if re.match(r'^\s+call\s', ln) or re.match(r'^\s+ret\b', ln):
                    return True
                # WRITES of W kill the question (W now redefined, the
                # prior `ld W, 0` was dead from THIS path's POV).
                if (self._C5_W_WRITE_LD_W_RE.match(ln)
                        or self._C5_W_WRITE_LD_WA_RE.match(ln)
                        or self._C5_W_WRITE_LDW_WA_MEM_RE.match(ln)
                        or self._C5_W_WRITE_LD_WA_ABS_RE.match(ln)
                        or self._C5_W_WRITE_POP_WA_RE.match(ln)):
                    break  # path dead, no read found
                # Explicit W mention in operand → READ
                if (' W,' in ln or ', W' in ln):
                    return True
                # push WA / push XWA reads W
                if re.match(r'^\s+push\s+(WA|XWA)\b', ln, re.IGNORECASE):
                    return True
                # Store via LDW (mem), WA — reads WA
                if re.match(r'^\s+db 0xB[89A-F], 0x[0-9A-F]{2}, 0x50\b',
                            ln, re.IGNORECASE):
                    return True
                # Use-def : XWA in uses without A-only escape
                uses, defs = _extract_uses_defs_from_text(ln)
                if 'XWA' in uses:
                    if not (re.search(r'\bcp\s+A,', ln)
                            or re.search(r'\bld\s+\(.*\),\s*A\b', ln)
                            or re.search(r'\bldb\s', ln)
                            or re.match(r'^\s+db 0x[BD][89A-F], 0x[0-9A-F]{2}, 0x4[0-7]\b', ln)):
                        return True
                if 'XWA' in defs and not re.match(self._LVT_A_ONLY_WRITE_RE, ln):
                    break  # full WA write — path dead, no read
                j += 1
            else:
                # Loop exited via window/depth
                if j >= n:
                    continue
                if remaining <= 0:
                    return True
        return False

    def _c5_find_label_idx(self, label: str) -> int | None:
        """Locate the line index of a label definition within the current fn."""
        n = len(self.lines)
        fn_start = self._ir_func_start_idx
        for k in range(fn_start, n):
            ln = self.lines[k]
            m = self._C5_LABEL_DEFN_RE.match(ln.strip())
            if m and m.group(1) == label:
                return k
        return None

    def _c5_elide_dead_ld_w_zero(self) -> None:
        """Post-emit DCE peephole : eliminate `ld W, 0` lines whose W
        write is dead (W is overwritten before any read within the
        current straight-line basic block).

        Algorithm :
          For each `ld W, 0` line :
            Walk forward through subsequent lines.
            - If a line WRITES W (covered by `_C5_W_WRITE_*_RE`) → the
              `ld W, 0` was dead → mark for deletion.
            - If a line READS W (use-def shows XWA in uses) → the load
              was live → keep.
            - If a line is a label / branch / call / ret → control flow
              boundary, can't continue analysis → conservative keep.
            - If we exhaust `WINDOW_CAP` lines → conservative keep.

        Safety reasoning :
          - At the moment of `ld W, 0`, W=0 is committed.
          - If no subsequent read of W occurs before W is overwritten,
            no observable behavior change from skipping the load.
          - Reads include : direct W reference (`adc W, H`, `or A, W`,
            `push WA` — push reads both A and W), AND indirect reads
            via `push XWA` / store via `db 0xB{N}, d, 0x50` (LDW WA,
            mem) etc.

        Estimated impact (per comparative disasm pass 38 analysis) :
          - ~2700 `ld W, 0` sites in j16 (1015 `+ ld A, (XIY)`, 794 `+ ld
            A, (XDE+d)`, 515 `+ ld A, (XIY+d)`, 328 `+ ld A, (#)`,
            others). ~41% have W dead (overwritten before read)
            measured on .asm files → ~1100 deletions × 2 bytes ≈ −2 KB.

        Gated by `_opt_c5_dead_ld_w` env var. Runs AFTER all other
        peepholes so it sees the final emission.
        """
        from t900cc_liveness import _extract_uses_defs_from_text
        WINDOW_CAP = 30
        start = self._ir_func_start_idx
        n = len(self.lines)
        to_delete: set = set()

        for i in range(start, n):
            if i in to_delete:
                continue
            if not self._C5_LD_W_ZERO_RE.match(self.lines[i]):
                continue
            # Walk forward.
            j = i + 1
            window = 0
            disposition = None  # 'dead' or 'live' once decided
            while j < n and window < WINDOW_CAP:
                if j in to_delete:
                    j += 1
                    continue
                nxt = self.lines[j]
                stripped = nxt.strip()
                if not stripped or stripped.startswith(';'):
                    j += 1
                    continue
                window += 1
                # Control-flow boundaries — try cross-branch analysis
                # before bailing. If neither the branch target nor any
                # fall-through path reads W before writing it, the
                # original `ld W, 0` is dead even across the branch.
                if self._C5_MS_LABEL_RE.match(stripped):
                    # Label : new BB starts here. Check if any path
                    # from this label reads W before writing.
                    if not self._c5_w_reads_in_window(j, depth=40):
                        disposition = 'dead'
                    else:
                        disposition = 'live'
                    break
                if self._C5_P4PB_OR_BRANCH_TEST(nxt):
                    # Branch / call / ret. For unconditional branches
                    # and calls, the fall-through path is not taken
                    # immediately ; for conditional branches both paths
                    # apply. Defer to `_c5_w_reads_in_window` which
                    # walks both forks of conditional branches and the
                    # target of unconditional branches. CALL is treated
                    # as W-read (callee may use W) → conservative keep.
                    if re.match(r'^\s+call\s|^\s+calr\s|^\s+ret\b',
                                nxt, re.IGNORECASE):
                        disposition = 'live'
                    elif not self._c5_w_reads_in_window(j, depth=40):
                        disposition = 'dead'
                    else:
                        disposition = 'live'
                    break
                # Check WRITES of W FIRST (so a `pop WA` killing W
                # without reading it elides the prior ld W, 0).
                if (self._C5_W_WRITE_LD_W_RE.match(nxt)
                        or self._C5_W_WRITE_LD_WA_RE.match(nxt)
                        or self._C5_W_WRITE_LDW_WA_MEM_RE.match(nxt)
                        or self._C5_W_WRITE_LD_WA_ABS_RE.match(nxt)
                        or self._C5_W_WRITE_POP_WA_RE.match(nxt)):
                    disposition = 'dead'
                    break
                # Check READS of W via use-def fallback. If 'XWA' is
                # in uses BEFORE any def, W is read → keep load.
                # `ld A, (mem)` parses as defs={XWA} (A→XWA parent)
                # but doesn't read W. The use-def parser handles A-only
                # writes via `_LVT_A_ONLY_WRITE_RE` indirectly. For
                # safety here, ALSO inspect explicit text patterns.
                if (' W,' in nxt or ',  W' in nxt or ',W' in nxt
                        or ', W' in nxt):
                    # Likely reads W as second operand (e.g. `or A, W`,
                    # `adc W, H` reads W lhs, etc.). Conservative keep.
                    disposition = 'live'
                    break
                # `push WA` / `push XWA` reads both A and W.
                if re.match(r'^\s+push\s+(WA|XWA)\b', nxt, re.IGNORECASE):
                    disposition = 'live'
                    break
                # Store via `db 0xB{N}, d, 0x50` (LDW mem, WA) reads WA.
                if re.match(r'^\s+db 0xB[89A-F], 0x[0-9A-F]{2}, 0x50\b',
                            nxt, re.IGNORECASE):
                    disposition = 'live'
                    break
                # Generic use-def fallback : if XWA in uses, W might be read.
                uses, defs = _extract_uses_defs_from_text(nxt)
                if 'XWA' in uses:
                    # Could still be A-only read — check the line for
                    # explicit A-only patterns (cp A, ; ld (mem), A ;
                    # ldb forms). These don't read W.
                    if (re.search(r'\bcp\s+A,', nxt)
                            or re.search(r'\bld\s+\(.*\),\s*A\b', nxt)
                            or re.search(r'\bldb\s', nxt)
                            or re.match(r'^\s+db 0x[BD][89A-F], 0x[0-9A-F]{2}, 0x4[0-7]\b', nxt)):
                        # A-only — doesn't touch W. Continue scan.
                        pass
                    else:
                        disposition = 'live'
                        break
                if 'XWA' in defs and 'XWA' not in uses:
                    # XWA written without being read → W also dead.
                    # But check if it's A-only write (W not actually
                    # written). If so, can't claim 'dead' yet.
                    if re.match(self._LVT_A_ONLY_WRITE_RE, nxt):
                        # A-only writes don't kill W. Keep scanning.
                        pass
                    else:
                        # Full WA write → W is overwritten.
                        disposition = 'dead'
                        break
                j += 1
            if disposition == 'dead':
                to_delete.add(i)

        if to_delete:
            self.lines = [
                ln for k, ln in enumerate(self.lines) if k not in to_delete
            ]

    # P-5.8 v7.4 (pass 40) : dead byte-split alu high-half peephole.
    # Matches the high-half byte-split ops we emit in `_emit_alu16` :
    #   `adc W, H` / `sbc W, H` / `and W, H` / `or W, H` / `xor W, H`
    # The `_emit_alu16` low-half is `add A, L` / `sub A, L` / etc.,
    # which writes A and sets flags. The high-half operates on W (and
    # sets flags). When the consumer of the alu result is a byte store
    # of A (`LDB (mem), A` = `db 0x[BD][89A-F], d, 0x4{X}`), W is dead
    # and the flags from the high-half are dead too.
    _C5_ALU_HIGH_HALF_RE = re.compile(
        r'^\s+(adc|sbc|and|or|xor)\s+W,\s+H\b', re.IGNORECASE
    )
    # Byte store of A : `db 0x[BD][89A-F], 0x{disp}, 0x4{R}` where R picks
    # the source R8 (0..7). The store doesn't read W or flags.
    _C5_BYTE_STORE_A_RE = re.compile(
        r'^\s+db 0x[BD][89A-F], 0x[0-9A-F]{2}, 0x4[0-7]\b', re.IGNORECASE
    )

    def _c5_elide_dead_alu_hi(self) -> None:
        """Eliminate dead byte-split alu high-half lines (`adc W, H` etc.)
        when followed by a byte store of A AND no LATER WA-reader in
        the same straight-line region. Saves 2 B/site.

        Algorithm (per function) :
          For each line matching `_C5_ALU_HIGH_HALF_RE` :
            Walk forward up to WINDOW_CAP lines.
            - Phase 1 : look for byte store of A (`LDB (mem), A`).
              If we hit it BEFORE any read of W / flag-reader / branch
              / call / label → goto Phase 2. Else keep (`safe=False`).
            - Phase 2 (post-store) : continue scanning until WINDOW_CAP
              or end-of-region. If ANY read of W / push WA / push XWA /
              LDW (mem), WA → conservative keep (the dropped high-half
              would have set W, dropping it ships stale W to that
              consumer).

        **Pass 41 HW regression fix** : earlier v7.4 declared dead at
        the FIRST byte store and stopped scanning. Missed cases where
        a later `push WA` (for call arg passing) needed the full
        16-bit result. Now we scan the entire window for WA-readers.

        Safety reasoning :
          - Low-half `add A, L` (etc.) writes A + sets flags.
          - High-half `adc W, H` (etc.) writes W + sets flags.
          - The byte store consumes A but NOT W.
          - If nothing reads W within the safe window, the high-half
            is dead and can be dropped.
          - Flags : conservative bail on any branch (= only consumer
            of flags in straight-line code).

        Gated by `T900CC_C5_DEAD_ALU_HI=1` (default OFF after pass 41
        regression). User must opt in.
        """
        WINDOW_CAP = 12
        start = self._ir_func_start_idx
        n = len(self.lines)
        to_delete: set = set()

        for i in range(start, n):
            if i in to_delete:
                continue
            if not self._C5_ALU_HIGH_HALF_RE.match(self.lines[i]):
                continue
            j = i + 1
            window = 0
            dead = False
            safe = True
            byte_store_seen = False
            while j < n and window < WINDOW_CAP and safe:
                nxt = self.lines[j]
                stripped = nxt.strip()
                if not stripped or stripped.startswith(';'):
                    j += 1
                    continue
                window += 1
                # Control-flow boundary → keep (conservative)
                if self._C5_MS_LABEL_RE.match(stripped):
                    safe = False
                    break
                if self._C5_P4PB_OR_BRANCH_TEST(nxt):
                    # If we already saw byte store of A AND no WA-reader
                    # since, declare dead — the branch is the natural
                    # stop point (control flow boundary).
                    if byte_store_seen:
                        dead = True
                    else:
                        safe = False
                    break
                # WA-readers : any line that reads W or full WA.
                # ' W,' = W as first operand (`adc W, H`, `sub W, H` etc.)
                # ', W' = W as second operand (`or A, W`, `and X, W`)
                if ' W,' in nxt or ', W' in nxt:
                    safe = False
                    break
                # `push WA` / `push XWA` reads W (high byte of pushed value)
                if re.match(r'^\s+push\s+(WA|XWA)\b', nxt, re.IGNORECASE):
                    safe = False
                    break
                # Word store via WA (`db 0xB{N}, d, 0x50` = LDW mem, WA)
                if re.match(r'^\s+db 0xB[89A-F], 0x[0-9A-F]{2}, 0x50\b',
                            nxt, re.IGNORECASE):
                    safe = False
                    break
                # `ld (label), WA` (abs16 store) reads WA
                if re.match(r'^\s+ld\s+\([^)]+\),\s*WA\b',
                            nxt, re.IGNORECASE):
                    safe = False
                    break
                # `ld R, WA` (D-prefix reg-to-reg, broken but emitted
                # for some intrinsics) — reads WA
                if re.match(r'^\s+ld\s+(BC|DE|HL|XBC|XDE|XHL|XIX|XIY|XIZ|XSP),\s*WA\b',
                            nxt, re.IGNORECASE):
                    safe = False
                    break
                # Byte store of A → consumer of A found, continue
                # scanning to verify no LATER WA-reader.
                if self._C5_BYTE_STORE_A_RE.match(nxt):
                    byte_store_seen = True
                    j += 1
                    continue
                # WRITE of WA (full overwrite) → kills W, dead-from-here.
                # `ld WA, ...` / `ld W, ...` / `LDW WA, (mem)` / `pop WA` /
                # `pop XWA` etc.
                if (re.match(r'^\s+ld\s+W,', nxt, re.IGNORECASE)
                        or re.match(r'^\s+ld\s+WA,', nxt, re.IGNORECASE)
                        or re.match(r'^\s+ld\s+XWA,', nxt, re.IGNORECASE)
                        or re.match(r'^\s+db 0x9[89A-F], 0x[0-9A-F]{2}, 0x20\b',
                                    nxt, re.IGNORECASE)
                        or re.match(r'^\s+pop\s+(WA|XWA)\b',
                                    nxt, re.IGNORECASE)):
                    # WA killed without read. If we'd seen byte store,
                    # high-half was dead. If not, the alu result was
                    # never used at all → also dead.
                    dead = True
                    break
                j += 1
            else:
                # Window/end reached without explicit dead/keep decision.
                # If we saw byte store, can declare dead (no consumer
                # observed within window). Else conservative keep.
                if byte_store_seen:
                    dead = True
            if dead and safe:
                to_delete.add(i)

        if to_delete:
            self.lines = [
                ln for k, ln in enumerate(self.lines) if k not in to_delete
            ]

    def _C5_P4PB_OR_BRANCH_TEST(self, line: str) -> bool:
        """True if `line` is a branch/call/ret (control-flow boundary)."""
        from t900cc_liveness import (
            COND_BRANCH_RE, UNCOND_BRANCH_RE, RETURN_RE, CALL_RE,
        )
        return bool(
            COND_BRANCH_RE.match(line) or UNCOND_BRANCH_RE.match(line)
            or RETURN_RE.match(line) or CALL_RE.match(line)
        )

    # ----- Chantier 4 Phase P-4'b wide-window peephole helper -----

    _C4_P4PB_LABEL_RE = re.compile(r'^\S+:\s*$')

    def _c4_p4pb_elide_push_pop_wide(self) -> None:
        """Wide-window extension of P-4': delete `push X; ...; pop X` pairs
        where the intermediate ops are PROVABLY non-clobbering for parent(X)
        and the window doesn't cross control flow.

        Safety analysis (= P-4' + 3 wider-window invariants):

          - As in P-4': `push R; pop R` is a no-op IF the value popped
            equals the value pushed. With adjacent pairs this is trivially
            true. With a window, we must prove no intermediate instruction
            wrote R between push and pop.

          - Use-def parsing (`_extract_uses_defs_from_text`) tells us
            which physical-reg parents each line writes. If `parent(X)`
            (where parent normalizes WA→XWA, A→XWA, …) is in any
            intermediate `defs`, the window is unsafe — bail.

          - Calls (call/calr) clobber all caller-saved → `parent(X)` is
            in `defs` if X is caller-saved → caught by the def check.
            We also bail eagerly on calls because the push/pop may
            be intentional ABI stack hygiene that an aggressive elide
            could break.

          - Labels inside the window are UNSAFE: another branch could
            jump INTO the label from outside, bypassing the push and
            making the pop read garbage from the stack. Bail.

          - Branches (jp/jr/jrl, conditional or not) and returns
            (ret/reti/retd) inside the window are UNSAFE: control may
            leave the window without reaching our pop, leaving the
            stack imbalanced. Bail.

          - Nested balanced `push Y; ...; pop Y` inside the window is
            fine — depth tracking lets us look past them to find OUR
            matching pop at depth 0.

          - Strict register name match (`pushed_reg == popped_reg`):
            `push WA` (2-byte SP delta) cannot match `pop XWA` (4-byte
            SP delta). Same name guarantees same width.

        Gain: 2 bytes per elision (push X = 1 B, pop X = 1 B for the
        common regs). Audit on stargunner_j16 shows ~300 candidate sites
        across the linked modules.

        Operates AFTER `_c4_p4p_elide_push_pop_self` so adjacent pairs
        are already handled. The wide-window catches the residual.
        """
        # Local import to avoid cycle at module-load time.
        from t900cc_liveness import (
            _extract_uses_defs_from_text,
            SUB_TO_PARENT,
            COND_BRANCH_RE,
            UNCOND_BRANCH_RE,
            RETURN_RE,
            CALL_RE,
        )

        start = self._ir_func_start_idx
        n = len(self.lines)
        to_delete: set = set()

        for i in range(start, n):
            if i in to_delete:
                continue
            line = self.lines[i]
            m_push = self._C4_P4P_PUSH_RE.match(line)
            if not m_push:
                continue
            pushed_reg = m_push.group(1)
            pushed_parent = SUB_TO_PARENT.get(pushed_reg.upper())
            if pushed_parent is None:
                continue
            # Walk forward looking for the matching pop X at depth 0.
            depth = 1
            j = i + 1
            safe = True
            match_idx: Optional[int] = None
            while j < n and safe:
                if j in to_delete:
                    j += 1
                    continue
                nxt = self.lines[j]
                # Unsafe terminators: anything that could move control
                # in or out of the window.
                if self._C4_P4PB_LABEL_RE.match(nxt.rstrip()):
                    safe = False
                    break
                if (COND_BRANCH_RE.match(nxt)
                        or UNCOND_BRANCH_RE.match(nxt)
                        or RETURN_RE.match(nxt)
                        or CALL_RE.match(nxt)):
                    safe = False
                    break
                m_push2 = self._C4_P4P_PUSH_RE.match(nxt)
                if m_push2:
                    depth += 1
                    j += 1
                    continue
                m_pop2 = self._C4_P4P_POP_RE.match(nxt)
                if m_pop2:
                    depth -= 1
                    if depth == 0:
                        if m_pop2.group(1) == pushed_reg:
                            match_idx = j
                        # Found the depth-0 pop. If reg mismatched, leave
                        # alone — that's a deliberate stack shuffle.
                        break
                    j += 1
                    continue
                # Other op — check if it writes parent(X).
                _uses, defs = _extract_uses_defs_from_text(nxt)
                if pushed_parent in defs:
                    safe = False
                    break
                j += 1
            if match_idx is not None and safe:
                to_delete.add(i)
                to_delete.add(match_idx)
                self._c4_p4pb_elision_count += 1

        if to_delete:
            self.lines = [
                ln for k, ln in enumerate(self.lines) if k not in to_delete
            ]

    def _count_locals(self, node) -> int:
        """Count total bytes needed for local variables (recursive)."""
        total = 0
        if isinstance(node, Block):
            for s in node.stmts:
                total += self._count_locals(s)
        elif isinstance(node, VarDecl):
            if not node.is_static:   # static locals go in BSS/DATA, not on frame
                sz = self.type_size(node.type_)
                if sz < 2:
                    sz = 2
                total += sz
        elif isinstance(node, IfStmt):
            total += self._count_locals(node.then)
            if node.else_:
                total += self._count_locals(node.else_)
        elif isinstance(node, WhileStmt):
            total += self._count_locals(node.body)
        elif isinstance(node, DoWhileStmt):
            total += self._count_locals(node.body)
        elif isinstance(node, ForStmt):
            if node.init:
                total += self._count_locals(node.init)
            total += self._count_locals(node.body)
        elif isinstance(node, SwitchStmt):
            for clause in node.clauses:
                for s in clause.stmts:
                    total += self._count_locals(s)
        return total

    def _assign_locals(self, node):
        """Assign stack offsets to local VarDecls (negative from XHL)."""
        if isinstance(node, Block):
            for s in node.stmts:
                self._assign_locals(s)
        elif isinstance(node, VarDecl) and node not in (self.bss_vars + self.data_vars):
            if node.is_static:
                # Static locals: persistent storage in BSS/DATA (not on frame).
                # Mangled name avoids collisions: funcname__varname
                mangled = f'{self.current_func_name}__{node.name}'
                init_expr = node.init_expr
                node_type = node.type_
                # Convert string literal init to InitList of bytes; respect declared array size.
                if (isinstance(init_expr, Const) and isinstance(init_expr.value, str)
                        and isinstance(node_type, ArrayType)):
                    s = init_expr.value
                    count = node_type.count
                    if count == 0:
                        bytes_list = [ord(c) for c in s] + [0]
                        node_type = ArrayType(node_type.elem, len(bytes_list))
                    else:
                        bytes_list = [ord(c) for c in s[:count]]
                        while len(bytes_list) < count:
                            bytes_list.append(0)
                    init_expr = InitList(bytes_list)
                proxy = VarDecl(mangled, node_type, init_expr=init_expr,
                                is_static=False, is_const=node.is_const, line=node.line)
                if isinstance(init_expr, InitList):
                    proxy.type_ = self._infer_unsized_array_type(proxy.type_, init_expr)
                    if node.is_const:
                        self.const_vars.append(proxy)
                    else:
                        self.data_vars.append(proxy)
                elif init_expr is None:
                    self.bss_vars.append(proxy)
                elif isinstance(init_expr, Const) and init_expr.value == 0:
                    # Zero-init → BSS (same as uninitialized; avoids DATA ROM address bug)
                    self.bss_vars.append(proxy)
                else:
                    self.data_vars.append(proxy)
                sym = Symbol(mangled, proxy.type_, 'global', offset=0)
                # const static locals with InitList go to f_const (ROM 0x200000+) → need far
                if isinstance(init_expr, InitList) and node.is_const:
                    sym.is_far = True
                self.static_local_globals[node.name] = sym
            else:
                can_bank_local = (
                    node.is_register and
                    self.opt_perf_lag_5 and
                    not self.is_interrupt and
                    not self._has_inline_asm and
                    isinstance(node.type_, IntType) and
                    self.type_size(node.type_) == 1 and
                    node.name not in self._addr_taken_local_names and
                    len(self._reg_bank_slots_free) > 0
                )
                if can_bank_local:
                    reg_name = self._reg_bank_slots_free.pop(0)
                    sym = Symbol(node.name, node.type_, 'local', offset=0, reg_name=reg_name)
                    self.local_vars[node.name] = sym
                    self._has_reg_bank_locals = True
                    return
                sz = self.type_size(node.type_)
                if sz < 2:
                    sz = 2
                self.local_offset -= sz
                sym = Symbol(node.name, node.type_, 'local', offset=self.local_offset)
                self.local_vars[node.name] = sym
        elif isinstance(node, IfStmt):
            self._assign_locals(node.then)
            if node.else_:
                self._assign_locals(node.else_)
        elif isinstance(node, WhileStmt):
            self._assign_locals(node.body)
        elif isinstance(node, DoWhileStmt):
            self._assign_locals(node.body)
        elif isinstance(node, ForStmt):
            if node.init:
                self._assign_locals(node.init)
            self._assign_locals(node.body)
        elif isinstance(node, SwitchStmt):
            for clause in node.clauses:
                for s in clause.stmts:
                    self._assign_locals(s)
        elif isinstance(node, LabelStmt):
            if node.stmt is not None:
                self._assign_locals(node.stmt)

    def _emit_epilogue(self, need_frame: bool):
        if need_frame:
            if self._frame_reg_saved:
                self.emit_instr('lda  XSP, XIX')   # drop locals, keep saved XIX on top
                self.emit_instr('pop  XIX')        # restore alternate frame register
            else:
                # unlk XIY: XSP = XIY+4; pop old XIY — deallocates all locals + restores frame ptr.
                self.emit_instr('unlk XIY')       # hw confirmed safe (link/unlk bug = N>=5 only)
        if self._save_xix_scratch:
            self.emit_instr('pop  XIX')
        if self._save_xiz_regbank:
            self.emit_instr('pop  XIZ')
        if self.is_interrupt:
            self.emit_instr('pop  XDE')           # restore XDE
            self.emit_instr('pop  HL')            # restore HL (clobbered by ld HL,1 in inc/dec)
            self.emit_instr('pop  BC')            # restore BC
            self.emit_instr('pop  WA')            # restore WA
            self.emit_instr('reti')               # interrupt handler: restore SR + PC
        else:
            self.emit_instr('ret')

    # -- Statement generation --

    def gen_block(self, block: Block):
        for s in block.stmts:
            self.gen_stmt(s)

    def gen_stmt(self, node):
        if node is None:
            return
        if isinstance(node, Block):
            self.gen_block(node)
        elif isinstance(node, VarDecl):
            self.gen_local_decl(node)
        elif isinstance(node, ExprStmt):
            if isinstance(node.expr, UnaryOp) and node.expr.op in ('post++', 'post--'):
                delta = 1 if node.expr.op == 'post++' else -1
                self.gen_inc_dec(node.expr.expr, delta=delta, post=False)
            else:
                self.gen_expr(node.expr)
        elif isinstance(node, IfStmt):
            self.gen_if(node)
            if not (node.else_ is None and self._stmt_never_falls_through(node.then)):
                self._invalidate_elem_base_cache()
        elif isinstance(node, WhileStmt):
            self.gen_while(node)
            self._invalidate_elem_base_cache()
        elif isinstance(node, DoWhileStmt):
            self.gen_do_while(node)
            self._invalidate_elem_base_cache()
        elif isinstance(node, ForStmt):
            self.gen_for(node)
            self._invalidate_elem_base_cache()
        elif isinstance(node, ReturnStmt):
            self._invalidate_elem_base_cache()
            self.gen_return(node)
        elif isinstance(node, BreakStmt):
            self._invalidate_elem_base_cache()
            if self.loop_break_label:
                self.emit_instr(f'jp   {self.loop_break_label}')
            else:
                self.emit_comment('ERROR: break outside loop')
        elif isinstance(node, ContinueStmt):
            self._invalidate_elem_base_cache()
            if self.loop_cont_label:
                self.emit_instr(f'jp   {self.loop_cont_label}')
            else:
                self.emit_comment('ERROR: continue outside loop')
        elif isinstance(node, SwitchStmt):
            self.gen_switch(node)
            self._invalidate_elem_base_cache()
        elif isinstance(node, GotoStmt):
            self._invalidate_elem_base_cache()
            lbl = f'.L_goto_{self.current_func_name}_{node.label}'
            self.emit_instr(f'jp   {lbl}')
        elif isinstance(node, LabelStmt):
            self._invalidate_elem_base_cache()
            lbl = f'.L_goto_{self.current_func_name}_{node.label}'
            self.emit_label(lbl)
            if node.stmt is not None:
                self.gen_stmt(node.stmt)
        else:
            self.emit_comment(f'TODO: {type(node).__name__}')

    def gen_local_decl(self, node: VarDecl):
        # Static locals: storage is in BSS/DATA (initialized at link time), skip runtime init.
        if node.is_static:
            return
        # Offset already assigned; emit init if needed
        sym = self.local_vars.get(node.name)
        if sym is None:
            self.emit_comment(f'ERROR: local {node.name!r} not in symbol table')
            return
        if node.init_expr is not None:
            self.gen_expr(node.init_expr)
            self._store_local(sym)

    def _store_local(self, sym: Symbol):
        """Store WA/A/XWA into a stack-backed symbol via its frame base register."""
        if sym.reg_name:
            if self.type_size(sym.type_) != 1:
                raise CodeGenError('register-bank locals currently support only 8-bit integers')
            self._emit_xiz_bank_store_a(sym.reg_name)
            return
        off = sym.offset
        d = off & 0xFF  # signed byte as unsigned
        sz = self.type_size(sym.type_)
        base_reg = self._stack_base_reg(sym)
        store_op = 0xB8 + self._stack_base_idx(base_reg)
        if sz == 1:
            self.emit_instr(
                f'db 0x{store_op:02X}, 0x{d:02X}, 0x41  ; LDB ({base_reg}{off:+d}), A   [u8 byte store]'
            )
        elif sz == 4:
            # Split 32-bit pointer store into two validated 16-bit frame-relative stores.
            # push XWA lays out: [XSP+0,1]=lo16, [XSP+2,3]=hi16 (little-endian).
            d_hi = (off + 2) & 0xFF
            self.emit_instr('push XWA')
            self.emit_instr(f'pop  WA')                                                   # WA = lo16; XSP+=2
            self.emit_instr(
                f'db 0x{store_op:02X}, 0x{d:02X}, 0x50  ; LDW ({base_reg}{off:+d}), WA  [ptr lo16]'
            )
            self.emit_instr(f'pop  WA')                                                   # WA = hi16; XSP+=2
            self.emit_instr(
                f'db 0x{store_op:02X}, 0x{d_hi:02X}, 0x50  ; LDW ({base_reg}{off+2:+d}), WA  [ptr hi16]'
            )
        else:
            self.emit_instr(f'db 0x{store_op:02X}, 0x{d:02X}, 0x50  ; LDW ({base_reg}{off:+d}), WA')

    def _load_local(self, sym: Symbol):
        """Load a stack-backed symbol into A/WA/XWA from its frame base register.

        Chantier 5 P-5.6.6 LVT: before emitting the LDW/LDB, check if
        WA already holds this local's value. If yes, skip the emission
        entirely — the value is already where the caller expects it.
        """
        if sym.reg_name:
            if self.type_size(sym.type_) != 1:
                raise CodeGenError('register-bank locals currently support only 8-bit integers')
            # Banked register load — different path, not LVT-cacheable yet
            self._lvt_wa = None
            # P4 peephole: zero-extend u8 via `ld W, 0` (2 B) instead of
            # `ld WA, 0` (3 B). A is overwritten by the byte load below.
            self.emit_instr('ld   W, 0                 ; u8 zero-extend (P4)')
            self._emit_xiz_bank_load_a(sym.reg_name)
            self._maybe_sign_extend_loaded_scalar(sym.type_)
            return
        off = sym.offset
        d = off & 0xFF
        sz = self.type_size(sym.type_)
        base_reg = self._stack_base_reg(sym)
        # LVT skip-check : for u16 loads on any stack base, check if
        # WA already holds the value at (BASE+off). For u8 / u32 the
        # post-load WA contents have a different shape (zero-ext byte
        # or 32-bit value), so caching them under ('local', base, off)
        # would confuse subsequent u16 loads at the same cell.
        if self._opt_c5_lvt and sz == 2:
            base_idx = self._stack_base_idx(base_reg)
            base_nib = format(8 + base_idx, 'X')  # 8..F for r16 base
            if self._lvt_wa == ('local', base_nib, off):
                # WA already holds the value at (BASE+off). Skip emission.
                self._c5_lvt_hits += 1
                self._maybe_sign_extend_loaded_scalar(sym.type_)
                return
        load8_op = 0x88 + self._stack_base_idx(base_reg)
        load16_op = 0x98 + self._stack_base_idx(base_reg)
        load32_op = 0xA8 + self._stack_base_idx(base_reg)
        if sz == 1:
            # P-5.8 v7.3 (pass 39) : byte-narrow load — caller hint
            # `_byte_narrow_load=True` means "I only need A, don't
            # waste 2 bytes zero-extending W". Saves 2 B per site at
            # u8 cmp / byte-store / byte-arith call sites.
            if not (self._byte_narrow_load and self._opt_c5_byte_narrow):
                # P4: `ld W, 0` (2 B) instead of `ld WA, 0` (3 B). A is set by LDB below.
                self.emit_instr('ld   W, 0                 ; u8 zero-extend (P4)')
            self.emit_instr(f'db 0x{load8_op:02X}, 0x{d:02X}, 0x21  ; LDB A, ({base_reg}{off:+d})')
            self._maybe_sign_extend_loaded_scalar(sym.type_)
        elif sz == 4:
            self.emit_instr(f'db 0x{load32_op:02X}, 0x{d:02X}, 0x20  ; LD XWA, ({base_reg}{off:+d}) [far ptr]')
        else:
            self.emit_instr(f'db 0x{load16_op:02X}, 0x{d:02X}, 0x20  ; LDW WA, ({base_reg}{off:+d})')

    def _load_param(self, sym: Symbol):
        """Load parameter into A/WA/XWA.

        Three paths:
          1. adecl_live_reg set (P2 leaf) — param lives in XWA/XBC/XDE, no
             memory access. Stack-balanced `push/pop` transit for non-XWA.
          2. Stack-based param (cdecl) — XIY+disp load via _load_local encoding
          3. Spilled adecl param — XIY+disp load on negative offset (same
             encoding as a local; offset is negative).
        """
        if sym.adecl_live_reg:
            reg = sym.adecl_live_reg
            sz = self.type_size(sym.type_)
            if reg == 'XWA':
                # arg0 — already in XWA. Ensure zero-extension for u8 u16
                # when signed scalars are involved.
                if sz == 1:
                    # A already has the byte. W may be non-zero from caller
                    # (if caller used 16-bit const load with W=0 it is fine,
                    # but generated code may leave junk). Zero W explicitly.
                    self.emit_instr(f'ld   W, 0                 ; zero-extend u8 param {sym.name}')
                # sz == 2 or 4 : result is already in WA / XWA. Nothing to emit.
                self._maybe_sign_extend_loaded_scalar(sym.type_)
            else:
                # XBC or XDE — transit via stack-balanced push/pop to reach XWA.
                # push X<reg> (4 B on stack) + pop XWA (4 B off stack) — balanced.
                if sz == 4:
                    self.emit_instr(f'push {reg}                ; leaf adecl param {sym.name} (32-bit)')
                    self.emit_instr('pop  XWA')
                elif sz == 2:
                    self.emit_instr(f'push {reg}                ; leaf adecl param {sym.name}')
                    self.emit_instr('pop  XWA')
                    # WA = low 16 of reg = param value ✓ (XWA high may contain junk
                    # from reg bank-3, but u16 code only uses WA).
                else:  # sz == 1
                    self.emit_instr(f'push {reg}                ; leaf adecl param {sym.name} (u8)')
                    self.emit_instr('pop  XWA')
                    if not (self._byte_narrow_load and self._opt_c5_byte_narrow):
                        self.emit_instr(f'ld   W, 0                 ; zero-extend u8 param {sym.name}')
                self._maybe_sign_extend_loaded_scalar(sym.type_)
            return

        off = sym.offset
        d = off & 0xFF
        sz = self.type_size(sym.type_)
        if sz == 1:
            # P-5.8 v7.3 byte-narrow load — see `_load_local`.
            if not (self._byte_narrow_load and self._opt_c5_byte_narrow):
                # P4: `ld W, 0` (2 B) instead of `ld WA, 0` (3 B). A is set by LDB below.
                self.emit_instr('ld   W, 0                 ; u8 zero-extend (P4)')
            self.emit_instr(f'db 0x8D, 0x{d:02X}, 0x21  ; LDB A, (XIY{off:+d}) [param]')
            self._maybe_sign_extend_loaded_scalar(sym.type_)
        elif sz == 4:
            self.emit_instr(f'db 0xAD, 0x{d:02X}, 0x20  ; LD XWA, (XIY{off:+d}) [far param]')
        else:
            self.emit_instr(f'db 0x9D, 0x{d:02X}, 0x20  ; LDW WA, (XIY{off:+d}) [param]')

    def _sign_extend_byte_to_wa(self):
        """Sign-extend byte value in A to 16-bit WA via safe 32-bit ops.
        Sequence:
          WA = 0x00bb
          extz XWA   -> 0x000000bb
          sll 8,XWA  -> 0x0000bb00
          exts XWA   -> 0x0000bb00 or 0xffffbb00 depending on bit7(bb)
          sra 8,XWA  -> 0x000000bb or 0xffffffffbb? low WA becomes signed 16-bit
        """
        self.emit_instr('db 0xE8, 0x12  ; extz XWA (zero-extend byte in A to XWA)')
        self.emit_instr('sll 8, XWA')
        self.emit_instr('db 0xE8, 0x13  ; exts XWA (propagate byte sign via WA bit15)')
        self.emit_instr('sra 8, XWA')

    def _maybe_sign_extend_loaded_scalar(self, ty: Optional[Type]):
        """If the just-loaded scalar is a signed 8-bit integer, sign-extend it into WA."""
        if isinstance(ty, IntType) and ty.nbytes == 1 and ty.signed:
            self._sign_extend_byte_to_wa()

    def gen_if(self, node: IfStmt):
        label_else = self.fresh_label('else')
        label_end  = self.fresh_label('endif')
        self.gen_expr_bool(node.cond, label_else, negate=True)
        saved_elem_key = self._xbc_cached_elem_key
        saved_elem_off = self._xbc_cached_elem_offset
        preserve_fallthrough_elem_cache = (
            node.else_ is None and self._stmt_never_falls_through(node.then)
        )
        self.gen_stmt(node.then)
        if preserve_fallthrough_elem_cache:
            self._xbc_cached_elem_key = saved_elem_key
            self._xbc_cached_elem_offset = saved_elem_off
        if node.else_:
            self.emit_instr(f'jp   {label_end}')
        self.emit_label(label_else)
        if node.else_:
            self.gen_stmt(node.else_)
            self.emit_label(label_end)

    def gen_while(self, node: WhileStmt):
        label_top   = self.fresh_label('wtest')
        label_body  = self.fresh_label('wbody')
        label_end   = self.fresh_label('wend')
        old_break = self.loop_break_label
        old_cont  = self.loop_cont_label
        self.loop_break_label = label_end
        self.loop_cont_label  = label_top
        self.emit_label(label_top)
        self.gen_expr_bool(node.cond, label_end, negate=True)
        self.gen_stmt(node.body)
        self.emit_instr(f'jp   {label_top}')   # jp (24-bit) — loop body may exceed jr range
        self.emit_label(label_end)
        self.loop_break_label = old_break
        self.loop_cont_label  = old_cont

    def gen_do_while(self, node: DoWhileStmt):
        label_top = self.fresh_label('dtop')
        label_end = self.fresh_label('dend')
        old_break = self.loop_break_label
        old_cont  = self.loop_cont_label
        self.loop_break_label = label_end
        self.loop_cont_label  = label_top
        self.emit_label(label_top)
        self.gen_stmt(node.body)
        self.gen_expr_bool(node.cond, label_top, negate=False)
        self.emit_label(label_end)
        self.loop_break_label = old_break
        self.loop_cont_label  = old_cont

    def gen_for(self, node: ForStmt):
        label_test = self.fresh_label('ftest')
        label_body = self.fresh_label('fbody')
        label_step = self.fresh_label('fstep')
        label_end  = self.fresh_label('fend')
        old_break = self.loop_break_label
        old_cont  = self.loop_cont_label
        self.loop_break_label = label_end
        self.loop_cont_label  = label_step
        if node.init:
            self.gen_stmt(node.init)
        self.emit_label(label_test)
        if node.cond:
            self.gen_expr_bool(node.cond, label_end, negate=True)
        self.gen_stmt(node.body)
        self.emit_label(label_step)
        if node.step:
            self.gen_expr(node.step)
        self.emit_instr(f'jp   {label_test}')  # jp (24-bit) — loop body may exceed jr range
        self.emit_label(label_end)
        self.loop_break_label = old_break
        self.loop_cont_label  = old_cont

    def gen_switch(self, node: SwitchStmt):
        """Generate switch with full C89 fallthrough semantics. break uses loop_break_label.

        Fast path — dense consecutive-from-0 u8 switch (cases exactly {0..N-1}):
          Decrement-chain dispatch, no push/pop overhead.
          Fallthrough: natural (bodies are sequential, no implicit jp emitted).
          or A,W (C8 E1) tests A==0; sub A,1 (C9 CA 01) decrements for each next case.
          NOTE: sub A,1 not yet hardware-bisected — flag if unexpected behaviour.

        Standard path — two-phase structure for correct C89 fallthrough:
          Phase 1 — dispatch: push expr; compare each case; jrl Z → disp_N on match;
            fall through to no-match (add XSP,2 + jp default/end).
          Phase 2 — bodies in source order:
            disp_N: add XSP,2  (dispatch entry — saved value on stack, discard here)
            body_N: [stmts]    (fallthrough entry — stack already clean, no discard)
            Fallthrough (no break): jp body_N+1 — skips the next disp's add XSP,2.
          u8 fast compare: expr is u8 → H==0 guaranteed, skip xor W,H + or A,W.
        """
        label_end = self.fresh_label('sw_e')
        old_break = self.loop_break_label
        self.loop_break_label = label_end

        explicit = [c for c in node.clauses if c.value is not None]
        defaults = [c for c in node.clauses if c.value is None]

        if explicit:
            vals = sorted(c.value for c in explicit)

            # Evaluate expression once; use the returned type for accurate u8 detection.
            # (typeof_expr falls back to U16 for FieldAccess/Subscript;
            #  gen_expr returns the real element type set by gen_field_access etc.)
            etype = self.gen_expr(node.expr)
            is_u8_expr = self.type_size(etype) == 1

            # Dense fast path: cases are exactly {0, 1, ..., N-1}, u8 expr, N >= 2
            # P3 disabled (sub A,1 / C9 CA 01 not yet HW-bisected)
            is_dense = False  # (
            #     is_u8_expr
            #     and len(vals) >= 2
            #     and vals == list(range(len(vals)))
            # )

            if is_dense:
                sorted_clauses = sorted(explicit, key=lambda c: c.value)
                label_default = self.fresh_label('sw_df') if defaults else label_end
                case_labels = [self.fresh_label('sw_c') for _ in vals]
                # A = switch value, W == 0 (u8 expr: ld WA,0 precedes every LDB load)

                # Case 0: or A,W → Z set iff A==0 (W==0 for u8)  [C8 E1, safe]
                self.emit_instr('or   A,  W')
                self.emit_instr(f'jrl  Z,  {case_labels[0]}')
                for i in range(1, len(vals)):
                    # sub A,1 → Z set iff A was 1 before decrement  [C9 CA 01]
                    # NOTE: sub A,imm8 not yet hardware-bisected independently
                    self.emit_instr('sub  A,  1')
                    self.emit_instr(f'jrl  Z,  {case_labels[i]}')
                # No match → default or end
                self.emit_instr(f'jp   {label_default}')

                for i, clause in enumerate(sorted_clauses):
                    self.emit_label(case_labels[i])
                    for stmt in clause.stmts:
                        self.gen_stmt(stmt)
                    # C89 fallthrough: no implicit jp — bodies are sequential.
                    # BreakStmt emits its own jp label_end via gen_stmt.
                    # Non-break tails: fall through to next case label (or default/end).

                if defaults:
                    self.emit_label(label_default)
                    for stmt in defaults[0].stmts:
                        self.gen_stmt(stmt)

            else:
                # Standard path — two-phase for C89 fallthrough.
                # Phase 1: all dispatch comparisons; jrl Z → disp_N on match.
                # Phase 2: bodies in source order.
                #   disp_N: add XSP,2   ← dispatch entry (saved WA on stack → discard)
                #   body_N: [stmts]     ← fallthrough entry (stack already clean)
                #   no break → jp body_{N+1} (skip next disp's add XSP,2)
                label_default = self.fresh_label('sw_df') if defaults else label_end
                disp_labels = [self.fresh_label('sw_d') for _ in explicit]
                body_labels = [self.fresh_label('sw_b') for _ in explicit]

                # Phase 1: dispatch comparisons
                self.emit_instr('push WA')
                for i, clause in enumerate(explicit):
                    self.emit_instr('pop  HL')
                    self.emit_instr('push HL')
                    if is_u8_expr and 0 <= clause.value <= 255:
                        # u8 fast compare: H==0 guaranteed, skip xor W,H + or A,W
                        self.emit_instr(f'ld   A,  {clause.value}')
                        self.emit_instr('xor  A,  L')
                    else:
                        self.emit_instr(f'ld   WA, {clause.value}')
                        self.emit_instr('xor  A,  L')
                        self.emit_instr('xor  W,  H')
                        self.emit_instr('or   A,  W')
                    # Z=1 on match → jump to dispatch entry for this case
                    self.emit_instr(f'jrl  Z,  {disp_labels[i]}')
                # No match: discard saved value, jump to default/end
                self.emit_instr('add  XSP, 2  ; no match — discard saved switch value')
                self.emit_instr(f'jp   {label_default}')

                # Phase 2: bodies in source order
                for i, clause in enumerate(explicit):
                    # Dispatch entry: came from Phase 1, saved WA on stack → discard
                    self.emit_label(disp_labels[i])
                    self.emit_instr('add  XSP, 2  ; discard saved switch value (dispatch entry)')
                    # Body entry: reachable from fallthrough (stack already clean)
                    self.emit_label(body_labels[i])
                    for stmt in clause.stmts:
                        self.gen_stmt(stmt)
                    tail = clause.stmts[-1] if clause.stmts else None
                    if not isinstance(tail, (BreakStmt, ContinueStmt, ReturnStmt, GotoStmt)):
                        # C89 fallthrough: jump to NEXT body entry (skip next disp's discard)
                        if i < len(explicit) - 1:
                            self.emit_instr(f'jp   {body_labels[i + 1]}')
                        # else: last case — fall naturally to label_default or label_end

                if defaults:
                    self.emit_label(label_default)
                    for stmt in defaults[0].stmts:
                        self.gen_stmt(stmt)

        self.emit_label(label_end)
        self.loop_break_label = old_break

    def gen_return(self, node: ReturnStmt):
        if node.expr is not None:
            self.gen_expr(node.expr)
            # Result is already in correct return register per ABI:
            #   8-bit  → A,  16-bit → WA,  32-bit → XWA
        if self._func_exit_label is None:
            self.emit_comment('ERROR: return outside function')
            self._emit_epilogue(self._need_frame)
            return
        self.emit_instr(f'jp   {self._func_exit_label}')

    def _stmt_never_falls_through(self, node) -> bool:
        """Return True when a statement cannot reach the following statement."""
        if node is None:
            return False
        if isinstance(node, (ReturnStmt, BreakStmt, ContinueStmt, GotoStmt)):
            return True
        if isinstance(node, Block):
            if not node.stmts:
                return False
            return self._stmt_never_falls_through(node.stmts[-1])
        if isinstance(node, IfStmt):
            return (node.else_ is not None
                    and self._stmt_never_falls_through(node.then)
                    and self._stmt_never_falls_through(node.else_))
        if isinstance(node, LabelStmt):
            return self._stmt_never_falls_through(node.stmt)
        return False

    def _begin_call_elem_cache_preserve(self, extra_exprs=None):
        """Save XBC when a live element cache can safely survive a call."""
        if self._xbc_cached_elem_key is None:
            return False, None, 0
        if extra_exprs is None:
            extra_exprs = []
        for expr in extra_exprs:
            if expr is not None and not self._expr_is_pure(expr):
                return False, None, 0
        saved_key = self._xbc_cached_elem_key
        saved_off = self._xbc_cached_elem_offset
        self.emit_instr('push XBC')
        return True, saved_key, saved_off

    def _end_call_elem_cache_preserve(self, preserve_elem_cache, saved_key, saved_off):
        """Restore XBC/cache state after a call, or invalidate when not preserved."""
        self._xde_cached_ptr_key = None
        if preserve_elem_cache:
            self.emit_instr('pop  XBC')
            self._xbc_cached_elem_key = saved_key
            self._xbc_cached_elem_offset = saved_off
        else:
            self._invalidate_elem_base_cache()

    # -- Expression generation --
    # Result is always left in WA (16-bit) or A (8-bit) or XWA (32-bit)
    # depending on the expression type.

    def gen_expr(self, node) -> Type:
        """Generate code for expression. Returns inferred type."""
        if node is None:
            return VOID

        if isinstance(node, Const):
            return self.gen_const(node)

        if isinstance(node, Ident):
            return self.gen_ident(node)

        if isinstance(node, FuncCall):
            return self.gen_call(node)

        if isinstance(node, IndirectCall):
            return self.gen_indirect_call(node)

        if isinstance(node, BinOp):
            return self.gen_binop(node)

        if isinstance(node, UnaryOp):
            return self.gen_unary(node)

        if isinstance(node, Assign):
            return self.gen_assign(node)

        if isinstance(node, Cast):
            return self.gen_cast(node)

        if isinstance(node, Subscript):
            return self.gen_subscript(node)

        if isinstance(node, Deref):
            return self.gen_deref(node)

        if isinstance(node, AddrOf):
            return self.gen_addrof(node)

        if isinstance(node, FieldAccess):
            return self.gen_field_access(node)

        if isinstance(node, Ternary):
            return self.gen_ternary(node)

        self.emit_comment(f'TODO expr: {type(node).__name__}')
        return U16

    def gen_const(self, node: Const) -> Type:
        v = node.value
        if isinstance(v, str):
            # String literal: create a label, load address
            lab = f'.Lstr_{self.str_counter}'
            self.str_counter += 1
            self.string_consts.append((lab, v))
            self.emit_instr(f'ld   XWA, {lab}')
            return PtrType(U8)
        if v == 0:
            self.emit_instr('ld   WA, 0')
        elif -128 <= v <= 65535:
            self.emit_instr(f'ld   WA, {v}')
        else:
            self.emit_instr(f'ld   XWA, {v}')
            return U32
        return U16

    def gen_ident(self, node: Ident) -> Type:
        name = node.name
        # Static local variable: accessed as global via direct abs16 load (stored in BSS/DATA)
        # Same D1/C1 optimization as global variables (DEVLOG 2026-03-30).
        if name in self.static_local_globals:
            sym = self.static_local_globals[name]
            label = f'_{sym.name}'
            sz = self.type_size(sym.type_)
            if self.opt_perf_lag_6 and not sym.is_far and sz <= 2:
                if sz == 1:
                    # P-5.8 v7.3 byte-narrow load.
                    if not (self._byte_narrow_load and self._opt_c5_byte_narrow):
                        # P4: `ld W, 0` (2 B) — A is set by `ld A, (label)` below.
                        self.emit_instr('ld   W, 0                 ; u8 zero-extend (P4)')
                    self.emit_instr(f'ld   A, ({label})')
                else:
                    self.emit_instr(f'ld   WA, ({label})')
                self._maybe_sign_extend_loaded_scalar(sym.type_)
                return sym.type_
            self.emit_instr('ld   WA, 0')
            self.emit_instr('push WA')
            self.emit_instr(f'ld   WA, {label}')
            self.emit_instr('push WA')
            self.emit_instr('pop  XDE')
            self._emit_load_from_de(sz)
            self._maybe_sign_extend_loaded_scalar(sym.type_)
            return sym.type_
        # Local variable
        if name in self.local_vars:
            sym = self.local_vars[name]
            if isinstance(sym.type_, ArrayType):
                # Array-to-pointer decay: produce frame_base+offset (address of array[0]) → XWA.
                self._emit_stack_sym_addr_to_xwa(sym)
                return PtrType(sym.type_.elem)
            self._load_local(sym)
            return sym.type_
        # Parameter
        if name in self.param_syms:
            sym = self.param_syms[name]
            self._load_param(sym)
            return sym.type_
        # Function used as rvalue (address) — e.g. HW_INT_VBL = isr_vblank.
        # Must be checked BEFORE globals: FuncDecl names are also registered in sem.globals
        # (semantic pass adds them as global symbols for call resolution), so the globals
        # check below would match first and generate a wrong dereference.
        # ld XIY, _label = opcode 0x46, safe per J8-1 bisect (LD R32, imm32 confirmed).
        # push XIY (0x3A) + pop XWA (0x48) are safe register push/pop instructions.
        if name in self.sem.func_decls:
            # XIZ = reg 6 = opcode 0x46 (LD R32,imm32 confirmed safe, J8-1 bisect + used in crt0).
            # MUST NOT use XIY here: XIY = frame pointer (link XIY,0), clobbering it breaks
            # unlk XIY in the epilogue → XSP corruption → crash.
            self._emit_label_addr_to('XWA', f'_{name}', 'function address (32-bit ROM ptr)')
            return PtrType(VoidType())
        # Global variable — direct abs16 load (D1/C1 prefix, hardware safe).
        # D1 = 0x98+0x39 (word load abs16), C1 = 0x88+0x39 (byte load abs16).
        # D1 standalone SAFE (D1..D7 safe, only D0 prefix broken per silicon tests).
        # C1 confirmed from CC900 dessab (DISASM_CROSSCHECK.md: c1 82 6f 27 = ld L,(0x6F82)).
        # Replaces: ld WA,0 / push WA / ld WA,label / push WA / pop XDE / LDW WA,(XDE+0)
        # Savings: sz=2 → 5 instrs saved (6→1); sz=1 → 4 instrs saved (6→2).
        if name in self.sem.globals:
            sym = self.sem.globals[name]
            label = f'_{name}'
            # Array-to-pointer decay: global arrays (f_const ROM or f_data) produce their
            # 32-bit address directly, NOT a dereference. ld XIZ,sym = LD R32,imm32
            # (opcode 0x4E for XIZ = reg 6), confirmed safe hardware (J8-1 bisect).
            if isinstance(sym.type_, ArrayType):
                self._emit_label_addr_to('XWA', label, 'far address of global array (32-bit ROM ptr)')
                return PtrType(sym.type_.elem, far=True)
            sz = self.type_size(sym.type_)
            # Const scalar/struct in f_const (ROM 0x200000+): must use far address load.
            # Near direct ld WA,(_sym) only works for RAM symbols (f_data/f_area).
            if sym.is_far:
                self._emit_label_addr_to('XDE', label, 'far address of const global (ROM)')
                self._xde_field_offset = 0
                self._xiy_sym_pending = None
                self._emit_load_from_de(sz)
                self._maybe_sign_extend_loaded_scalar(sym.type_)
                return sym.type_
            if self.opt_perf_lag_6 and sz <= 2:
                if sz == 1:
                    # P4: `ld W, 0` (2 B) — A is set by `ld A, (label)` below.
                    self.emit_instr('ld   W, 0                 ; u8 zero-extend (P4)')
                    self.emit_instr(f'ld   A, ({label})')
                else:
                    self.emit_instr(f'ld   WA, ({label})')
                self._maybe_sign_extend_loaded_scalar(sym.type_)
                return sym.type_
            self.emit_instr('ld   WA, 0')
            self.emit_instr('push WA')
            self.emit_instr(f'ld   WA, {label}')
            self.emit_instr('push WA')
            self.emit_instr('pop  XDE')
            self._emit_load_from_de(sz)
            self._maybe_sign_extend_loaded_scalar(sym.type_)
            return sym.type_
        # CC900 control-register read intrinsics: __DMACn → LDC WA, DMACn
        # Encoding: [C8+zz+r][0x2F][cr_number]  (0x2F = read direction, vs 0x2E = write)
        # DMAC0=CR0x20, DMAC1=CR0x24, DMAC2=CR0x28, DMAC3=CR0x2C
        # WA → R16 idx=0, zz=0x08 → first byte=0xD0
        _CR_READ_MAP = {
            '__DMAC0': (0xD0, 0x20), '__DMAC1': (0xD0, 0x24),
            '__DMAC2': (0xD0, 0x28), '__DMAC3': (0xD0, 0x2C),
        }
        if name in _CR_READ_MAP:
            prefix, cr_num = _CR_READ_MAP[name]
            self.emit_instr(
                f'db 0x{prefix:02X}, 0x2F, 0x{cr_num:02X}'
                f'  ; LDC WA, {name[2:]}  [CC900 CR-read intrinsic]'
            )
            return U16
        # Unknown: treat as extern global
        self.emit_comment(f'NOTE: undeclared {name!r}, loading as extern u16')
        self.emit_instr('ld   WA, 0')
        self.emit_instr('push WA')
        self.emit_instr(f'ld   WA, _{name}')
        self.emit_instr('push WA')
        self.emit_instr('pop  XDE')
        self._emit_load_from_de(2)
        return U16

    def gen_call(self, node: FuncCall) -> Type:
        name = node.name
        if name == '__indirect__':
            self.emit_comment('ERROR: indirect function call not supported in v1')
            return U16

        # __asm("inline asm string") — emit string directly as assembly
        if name in ('__asm', '__asm__'):
            if node.args and isinstance(node.args[0], Const) and isinstance(node.args[0].value, str):
                # P-5.6.6 LVT: inline asm can do ANYTHING — clear LVT cache
                # before and after to be safe (parser may not recognize all
                # ops the asm string contains).
                self._lvt_reset_all()
                self.emit_instr(self._translate_asm(node.args[0].value))
                self._lvt_reset_all()
            else:
                self.emit_comment(f'{name}: non-string arg (skipped)')
            return VOID

        # Lookup return type
        ret_ty = U16
        if name in self.sem.func_decls:
            ret_ty = self.sem.func_decls[name].ret_type

        # P1 — adecl: first N args go in XWA, XBC, XDE (scalars/ptrs ≤ 4 bytes).
        # Only applies when the callee is internal (local_func_defs) and the
        # global flag is on. Externs keep cdecl (HAL/ASM compat).
        use_adecl = self._func_uses_adecl(name)
        if use_adecl:
            # Caller-side cache invalidation is done AFTER the call (below),
            # not here. During arg evaluation the caches are still valid for
            # the arg expressions themselves.
            preserve_elem_cache, saved_elem_key, saved_elem_off = False, None, 0
            # Determine how many initial args actually fit in regs
            # (same rule as the callee's _assign_param_registers).
            reg_targets = ['XWA', 'XBC', 'XDE']
            n_reg_args = 0
            for arg in node.args:
                if n_reg_args >= len(reg_targets):
                    break
                # Can't pre-know the arg type without evaluating — rely on the
                # callee's declared signature when available, else assume fits.
                if name in self.sem.func_decls:
                    params = self.sem.func_decls[name].params
                    if n_reg_args >= len(params):
                        break
                    if self.stack_size_for(params[n_reg_args][1]) > 4:
                        break
                n_reg_args += 1
            reg_args = node.args[:n_reg_args]
            stack_args = node.args[n_reg_args:]
        else:
            preserve_elem_cache, saved_elem_key, saved_elem_off = self._begin_call_elem_cache_preserve(node.args)
            reg_args = []
            stack_args = list(node.args)

        # Push stack-tail args right-to-left (cdecl for extras, or everything when non-adecl)
        total_pushed = 0
        for arg in reversed(stack_args):
            arg_ty = self.gen_expr(arg)
            sz = self.stack_size_for(arg_ty)
            if sz == 4:
                self.emit_instr('push XWA')
            else:
                self.emit_instr('push WA')
            total_pushed += sz

        # P1 adecl: evaluate reg args. Last-to-first order with push/pop transit
        # so earlier XWA results are not clobbered by later gen_expr's.
        # Layout after loop: XWA=arg0 (live), stack top = arg1, below = arg2.
        if use_adecl and reg_args:
            for i in range(len(reg_args) - 1, 0, -1):
                self.gen_expr(reg_args[i])
                self.emit_instr('push XWA')
            self.gen_expr(reg_args[0])  # arg0 stays in XWA
            for i in range(1, len(reg_args)):
                self.emit_instr(f'pop  {reg_targets[i]}          ; adecl arg{i}')

        # Call
        if self._save_xiz_regbank:
            self.emit_instr('push XIZ')
        if name in self.local_func_defs:
            self.emit_instr(f'calr _{name}')
        else:
            self.emit_instr(f'call _{name}')
        if self._save_xiz_regbank:
            self.emit_instr('pop  XIZ')

        # Caller cleans up stack (only the tail/stack-args portion)
        if total_pushed > 0:
            self.emit_instr(f'add  XSP, {total_pushed}')
        if not use_adecl:
            self._end_call_elem_cache_preserve(preserve_elem_cache, saved_elem_key, saved_elem_off)
        else:
            # adecl call clobbered XBC (arg1) and XDE (arg2) and may have
            # clobbered caller's XDE cached pointer. Invalidate both
            # register-bound caches so subsequent expressions re-materialize
            # their base addresses instead of reusing stale register values.
            # (Bug witnessed on HW 2026-04-20: intermittent sprite swap —
            # explosion vs enemy bullet — from stale XDE cache between calls.)
            self._invalidate_elem_base_cache()
            self._xde_cached_ptr_key = None

        # Return value is already in correct register per ABI:
        #   8-bit → A,  16-bit → WA,  32-bit → XWA  (no copy needed)

        return ret_ty

    def _translate_asm(self, s: str) -> str:
        """Translate Toshiba CC900/AT900-style inline ASM to t900as.py-compatible form.

        Handled forms (from T900_DENSE_REF.md section 21 + 28):
          ldb raN, imm     → db 0xC7,0x30,(0xA8+n)   LD RA3,n  (bank-3 byte reg)
          ldb rwN, imm     → db 0xC7,0x31,(0xA8+n)   LD RW3,n  (bank-3 word reg)
          ld  rwN, imm     → db 0xC7,0x31,(0xA8+n)   idem
          ldf N            → db 0x17,N                LDF N  (switch register file)
          call xix         → db 0xB4,0xE8             CALL T,XIX (indirect via XIX)
          ld xde,(xsp+4)   → db 0xAF,0x04,0x22        LD XDE,(XSP+4) (stack param)
          ld xix,(xix+w)   → db 0xE3,0x03,0xF0        LD XIX,(XIX+W) (table lookup)
        """
        import re

        def _parse_imm(tok: str) -> int:
            tok = tok.rstrip(';').strip()
            return int(tok, 16) if tok.startswith('0x') or tok.startswith('0X') else int(tok, 10)

        sl = s.strip().lower()

        # ldb ra3 / ldb rw3 / ld rw3 — bank-3 register loads (BIOS SWI & vector convention)
        m = re.match(r'^(\s*)(ldb|ld)\s+(ra3|rw3)\s*,\s*(\S+)', s, re.IGNORECASE)
        if m:
            indent, alias = m.group(1), m.group(3).lower()
            try:
                n = _parse_imm(m.group(4))
                reg_byte = 0x30 if alias == 'ra3' else 0x31
                return (f'{indent}db 0xC7, 0x{reg_byte:02X}, 0x{(0xA8+n)&0xFF:02X}'
                        f'  ; {"LD RA3" if alias == "ra3" else "LD RW3"}, {n} (bank-3)')
            except (ValueError, TypeError):
                pass  # fall through — leave as-is if imm not resolvable

        # ldf N — switch register file (LDF N = 0x17, N)
        m = re.match(r'^(\s*)ldf\s+(\S+)', s, re.IGNORECASE)
        if m:
            try:
                n = _parse_imm(m.group(2))
                return f'{m.group(1)}db 0x17, 0x{n:02X}  ; LDF {n} (switch register file)'
            except (ValueError, TypeError):
                pass

        # call xix — indirect call via XIX register (BIOS vector table)
        if re.match(r'^\s*call\s+xix\s*$', s, re.IGNORECASE):
            return s.rstrip() + '  ' + '  ' * 0 + '; ' if False else \
                   re.sub(r'call\s+xix', 'db 0xB4, 0xE8  ; CALL T,XIX', s, flags=re.IGNORECASE)

        # ld xde, (xsp+4) — load XDE from (XSP+4), used for stack parameter in BIOS calls
        if re.match(r'^\s*ld\s+xde\s*,\s*\(xsp\+4\)\s*$', s, re.IGNORECASE):
            indent = re.match(r'^(\s*)', s).group(1)
            return f'{indent}db 0xAF, 0x04, 0x22  ; LD XDE,(XSP+4)'

        # ld xix, (xix+w) — load XIX from BIOS function table at (XIX+W)
        if re.match(r'^\s*ld\s+xix\s*,\s*\(xix\+w\)\s*$', s, re.IGNORECASE):
            indent = re.match(r'^(\s*)', s).group(1)
            return f'{indent}db 0xE3, 0x03, 0xF0  ; LD XIX,(XIX+W) (BIOS table lookup)'

        return s

    def gen_indirect_call(self, node) -> Type:
        """Call through a u32 function pointer stored in a struct field or variable.
        Sequence: push args r-to-l, eval callee->XWA (u32, full ROM addr), push XWA,
        pop XIZ, CALL TT,(XIZ).
        NOTE: XIY is the frame pointer (link XIY,0) — never clobber it here.
        NOTE: no extz XWA — callee is u32 so XWA already holds the full 24-bit ROM addr."""
        preserve_elem_cache, saved_elem_key, saved_elem_off = (
            self._begin_call_elem_cache_preserve([node.callee] + list(node.args))
        )
        # Push args right-to-left (cdecl, same as gen_call)
        total_pushed = 0
        for arg in reversed(node.args):
            arg_ty = self.gen_expr(arg)
            sz = self.stack_size_for(arg_ty)
            if sz == 4:
                self.emit_instr('push XWA')
            else:
                self.emit_instr('push WA')
            total_pushed += sz
        # Evaluate callee into XWA (u32 field → 4-byte load → full ROM address preserved)
        self.gen_expr(node.callee)
        if self._save_xiz_regbank and self._scratch_addr_reg == 'XIZ':
            self.emit_instr('push XIZ')
        self.emit_instr('push XWA')
        self.emit_instr(f'pop  {self._scratch_addr_reg}')
        call_op = 0xB0 + self._stack_base_idx(self._scratch_addr_reg)
        self.emit_instr(
            f'db 0x{call_op:02X}, 0xE8               ; CALL TT,({self._scratch_addr_reg}) â€” indirect call'
        )
        if self._save_xiz_regbank and self._scratch_addr_reg == 'XIZ':
            self.emit_instr('pop  XIZ')
        if total_pushed > 0:
            self.emit_instr(f'add  XSP, {total_pushed}')
        self._end_call_elem_cache_preserve(preserve_elem_cache, saved_elem_key, saved_elem_off)
        return U16
        # Load address into XIZ (scratch reg, not frame pointer), then indirect call
        self.emit_instr('push XWA')
        self.emit_instr('pop  XIZ')
        self.emit_instr('db 0xB6, 0xE8               ; CALL TT,(XIZ) — indirect call')
        # Caller cleanup
        if total_pushed > 0:
            self.emit_instr(f'add  XSP, {total_pushed}')
        self._end_call_elem_cache_preserve(preserve_elem_cache, saved_elem_key, saved_elem_off)
        return U16

    def gen_ternary(self, node: Ternary) -> Type:
        """cond ? then : else_ — result in WA."""
        lbl_else = self.fresh_label('tern_else')
        lbl_end  = self.fresh_label('tern_end')
        # Evaluate condition into WA
        self.gen_expr(node.cond)
        self.emit_instr('or   A,  W')           # Z=1 if WA==0 (false)
        self.emit_instr(f'jrl  Z,  {lbl_else}') # zero → else branch
        # Then branch
        ty = self.gen_expr(node.then)
        self.emit_instr(f'jp   {lbl_end}')
        self.emit_label(lbl_else)
        # Else branch
        ty2 = self.gen_expr(node.else_)
        self.emit_label(lbl_end)
        return ty if ty else (ty2 if ty2 else U16)

    def gen_binop(self, node: BinOp) -> Type:
        op = node.op

        # Logical operators with short-circuit
        if op == '&&':
            return self.gen_logical_and(node)
        if op == '||':
            return self.gen_logical_or(node)

        # Optimization: multiply by compile-time power-of-2 constant → doublings
        # Avoids mul XHL,(XSP+0) opcode (0x9F,0x00,0x43) and sll N,r opcode
        # (0xE8,0xEE,N): both are not reliably emulated by BizHawk.
        # Uses repeated "WA = WA + WA" via safe add A,L / adc W,H byte-pair ops.
        if op == '*':
            pow2_val = None
            var_node = None
            for (cnode, other) in [(node.right, node.left), (node.left, node.right)]:
                if isinstance(cnode, Const) and isinstance(cnode.value, int) and cnode.value > 0:
                    v = cnode.value
                    if (v & (v - 1)) == 0:   # power of 2
                        pow2_val = v
                        var_node = other
                        break
            if pow2_val is not None:
                shift = (pow2_val).bit_length() - 1   # log2(pow2_val)
                var_ty = self.gen_expr(var_node)
                if shift == 0:
                    return var_ty   # x * 1 = x, no-op
                # Zero-extend var to 16-bit (W may be garbage after LDB for u8).
                # Pattern: push WA, ld WA,0, pop HL, add A,L [,adc W,H for u16]
                self.emit_instr('push WA')
                self.emit_instr('ld   WA, 0')
                self.emit_instr('pop  HL')
                self.emit_instr('add  A,  L')
                if self.type_size(var_ty) > 1:
                    self.emit_instr('adc  W,  H')   # preserve u16 high byte
                # WA = zero-extended var value. Now double shift times: WA *= pow2_val.
                # Each doubling: push WA / pop HL / add A,L / adc W,H (100% safe).
                self.emit_comment(f'multiply WA by {pow2_val} via {shift} doublings')
                for _ in range(shift):
                    self.emit_instr('push WA')
                    self.emit_instr('pop  HL')
                    self.emit_instr('add  A,  L')
                    self.emit_instr('adc  W,  H')
                return U16

        # Optimization and correctness: signed divide by a positive power-of-two.
        # C truncates signed division toward zero, so a raw arithmetic shift is wrong
        # for negative values. Use bias + arithmetic shift for 8/16-bit signed ints.
        if op == '/':
            if isinstance(node.right, Const) and isinstance(node.right.value, int):
                div_val = int(node.right.value)
                if div_val > 0 and (div_val & (div_val - 1)) == 0:
                    left_decl_ty = self.typeof_expr(node.left)
                    if isinstance(left_decl_ty, IntType) and left_decl_ty.signed:
                        left_ty = self.gen_expr(node.left)
                        if self.type_size(left_ty) <= 2:
                            shift = div_val.bit_length() - 1
                            self._emit_signed_div_pow2_u16(shift)
                            return left_ty

        # Optimization: shift by compile-time constant -> sll/srl N, r (immediate form).
        # Hardware rules (confirmed silicon):
        #   sz=1 (A)  : sll/srl N, A   → C9 EE/EF N  (C8..CF range, safe)
        #   sz=2 (WA) : sll/srl N, WA  → D0 EE/EF N  (D0..D7 range, BROKEN on NGPC silicon)
        #               Fix: extz XWA (E8 12) then sll/srl N, XWA → D8 EE/EF N (safe)
        #   sz=4 (XWA): sll/srl N, XWA → D8 EE/EF N  (D8..DF range, safe)
        if op in ('<<', '>>'):
            if isinstance(node.right, Const) and isinstance(node.right.value, int):
                count = int(node.right.value)
                if 0 < count < 32:
                    left_ty = self.gen_expr(node.left)
                    actual_sz = self.type_size(left_ty) if left_ty else 2
                    signed = isinstance(left_ty, IntType) and left_ty.signed if left_ty else False
                    mnem = 'sll' if op == '<<' else ('sra' if signed else 'srl')
                    if actual_sz == 1:
                        reg = 'A'
                    elif actual_sz == 2:
                        # WA uses D0 prefix (BROKEN). Extend to XWA (D8 prefix, safe).
                        if op == '>>' and signed:
                            self.emit_instr('db 0xE8, 0x13  ; exts XWA (sign-extend WA→XWA)')
                        else:
                            self.emit_instr('db 0xE8, 0x12  ; extz XWA (zero-extend WA→XWA)')
                        reg = 'XWA'
                    else:  # sz == 4
                        reg = 'XWA'
                    remaining = count
                    while remaining > 0:
                        n = min(remaining, 8)
                        self.emit_instr(f'{mnem} {n}, {reg}')
                        remaining -= n
                    return left_ty or U8

        imm_rhs = self._small_const_u16(node.right)
        # Peek size BEFORE emitting left: this fast path only applies to sz<=2.
        # Without the peek, we'd emit left then fall through to the generic
        # path which re-emits left → duplicate LDW for every 32-bit `x op imm`
        # site (same class of bug as gen_unary's pre-load before gen_inc_dec).
        if (imm_rhs is not None
                and op in ('+', '-', '&', '|', '^', '==', '!=', '<', '<=', '>', '>=')
                and max(self.type_size(self.typeof_expr(node.left)) if self.typeof_expr(node.left) else 2, 2) <= 2):
            left_ty = self.gen_expr(node.left)
            sz = max(self.type_size(left_ty) if left_ty else 2, 2)
            if sz <= 2:
                self.emit_instr(f'ld   HL, {imm_rhs}')
                result_ty = left_ty
                if op == '+':
                    self._emit_alu16('+')
                elif op == '-':
                    self._emit_alu16('-')
                elif op == '&':
                    self._emit_alu16('&')
                elif op == '|':
                    self._emit_alu16('|')
                elif op == '^':
                    self._emit_alu16('^')
                else:
                    label_true = self.fresh_label('cmp_t')
                    label_end  = self.fresh_label('cmp_e')
                    signed_cmp = self._cmp_is_signed(left_ty, self.typeof_expr(node.right))
                    if signed_cmp and op in ('<', '<=', '>', '>='):
                        self._emit_cmp16_signed_branch(op, label_true)
                    else:
                        self._emit_alu16(op)
                        cond = self._CMP16_CC[op]
                        self.emit_instr(f'jrl  {cond}, {label_true}')
                    self.emit_instr('ld   WA, 0')
                    self.emit_instr(f'jp   {label_end}')
                    self.emit_label(label_true)
                    self.emit_instr('ld   WA, 1')
                    self.emit_label(label_end)
                    result_ty = U16
                return result_ty or U16

        # Fast path: right-first evaluation when left is HL-safe.
        # Saves 2 stack ops vs generic path (1 push+pop vs 2 push+2 pop).
        # Strategy: gen right→WA, copy WA→HL (1 push+pop), gen left→WA.
        # WA=left / HL=right layout is identical to the generic path → _emit_alu16 unchanged.
        # Constraint: left must not clobber HL (_is_hl_safe_expr). Pointer arithmetic and
        # sz=4 ops are excluded (scaling or 32-bit ops need the generic path).
        # P-5.7 (pass 30) : try `_eval_expr_into_hl(node.right)` FIRST.
        # If RHS is simple (Const, local Ident u16, global near u16),
        # load HL directly without transit (saves 2 B per site).
        if (self.opt_perf_lag_1
                and op in ('+', '-', '&', '|', '^', '==', '!=', '<', '<=', '>', '>=')
                and imm_rhs is None
                and self._is_hl_safe_expr(node.left)):
            left_peekty = self.typeof_expr(node.left)
            sz_peek = max(self.type_size(left_peekty) if left_peekty else 2, 2)
            if sz_peek <= 2 and not isinstance(left_peekty, PtrType):
                # P-5.7 (pass 30) — try direct-to-HL emission for RHS
                right_ty = self._eval_expr_into_hl(node.right)
                if right_ty is None:
                    # Fall back to transit (gen WA → copy → HL)
                    right_ty = self.gen_expr(node.right)    # WA = right
                    self._emit_copy_wa_to_hl()              # HL = right
                left_ty  = self.gen_expr(node.left)     # WA = left   (HL-safe: HL preserved)
                if op in ('+', '-', '&', '|', '^'):
                    self._emit_alu16(op)                # WA op= HL
                    return left_ty if left_ty is not None else right_ty or U16
                label_true = self.fresh_label('cmp_t')
                label_end  = self.fresh_label('cmp_e')
                signed_cmp = self._cmp_is_signed(left_ty, right_ty)
                if signed_cmp and op in ('<', '<=', '>', '>='):
                    self._emit_cmp16_signed_branch(op, label_true)
                else:
                    self._emit_alu16(op)
                    cond = self._CMP16_CC[op]
                    self.emit_instr(f'jrl  {cond}, {label_true}')
                self.emit_instr('ld   WA, 0')
                self.emit_instr(f'jp   {label_end}')
                self.emit_label(label_true)
                self.emit_instr('ld   WA, 1')
                self.emit_label(label_end)
                return U16

        # P-5.7 (pass 30) — try direct-to-HL for RHS in generic path.
        # If RHS is a Const / Ident u16 local / Ident u16 near global,
        # we can emit HL = right WITHOUT clobbering WA, eliminating
        # the entire push/pop scaffolding (save 4 B per site).
        # Restrictions : left peek must not be ptr, sz must be 2,
        # op must be byte-split ALU (no mul/div/shift).
        if (op in ('+', '-', '&', '|', '^', '==', '!=', '<', '<=', '>', '>=')
                and imm_rhs is None
                and self._can_eval_expr_into_hl(node.right)):
            left_peekty = self.typeof_expr(node.left)
            sz_peek = max(self.type_size(left_peekty) if left_peekty else 2, 2)
            right_peekty = self.typeof_expr(node.right)
            if (sz_peek <= 2
                    and not isinstance(left_peekty, PtrType)
                    and right_peekty is not None
                    and not isinstance(right_peekty, PtrType)):
                # OPTIMIZED PATH : gen LHS → WA, then RHS direct → HL,
                # then alu. No push/pop, no transit. Saves 4 B/site.
                left_ty = self.gen_expr(node.left)
                right_ty = self._eval_expr_into_hl(node.right)
                # _eval should succeed since _can_eval was True
                result_ty = left_ty if left_ty is not None else right_ty
                if op in ('+', '-', '&', '|', '^'):
                    self._emit_alu16(op)
                    return result_ty or U16
                # Comparison path
                label_true = self.fresh_label('cmp_t')
                label_end  = self.fresh_label('cmp_e')
                signed_cmp = self._cmp_is_signed(left_ty, right_ty)
                if signed_cmp and op in ('<', '<=', '>', '>='):
                    self._emit_cmp16_signed_branch(op, label_true)
                else:
                    self._emit_alu16(op)
                    cond = self._CMP16_CC[op]
                    self.emit_instr(f'jrl  {cond}, {label_true}')
                self.emit_instr('ld   WA, 0')
                self.emit_instr(f'jp   {label_end}')
                self.emit_label(label_true)
                self.emit_instr('ld   WA, 1')
                self.emit_label(label_end)
                return U16

        # Evaluate left into WA, push, evaluate right into WA, move right to HL
        left_ty  = self.gen_expr(node.left)
        sz = max(self.type_size(left_ty), 2)
        if sz == 4:
            self.emit_instr('push XWA')
        else:
            self.emit_instr('push WA')

        right_ty = self.gen_expr(node.right)
        # Pointer arithmetic scaling: ptr+N / ptr-N must scale N by sizeof(*ptr).
        # e.g. u16 *p; p + 3  →  address += 3*2 = 6  (not 3).
        # Right operand is in WA. Scale WA before moving to HL.
        # Only for + and - where left is a pointer; ptr-ptr difference is not handled.
        if op in ('+', '-') and isinstance(left_ty, PtrType) and left_ty.base:
            esz = max(self.type_size(left_ty.base), 1)
            if esz == 2:
                # WA *= 2: safe doubling (push/pop/add A,L/adc W,H)
                self.emit_instr('push WA')
                self.emit_instr('pop  HL')
                self.emit_instr('add  A, L           ; ptr scale ×2 for u16*')
                self.emit_instr('adc  W, H')
            elif esz == 4:
                # WA *= 4: two doublings
                for _ in range(2):
                    self.emit_instr('push WA')
                    self.emit_instr('pop  HL')
                    self.emit_instr('add  A, L           ; ptr scale ×4 for u32*')
                    self.emit_instr('adc  W, H')
            elif esz > 4:
                # General: multiply WA by esz using safe mem-form mul
                self.emit_instr(f'ld   HL, {esz}')
                self.emit_instr('push WA')
                self.emit_instr('db 0x9F,0x00,0x43  ; mul XHL,(XSP+0) — ptr scale ×esz')
                self.emit_instr('add  XSP, 2')
                self.emit_instr('push HL')
                self.emit_instr('pop  WA')
        # Move right result to HL secondary register
        if sz == 4:
            self._emit_copy_xwa_to_xhl()   # push XWA; pop XHL (long r+r LD broken too)
        else:
            self._emit_copy_wa_to_hl()     # push WA; pop HL (word r+r LD broken)

        # Pop left back into WA
        if sz == 4:
            self.emit_instr('pop  XWA')
        else:
            self.emit_instr('pop  WA')

        result_ty = left_ty if left_ty is not None else right_ty

        # Emit operation
        if op == '+':
            if sz == 4:
                if self._opt_c5_alu32:
                    # Phase 5: native full-32-bit add (E8..EF enc, HW-safe via t900as).
                    self.emit_instr('add  XWA, XHL       ; full 32-bit (Phase5 alu32)')
                else:
                    # Legacy: byte-split touches LOW 16 ONLY → wrong above 64 KB.
                    self.emit_instr('add  A, L           ; low 16: A += XHL.low (CF safe)')
                    self.emit_instr('adc  W, H           ; high 16: W += XHL.high + carry (CE safe)')
            else:
                self._emit_alu16('+')
        elif op == '-':
            if sz == 4:
                if self._opt_c5_alu32:
                    self.emit_instr('sub  XWA, XHL       ; full 32-bit (Phase5 alu32)')
                else:
                    # Legacy: byte-split touches LOW 16 ONLY → wrong above 64 KB.
                    self.emit_instr('sub  A, L           ; low 16: A -= XHL.low (CF safe)')
                    self.emit_instr('sbc  W, H           ; high 16: W -= XHL.high - borrow (CE safe)')
            else:
                self._emit_alu16('-')
        elif op == '*':
            if sz == 4:
                # 32-bit mul: safe 16×16→32 using db 0x9F,0x00,0x43 form.
                # MUL WA,HL (D3:43) uses D0-prefix which is broken silicon — must avoid.
                # Use lower 16 bits of each operand; full 32-bit result goes to XWA.
                self.emit_instr('push WA')
                self.emit_instr('db 0x9F,0x00,0x43  ; mul XHL,(XSP+0) — WA.lo*HL.lo->XHL')
                self.emit_instr('add  XSP, 2')
                self.emit_instr('push XHL')
                self.emit_instr('pop  XWA')
            else:
                # mul XHL,(XSP+0): HL * (XSP+0) → XHL.  WA=left, HL=right.
                # Push left onto stack so it's at (XSP+0), then mul, extract HL→WA.
                self.emit_instr('push WA')
                self.emit_instr('db 0x9F,0x00,0x43  ; mul XHL,(XSP+0) — u16*HL->XHL')
                self.emit_instr('add  XSP, 2')
                self.emit_instr('push HL')
                self.emit_instr('pop  WA')
        elif op == '/':
            if sz == 4:
                # 32-bit div: safe 16-bit form. DIV XWA,HL (D3:53) uses D0-prefix → broken.
                self.emit_instr('push HL')
                self._emit_copy_wa_to_hl()
                self.emit_instr('db 0xEB,0x12          ; extz XHL')
                self.emit_instr('db 0x9F,0x00,0x53     ; div XHL,(XSP+0) — XHL/u16->HL')
                self.emit_instr('add  XSP, 2')
                self.emit_instr('push HL')
                self.emit_instr('pop  WA')
                self.emit_instr('db 0xE8, 0x12         ; extz XWA')
            else:
                # div XHL,(XSP+0): XHL/divisor → quotient in HL.  WA=dividend, HL=divisor.
                self.emit_instr('push HL')             # (XSP+0) = divisor
                self._emit_copy_wa_to_hl()             # HL = dividend (push WA; pop HL)
                self.emit_instr('db 0xEB,0x12          ; extz XHL — HL->XHL zero-extend')
                self.emit_instr('db 0x9F,0x00,0x53     ; div XHL,(XSP+0) — XHL/u16->HL')
                self.emit_instr('add  XSP, 2')          # cleanup divisor
                self.emit_instr('push HL')              # push quotient
                self.emit_instr('pop  WA')              # WA = quotient
        elif op == '%':
            if sz == 4:
                # 32-bit mod: safe 16-bit form, extract remainder.
                self.emit_instr('push HL')
                self._emit_copy_wa_to_hl()
                self.emit_instr('db 0xEB,0x12          ; extz XHL')
                self.emit_instr('db 0x9F,0x00,0x53     ; div XHL,(XSP+0)')
                self.emit_instr('add  XSP, 2')
                self.emit_instr('push XHL')
                self.emit_instr('add  XSP, 2')
                self.emit_instr('pop  WA')
                self.emit_instr('db 0xE8, 0x12         ; extz XWA')
            else:
                # Same div as /, but extract remainder (upper 16 of XHL) via push XHL trick.
                # After div: quotient=HL, remainder=upper 16 of XHL (bits 16-31).
                # push XHL (4 bytes): low2=quotient, high2=remainder. Skip low2, pop WA=remainder.
                self.emit_instr('push HL')             # (XSP+0) = divisor
                self._emit_copy_wa_to_hl()             # HL = dividend
                self.emit_instr('db 0xEB,0x12          ; extz XHL')
                self.emit_instr('db 0x9F,0x00,0x53     ; div XHL,(XSP+0)')
                self.emit_instr('add  XSP, 2')          # cleanup divisor
                self.emit_instr('push XHL')             # 4 bytes: quotient(low), remainder(high)
                self.emit_instr('add  XSP, 2')          # skip quotient (low 2 bytes)
                self.emit_instr('pop  WA')              # WA = remainder
        elif op == '&':
            if sz == 4:
                if self._opt_c5_alu32:
                    self.emit_instr('and  XWA, XHL       ; full 32-bit (Phase5 alu32)')
                else:
                    # Legacy: ANDs low 16 then ZEROES high 16 (extz) — wrong for
                    # true 32-bit AND (should AND the high halves, not clear them).
                    self.emit_instr('and  A, L           ; low 16: A &= XHL.low (CF safe)')
                    self.emit_instr('and  W, H           ; high 16: W &= XHL.high (CE safe)')
                    self.emit_instr('db 0xE8, 0x12       ; extz XWA — zero high 16 (E8 safe)')
            else:
                self._emit_alu16('&')
        elif op == '|':
            if sz == 4:
                if self._opt_c5_alu32:
                    self.emit_instr('or   XWA, XHL       ; full 32-bit (Phase5 alu32)')
                else:
                    # Legacy: byte-split touches LOW 16 ONLY → high 16 left stale.
                    self.emit_instr('or   A, L           ; low 16: A |= XHL.low (CF safe)')
                    self.emit_instr('or   W, H           ; high 16: W |= XHL.high (CE safe)')
            else:
                self._emit_alu16('|')
        elif op == '^':
            if sz == 4:
                if self._opt_c5_alu32:
                    self.emit_instr('xor  XWA, XHL       ; full 32-bit (Phase5 alu32)')
                else:
                    # Legacy: byte-split touches LOW 16 ONLY → high 16 left stale.
                    self.emit_instr('xor  A, L           ; low 16: A ^= XHL.low (CF safe)')
                    self.emit_instr('xor  W, H           ; high 16: W ^= XHL.high (CE safe)')
            else:
                self._emit_alu16('^')
        elif op == '<<':
            # Runtime shift: HL = count (L = low byte). WA/XWA = value.
            # sll/srl N, WA (D0 prefix) BROKEN. sla WA, B wrong syntax.
            # Safe pattern: count→A via ld A,L (CF 89, safe), value→XDE via push/pop,
            # shift XDE with sll A,XDE (DA FE, safe), copy XDE back to XWA.
            if sz == 4:
                self.emit_instr('push XWA')
                self.emit_instr('pop  XDE            ; XDE = value (safe push/pop)')
                self.emit_instr('ld   A, L           ; A = count low byte (CF 89 safe)')
                self.emit_instr('sll  A, XDE         ; XDE <<= count (DA FE safe)')
                self.emit_instr('push XDE')
                self.emit_instr('pop  XWA            ; XWA = result')
            elif sz == 2:
                self.emit_instr('db 0xE8, 0x12       ; extz XWA (WA→XWA zero-extend)')
                self.emit_instr('push XWA')
                self.emit_instr('pop  XDE            ; XDE = 0x0000_value')
                self.emit_instr('ld   A, L           ; A = count low byte (CF 89 safe)')
                # Zero-guard: sll A,XDE with A=0 is broken on NGPC silicon (gives XDE=0).
                # or A,L: after ld A,L both A and L hold count → A|L=count, Z set if 0.
                # Does NOT involve W (which holds value high byte) — no corruption risk.
                lbl_lsh_z = self.fresh_label('lsh_z')
                self.emit_instr('or   A, L           ; Z set if count==0; A unchanged (CF E1 safe)')
                self.emit_instr(f'jrl  Z, {lbl_lsh_z} ; skip sll if count==0')
                self.emit_instr('sll  A, XDE         ; XDE <<= count (DA FE safe)')
                self.emit_label(lbl_lsh_z)
                self.emit_instr('push XDE')
                self.emit_instr('pop  XWA            ; XWA = result, WA = lower 16 bits')
            else:  # sz == 1, value in A (W=0)
                self.emit_instr('ld   D, A           ; D = value (C9 8C safe)')
                self.emit_instr('ld   A, L           ; A = count (CF 89 safe)')
                self.emit_instr('sll  A, D           ; D <<= count (CC FE safe)')
                self.emit_instr('ld   A, D           ; A = result (CC 89 safe)')
        elif op == '>>':
            # Same pattern as << but srl/sra instead of sll.
            ty = left_ty or U16
            signed = isinstance(ty, IntType) and ty.signed
            mnem = 'sra' if signed else 'srl'
            if sz == 4:
                self.emit_instr('push XWA')
                self.emit_instr('pop  XDE            ; XDE = value')
                self.emit_instr('ld   A, L           ; A = count (CF 89 safe)')
                self.emit_instr(f'{mnem}  A, XDE     ; XDE >>= count (DA FD/FF safe)')
                self.emit_instr('push XDE')
                self.emit_instr('pop  XWA')
            elif sz == 2:
                if signed:
                    self.emit_instr('db 0xE8, 0x13   ; exts XWA (sign-extend WA→XWA)')
                else:
                    self.emit_instr('db 0xE8, 0x12   ; extz XWA (zero-extend WA→XWA)')
                self.emit_instr('push XWA')
                self.emit_instr('pop  XDE            ; XDE = value')
                self.emit_instr('ld   A, L           ; A = count (CF 89 safe)')
                # Zero-guard: srl A,XDE with A=0 is broken on NGPC silicon (gives XDE=0).
                # or A,L: after ld A,L both A and L hold count → A|L=count, Z set if 0.
                # Does NOT involve W (which holds value high byte) — no corruption risk.
                lbl_rsh_z = self.fresh_label('rsh_z')
                self.emit_instr('or   A, L           ; Z set if count==0; A unchanged (CF E1 safe)')
                self.emit_instr(f'jrl  Z, {lbl_rsh_z} ; skip srl if count==0')
                self.emit_instr(f'{mnem}  A, XDE     ; XDE >>= count (DA FD/FF safe)')
                self.emit_label(lbl_rsh_z)
                self.emit_instr('push XDE')
                self.emit_instr('pop  XWA')
            else:  # sz == 1
                self.emit_instr('ld   D, A           ; D = value (C9 8C safe)')
                self.emit_instr('ld   A, L           ; A = count (CF 89 safe)')
                self.emit_instr(f'{mnem}  A, D       ; D >>= count (CC FD/FF safe)')
                self.emit_instr('ld   A, D           ; A = result (CC 89 safe)')
        elif op in ('==', '!=', '<', '<=', '>', '>='):
            # Compare and set WA to 0 or 1
            label_true = self.fresh_label('cmp_t')
            label_end  = self.fresh_label('cmp_e')
            signed_cmp = self._cmp_is_signed(left_ty, right_ty)
            if sz == 4:
                self.emit_instr('cp   XWA, XHL')
                cond = self._cmp_to_cc(op, signed=signed_cmp)
                self.emit_instr(f'jrl  {cond}, {label_true}')
            else:
                if signed_cmp and op in ('<', '<=', '>', '>='):
                    self._emit_cmp16_signed_branch(op, label_true)
                else:
                    self._emit_alu16(op)            # byte-split sets flags
                    cond = self._CMP16_CC[op]
                    self.emit_instr(f'jrl  {cond}, {label_true}')
            self.emit_instr('ld   WA, 0')
            self.emit_instr(f'jp   {label_end}')
            self.emit_label(label_true)
            self.emit_instr('ld   WA, 1')
            self.emit_label(label_end)
            result_ty = U16
        else:
            self.emit_comment(f'TODO binop: {op}')

        return result_ty or U16

    def gen_logical_and(self, node: BinOp) -> Type:
        label_false = self.fresh_label('and_f')
        label_end   = self.fresh_label('and_e')
        self.gen_expr(node.left)
        self.emit_instr('or   A,  W')
        self.emit_instr(f'jrl  Z,{label_false}')
        self.gen_expr(node.right)
        self.emit_instr('or   A,  W')
        self.emit_instr(f'jrl  Z,{label_false}')
        self.emit_instr('ld   WA, 1')
        self.emit_instr(f'jp   {label_end}')
        self.emit_label(label_false)
        self.emit_instr('ld   WA, 0')
        self.emit_label(label_end)
        return U16

    def gen_logical_or(self, node: BinOp) -> Type:
        label_true = self.fresh_label('or_t')
        label_end  = self.fresh_label('or_e')
        self.gen_expr(node.left)
        self.emit_instr('or   A,  W')
        self.emit_instr(f'jrl  NZ,{label_true}')
        self.gen_expr(node.right)
        self.emit_instr('or   A,  W')
        self.emit_instr(f'jrl  NZ,{label_true}')
        self.emit_instr('ld   WA, 0')
        self.emit_instr(f'jp   {label_end}')
        self.emit_label(label_true)
        self.emit_instr('ld   WA, 1')
        self.emit_label(label_end)
        return U16

    def _cmp_to_cc(self, op: str, signed: bool) -> str:
        if signed:
            table = {'==': 'Z', '!=': 'NZ', '<': 'LT', '<=': 'LE', '>': 'GT', '>=': 'GE'}
        else:
            table = {'==': 'Z', '!=': 'NZ', '<': 'C', '<=': 'ULE', '>': 'UGT', '>=': 'NC'}
        return table.get(op, 'Z')

    def _cmp_is_signed(self, left_ty: Optional[Type], right_ty: Optional[Type]) -> bool:
        """Signed branches only apply to integer compares; pointers stay unsigned."""
        if isinstance(left_ty, PtrType) or isinstance(right_ty, PtrType):
            return False
        return ((isinstance(left_ty, IntType) and left_ty.signed) or
                (isinstance(right_ty, IntType) and right_ty.signed))

    def typeof_expr(self, node) -> Type:
        """Return the type of an expression without emitting any code.
        Used by sizeof(expr) to get the compile-time size.
        Falls back to U16 (size=2) if type cannot be determined statically."""
        if isinstance(node, Const):
            return node.type_ if node.type_ is not None else U16
        if isinstance(node, Ident):
            name = node.name
            if name in self.static_local_globals:
                return self.static_local_globals[name].type_
            if name in self.local_vars:
                return self.local_vars[name].type_
            if name in self.param_syms:
                return self.param_syms[name].type_
            if name in self.sem.globals:
                return self.sem.globals[name].type_
            return U16
        if isinstance(node, Cast):
            return node.type_
        if isinstance(node, Deref):
            base_ty = self.typeof_expr(node.expr)
            if isinstance(base_ty, PtrType):
                return base_ty.base
            if isinstance(base_ty, ArrayType):
                return base_ty.elem
            return U16
        if isinstance(node, Subscript):
            base_ty = self.typeof_expr(node.base)
            if isinstance(base_ty, PtrType):
                return base_ty.base
            if isinstance(base_ty, ArrayType):
                return base_ty.elem
            return U16
        if isinstance(node, FieldAccess):
            if node.type_ is not None:
                return node.type_
            return U16
        return U16

    def gen_unary(self, node: UnaryOp) -> Type:
        op = node.op
        if op == 'sizeof':
            ty = self.typeof_expr(node.expr)
            sz = self.type_size(ty)
            self.emit_instr(f'ld   WA, {sz}')
            return U16
        # Pre/post inc/dec own their load — gen_inc_dec re-emits gen_lvalue_addr +
        # _emit_load_from_de internally. Pre-loading here just to obtain the type
        # produces a redundant LDW right before the loop body load (e.g. while(len--)).
        if op in ('pre++', 'pre--', 'post++', 'post--'):
            delta = 1 if op in ('pre++', 'post++') else -1
            post = op.startswith('post')
            return self.gen_inc_dec(node.expr, delta=delta, post=post)
        # P-5.7 (pass 30+) — direct-to-HL optim for u16 unary - / ~.
        # If expr is simple (Const / Ident u16 local/global), we can
        # load HL directly without the push/pop transit (saves 2 B/site).
        # Audit pré : 24 `~` sites + 202 `-` sites = ~450 B potential.
        ty = None  # type, set below
        sz = 2
        if op in ('-', '~') and self._can_eval_expr_into_hl(node.expr):
            ty_peek = self.typeof_expr(node.expr)
            sz_peek = self.type_size(ty_peek) if ty_peek else 2
            if sz_peek <= 2 and not isinstance(ty_peek, PtrType):
                # Direct path : HL = value, then WA = const, then alu
                ty = self._eval_expr_into_hl(node.expr)
                if op == '-':
                    self.emit_instr('ld   WA, 0')
                    self.emit_instr('sub  A,  L')       # CF A1 — safe
                    self.emit_instr('sbc  W,  H')       # CE B0 — safe
                else:  # ~
                    self.emit_instr('ld   WA, 65535')
                    self.emit_instr('xor  A,  L')
                    self.emit_instr('xor  W,  H')
                return ty or U16
        ty = self.gen_expr(node.expr)
        sz = self.type_size(ty) if ty else 2
        if op == '-':
            if sz == 4:
                self.emit_instr('neg  XWA')
            else:
                # neg WA = D0 07 — BROKEN (D0..D7 family, NGPC silicon bug)
                # Safe replacement: 0 - WA using CF/CE byte-level ops
                self.emit_instr('push WA')
                self.emit_instr('ld   WA, 0')
                self.emit_instr('pop  HL')
                self.emit_instr('sub  A,  L')       # CF A1 — safe
                self.emit_instr('sbc  W,  H')       # CE B0 — safe
        elif op == '~':
            if sz == 4:
                self.emit_instr('cpl  XWA')         # E8 06 — safe (E8+r family)
            else:
                # cpl WA = D0 06 — BROKEN (D0..D7 family, NGPC silicon bug)
                # Safe replacement: WA = WA ^ 0xFFFF using CF/CE byte-level ops
                self.emit_instr('push WA')
                self.emit_instr('ld   WA, 65535')   # WA = 0xFFFF
                self.emit_instr('pop  HL')
                self.emit_instr('xor  A,  L')       # CF D1 — safe
                self.emit_instr('xor  W,  H')       # CE D0 — safe
        elif op == '!':
            label_zero = self.fresh_label('not_z')
            label_end  = self.fresh_label('not_e')
            self.emit_instr('or   A,  W')
            self.emit_instr(f'jrl  Z,{label_zero}')
            self.emit_instr('ld   WA, 0')
            self.emit_instr(f'jp   {label_end}')
            self.emit_label(label_zero)
            self.emit_instr('ld   WA, 1')
            self.emit_label(label_end)
            return U16
        else:
            self.emit_comment(f'TODO unary: {op}')
        return ty or U16

    def gen_inc_dec(self, target, delta: int, post: bool) -> Type:
        """Generate pre/post increment/decrement. Result in WA."""
        if self._lvalue_writes_cached_index(target):
            self._invalidate_elem_base_cache()
        # P-5.8 v7 Axe A (pass 37) : mem-form INC/DEC fast path.
        # When :
        #   - target is a simple XIY-rel u16 local/param (frame slot)
        #   - delta is in -8..-1 or 1..8 (encodable in INC #n / DEC #n)
        #   - post=False (result value unused — caller is gen_stmt's
        #     ExprStmt(UnaryOp post++/post--) special-case dispatch)
        # we emit `INCW n, (XIY+d)` (3 B = 0x9D d8 0x60+n) or DECW
        # (sub-op 0x68+n) directly to memory. Saves ~10 B/site vs the
        # legacy load+alu+store byte-split sequence.
        # Encoding source : ngdis/tlcs900_zz_mem.c (sub-op 0x60+n INC,
        # 0x68+n DEC ; n = (sub-op & 7), n=0 encodes 8).
        # HW status : NOT in quirks_db v4 → presumed safe (encoding
        # used in commercial NGPC ROMs per opcode-coverage analysis),
        # but UNCONFIRMED on our own HW. Gate behind env var so user
        # can roll back if HW fails.
        if (not post and isinstance(target, Ident)
                and self._opt_c5_memform_alu):
            sym = (self.local_vars.get(target.name)
                   or self.param_syms.get(target.name))
            if (sym is not None
                    and not sym.reg_name
                    and not sym.adecl_live_reg
                    and not isinstance(sym.type_, ArrayType)
                    and self.type_size(sym.type_) in (1, 2)
                    and self._stack_base_reg(sym) == 'XIY'
                    and -128 <= sym.offset <= 127
                    and -8 <= delta <= 8 and delta != 0):
                sz = self.type_size(sym.type_)
                # First-byte prefix : 0x8D byte / 0x9D word (ARID XIY).
                prefix = 0x8D if sz == 1 else 0x9D
                d = sym.offset & 0xFF
                if delta > 0:
                    n_encoded = delta if delta < 8 else 0
                    sub_op = 0x60 + n_encoded
                    mnem_op = 'INC' if sz == 1 else 'INCW'
                    mnem = f'{mnem_op} {delta}, (XIY{sym.offset:+d})'
                else:
                    mag = -delta
                    n_encoded = mag if mag < 8 else 0
                    sub_op = 0x68 + n_encoded
                    mnem_op = 'DEC' if sz == 1 else 'DECW'
                    mnem = f'{mnem_op} {mag}, (XIY{sym.offset:+d})'
                self.emit_instr(
                    f'db 0x{prefix:02X}, 0x{d:02X}, 0x{sub_op:02X}'
                    f'  ; {mnem}  [v7 Axe A mem-form INC/DEC]'
                )
                # Mem-form ALU writes directly to the cell — invalidate
                # any LVT cache pointing at (XIY+d).
                if sz == 2:
                    self._lvt_invalidate_word_write('D', sym.offset)
                else:
                    self._lvt_invalidate_byte_write('D', sym.offset)
                # The caller (gen_stmt's ExprStmt(UnaryOp) dispatch
                # with post=False) discards the return value.
                return sym.type_
        direct_abs = self._direct_abs_int_symbol(target)
        if direct_abs is not None:
            direct_sym, direct_label = direct_abs
            ty = direct_sym.type_
            sz = self.type_size(ty)
            if self.opt_perf_lag_8 and not post and delta in (1, -1):
                self._emit_direct_abs_mem_inc_dec(direct_label, sz, delta)
                self.gen_ident(target)
                return ty
            self.gen_ident(target)
            if post:
                self.emit_instr('push WA')
            uval = delta & 0xFFFF
            self.emit_instr(f'ld   HL, {uval}')
            self.emit_instr('add  A,  L')
            self.emit_instr('adc  W,  H')
            self._emit_direct_abs_scalar_store(direct_label, sz)
            if post:
                self.emit_instr('pop  WA')
            return ty
        ty = self.gen_lvalue_addr(target)
        sz = self.type_size(ty) if ty else 2

        # Load current value into WA, but keep the resolved lvalue state for the store below.
        self._emit_load_from_de(sz, preserve_lvalue=True)
        self._maybe_sign_extend_loaded_scalar(ty)

        if post:
            # Save old value
            if sz == 4:
                self.emit_instr('push XWA')
            else:
                self.emit_instr('push WA')

        # Compute new value
        # NOTE: inc/dec WA (D0 61/D0 69) is broken on NGPC hardware (bisect_j7d confirmed).
        #       add A,C (CB 81) = ALU byte C-source (CB sous-op 0x80..0xFF) CASSÉ sur silicium NGPC (bisect_j8z13).
        #       Sous-op-spécifique : le byte mul/div reg-reg CB 0x40..0x5F (CB 51 = div A,C) est HW-cleared/SAFE
        #       (hw_test_bytediv 2026-07-08, miroir word D8..DF 0x40..0x5F) ; seuls les ALU C-source cassent.
        #       Fix: utiliser HL — add A,L (CF 81) + adc W,H (CE B0) sont SAFE.
        #       delta=1  -> ld HL, 1      (H=0, L=1)
        #       delta=-1 -> ld HL, 65535  (H=0xFF, L=0xFF = -1 en u16)
        if sz == 4:
            # Pointer arithmetic: add sizeof(*elem) * delta to pointer low word.
            # Use safe ld HL/add A,L/adc W,H pattern — avoids unvalidated D8/inc opcodes.
            # NGPC pointers fit in 16-bit low word (RAM ≤0x7FFF, ROM ≤0x2FFFFF but
            # same-segment increments don't carry into high 16 bits for typical strings).
            step = 1
            if isinstance(ty, PtrType) and ty.base:
                step = max(self.type_size(ty.base), 1)
            val = (step * delta) & 0xFFFF
            self._emit_add_i16_to_xwa(val)
        else:
            uval = delta & 0xFFFF  # 1 -> 1, -1 -> 0xFFFF
            self.emit_instr(f'ld   HL, {uval}')
            self.emit_instr('add  A,  L')   # CF 81 — safe (add A,C = CB 81 CASSÉ)
            self.emit_instr('adc  W,  H')   # CE B0 — safe (adc W,B = CA 90 CASSÉ)

        # Store new value (XDE still holds address)
        self._emit_store_to_de(sz)

        if post:
            if sz == 4:
                self.emit_instr('pop  XWA')
            else:
                self.emit_instr('pop  WA')

        return ty or U16

    def gen_lvalue_addr(self, node, target: str = 'XDE') -> Optional[Type]:
        """Load address of lvalue into XDE (or set _xiy_sym_pending for locals). Returns element type.
        Also uses _xde_field_offset for struct field access via XDE: callers use _emit_load/store_from_de.
        Resets both flags at entry so each call starts fresh.

        Chantier C (2026-04-20): the optional `target` hint lets callers ask
        for the address in XBC directly when they intend to cache it there
        for subsequent field accesses. This skips the post-hoc
        `push XDE; pop XBC` transit (2 B + 2 cycles per site) when the
        subscript fast path produces the address itself. `target='XDE'` is
        the default and preserves all existing callers.
        """
        self._xiy_sym_pending = None
        self._xde_field_offset = 0
        self._xde_ptr_is_array_decay = False
        self._xde_addr_is_far = False
        self._mem_base_reg = 'XDE'
        self._lvalue_target_hint = target
        if isinstance(node, FieldAccess):
            field_ty, field_off = self._resolve_field(node)
            if field_ty is None:
                self.emit_comment(f'ERROR: unknown field {node.field!r}')
                return U16
            if not node.is_arrow:
                cache_key = None
                cache_hit = False
                if not isinstance(field_ty, ArrayType):
                    if isinstance(node.expr, Subscript):
                        cache_key = self._cacheable_struct_array_elem_key(node.expr)
                    elif isinstance(node.expr, Ident):
                        cache_key = self._cacheable_near_struct_base_key(node.expr)
                    cache_hit = cache_key is not None and self._xbc_cached_elem_key == cache_key
                if cache_hit:
                    self._mem_base_reg = 'XBC'
                    # Restore idx*esz so _emit_load/store_from_de uses correct displacement.
                    # Without this, _xde_field_offset stays 0 (reset at gen_lvalue_addr entry)
                    # and subsequent field accesses silently read/write arr[0].field instead of
                    # arr[idx].field — Bug #20.
                    self._xde_field_offset = self._xbc_cached_elem_offset
                else:
                    # s.field: get lvalue addr of s, then adjust by field_off.
                    # Chantier C (2026-04-20): if we plan to cache the address
                    # in XBC, pass target='XBC' so the subscript fast path pops
                    # directly into XBC and we skip the later push/pop transit.
                    want_xbc_cache = (cache_key is not None)
                    target = 'XBC' if want_xbc_cache else 'XDE'
                    self.gen_lvalue_addr(node.expr, target=target)
                    if cache_key is not None and self._xiy_sym_pending is None and not self._xde_addr_is_far:
                        # Only emit push XDE; pop XBC when the subscript fast path did NOT
                        # already load XBC (via _cache_near_symbol_base or target='XBC').
                        # If _mem_base_reg is already 'XBC', XBC holds the correct element
                        # address — emitting push XDE; pop XBC would overwrite it.
                        if self._mem_base_reg != 'XBC':
                            self.emit_instr('push XDE')
                            self.emit_instr('pop  XBC')
                        self._xbc_cached_elem_key = cache_key
                        # Save idx*esz so cache hits can restore the correct displacement.
                        self._xbc_cached_elem_offset = self._xde_field_offset
                self._xde_cached_ptr_key = None
                is_base_far = self._xde_addr_is_far
                if self._xiy_sym_pending is not None:
                    # Local struct: create synthetic sym at struct_base + field_off
                    sym = self._xiy_sym_pending
                    adj = Symbol(sym.name, field_ty, sym.scope,
                                 offset=sym.offset + field_off, label=sym.label)
                    self._xiy_sym_pending = adj
                else:
                    # Global / pointer-backed struct: XDE = base address.
                    # Keep accumulating nested offsets (a.b.c) instead of overwriting.
                    self._xde_field_offset += field_off
                    # For struct array fields, pre-apply the offset so a following
                    # subscript sees the array base directly.
                    if isinstance(field_ty, ArrayType):
                        if self._xde_field_offset > 0:
                            if is_base_far:
                                self._apply_far_field_offset(self._xde_field_offset)
                            else:
                                self._apply_near_field_offset(self._xde_field_offset)
                        self._xde_field_offset = 0
                        self._xde_ptr_is_array_decay = True  # XDE = array base, no deref in subscript
                        self._xde_addr_is_far = is_base_far
                        return PtrType(field_ty.elem, far=is_base_far)
            else:
                # ptr->field: evaluate ptr into XWA, move to XDE, set field offset
                ptr_ty = self.gen_expr(node.expr)   # XWA = ptr value
                self.emit_instr('push XWA')   # 0x38 — safe r32 push
                self.emit_instr('pop  XDE')   # 0x4A — safe r32 pop
                # Pointer values carried in variables/returns may legally contain full ROM
                # addresses even when the source type is not explicitly marked far.
                # Keep the conservative 32-bit path here to avoid truncating the hi16.
                is_ptr_far = isinstance(ptr_ty, PtrType)
                self._xde_addr_is_far = is_ptr_far
                self._xde_field_offset = field_off
                # If field is an array (subscript will follow), pre-apply the field
                # offset to XDE and signal that XDE now holds the array base.
                if isinstance(field_ty, ArrayType):
                    if field_off > 0:
                        if is_ptr_far:
                            self._apply_far_field_offset(field_off)
                        else:
                            self._apply_near_field_offset(field_off)
                    self._xde_field_offset = 0  # already applied to XDE
                    self._xde_ptr_is_array_decay = True  # XDE = array base, no deref in subscript
                    self._xde_cached_ptr_key = None
                    self._xde_addr_is_far = is_ptr_far
                    return PtrType(field_ty.elem, far=is_ptr_far)
            return field_ty
        if isinstance(node, Ident):
            name = node.name
            # Static local: access as global via XDE
            if name in self.static_local_globals:
                sym = self.static_local_globals[name]
                if sym.is_far:
                    # const static local in f_const (ROM 0x200000+) → must use far 32-bit addr
                    self._emit_label_addr_to('XDE', f'_{sym.name}', 'far address of static const local (ROM)')
                    self._xde_cached_ptr_key = None
                    self._xde_addr_is_far = True
                    if isinstance(sym.type_, ArrayType):
                        self._xde_ptr_is_array_decay = True
                        return PtrType(sym.type_.elem, far=True)
                    else:
                        return PtrType(sym.type_, far=True)
                self._emit_label_addr_to('XDE', f'_{sym.name}', 'near address of static local')
                self._xde_cached_ptr_key = None
                self._xde_addr_is_far = False
                return sym.type_
            if name in self.local_vars:
                sym = self.local_vars[name]
                # Use direct XIY-relative encoding — avoids add XDE,imm32 (DA C8 xx)
                # which is unreliable on some NGPC emulators (D0..DF range).
                # _emit_store_to_de / _emit_load_from_de check _xiy_sym_pending and
                # emit BD/9D d8 encodings instead of BA/9A 00 forms.
                self._xiy_sym_pending = sym
                return sym.type_
            if name in self.param_syms:
                sym = self.param_syms[name]
                self._xiy_sym_pending = sym
                return sym.type_
            if name in self.sem.globals:
                sym = self.sem.globals[name]
                # f_const globals (const array/struct) live in ROM (0x200000+).
                # Must use 32-bit LD R32,imm32 to get the full 24-bit ROM address.
                # ld XIZ,label (opcode 0x4E) is safe (confirmed hardware J8-1 bisect).
                # push XIZ; pop XDE is safe (not r+r).
                if sym.is_far:
                    self._emit_label_addr_to('XDE', f'_{name}', 'far address of global (32-bit ROM ptr)')
                    self._xde_cached_ptr_key = None
                    self._xde_addr_is_far = True
                    if isinstance(sym.type_, ArrayType):
                        self._xde_ptr_is_array_decay = True  # XDE = array base, no deref in subscript
                        return PtrType(sym.type_.elem, far=True)
                    else:
                        # Const struct or other: return far ptr to the whole type.
                        return PtrType(sym.type_, far=True)
                # Scalar/non-const global (f_data/f_bss): linker remaps to RAM, near address OK.
                self._emit_label_addr_to('XDE', f'_{name}', 'near address of global (RAM ptr)')
                self._xde_cached_ptr_key = None
                self._xde_addr_is_far = False
                return sym.type_
            self.emit_comment(f'ERROR: unknown lvalue {name!r}')
            return U16
        elif isinstance(node, Deref):
            # *ptr: load ptr address into XDE
            # ld XDE,XWA is broken (32-bit r+r D8+sub≥80). Use push/pop.
            ptr_ty = self.gen_expr(node.expr)
            # Zero-extend only if result is not already a 32-bit pointer.
            # gen_cast(→PtrType) already emits extz; raw integers (u8/u16) do not.
            is_far = isinstance(ptr_ty, PtrType)
            if not isinstance(ptr_ty, PtrType):
                self.emit_instr('extz XWA')   # E8 12 — WA->XWA zero-extend (safe)
            self.emit_instr('push XWA')   # 0x38 — safe r32 push
            self.emit_instr('pop  XDE')   # 0x4A — safe r32 pop
            self._xde_cached_ptr_key = None
            self._xde_addr_is_far = is_far
            if isinstance(ptr_ty, PtrType):
                return ptr_ty.base
            return U16
        elif isinstance(node, Cast):
            # (TYPE*)value used as base for subscript or pointer lvalue.
            # gen_expr(Cast→PtrType) already emits extz; no need to repeat here.
            ty = self.gen_expr(node)
            self.emit_instr('push XWA')
            self.emit_instr('pop  XDE')
            self._xde_cached_ptr_key = None
            # XDE = the cast value IS the subscript base (no deref needed).
            # e.g. ((u16*)0x9800)[i] — XDE=0x9800, just add i*2.
            if isinstance(ty, PtrType):
                self._xde_ptr_is_array_decay = True
                self._xde_addr_is_far = ty.far
            return ty
        elif isinstance(node, Subscript):
            # Fast path: direct near array symbol + index.
            # This avoids the generic 32-bit base-address stack dance for hot code like:
            #   s_sfxTimer[ch], s_sfx_tone_sw_on[ch], ...
            # where the base is a known near global/static-local array.
            direct_sym = None
            direct_arr_ty = None
            if isinstance(node.base, Ident):
                name = node.base.name
                if name in self.static_local_globals:
                    sym = self.static_local_globals[name]
                    if isinstance(sym.type_, ArrayType) and not sym.is_far:
                        direct_sym = sym
                        direct_arr_ty = sym.type_
                elif name in self.sem.globals:
                    sym = self.sem.globals[name]
                    if isinstance(sym.type_, ArrayType) and not sym.is_far:
                        direct_sym = sym
                        direct_arr_ty = sym.type_
            if direct_sym is not None and direct_arr_ty is not None:
                elem_ty = direct_arr_ty.elem
                esz = self.type_size(elem_ty)
                idx_const = self._small_const_u16(node.index)
                if idx_const is not None:
                    byte_off = idx_const * esz
                    if 0 <= byte_off <= 0xFF:
                        self._cache_near_symbol_base(('near_array_base', direct_sym.name), direct_sym.name)
                        self._xde_field_offset = byte_off
                        return elem_ty
                self.gen_expr(node.index)   # WA = index
                if esz == 2:
                    self.emit_instr('push WA')
                    self.emit_instr('pop  HL')           # HL = index
                    self.emit_instr(f'ld   WA, _{direct_sym.name}')
                    self.emit_instr('add  A,  L')
                    self.emit_instr('adc  W,  H')
                    self.emit_instr('add  A,  L')
                    self.emit_instr('adc  W,  H')        # WA = base + 2*index
                else:
                    if esz == 4:
                        self.emit_instr('db 0xE8,0xEE,0x02    ; sll 0x2,WA -> WA*4')
                    elif esz != 1:
                        self.emit_instr(f'ld   HL, {esz}')
                        self.emit_instr('push WA')
                        self.emit_instr('db 0x9F,0x00,0x43    ; mul XHL,(XSP+0) -> XHL=esz*idx')
                        self.emit_instr('add  XSP, 2')
                        self.emit_instr('push HL')
                        self.emit_instr('pop  WA')
                    self.emit_instr('push WA')
                    self.emit_instr(f'ld   WA, _{direct_sym.name}')
                    self.emit_instr('pop  HL')
                    self.emit_instr('add  A,  L')
                    self.emit_instr('adc  W,  H')        # WA = base + esz*index
                self.emit_instr('extz XWA')
                # Chantier C: if caller hinted XBC (for struct field cache),
                # pop directly into XBC and skip the downstream transit.
                if getattr(self, '_lvalue_target_hint', 'XDE') == 'XBC':
                    self.emit_instr('push XWA')
                    self.emit_instr('pop  XBC           ; Chantier C: direct XBC (skip XDE transit)')
                    self._mem_base_reg = 'XBC'
                else:
                    self.emit_instr('push XWA')
                    self.emit_instr('pop  XDE')
                self._xde_cached_ptr_key = None
                return elem_ty

            base_ty = self.gen_lvalue_addr(node.base)
            if isinstance(base_ty, PtrType):
                elem_ty = base_ty.base
            elif isinstance(base_ty, ArrayType):
                elem_ty = base_ty.elem
            else:
                elem_ty = U16
            esz = self.type_size(elem_ty)
            is_far = isinstance(base_ty, PtrType)
            # Push 4-byte base address onto stack for add XWA,(XSP+0) below.
            # For locals/params, _xiy_sym_pending is set but XDE was never loaded
            # (gen_lvalue_addr(Ident) only sets the flag, does not touch XDE).
            # Fix: compute XIY+sym.offset explicitly and push as 32-bit address.
            if self._xiy_sym_pending is not None:
                sym = self._xiy_sym_pending
                self._xiy_sym_pending = None
                if isinstance(base_ty, PtrType):
                    # Pointer variable: load its VALUE (the address it points to) into XWA.
                    # base_ty is PtrType → sym holds the pointer value, not an array base.
                    # _load_local emits db 0xAD,d,0x20 = LD XWA,(XIY+d) [32-bit load].
                    self._load_local(sym)              # XWA = *(XIY+offset) = 32-bit ptr value
                    self.emit_instr('push XWA')        # push as 4-byte base address
                else:
                    # Array (or unknown): compute frame_base+offset = address of array[0].
                    self.emit_instr('ld   WA, 0')
                    self.emit_instr('push WA')             # high word of 32-bit address = 0
                    self._emit_stack_sym_addr_to_xwa(sym)
                    self.emit_instr('push WA')             # low word — stack now has 4-byte addr
            else:
                if isinstance(base_ty, PtrType) and not self._xde_ptr_is_array_decay:
                    # Pointer variable/field accessed via XDE (e.g. p->script[i]).
                    # _xde_ptr_is_array_decay is False → XDE is the address OF the pointer
                    # (struct addr or global ptr var addr), not the base address to subscript from.
                    # Load the pointer VALUE from XDE+_xde_field_offset and push it.
                    self._emit_load_from_de(4)   # XWA = *(XDE + field_off) = ptr value
                    self.emit_instr('push XWA')  # push 4-byte ptr value as subscript base
                else:
                    # Array base: XDE already holds the subscript base address.
                    # (_xde_ptr_is_array_decay=True: array global or pre-applied array field)
                    self.emit_instr('push XDE')
            self.gen_expr(node.index)
            # WA = index
            if esz == 2:
                if is_far:
                    # Far ptr (ROM, 24-bit): preserve all bits of the base address.
                    # Stack: [lo16_base(2B), hi16_base(2B)], WA = index.
                    # Step 1: byte_offset = index*2 in HL (using safe double-add).
                    self.emit_instr('push WA')           # save index
                    self.emit_instr('pop  HL')           # HL = index, WA = index
                    self.emit_instr('add  A,  L')        # CF 81: WA = index*2 (lo)
                    self.emit_instr('adc  W,  H')        # CE B0: WA = index*2 (hi)
                    self.emit_instr('push WA')
                    self.emit_instr('pop  HL')           # HL = byte_offset = index*2
                    # Step 2: single-add to lo16_base with correct carry.
                    self.emit_instr('pop  WA')           # WA = lo16_base, stack=[hi16_base(2B)]
                    self.emit_instr('add  A,  L')        # CF 81
                    self.emit_instr('adc  W,  H')        # CE B0: WA=new_lo16, CF=carry to bit16
                    # Step 3: reconstruct 32-bit far addr = (hi16_base+carry)(new_lo16).
                    self.emit_instr('push WA')           # [new_lo16(2B),hi16_base(2B)], CF kept
                    self.emit_instr('pop  HL')           # HL=new_lo16, stack=[hi16_base], CF kept
                    self.emit_instr('pop  WA')           # WA=hi16_base, stack=[], CF kept
                    self.emit_instr('push HL')           # save new_lo16, CF kept
                    self.emit_instr('ld   HL, 0')       # HL=0, CF kept (LD never modifies C)
                    self.emit_instr('adc  W,  H')        # CE B0: W+=carry (applies carry to hi16)
                    self.emit_instr('pop  HL')           # HL=new_lo16 restored
                    self.emit_instr('push WA')           # push hi16_result
                    self.emit_instr('push HL')           # push new_lo16
                    self.emit_instr('pop  XDE')          # XDE=(hi16_result<<16)|new_lo16 = far addr
                    self._xde_cached_ptr_key = None
                else:
                    # Near ptr: high 2 bytes are always 0 (RAM/HW-reg addresses fit in 16 bits).
                    # Stack: [base_lo16(2B), 0x0000(2B)], WA = index.
                    self.emit_instr('push WA')
                    self.emit_instr('pop  HL')           # HL = index
                    self.emit_instr('pop  WA')           # WA = base_ptr lo16
                    self.emit_instr('add  XSP, 2')       # discard hi16 (always 0 for near)
                    self.emit_instr('add  A,  L')
                    self.emit_instr('adc  W,  H')        # WA = base_lo + index
                    self.emit_instr('add  A,  L')
                    self.emit_instr('adc  W,  H')        # WA = base_lo + 2*index
                    self.emit_instr('extz XWA')
                    self.emit_instr('push XWA')
                    self.emit_instr('pop  XDE')
                    self._xde_cached_ptr_key = None
                return elem_ty
            elif esz == 4:
                # stride ×4 via sll 0x2,WA — E8 EE 02 from §15.4 (reverse-engineered cc900).
                self.emit_instr('db 0xE8,0xEE,0x02    ; sll 0x2,WA -> WA*4')
            elif esz != 1:
                # General stride: mul XHL,(XSP+0) memory form — safe.
                self.emit_instr(f'ld   HL, {esz}')
                self.emit_instr('push WA')
                self.emit_instr('db 0x9F,0x00,0x43    ; mul XHL,(XSP+0) -> XHL=esz*idx')
                self.emit_instr('add  XSP, 2')
                self.emit_instr('push HL')
                self.emit_instr('pop  WA')
            # esz != 2: add base_ptr (on stack) to WA=stride*idx using safe 16-bit adds.
            # Stack: [base_lo16(2B), base_hi16(2B)]. WA = stride*idx.
            self.emit_instr('push WA')           # save stride*idx
            self.emit_instr('pop  HL')           # HL = stride*idx
            self.emit_instr('pop  WA')           # WA = base_ptr lo16
            if is_far:
                # Far ptr: preserve hi16 and apply carry (same pattern as esz==2 far path).
                self.emit_instr('add  A,  L')
                self.emit_instr('adc  W,  H')    # WA=new_lo16, CF=carry
                self.emit_instr('push WA')
                self.emit_instr('pop  HL')
                self.emit_instr('pop  WA')        # WA=hi16_base, CF kept
                self.emit_instr('push HL')
                self.emit_instr('ld   HL, 0')
                self.emit_instr('adc  W,  H')    # W+=carry
                self.emit_instr('pop  HL')
                self.emit_instr('push WA')
                self.emit_instr('push HL')
                self.emit_instr('pop  XDE')
                self._xde_cached_ptr_key = None
            else:
                self.emit_instr('add  XSP, 2')   # discard base_ptr high 2 bytes (always 0)
                self.emit_instr('add  A,  L')
                self.emit_instr('adc  W,  H')    # WA = base_lo + stride*idx
                self.emit_instr('extz XWA')
                self.emit_instr('push XWA')
                self.emit_instr('pop  XDE')
                self._xde_cached_ptr_key = None
            return elem_ty
        else:
            self.emit_comment(f'ERROR: cannot take lvalue of {type(node).__name__}')
            return U16

    def _emit_add_i16_to_xwa(self, val: int):
        """Add a sign-extended 16-bit constant to XWA."""
        lo = val & 0xFFFF
        hi = 0xFFFF if (lo & 0x8000) else 0x0000
        self.emit_instr('push XWA')
        self.emit_instr('pop  WA')            # WA = lo16, stack: [hi16]
        self.emit_instr(f'ld   HL, {lo}')
        self.emit_instr('add  A,  L')
        self.emit_instr('adc  W,  H')         # low16 += lo, CF = carry/borrow into hi16
        self.emit_instr('push WA')            # stack: [hi16, new_lo16]
        self.emit_instr('pop  HL')            # HL = new_lo16, stack: [hi16]
        self.emit_instr('pop  WA')            # WA = hi16
        self.emit_instr('push HL')            # preserve new_lo16
        self.emit_instr(f'ld   HL, {hi}')
        self.emit_instr('adc  A,  L')
        self.emit_instr('adc  W,  H')         # hi16 += sign(lo) + carry/borrow
        self.emit_instr('pop  HL')            # HL = new_lo16 restored
        self.emit_instr('push WA')            # hi16 result
        self.emit_instr('push HL')            # lo16 result
        self.emit_instr('pop  XWA')

    def _emit_signed_div_pow2_u16(self, shift: int):
        """Compute WA = (signed16)WA / (1<<shift), truncating toward zero."""
        if shift <= 0:
            return
        bias = (1 << shift) - 1
        self.emit_instr('push WA')
        self.emit_instr('db 0xE8, 0x13       ; exts XWA (sign-extend dividend)')
        remaining = 15
        while remaining > 0:
            n = min(remaining, 8)
            self.emit_instr(f'sra {n}, XWA')
            remaining -= n
        self.emit_instr(f'ld   HL, {bias}')
        self.emit_instr('and  A,  L')
        self.emit_instr('and  W,  H')
        self.emit_instr('pop  HL')
        self.emit_instr('add  A,  L')
        self.emit_instr('adc  W,  H')
        self.emit_instr('db 0xE8, 0x13       ; exts XWA (sign-extend biased dividend)')
        remaining = shift
        while remaining > 0:
            n = min(remaining, 8)
            self.emit_instr(f'sra {n}, XWA')
            remaining -= n

    def _apply_near_field_offset(self, field_off: int):
        """Adjust a near address in XDE by a small field offset."""
        if self._mem_base_reg == 'XBC':
            self.emit_instr('push XBC')
            self.emit_instr('pop  XDE')
            self._mem_base_reg = 'XDE'
        self.emit_instr('push XDE')           # stack: [hi16_XDE, lo16_XDE]
        self.emit_instr('pop  WA')            # WA = lo16
        self.emit_instr('add  XSP, 2')        # discard hi16 (always 0 for near addresses)
        self.emit_instr(f'ld   HL, {field_off}')
        self.emit_instr('add  A,  L')
        self.emit_instr('adc  W,  H')
        self.emit_instr('extz XWA')
        self.emit_instr('push XWA')
        self.emit_instr('pop  XDE')

    def _apply_far_field_offset(self, field_off: int):
        """Add field_off to XDE using carry-safe 32-bit arithmetic.
        Used when a struct array field is accessed via a far pointer: XDE holds the
        far (ROM) address of the struct, and we need XDE = XDE + field_off so that
        the subsequent subscript arithmetic uses the correct base.
        Carry from the lo16 addition is propagated into hi16 (needed for ROM addresses
        near a 64 KB boundary, i.e. lo16 near 0xFFFF + small field_off).
        Only safe opcodes (no D0/CB prefix, no r+r 32-bit moves) are used.
        """
        self.emit_instr('push XDE')           # stack: [hi16_XDE, lo16_XDE]
        self.emit_instr('pop  WA')            # WA = lo16, stack: [hi16_XDE]
        self.emit_instr(f'ld   HL, {field_off}')
        self.emit_instr('add  A,  L')         # A += lo(field_off), sets CF1
        self.emit_instr('adc  W,  H')         # W += hi(field_off) + CF1, sets CF2
        self.emit_instr('push WA')            # stack: [hi16_XDE, new_lo16]
        self.emit_instr('pop  HL')            # HL = new_lo16, stack: [hi16_XDE]
        self.emit_instr('pop  WA')            # WA = hi16_XDE, stack: empty, CF2 kept
        self.emit_instr('push HL')            # stack: [new_lo16]
        self.emit_instr('ld   HL, 0')         # HL = 0 (LD never modifies CF)
        self.emit_instr('adc  W,  H')         # W += 0 + CF2 → propagate carry into hi16
        self.emit_instr('pop  HL')            # HL = new_lo16 restored
        self.emit_instr('push WA')            # stack: [adj_hi16]
        self.emit_instr('push HL')            # stack: [adj_hi16, new_lo16]
        self.emit_instr('pop  XDE')           # XDE = (adj_hi16 << 16) | new_lo16

    def _emit_load_from_de(self, sz: int, preserve_lvalue: bool = False):
        """Load (XDE+d) into A/WA/XWA.
        If _xiy_sym_pending is set (local/param), use direct XIY-relative load instead.
        Uses _xde_field_offset as d8 (for struct field access via pointer, default 0).
        Encoding (§15): 0x9A = 0x98+XDE(2), d8, sub-op
        """
        if self._xiy_sym_pending is not None:
            self._load_local(self._xiy_sym_pending)
            if not preserve_lvalue:
                self._xiy_sym_pending = None
                self._xde_field_offset = 0
                self._mem_base_reg = 'XDE'
            return
        d = self._xde_field_offset & 0xFF
        off = self._xde_field_offset
        base_reg = self._mem_base_reg
        base_idx = 1 if base_reg == 'XBC' else 2
        if sz == 1:
            # P4: `ld W, 0` (2 B) — A is set by LDB below.
            self.emit_instr('ld   W, 0                 ; u8 zero-extend (P4)')
            self.emit_instr(f'db 0x{(0x88 + base_idx):02X}, 0x{d:02X}, 0x21  ; LDB A, ({base_reg}{off:+d})')
        elif sz == 4:
            self.emit_instr(f'db 0x{(0xA8 + base_idx):02X}, 0x{d:02X}, 0x20  ; LD XWA, ({base_reg}{off:+d})')
        else:
            self.emit_instr(f'db 0x{(0x98 + base_idx):02X}, 0x{d:02X}, 0x20  ; LDW WA, ({base_reg}{off:+d})')
        if not preserve_lvalue:
            self._xiy_sym_pending = None
            self._xde_field_offset = 0
            self._mem_base_reg = 'XDE'

    def _emit_load_from_xwa(self, sz: int, preserve_lvalue: bool = False):
        """Load value from far ptr in XWA.
        XWA as memory base register may not be valid in TLCS-900H (accumulator reg).
        Safe path: copy XWA → XDE via push/pop, then deref from XDE.
        push XWA (0x38) + pop XDE (0x5A) are safe (not r+r ops).
        """
        self.emit_instr('push XWA')   # 0x38 — far ptr addr onto stack
        self.emit_instr('pop  XDE')   # 0x5A — into XDE (valid base register)
        self._emit_load_from_de(sz, preserve_lvalue=preserve_lvalue)

    def _emit_store_to_xwa(self, sz: int):
        """Store A/WA to (XWA+0) for far pointer write.
        Encoding: 0xB8+XWA(0)=0xB8, d8=0x00, sub-op:
          sz=1: 0xB8 0x00 0x41 = LDB (XWA+0),A  [confirmed functional DEVLOG §20 2026-03-22]
          sz=2: 0xB8 0x00 0x50 = LDW (XWA+0),WA
        """
        if sz == 1:
            self.emit_instr('db 0xB8, 0x00, 0x41  ; LDB (XWA+0), A   [u8 byte store]')
        else:
            self.emit_instr('db 0xB8, 0x00, 0x50  ; LDW (XWA+0), WA [far store u16]')

    def _emit_store_to_de(self, sz: int):
        """Store WA/A into (XDE+d).
        If _xiy_sym_pending is set (local/param), use direct XIY-relative store instead.
        Uses _xde_field_offset as d8 (for struct field writes, default 0).
        Clears both flags after use.
        Encoding: 0xBA = 0xB8+XDE(2), d8, sub-op
        """
        if self._xiy_sym_pending is not None:
            self._store_local(self._xiy_sym_pending)
            self._xiy_sym_pending = None
            self._xde_field_offset = 0
            self._mem_base_reg = 'XDE'
            return
        d = self._xde_field_offset & 0xFF
        off = self._xde_field_offset
        self._xde_field_offset = 0
        base_reg = self._mem_base_reg
        base_idx = 1 if base_reg == 'XBC' else 2
        if sz == 1:
            self.emit_instr(f'db 0x{(0xB8 + base_idx):02X}, 0x{d:02X}, 0x41  ; LDB ({base_reg}{off:+d}), A   [u8 byte store]')
        elif sz == 4:
            # Split 32-bit store into two validated 16-bit LDW stores.
            # LD (XDE+d), XWA (0xBA d 0x60) is unvalidated on hardware; avoid.
            # Push XWA, pop lo16 to WA, store at (XDE+d); pop hi16, store at (XDE+d+2).
            d_hi = (off + 2) & 0xFF
            self.emit_instr('push XWA')
            self.emit_instr('pop  WA')                                                   # WA = lo16; XSP+=2
            self.emit_instr(f'db 0x{(0xB8 + base_idx):02X}, 0x{d:02X}, 0x50  ; LDW ({base_reg}{off:+d}), WA  [ptr lo16]')
            self.emit_instr('pop  WA')                                                   # WA = hi16; XSP+=2
            self.emit_instr(f'db 0x{(0xB8 + base_idx):02X}, 0x{d_hi:02X}, 0x50  ; LDW ({base_reg}{off+2:+d}), WA  [ptr hi16]')
        else:
            self.emit_instr(f'db 0x{(0xB8 + base_idx):02X}, 0x{d:02X}, 0x50  ; LDW ({base_reg}{off:+d}), WA')
        self._mem_base_reg = 'XDE'

    def _c5_try_emit_local16_const(self, node) -> Optional[Type]:
        """P-5.6.1 wiring: first migrated codegen pattern.

        Matches `local16 = const_u16` (target is a u16 local Ident or
        param at XIY+offset, value is a small const that fits in u16).

        Emits BOTH:
        - Legacy text directly to `self.lines` (bypasses `emit_instr` so
          no EmitRaw is created in the IR for these lines).
        - Structured ops `LoadImm` + `StoreLocal` to `self.ir_function`.

        The IR round-trip check (`lower_to_asm` with XWA-default
        lowering) reproduces the legacy text → round-trip OK.

        The C5 pipeline (`lower_ir_with_allocation`) emits real
        allocation. The %t vreg gets `pref='XWA'` in `_c5_run_pipeline`,
        so when XWA is free the allocator picks it → output identical
        to legacy → shadow mode green. When XWA is contended, allocator
        falls back to XBC → output diverges legitimately (= the case
        that eventually saves bytes by avoiding push/pop scaffolding).

        Returns the resulting Type, or `None` if the pattern doesn't
        match (caller falls back to legacy paths).
        """
        if not self._opt_c5_use_structured:
            return None
        if self._opt_c5_regalloc == '0':
            # Pipeline must be active to consume the structured ops.
            return None
        direct_stack_sym = self._direct_stack_store_sym(node.target)
        if direct_stack_sym is None:
            return None
        if direct_stack_sym.reg_name:
            return None  # register-bank local — different storage path
        const_u16 = self._small_const_u16(node.value)
        if const_u16 is None:
            return None
        if not self._expr_is_pure(node.target):
            return None
        # Restrict to u16/s16 to keep the first migration tight.
        store_sz = self.type_size(direct_stack_sym.type_)
        if store_sz != 2:
            return None
        # Only XIY-relative stack base is wired in P-5.6.1 lowering.
        base_reg = self._stack_base_reg(direct_stack_sym)
        if base_reg != 'XIY':
            return None

        if self._lvalue_writes_cached_index(node.target):
            self._invalidate_elem_base_cache()

        off = direct_stack_sym.offset
        d = off & 0xFF

        # 1. Emit legacy text DIRECTLY to self.lines (no EmitRaw in IR).
        legacy_load_line = f'    ld   WA, {const_u16}'
        legacy_store_line = (
            f'    db 0x{0xBD:02X}, 0x{d:02X}, 0x{0x50:02X}  '
            f'; LDW (XIY{off:+d}), WA'
        )
        self.lines.append(legacy_load_line)
        self.lines.append(legacy_store_line)
        # Keep reg-tracker invalidation in sync with what `emit_instr`
        # would have done — `ld WA, ...` clobbers any cached sym for WA.
        self._c4_p3_check_emit_invalidation(legacy_load_line)
        self._c4_p3_check_emit_invalidation(legacy_store_line)

        # 2. Append STRUCTURED ops to ir_function (no EmitRaw).
        vreg = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        self.ir_function.append(LoadImm(dest=vreg, value=const_u16, width='u16'))
        self.ir_function.append(StoreLocal(offset=off, src=vreg, width='u16'))

        self._c5_stats['structured_emits'] += 1
        self._c5_stats['structured_emits_this_function'] += 1
        return direct_stack_sym.type_

    def _c5_try_emit_local16_eq_local16_op_global16(self, node) -> Optional[Type]:
        """P-5.6.4b : `local_u16 = local_u16 OP global_u16` (assignment, not compound).

        Cousin de `_c5_try_emit_local16_eq_local16_op_local16` mais avec
        right operand = near global au lieu de local. Émet OPTIMAL 14 B
        (vs legacy ~17-19 B selon gen_binop fast-paths) :

            LDW WA, (XIY+off_left)
            ld HL, (_global)
            <op A,L>; <op-carry W,H>
            LDW (XIY+off_target), WA

        Aussi essaie le mirror `local = global OP local` (op commutative)
        en swappant les opérandes, car `add/and/or/xor` sont commutatives
        (mais pas `sub` — donc cas asymétrique).

        Returns Type or None.
        """
        if not self._opt_c5_use_structured:
            return None
        if self._opt_c5_regalloc == '0':
            return None
        target_sym = self._direct_stack_store_sym(node.target)
        if target_sym is None or target_sym.reg_name:
            return None
        if self.type_size(target_sym.type_) != 2:
            return None
        if self._stack_base_reg(target_sym) != 'XIY':
            return None
        val = node.value
        if not isinstance(val, BinOp):
            return None
        op_entry = self._C5_BYTE_SPLIT_OPS.get(val.op)
        if op_entry is None:
            return None
        ir_op, line_alu_lo, line_alu_hi = op_entry

        # Try direct order : left = local, right = global
        left_sym = self._resolve_local_u16_xiy_sym(val.left)
        right_global = self._direct_abs_scalar_symbol(val.right)
        # Mirror order (only for commutative ops add/and/or/xor) :
        # left = global, right = local
        commutative = val.op in ('+', '&', '|', '^')
        if (left_sym is None or right_global is None) and commutative:
            left_global = self._direct_abs_scalar_symbol(val.left)
            right_sym = self._resolve_local_u16_xiy_sym(val.right)
            if left_global is not None and right_sym is not None:
                # Swap to keep `local in WA, global in HL` shape
                left_sym = right_sym
                right_global = left_global
        if left_sym is None or right_global is None:
            return None
        global_sym, global_label = right_global
        if self.type_size(global_sym.type_) != 2:
            return None
        if not self._expr_is_pure(node.target):
            return None
        if self._lvalue_writes_cached_index(node.target):
            self._invalidate_elem_base_cache()

        off_a = left_sym.offset
        off_t = target_sym.offset
        d_a = off_a & 0xFF
        d_t = off_t & 0xFF

        # 1. Emit OPTIMAL 5 instr / 14 B
        line1 = f'    db 0x9D, 0x{d_a:02X}, 0x20  ; LDW WA, (XIY{off_a:+d})'
        line2 = f'    ld   HL, ({global_label})'
        line5 = f'    db 0xBD, 0x{d_t:02X}, 0x50  ; LDW (XIY{off_t:+d}), WA'
        for ln in (line1, line2, line_alu_lo, line_alu_hi, line5):
            self.lines.append(ln)
            self._c4_p3_check_emit_invalidation(ln)

        # 2. Append STRUCTURED ops.
        vreg_a = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_g = f'%hl{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_r = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        self._c5_vreg_cls[vreg_a] = 'WA_ONLY'
        self._c5_vreg_cls[vreg_g] = 'HL_ONLY'
        self._c5_vreg_cls[vreg_r] = 'WA_ONLY'
        self.ir_function.append(LoadLocal(dest=vreg_a, offset=off_a, width='u16'))
        self.ir_function.append(LoadGlobal(dest=vreg_g, sym=global_label, width='u16'))
        self.ir_function.append(IRBinOp(dest=vreg_r, src_a=vreg_a, src_b=vreg_g,
                                       op=ir_op, width='u16'))
        self.ir_function.append(StoreLocal(offset=off_t, src=vreg_r, width='u16'))

        self._c5_stats['structured_emits'] += 1
        self._c5_stats['structured_emits_this_function'] += 1
        self._c5_stats['p5_6_4b_emits'] = (
            self._c5_stats.get('p5_6_4b_emits', 0) + 1
        )
        return target_sym.type_

    def _c5_try_emit_compound_local16_op_global16(self, node) -> Optional[Type]:
        """P-5.6.4 (2026-05-20) : compound `local_u16 OP= global_u16` (near scalar).

        Pattern compound (op ∈ +=, -=, &=, |=, ^=) où target est u16 local
        à XIY-rel ET value est un Ident de global near u16 (= scalaire pas
        far, pas array, taille ≤ 2). Sémantiquement équivalent à
        `target = target OP global`.

        Aujourd'hui la legacy fast path B émet 16 B :
            ld WA, (_global)           ; gen_expr(value)
            push WA                    ; copy_wa_to_hl part 1
            pop HL                     ; copy_wa_to_hl part 2
            LDW WA, (XIY+off_target)
            <op A,L>; <op-carry W,H>
            LDW (XIY+off_target), WA

        Cette migration émet OPTIMAL 14 B (savings 2 B/site) :
            LDW WA, (XIY+off_target)
            ld HL, (_global)           ← LDW HL abs16, encoding `D1 abs16 0x23`
            <op A,L>; <op-carry W,H>
            LDW (XIY+off_target), WA

        Encoding `ld HL, (_global)` = `D1 abs16_lo abs16_hi 0x23` :
        SAME family as legacy `ld WA, (_global)` = `D1 abs16 0x20`
        (HW-shippé baseline). Sub-op 0x23 nouveau ROM-side, théoriquement
        safe mais ⚠️ NEEDS HW TEST première fois.

        Structured ops : LoadLocal %t (target read) + LoadGlobal %hl (rhs)
        + BinOp %r + StoreLocal target.

        Returns Type or None.
        """
        if not self._opt_c5_use_structured:
            return None
        if self._opt_c5_regalloc == '0':
            return None
        if node.op not in ('+=', '-=', '&=', '|=', '^='):
            return None
        base_op = node.op[:-1]
        op_entry = self._C5_BYTE_SPLIT_OPS.get(base_op)
        if op_entry is None:
            return None
        ir_op, line_alu_lo, line_alu_hi = op_entry
        # Target : u16 local at XIY+off
        target_sym = self._direct_stack_store_sym(node.target)
        if target_sym is None or target_sym.reg_name:
            return None
        if self.type_size(target_sym.type_) != 2:
            return None
        if self._stack_base_reg(target_sym) != 'XIY':
            return None
        # Value : near global u16 scalar (via existing helper)
        direct = self._direct_abs_scalar_symbol(node.value)
        if direct is None:
            return None
        global_sym, global_label = direct
        # _direct_abs_scalar_symbol filters out far + arrays + sz>2 already.
        # Restrict further to u16 (= same as target_sym size).
        if self.type_size(global_sym.type_) != 2:
            return None
        if not self._expr_is_pure(node.target):
            return None
        if self._lvalue_writes_cached_index(node.target):
            self._invalidate_elem_base_cache()

        off_t = target_sym.offset
        d_t = off_t & 0xFF

        # 1. Emit OPTIMAL legacy text DIRECTLY to self.lines (5 instr / 14 B).
        line_load_t = f'    db 0x9D, 0x{d_t:02X}, 0x20  ; LDW WA, (XIY{off_t:+d})'
        line_load_g = f'    ld   HL, ({global_label})'
        line_store_t = f'    db 0xBD, 0x{d_t:02X}, 0x50  ; LDW (XIY{off_t:+d}), WA'
        for ln in (line_load_t, line_load_g, line_alu_lo, line_alu_hi, line_store_t):
            self.lines.append(ln)
            self._c4_p3_check_emit_invalidation(ln)

        # 2. Append STRUCTURED ops.
        vreg_a = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_g = f'%hl{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_r = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        self._c5_vreg_cls[vreg_a] = 'WA_ONLY'
        self._c5_vreg_cls[vreg_g] = 'HL_ONLY'
        self._c5_vreg_cls[vreg_r] = 'WA_ONLY'
        self.ir_function.append(LoadLocal(dest=vreg_a, offset=off_t, width='u16'))
        self.ir_function.append(LoadGlobal(dest=vreg_g, sym=global_label, width='u16'))
        self.ir_function.append(IRBinOp(dest=vreg_r, src_a=vreg_a, src_b=vreg_g,
                                       op=ir_op, width='u16'))
        self.ir_function.append(StoreLocal(offset=off_t, src=vreg_r, width='u16'))

        self._c5_stats['structured_emits'] += 1
        self._c5_stats['structured_emits_this_function'] += 1
        self._c5_stats['p5_6_4_emits'] = (
            self._c5_stats.get('p5_6_4_emits', 0) + 1
        )
        self._c5_stats[f'p5_6_4_op_{node.op}'] = (
            self._c5_stats.get(f'p5_6_4_op_{node.op}', 0) + 1
        )
        return target_sym.type_

    def _c5_try_emit_compound_local16_op_local16(self, node) -> Optional[Type]:
        """P-5.6.3c (2026-05-20) : compound assign `local_u16 OP= local_u16`.

        Pattern compound (op ∈ +=, -=, &=, |=, ^=) où target ET value
        sont u16 locals à XIY-rel. Sémantiquement équivalent à
        `target = target OP value`.

        Aujourd'hui la legacy compound fast path B émet 15 B :
            LDW WA, (XIY+off_value)   ; gen_expr(value)
            push WA                    ; copy_wa_to_hl part 1
            pop HL                     ; copy_wa_to_hl part 2
            LDW WA, (XIY+off_target)  ; gen_ident(target)
            <op A,L>; <op-carry W,H>   ; _emit_alu16
            LDW (XIY+off_target), WA   ; _store_local(target)

        Cette migration émet la séquence OPTIMALE 13 B (savings 2 B/site)
        en utilisant `LDW HL, (XIY+off_value)` direct (encoding `0x9D d 0x23`,
        HW-validé pass 22) :
            LDW WA, (XIY+off_target)
            LDW HL, (XIY+off_value)
            <op A,L>; <op-carry W,H>
            LDW (XIY+off_target), WA

        Structured ops émis : LoadLocal %t (target read) + LoadLocal %hl (value)
        + BinOp %r + StoreLocal target.

        Returns Type or None.
        """
        if not self._opt_c5_use_structured:
            return None
        if self._opt_c5_regalloc == '0':
            return None
        if node.op not in ('+=', '-=', '&=', '|=', '^='):
            return None
        base_op = node.op[:-1]
        op_entry = self._C5_BYTE_SPLIT_OPS.get(base_op)
        if op_entry is None:
            return None
        ir_op, line3, line4 = op_entry
        # Target side: u16 local at XIY+off (must be also readable, hence
        # _resolve_local_u16_xiy_sym AND _direct_stack_store_sym share shape)
        target_sym = self._direct_stack_store_sym(node.target)
        if target_sym is None or target_sym.reg_name:
            return None
        if self.type_size(target_sym.type_) != 2:
            return None
        if self._stack_base_reg(target_sym) != 'XIY':
            return None
        # Value side: u16 local Ident at XIY+off
        value_sym = self._resolve_local_u16_xiy_sym(node.value)
        if value_sym is None:
            return None
        if not self._expr_is_pure(node.target):
            return None
        if self._lvalue_writes_cached_index(node.target):
            self._invalidate_elem_base_cache()

        off_t = target_sym.offset
        off_v = value_sym.offset
        d_t = off_t & 0xFF
        d_v = off_v & 0xFF

        # 1. Emit OPTIMAL legacy text DIRECTLY to self.lines (5 instr / 13 B).
        line1 = f'    db 0x9D, 0x{d_t:02X}, 0x20  ; LDW WA, (XIY{off_t:+d})'
        line2 = f'    db 0x9D, 0x{d_v:02X}, 0x23  ; LDW HL, (XIY{off_v:+d})'
        line5 = f'    db 0xBD, 0x{d_t:02X}, 0x50  ; LDW (XIY{off_t:+d}), WA'
        for ln in (line1, line2, line3, line4, line5):
            self.lines.append(ln)
            self._c4_p3_check_emit_invalidation(ln)

        # 2. Append STRUCTURED ops.
        vreg_a = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_b = f'%hl{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_r = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        self._c5_vreg_cls[vreg_a] = 'WA_ONLY'
        self._c5_vreg_cls[vreg_b] = 'HL_ONLY'
        self._c5_vreg_cls[vreg_r] = 'WA_ONLY'
        self.ir_function.append(LoadLocal(dest=vreg_a, offset=off_t, width='u16'))
        self.ir_function.append(LoadLocal(dest=vreg_b, offset=off_v, width='u16'))
        self.ir_function.append(IRBinOp(dest=vreg_r, src_a=vreg_a, src_b=vreg_b,
                                       op=ir_op, width='u16'))
        self.ir_function.append(StoreLocal(offset=off_t, src=vreg_r, width='u16'))

        self._c5_stats['structured_emits'] += 1
        self._c5_stats['structured_emits_this_function'] += 1
        self._c5_stats['p5_6_3c_emits'] = (
            self._c5_stats.get('p5_6_3c_emits', 0) + 1
        )
        self._c5_stats[f'p5_6_3c_op_{node.op}'] = (
            self._c5_stats.get(f'p5_6_3c_op_{node.op}', 0) + 1
        )
        return target_sym.type_

    def _resolve_local_u16_xiy_sym(self, node) -> Optional[Symbol]:
        """If `node` names a u16 local at XIY+offset (not a register-bank
        local), return its Symbol. Else None.

        P-5.6.2 helper: the source side of a `local16 = local16` migration
        needs the same kind of resolution as `_direct_stack_store_sym`
        does for the target side, but expressed against a value-context
        Ident (we want to read, not write). Restricts to:
          - `Ident` node
          - resolves in `local_vars` or `param_syms`
          - storage class is 'local'
          - type is u16/s16 (2 bytes)
          - `reg_name` is None (= stack-backed, not banked)
          - `_stack_base_reg(sym) == 'XIY'` (frame pointer)
        """
        if not isinstance(node, Ident):
            return None
        name = node.name
        sym = self.local_vars.get(name) or self.param_syms.get(name)
        if sym is None:
            return None
        if sym.scope not in ('local', 'param'):
            return None
        if sym.reg_name:
            return None
        if sym.adecl_live_reg:
            # P2 leaf adecl param stays live in incoming reg — not a
            # stack-backed symbol, different load encoding.
            return None
        if self.type_size(sym.type_) != 2:
            return None
        if self._stack_base_reg(sym) != 'XIY':
            return None
        return sym

    # P-5.6.3b (2026-05-20) extension : 5 ops byte-split alu via WA/HL.
    # AST op str → IR op str + (lo_byte_split, hi_byte_split) inline asm.
    # All these encodings are HW-shipped already (legacy `_emit_alu16`).
    # The `_lower_binop` in alloc.py mirrors this table.
    _C5_BYTE_SPLIT_OPS = {
        '+': ('add', '    add  A,  L           ; CF 81 — low byte',
                     '    adc  W,  H           ; CE 90 — high byte + carry'),
        '-': ('sub', '    sub  A,  L           ; CF A1 — low byte',
                     '    sbc  W,  H           ; CE B0 — high byte - borrow'),
        '&': ('and', '    and  A,  L           ; CF C1 — low byte',
                     '    and  W,  H           ; CE C0 — high byte'),
        '|': ('or',  '    or   A,  L           ; CF E1 — low byte',
                     '    or   W,  H           ; CE E0 — high byte'),
        '^': ('xor', '    xor  A,  L           ; CF D1 — low byte',
                     '    xor  W,  H           ; CE D0 — high byte'),
    }

    def _c5_try_emit_local16_eq_local16_op_local16(self, node) -> Optional[Type]:
        """P-5.6.3 + P-5.6.3b migration : `local_u16 = local_u16 OP local_u16`
        pour OP ∈ {+, -, &, |, ^}.

        Pattern :
          - target = u16 local Ident à XIY+off
          - value = BinOp(Ident_local_u16, OP, Ident_local_u16)
          - les 3 sont u16 locals à XIY-rel (pas banked, pas adecl-live)
          - OP ∈ byte-split family (HW-validé via legacy `_emit_alu16`)

        Émet OPTIMAL legacy text DIRECTLY (5 instr / 13 B vs legacy 17 B,
        savings 4 B/site) + structured ops (LoadLocal + LoadLocal + BinOp
        + StoreLocal) avec cls hints WA_ONLY/HL_ONLY/WA_ONLY.

          Legacy 17 B :              Optimal 13 B :
            LDW WA, (a)                LDW WA, (a)
            push WA                    LDW HL, (b)       ← direct load HW-validé
            LDW WA, (b)                op A, L
            push WA                    op-carry W, H
            pop HL                     LDW (t), WA
            pop WA
            op A, L
            op-carry W, H
            LDW (t), WA

        Encoding `LDW HL, (XIY+d)` = `db 0x9D <disp> 0x23` HW-validé
        pass 22 ("ok hardware" user). Tous les opcodes `op A,L` / `op-carry W,H`
        sont des CF/CE prefix (déjà shippés via _emit_alu16 legacy).

        Savings : 4 B/site. Pattern strict (3 idents locaux) reste rare en
        C réel, mais étendre les ops multiplie les sites.

        Returns Type or None (pattern non-matché → fall through).
        """
        if not self._opt_c5_use_structured:
            return None
        if self._opt_c5_regalloc == '0':
            return None
        # Target side: u16 local at XIY+off
        target_sym = self._direct_stack_store_sym(node.target)
        if target_sym is None or target_sym.reg_name:
            return None
        if self.type_size(target_sym.type_) != 2:
            return None
        if self._stack_base_reg(target_sym) != 'XIY':
            return None
        # Value side: BinOp(local_u16, OP, local_u16) for OP in byte-split family
        val = node.value
        if not isinstance(val, BinOp):
            return None
        op_entry = self._C5_BYTE_SPLIT_OPS.get(val.op)
        if op_entry is None:
            return None
        ir_op, line3, line4 = op_entry
        left_sym = self._resolve_local_u16_xiy_sym(val.left)
        if left_sym is None:
            return None
        right_sym = self._resolve_local_u16_xiy_sym(val.right)
        if right_sym is None:
            return None
        if not self._expr_is_pure(node.target):
            return None
        if self._lvalue_writes_cached_index(node.target):
            self._invalidate_elem_base_cache()

        off_a = left_sym.offset
        off_b = right_sym.offset
        off_t = target_sym.offset
        d_a = off_a & 0xFF
        d_b = off_b & 0xFF
        d_t = off_t & 0xFF

        # 1. Emit OPTIMAL legacy text DIRECTLY to self.lines (5 instr / 13 B).
        line1 = f'    db 0x9D, 0x{d_a:02X}, 0x20  ; LDW WA, (XIY{off_a:+d})'
        line2 = f'    db 0x9D, 0x{d_b:02X}, 0x23  ; LDW HL, (XIY{off_b:+d})'
        line5 = f'    db 0xBD, 0x{d_t:02X}, 0x50  ; LDW (XIY{off_t:+d}), WA'
        for ln in (line1, line2, line3, line4, line5):
            self.lines.append(ln)
            self._c4_p3_check_emit_invalidation(ln)

        # 2. Append STRUCTURED ops.
        # Naming convention : `%hl*` signals HL_ONLY (consumed by both
        # round-trip default in ir.py and pipeline cls hint).
        vreg_a = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_b = f'%hl{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        vreg_r = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        self._c5_vreg_cls[vreg_a] = 'WA_ONLY'
        self._c5_vreg_cls[vreg_b] = 'HL_ONLY'
        self._c5_vreg_cls[vreg_r] = 'WA_ONLY'
        self.ir_function.append(LoadLocal(dest=vreg_a, offset=off_a, width='u16'))
        self.ir_function.append(LoadLocal(dest=vreg_b, offset=off_b, width='u16'))
        self.ir_function.append(IRBinOp(dest=vreg_r, src_a=vreg_a, src_b=vreg_b,
                                       op=ir_op, width='u16'))
        self.ir_function.append(StoreLocal(offset=off_t, src=vreg_r, width='u16'))

        self._c5_stats['structured_emits'] += 1
        self._c5_stats['structured_emits_this_function'] += 1
        self._c5_stats['p5_6_3_emits'] = (
            self._c5_stats.get('p5_6_3_emits', 0) + 1
        )
        # Per-op subcount for stats / measurement.
        self._c5_stats[f'p5_6_3_op_{val.op}'] = (
            self._c5_stats.get(f'p5_6_3_op_{val.op}', 0) + 1
        )
        return target_sym.type_

    def _c5_try_emit_local16_local16_copy(self, node) -> Optional[Type]:
        """P-5.6.2 migration: `local_u16 = local_u16` copy via structured ops.

        Matches `local_a = local_b` where both are u16/s16 locals or params
        at XIY-relative offsets (not register-bank locals).

        Emits BOTH:
        - Legacy text (= same 2 instructions `_load_local` + `_store_local`
          would have produced) directly to `self.lines`.
        - Structured ops `LoadLocal` + `StoreLocal` to `self.ir_function`.

        The pipeline allocator picks XWA via `pref='XWA'` → output identical
        to legacy → shadow mode green.

        This is the SECOND migration after P-5.6.1 (`local16 = const_u16`).
        Body delta is ZERO in isolation (same 6 bytes) but exercises a new
        structured op (`LoadLocal`) end-to-end, building the foundation
        for richer migrations (e.g. `local = local OP local` in P-5.6.3+,
        which CAN deliver byte savings when the allocator avoids transit).

        Returns the resulting Type or `None` when the pattern doesn't match.
        """
        if not self._opt_c5_use_structured:
            return None
        if self._opt_c5_regalloc == '0':
            return None
        # Target side: u16 local at XIY+offset, not banked.
        target_sym = self._direct_stack_store_sym(node.target)
        if target_sym is None or target_sym.reg_name:
            return None
        if self.type_size(target_sym.type_) != 2:
            return None
        if self._stack_base_reg(target_sym) != 'XIY':
            return None
        # Source side: same shape from a value-context Ident.
        src_sym = self._resolve_local_u16_xiy_sym(node.value)
        if src_sym is None:
            return None
        if not self._expr_is_pure(node.target):
            return None
        if self._lvalue_writes_cached_index(node.target):
            self._invalidate_elem_base_cache()

        off_src = src_sym.offset
        off_dst = target_sym.offset
        d_src = off_src & 0xFF
        d_dst = off_dst & 0xFF

        # 1. Emit legacy text DIRECTLY to self.lines.
        # Format mirrors _load_local (sz==2 path, XIY base, op 0x9D) +
        # _store_local (sz==2 path, XIY base, op 0xBD).
        legacy_load_line = (
            f'    db 0x9D, 0x{d_src:02X}, 0x20  '
            f'; LDW WA, (XIY{off_src:+d})'
        )
        legacy_store_line = (
            f'    db 0xBD, 0x{d_dst:02X}, 0x50  '
            f'; LDW (XIY{off_dst:+d}), WA'
        )
        self.lines.append(legacy_load_line)
        self.lines.append(legacy_store_line)
        # `LDW WA, ...` clobbers any cached sym for WA — keep reg tracker honest.
        self._c4_p3_check_emit_invalidation(legacy_load_line)
        self._c4_p3_check_emit_invalidation(legacy_store_line)

        # 2. Append STRUCTURED ops.
        vreg = f'%t{self._c5_vreg_counter}'
        self._c5_vreg_counter += 1
        self.ir_function.append(LoadLocal(dest=vreg, offset=off_src, width='u16'))
        self.ir_function.append(StoreLocal(offset=off_dst, src=vreg, width='u16'))

        self._c5_stats['structured_emits'] += 1
        self._c5_stats['structured_emits_this_function'] += 1
        return target_sym.type_

    def gen_assign(self, node: Assign) -> Type:
        op = node.op

        # P-5.8 v7 Axe A (pass 37) : mem-form INC/DEC fast path for
        # compound `local += imm` / `local -= imm` where imm is small
        # (1..8). Emits `INCW #n, (XIY+d)` or `DECW #n, (XIY+d)` —
        # 3 bytes total vs ~13 bytes legacy (load + alu + store).
        # Triggers on `+=` / `-=` with Const(1..8) rhs and u16 XIY-rel
        # local target. Returns the local's type after emitting.
        if (op in ('+=', '-=') and self._opt_c5_memform_alu
                and isinstance(node.target, Ident)
                and isinstance(node.value, Const)
                and isinstance(node.value.value, int)):
            mag = abs(node.value.value)
            if 1 <= mag <= 8:
                sym = (self.local_vars.get(node.target.name)
                       or self.param_syms.get(node.target.name))
                if (sym is not None
                        and not sym.reg_name
                        and not sym.adecl_live_reg
                        and not isinstance(sym.type_, ArrayType)
                        and self.type_size(sym.type_) in (1, 2)
                        and self._stack_base_reg(sym) == 'XIY'
                        and -128 <= sym.offset <= 127):
                    sz = self.type_size(sym.type_)
                    prefix = 0x8D if sz == 1 else 0x9D
                    sign = 1 if op == '+=' else -1
                    if node.value.value < 0:
                        sign = -sign
                    delta = sign * mag
                    d = sym.offset & 0xFF
                    if delta > 0:
                        n_encoded = delta if delta < 8 else 0
                        sub_op = 0x60 + n_encoded
                        mnem_op = 'INC' if sz == 1 else 'INCW'
                        mnem = f'{mnem_op} {delta}, (XIY{sym.offset:+d})'
                    else:
                        n_encoded = (-delta) if (-delta) < 8 else 0
                        sub_op = 0x68 + n_encoded
                        mnem_op = 'DEC' if sz == 1 else 'DECW'
                        mnem = f'{mnem_op} {-delta}, (XIY{sym.offset:+d})'
                    self.emit_instr(
                        f'db 0x{prefix:02X}, 0x{d:02X}, 0x{sub_op:02X}'
                        f'  ; {mnem}  [v7 Axe A mem-form compound assign]'
                    )
                    if sz == 2:
                        self._lvt_invalidate_word_write('D', sym.offset)
                    else:
                        self._lvt_invalidate_byte_write('D', sym.offset)
                    return sym.type_

        if op == '=':
            # P-5.6.3 + P-5.6.3b: try the BinOp fast path FIRST (most
            # specific pattern). `local = local OP local` for OP ∈
            # {+, -, &, |, ^} — premier vrai body delta (−4 B/site)
            # via élimination du transit push/pop WA-HL grâce au load
            # direct HL (encoding `db 0x9D <disp> 0x23`, HW-validé pass 22).
            c5_ty = self._c5_try_emit_local16_eq_local16_op_local16(node)
            if c5_ty is not None:
                return c5_ty
            # P-5.6.4b: try local = local OP global pattern (one side global)
            c5_ty = self._c5_try_emit_local16_eq_local16_op_global16(node)
            if c5_ty is not None:
                return c5_ty
            # P-5.6.1: try the structured-ops fast path FIRST. Returns
            # None when env is off OR the pattern doesn't match — in
            # which case we fall through to the legacy paths below.
            c5_ty = self._c5_try_emit_local16_const(node)
            if c5_ty is not None:
                return c5_ty
            # P-5.6.2: local_u16 = local_u16 copy via structured ops.
            # Same shape contract as P-5.6.1 but with LoadLocal instead
            # of LoadImm for the source side.
            c5_ty = self._c5_try_emit_local16_local16_copy(node)
            if c5_ty is not None:
                return c5_ty
            direct_abs = self._direct_abs_scalar_symbol(node.target)
            const_u16 = self._small_const_u16(node.value)
            if direct_abs is not None and const_u16 is not None and self._expr_is_pure(node.target):
                direct_sym, direct_label = direct_abs
                if self._lvalue_writes_cached_index(node.target):
                    self._invalidate_elem_base_cache()
                self.emit_instr(f'ld   WA, {const_u16}')
                self._emit_direct_abs_scalar_store(direct_label, self.type_size(direct_sym.type_))
                return direct_sym.type_
            if const_u16 is not None and self._expr_is_pure(node.target):
                if self._lvalue_writes_cached_index(node.target):
                    self._invalidate_elem_base_cache()
                lval_ty = self.gen_lvalue_addr(node.target)
                store_sz = self.type_size(lval_ty) if lval_ty else 2
                self.emit_instr(f'ld   WA, {const_u16}')
                if store_sz == 4:
                    self.emit_instr('extz XWA')
                self._emit_store_to_de(store_sz)
                return lval_ty or U16
            direct_stack_sym = self._direct_stack_store_sym(node.target)
            if direct_stack_sym is not None:
                if self._lvalue_writes_cached_index(node.target):
                    self._invalidate_elem_base_cache()
                # P-5.8 v7.3 byte-narrow load : when target is u8
                # AND rhs is a simple expression that doesn't need
                # WA-wide intermediate (Ident or Const), we only need
                # A for the LDB store. Skip the `ld W, 0` zero-extend.
                # P-5.8 v7.4 (pass 40) : extended to BinOp rhs with
                # arith/bitwise op + simple leaf-expr operands. Sets
                # `_byte_narrow_alu` so `_emit_alu16` skips the second
                # byte half (`adc W, H`, `and W, H`, etc.). Saves 4 B
                # per site (2 from skipped `ld W, 0`, 2 from skipped
                # high-byte alu).
                store_sz_predict = self.type_size(direct_stack_sym.type_)
                use_byte_narrow_load = False
                use_byte_narrow_alu = False
                if store_sz_predict == 1:
                    if isinstance(node.value, (Ident, Const)):
                        use_byte_narrow_load = True
                    elif (isinstance(node.value, BinOp)
                            and node.value.op in ('+', '-', '&', '|', '^')
                            and isinstance(node.value.left, (Ident, Const))
                            and isinstance(node.value.right, (Ident, Const))):
                        # Both operands must be byte-narrow-safe types.
                        # Check via `_type_of` ; if either side is u32/ptr
                        # or addr-taken, fall back to full WA path.
                        lt = self._type_of(node.value.left)
                        rt = self._type_of(node.value.right)
                        # Allow IntType u8/u16 leaves (we discard the
                        # high byte either way for u8 sink).
                        if (isinstance(lt, IntType) and lt.nbytes <= 2
                                and isinstance(rt, IntType)
                                and rt.nbytes <= 2):
                            use_byte_narrow_load = True
                            use_byte_narrow_alu = True
                if use_byte_narrow_load or use_byte_narrow_alu:
                    prev_load = self._byte_narrow_load
                    prev_alu = self._byte_narrow_alu
                    self._byte_narrow_load = use_byte_narrow_load
                    self._byte_narrow_alu = use_byte_narrow_alu
                    try:
                        val_ty = self.gen_expr(node.value)
                    finally:
                        self._byte_narrow_load = prev_load
                        self._byte_narrow_alu = prev_alu
                else:
                    val_ty = self.gen_expr(node.value)
                val_sz = self.type_size(val_ty) if val_ty else 2
                store_sz = self.type_size(direct_stack_sym.type_)
                if store_sz == 4 and val_sz <= 2:
                    if isinstance(val_ty, IntType) and val_ty.signed:
                        self.emit_instr('db 0xE8, 0x13  ; exts XWA (sign-extend WA->XWA)')
                    else:
                        self.emit_instr('extz XWA')
                    val_sz = 4
                if val_sz == store_sz or (val_sz <= 2 and store_sz <= 2):
                    self._store_local(direct_stack_sym)
                    return direct_stack_sym.type_
            if direct_abs is not None:
                direct_sym, direct_label = direct_abs
                if self._lvalue_writes_cached_index(node.target):
                    self._invalidate_elem_base_cache()
                self.gen_expr(node.value)
                self._emit_direct_abs_scalar_store(direct_label, self.type_size(direct_sym.type_))
                return direct_sym.type_
            if self._lvalue_writes_cached_index(node.target):
                self._invalidate_elem_base_cache()
            # Evaluate value first
            val_ty = self.gen_expr(node.value)
            sz = self.type_size(val_ty) if val_ty else 2
            # Push result
            if sz == 4:
                self.emit_instr('push XWA')
            else:
                self.emit_instr('push WA')
            # Get lvalue address into XDE
            lval_ty = self.gen_lvalue_addr(node.target)
            # Restore value
            if sz == 4:
                self.emit_instr('pop  XWA')
            else:
                self.emit_instr('pop  WA')
            # Store
            store_sz = self.type_size(lval_ty) if lval_ty else sz
            self._emit_store_to_de(store_sz)
            return lval_ty or val_ty or U16
        else:
            if self.opt_perf_lag_4:
                fast_ty = self._try_compound_assign_scalar_fastpath(node)
                if fast_ty is not None:
                    return fast_ty
            # Compound assignment: x += y → x = x + y
            base_op = op[:-1]  # strip '='
            equiv = BinOp(base_op, node.target, node.value, line=node.line)
            return self.gen_assign(Assign('=', node.target, equiv, line=node.line))

    def gen_cast(self, node: Cast) -> Type:
        ty = self.gen_expr(node.expr)
        target = node.type_
        tsz = self.type_size(target)
        src_sz = self.type_size(ty) if ty else 2
        if tsz == 1 and src_sz > 1:
            # truncate to byte: keep A, zero W
            # 'and WA, 0x00FF' = D0 prefix → broken silicon. Use safe HL-register form.
            self.emit_instr('ld   HL, 0x00FF')  # L=0xFF H=0x00
            self.emit_instr('and  A,  L')        # CF C1 — keep low byte
            self.emit_instr('and  W,  H')        # CE C0 — zero high byte
        elif tsz == 2 and src_sz == 4:
            # truncate to u16: ld WA, WA (nop; just use lower WA)
            pass
        elif tsz == 4 and src_sz <= 2:
            if isinstance(ty, IntType) and ty.signed:
                self.emit_instr('db 0xE8, 0x13  ; exts XWA (sign-extend WA->XWA)')
                return target
            # zero-extend WA to 32-bit XWA.
            # NOTE: 'ld XWA, 0' would clear WA before we can use it — use extz instead.
            self.emit_instr('extz XWA')     # E8 12 — WA (16-bit) → XWA zero-extend (safe E8+r form)
        return target

    def gen_subscript(self, node: Subscript) -> Type:
        """Generate code for base[index], loading value into WA."""
        lval_ty = self.gen_lvalue_addr(node)
        sz = self.type_size(lval_ty) if lval_ty else 2
        self._emit_load_from_de(sz)
        self._maybe_sign_extend_loaded_scalar(lval_ty)
        return lval_ty or U16

    def gen_deref(self, node: Deref) -> Type:
        """Generate *ptr, loading value into A/WA.
        Far ptr (NGP_FAR): XWA = 32-bit ROM address → deref via (XWA+0), no XDE copy.
        Near ptr: XWA contains 16-bit address → push/pop to XDE (avoids broken ld XDE,XWA).
        """
        ptr_ty = self.gen_expr(node.expr)
        if isinstance(ptr_ty, PtrType):
            elem_ty = ptr_ty.base
            sz = self.type_size(elem_ty)
            if ptr_ty.far:
                # XWA = far address; deref directly — no need to copy to XDE
                self._emit_load_from_xwa(sz)
            else:
                # Near ptr: ld XDE,XWA is broken (D8 prefix r+r). Use push/pop.
                self.emit_instr('push XWA')   # 0x38 — safe r32 push
                self.emit_instr('pop  XDE')   # 0x4A — safe r32 pop
                self._emit_load_from_de(sz)
            self._maybe_sign_extend_loaded_scalar(elem_ty)
            return elem_ty
        # Unknown pointer type: assume near u16
        self.emit_instr('push XWA')
        self.emit_instr('pop  XDE')
        self._emit_load_from_de(2)
        return U16

    def gen_addrof(self, node: AddrOf) -> Type:
        """Generate &var, loading address into XWA.
        ld XWA,XDE is broken (32-bit r+r D8+sub≥80). Use push/pop.
        """
        elem_ty = self.gen_lvalue_addr(node.expr)
        if self._xiy_sym_pending is not None:
            sym = self._xiy_sym_pending
            self._xiy_sym_pending = None
            self._emit_stack_sym_addr_to_xwa(sym)
            self.emit_instr('push XWA')
            self.emit_instr('pop  XDE')
        elif self._xde_field_offset != 0:
            if self._xde_addr_is_far:
                self._apply_far_field_offset(self._xde_field_offset)
            else:
                self._apply_near_field_offset(self._xde_field_offset)
            self._xde_field_offset = 0
        elif self._mem_base_reg == 'XBC':
            # XBC holds the address (from _cache_near_symbol_base, byte_off=0 path).
            # XDE is stale (e.g. clobbered by a prior function call). Sync XDE from XBC.
            # Triggered by &arr[0] where arr is a near global struct array.
            self.emit_instr('push XBC')
            self.emit_instr('pop  XDE')
        self.emit_instr('push XDE')
        self.emit_instr('pop  XWA')
        self._xde_cached_ptr_key = None
        return PtrType(elem_ty or U16, far=self._is_lvalue_far(node.expr))
        self.emit_instr('push XDE')   # 0x3A — safe r32 push
        self.emit_instr('pop  XWA')   # 0x48 — safe r32 pop
        return PtrType(elem_ty or U16)

    # -- Struct support --

    def _type_of(self, node) -> Optional[Type]:
        """Statically determine type of expression without emitting code."""
        if isinstance(node, Const):
            return node.type_ or U16
        if isinstance(node, Ident):
            name = node.name
            if name in self.local_vars:
                return self.local_vars[name].type_
            if name in self.param_syms:
                return self.param_syms[name].type_
            if name in self.sem.globals:
                return self.sem.globals[name].type_
            return None
        if isinstance(node, FuncCall):
            if node.name in self.sem.func_decls:
                return self.sem.func_decls[node.name].ret_type
            return U16
        if isinstance(node, Cast):
            return node.type_
        if isinstance(node, Deref):
            pt = self._type_of(node.expr)
            if isinstance(pt, PtrType):
                return pt.base
            return None
        if isinstance(node, AddrOf):
            base_ty = self._type_of(node.expr)
            if base_ty is None:
                return None
            return PtrType(base_ty, far=self._is_lvalue_far(node.expr))
        if isinstance(node, FieldAccess):
            base_ty = self._type_of(node.expr)
            if node.is_arrow and isinstance(base_ty, PtrType):
                base_ty = base_ty.base
            if isinstance(base_ty, StructType):
                for f in base_ty.fields:
                    if f.name == node.field:
                        return f.type_
            return None
        if isinstance(node, Subscript):
            arr_ty = self._type_of(node.base)
            if isinstance(arr_ty, ArrayType):
                return arr_ty.elem
            if isinstance(arr_ty, PtrType):
                return arr_ty.base
            return None
        return None

    def _is_lvalue_far(self, node) -> bool:
        """Best-effort static answer for whether &node yields a far pointer."""
        if isinstance(node, Ident):
            name = node.name
            if name in self.sem.globals:
                return self.sem.globals[name].is_far
            return False
        if isinstance(node, FieldAccess):
            if node.is_arrow:
                base_ty = self._type_of(node.expr)
                return isinstance(base_ty, PtrType) and base_ty.far
            return self._is_lvalue_far(node.expr)
        if isinstance(node, Subscript):
            base_ty = self._type_of(node.base)
            if isinstance(base_ty, PtrType):
                return base_ty.far
            return self._is_lvalue_far(node.base)
        if isinstance(node, Deref):
            ptr_ty = self._type_of(node.expr)
            return isinstance(ptr_ty, PtrType) and ptr_ty.far
        if isinstance(node, Cast):
            return isinstance(node.type_, PtrType) and node.type_.far
        return False

    def _resolve_field(self, node: 'FieldAccess'):
        """Return (field_type, field_offset) for a FieldAccess node, or (None, 0)."""
        if node.is_arrow:
            base_ty = self._type_of(node.expr)
            struct_ty = base_ty.base if isinstance(base_ty, PtrType) else base_ty
        else:
            struct_ty = self._type_of(node.expr)
        if isinstance(struct_ty, StructType):
            for f in struct_ty.fields:
                if f.name == node.field:
                    return f.type_, f.offset
        return None, 0

    def _invalidate_elem_base_cache(self):
        self._xbc_cached_elem_key = None
        self._xbc_cached_elem_offset = 0

    def _expr_cache_identity(self, node):
        if isinstance(node, Const) and isinstance(node.value, int):
            return ('const', int(node.value) & 0xFFFFFFFF)
        if isinstance(node, Ident):
            name = node.name
            if name in self.local_vars:
                return ('local', self.local_vars[name].offset)
            if name in self.param_syms:
                return ('param', self.param_syms[name].offset)
            if name in self.static_local_globals:
                return ('static', self.static_local_globals[name].name)
            if name in self.sem.globals:
                return ('global', name)
        return None

    def _cacheable_struct_array_elem_key(self, node):
        if not isinstance(node, Subscript):
            return None
        if not isinstance(node.base, Ident):
            return None
        base_name = node.base.name
        base_id = None
        arr_ty = None
        if base_name in self.local_vars:
            sym = self.local_vars[base_name]
            if isinstance(sym.type_, ArrayType):
                base_id = ('local_array', sym.offset)
                arr_ty = sym.type_
        elif base_name in self.static_local_globals:
            sym = self.static_local_globals[base_name]
            if isinstance(sym.type_, ArrayType) and not sym.is_far:
                base_id = ('static_array', sym.name)
                arr_ty = sym.type_
        elif base_name in self.sem.globals:
            sym = self.sem.globals[base_name]
            if isinstance(sym.type_, ArrayType) and not sym.is_far:
                base_id = ('global_array', base_name)
                arr_ty = sym.type_
        idx_id = self._expr_cache_identity(node.index)
        if base_id is None or arr_ty is None or idx_id is None:
            return None
        if not isinstance(arr_ty.elem, StructType):
            return None
        return ('struct_array_elem', base_id, idx_id, self.type_size(arr_ty.elem))

    def _cacheable_near_struct_base_key(self, node):
        if not isinstance(node, Ident):
            return None
        name = node.name
        if name in self.static_local_globals:
            sym = self.static_local_globals[name]
            if not sym.is_far and isinstance(sym.type_, StructType):
                return ('near_struct_base', ('static', sym.name))
        if name in self.sem.globals:
            sym = self.sem.globals[name]
            if not sym.is_far and isinstance(sym.type_, StructType):
                return ('near_struct_base', ('global', name))
        return None

    def _lvalue_writes_cached_index(self, target) -> bool:
        if self._xbc_cached_elem_key is None:
            return False
        if self._xbc_cached_elem_key[0] != 'struct_array_elem':
            return False
        idx_id = self._xbc_cached_elem_key[2]
        target_id = self._expr_cache_identity(target)
        if target_id is not None:
            return target_id == idx_id
        if isinstance(target, Deref):
            return True
        if isinstance(target, FieldAccess) and target.is_arrow:
            return True
        if isinstance(target, Subscript) and not isinstance(target.base, Ident):
            return True
        return False

    def _is_hl_safe_expr(self, node) -> bool:
        """Return True if gen_expr(node) is guaranteed NOT to clobber HL/H/L registers.
        Used to enable the right-first binop fast path (saves 2 push/pop vs generic path).

        Safe cases:
          - Const           : ld WA, imm  — no HL touch
          - Ident scalar    : LDW WA,(XIY+d) or abs16 load — no HL touch
                             (array-decay uses add A,L / adc W,H → CLOBBERS HL → excluded)
          - Cast of safe    : only changes interpretation, same gen_expr path
        All other nodes (BinOp, FuncCall, Subscript, etc.) may emit _emit_copy_wa_to_hl
        internally and are conservatively treated as unsafe.
        """
        if isinstance(node, Const):
            return True
        if isinstance(node, Ident):
            name = node.name
            # Array-to-pointer decay: uses 'add A,L; adc W,H' with HL = offset → HL clobbered
            if name in self.local_vars:
                return not isinstance(self.local_vars[name].type_, ArrayType)
            if name in self.param_syms:
                return not isinstance(self.param_syms[name].type_, ArrayType)
            if name in self.sem.globals:
                sym = self.sem.globals[name]
                if isinstance(sym.type_, ArrayType):
                    return False
                # Near global: ld WA,0; push WA; ld WA,lbl; push WA; pop XDE; ldw WA,(xde+0)
                # Far global:  ld XIZ,lbl; push XIZ; pop XDE; ldw WA,(xde+0)
                # Neither path touches HL ✓
                return True
            if name in self.static_local_globals:
                return True   # same near-global path — no HL touch
            # Function name used as rvalue: ld XIZ,_name; push XIZ; pop XWA — no HL touch ✓
            return True
        if isinstance(node, Cast):
            return self._is_hl_safe_expr(node.expr)
        if isinstance(node, FieldAccess):
            if node.is_arrow:
                return False
            field_ty, _ = self._resolve_field(node)
            if field_ty is None or isinstance(field_ty, ArrayType):
                return False
            base = node.expr
            if isinstance(base, Ident):
                name = base.name
                if name in self.local_vars or name in self.param_syms:
                    return True
                if name in self.static_local_globals:
                    return True
                if name in self.sem.globals:
                    return True
                return False
            if isinstance(base, Subscript):
                cache_key = self._cacheable_struct_array_elem_key(base)
                return cache_key is not None and self._xbc_cached_elem_key == cache_key
            if isinstance(base, FieldAccess):
                return self._is_hl_safe_expr(base)
            return False
        return False

    def _small_const_u16(self, node):
        if isinstance(node, Const) and isinstance(node.value, int):
            v = int(node.value)
            if -32768 <= v <= 65535:
                return v & 0xFFFF
        return None

    def _expr_is_pure(self, node):
        if node is None:
            return True
        if isinstance(node, (Const, Ident)):
            return True
        if isinstance(node, Cast):
            return self._expr_is_pure(node.expr)
        if isinstance(node, FieldAccess):
            return self._expr_is_pure(node.expr)
        if isinstance(node, Subscript):
            return self._expr_is_pure(node.base) and self._expr_is_pure(node.index)
        if isinstance(node, Deref):
            return self._expr_is_pure(node.expr)
        if isinstance(node, UnaryOp):
            return node.op in ('+', '-', '~', '!') and self._expr_is_pure(node.expr)
        if isinstance(node, BinOp):
            return self._expr_is_pure(node.left) and self._expr_is_pure(node.right)
        if isinstance(node, Ternary):
            return (self._expr_is_pure(node.cond)
                    and self._expr_is_pure(node.then)
                    and self._expr_is_pure(node.else_))
        return False

    def _expr_preserves_xbc_cache(self, node):
        """Return True when gen_expr(node) can keep the current XBC cache live.
        Used by XBC-backed compound assigns to avoid an extra outer push/pop XBC
        when the RHS already preserves (or does not touch) the cached base.
        """
        if node is None:
            return True
        if isinstance(node, (Const, Ident)):
            return True
        if isinstance(node, Cast):
            return self._expr_preserves_xbc_cache(node.expr)
        if isinstance(node, UnaryOp):
            return node.op in ('+', '-', '~', '!') and self._expr_preserves_xbc_cache(node.expr)
        if isinstance(node, BinOp):
            return (self._expr_preserves_xbc_cache(node.left)
                    and self._expr_preserves_xbc_cache(node.right))
        if isinstance(node, Ternary):
            return (self._expr_preserves_xbc_cache(node.cond)
                    and self._expr_preserves_xbc_cache(node.then)
                    and self._expr_preserves_xbc_cache(node.else_))
        if isinstance(node, FieldAccess):
            if node.is_arrow:
                return self._expr_preserves_xbc_cache(node.expr)
            base = node.expr
            if isinstance(base, Subscript):
                cache_key = self._cacheable_struct_array_elem_key(base)
                return cache_key is not None and self._xbc_cached_elem_key == cache_key
            if isinstance(base, Ident):
                cache_key = self._cacheable_near_struct_base_key(base)
                return cache_key is not None and self._xbc_cached_elem_key == cache_key
            if isinstance(base, FieldAccess):
                return self._expr_preserves_xbc_cache(base)
            return False
        if isinstance(node, FuncCall):
            return (all(self._expr_is_pure(arg) for arg in node.args)
                    and all(self._expr_preserves_xbc_cache(arg) for arg in node.args))
        if isinstance(node, IndirectCall):
            return (self._expr_is_pure(node.callee)
                    and all(self._expr_is_pure(arg) for arg in node.args)
                    and self._expr_preserves_xbc_cache(node.callee)
                    and all(self._expr_preserves_xbc_cache(arg) for arg in node.args))
        return False

    def _try_compound_assign_scalar_fastpath(self, node: Assign) -> Optional[Type]:
        """Emit a bounded fast path for scalar 8/16-bit compound assignments."""
        if node.op not in ('+=', '-=', '&=', '|=', '^='):
            return None

        # P-5.6.3c: try the C5 structured-ops compound fast path FIRST.
        # Catches `local_u16 OP= local_u16` and emits optimal 13 B sequence
        # (vs legacy fast path B's 15 B). Savings 2 B per matched site.
        c5_ty = self._c5_try_emit_compound_local16_op_local16(node)
        if c5_ty is not None:
            return c5_ty
        # P-5.6.4: `local_u16 OP= global_u16` (near scalar).
        # Emits optimal 14 B sequence (vs legacy fast path B's 16 B).
        # Savings 2 B per matched site. Sites BEAUCOUP plus communs que
        # local-only car les globals sont partout en NGPC code.
        c5_ty = self._c5_try_emit_compound_local16_op_global16(node)
        if c5_ty is not None:
            return c5_ty

        lval_ty = self.typeof_expr(node.target)
        if not isinstance(lval_ty, IntType) or lval_ty.nbytes > 2:
            return None

        rhs_ty = self.typeof_expr(node.value)
        if isinstance(rhs_ty, PtrType) or self.type_size(rhs_ty) > 2:
            return None

        if self._lvalue_writes_cached_index(node.target):
            self._invalidate_elem_base_cache()

        store_sz = self.type_size(lval_ty)
        base_op = node.op[:-1]

        # XIY-relative local/param target: keep the direct path fully local.
        direct_stack_sym = self._direct_stack_store_sym(node.target)
        if direct_stack_sym is not None:
            # Fast path A: RHS is a small constant → 0 push/pop.
            # x += 1 / x -= 5 / x &= 0xFF etc: WA=old, LD HL,imm, op, store.
            rhs_const = self._small_const_u16(node.value)
            if rhs_const is not None:
                self.gen_ident(node.target)                # WA = current value
                self.emit_instr(f'ld   HL, {rhs_const}')  # HL = const (no stack)
                self._emit_alu16(base_op)                  # WA op= HL
                self._store_local(direct_stack_sym)        # write back
                return lval_ty
            # Fast path B: RHS is HL-safe scalar → 1 push+pop (via copy_wa_to_hl).
            # x += y / x |= flag: gen RHS→WA, copy to HL, gen old value→WA (HL-safe), op, store.
            # gen_ident(local) = _load_local = db 0x9D,d,0x20 → does NOT clobber HL ✓
            if self._is_hl_safe_expr(node.value):
                rhs_peekty = self.typeof_expr(node.value)
                if self.type_size(rhs_peekty) <= 2 and not isinstance(rhs_peekty, PtrType):
                    self.gen_expr(node.value)              # WA = RHS
                    self._emit_copy_wa_to_hl()             # HL = RHS  (1 push + 1 pop)
                    self.gen_ident(node.target)            # WA = old value (HL unchanged)
                    self._emit_alu16(base_op)              # WA op= HL
                    self._store_local(direct_stack_sym)    # write back
                    return lval_ty
            # Generic local path: 2 push + 2 pop.
            self.gen_ident(node.target)
            self.emit_instr('push WA')
            self.gen_expr(node.value)
            self._emit_copy_wa_to_hl()
            self.emit_instr('pop  WA')
            self._emit_alu16(base_op)
            self._store_local(direct_stack_sym)
            return lval_ty

        direct_abs = self._direct_abs_scalar_symbol(node.target)
        if direct_abs is not None:
            _, direct_label = direct_abs
            rhs_const = self._small_const_u16(node.value)
            if (self.opt_perf_lag_8 and rhs_const is not None and
                    store_sz <= 2 and base_op in ('+', '-', '&', '|', '^')):
                self._emit_direct_abs_mem_alu_const(base_op, direct_label, store_sz, rhs_const)
                self.gen_ident(node.target)
                return lval_ty
            if rhs_const is not None:
                self.gen_ident(node.target)
                self.emit_instr(f'ld   HL, {rhs_const}')
                self._emit_alu16(base_op)
                self._emit_direct_abs_scalar_store(direct_label, store_sz)
                return lval_ty
            # mem-reg fast path: gen_expr(RHS)→WA, op (sym),WA/A, reload.
            # No HL needed → cheaper than HL-safe path (no copy) and generic (no push/pop).
            if (self.opt_perf_lag_8 and store_sz <= 2 and
                    base_op in ('+', '-', '&', '|', '^')):
                rhs_peekty = self.typeof_expr(node.value)
                if self.type_size(rhs_peekty) <= 2 and not isinstance(rhs_peekty, PtrType):
                    reg = 'WA' if store_sz == 2 else 'A'
                    self.gen_expr(node.value)
                    self._emit_direct_abs_mem_alu_reg(base_op, direct_label, store_sz, reg)
                    self.gen_ident(node.target)
                    return lval_ty
            self.gen_ident(node.target)
            self.emit_instr('push WA')
            self.gen_expr(node.value)
            self._emit_copy_wa_to_hl()
            self.emit_instr('pop  WA')
            self._emit_alu16(base_op)
            self._emit_direct_abs_scalar_store(direct_label, store_sz)
            return lval_ty

        # General memory lvalue path.
        self.gen_lvalue_addr(node.target)
        if self._xiy_sym_pending is not None:
            return None
        if self._mem_base_reg == 'XBC' and not self._xde_addr_is_far:
            saved_field_off = self._xde_field_offset
            saved_cache_key = self._xbc_cached_elem_key
            saved_cache_off = self._xbc_cached_elem_offset
            self._emit_load_from_de(store_sz, preserve_lvalue=True)
            self._maybe_sign_extend_loaded_scalar(lval_ty)
            rhs_const = self._small_const_u16(node.value)
            if rhs_const is not None:
                self.emit_instr(f'ld   HL, {rhs_const}')
            elif self._expr_preserves_xbc_cache(node.value):
                self.emit_instr('push WA')
                self.gen_expr(node.value)
                self._emit_copy_wa_to_hl()
                self.emit_instr('pop  WA')
                self._xbc_cached_elem_key = saved_cache_key
                self._xbc_cached_elem_offset = saved_cache_off
            else:
                # Keep the cached near struct base live across RHS evaluation so
                # hot field updates like s_enemies[i].y += s_enemies[i].step_dir
                # can stay on direct (XBC+disp) loads/stores.
                self.emit_instr('push XBC')
                self.emit_instr('push WA')
                self.gen_expr(node.value)
                self._emit_copy_wa_to_hl()
                self.emit_instr('pop  WA')
                self.emit_instr('pop  XBC')
                self._xbc_cached_elem_key = saved_cache_key
                self._xbc_cached_elem_offset = saved_cache_off
            self._mem_base_reg = 'XBC'
            self._xde_field_offset = saved_field_off
            self._xiy_sym_pending = None
            self._xde_addr_is_far = False
            self._emit_alu16(base_op)
            self._emit_store_to_de(store_sz)
            return lval_ty
        if self._mem_base_reg == 'XBC' or self._xde_field_offset != 0:
            if self._xde_addr_is_far:
                self._apply_far_field_offset(self._xde_field_offset)
            else:
                self._apply_near_field_offset(self._xde_field_offset)
        self._xde_field_offset = 0
        self._mem_base_reg = 'XDE'

        # Fast path C: RHS is a constant → 0 push/pop.
        # _emit_load_from_de reads (XDE+0), does NOT modify XDE.
        # _emit_store_to_de writes (XDE+0), does NOT modify XDE.
        # So XDE is preserved across load+op+store: no push XDE needed.
        # e->timer -= 1 / e->hp -= dmg(const) → typical shmup hotpath.
        rhs_const = self._small_const_u16(node.value)
        if rhs_const is not None:
            self._emit_load_from_de(store_sz, preserve_lvalue=True)  # WA = old, XDE intact
            self._maybe_sign_extend_loaded_scalar(lval_ty)
            self.emit_instr(f'ld   HL, {rhs_const}')                 # HL = const  (no stack)
            self._emit_alu16(base_op)                                 # WA op= HL
            self._mem_base_reg = 'XDE'
            self._xde_field_offset = 0
            self._emit_store_to_de(store_sz)                         # store via XDE (still valid)
            return lval_ty

        # Generic: push XDE to survive RHS evaluation, 2 push WA for old/RHS swap.
        self.emit_instr('push XDE')
        self._emit_load_from_de(store_sz)
        self._maybe_sign_extend_loaded_scalar(lval_ty)
        self.emit_instr('push WA')
        self.gen_expr(node.value)
        self._emit_copy_wa_to_hl()
        self.emit_instr('pop  WA')
        self._emit_alu16(base_op)
        self.emit_instr('pop  XDE')
        self._mem_base_reg = 'XDE'
        self._xde_field_offset = 0
        self._emit_store_to_de(store_sz)
        return lval_ty

    def _direct_stack_store_sym(self, node):
        if not isinstance(node, Ident):
            return None
        name = node.name
        if name in self.local_vars:
            return self.local_vars[name]
        if name in self.param_syms:
            return self.param_syms[name]
        return None

    def _cache_near_symbol_base(self, cache_key, sym_name: str):
        if self._xbc_cached_elem_key != cache_key:
            self._emit_label_addr_to('XBC', f'_{sym_name}', 'near array base')
            self._xbc_cached_elem_key = cache_key
        self._mem_base_reg = 'XBC'
        self._xde_addr_is_far = False

    def gen_field_access(self, node: 'FieldAccess') -> Type:
        """Generate code to load a struct field value into WA/A/XWA."""
        field_ty = self.gen_lvalue_addr(node)
        sz = self.type_size(field_ty) if field_ty else 2
        self._emit_load_from_de(sz)
        self._maybe_sign_extend_loaded_scalar(field_ty)
        return field_ty or U16

    def gen_expr_bool(self, node, label_target: str, negate: bool = False):
        """
        Generate code that jumps to label_target based on expression truth.
        negate=True  → jump if expression is FALSE (for 'if cond → skip body on false')
        negate=False → jump if expression is TRUE
        """
        # Branch-oriented short-circuit for boolean trees.
        # This avoids materializing intermediate 0/1 values in WA for conditions like:
        #   if (!a && !b && (x < y || z))
        # which are common in the audio runtime hot paths.
        if isinstance(node, UnaryOp) and node.op == '!':
            self.gen_expr_bool(node.expr, label_target, negate=not negate)
            return

        if isinstance(node, BinOp) and node.op == '&&':
            if negate:
                # Jump on FALSE: left false or right false.
                self.gen_expr_bool(node.left, label_target, negate=True)
                self.gen_expr_bool(node.right, label_target, negate=True)
            else:
                # Jump on TRUE: left must be true, then right must be true.
                label_skip = self.fresh_label('and_skip')
                self.gen_expr_bool(node.left, label_skip, negate=True)
                self.gen_expr_bool(node.right, label_target, negate=False)
                self.emit_label(label_skip)
            return

        if isinstance(node, BinOp) and node.op == '||':
            if negate:
                # Jump on FALSE: left must be false, then right must be false.
                label_skip = self.fresh_label('or_skip')
                self.gen_expr_bool(node.left, label_skip, negate=False)
                self.gen_expr_bool(node.right, label_target, negate=True)
                self.emit_label(label_skip)
            else:
                # Jump on TRUE: left true or right true.
                self.gen_expr_bool(node.left, label_target, negate=False)
                self.gen_expr_bool(node.right, label_target, negate=False)
            return

        # Cheap 8-bit compare-against-constant path.
        # This follows the official codegen more closely on hot checks like:
        #   if (s_enemies[i].type == 3u) ...
        #   if (s_enemies[i].phase == 0u) ...
        #   if (s_enemies[i].step_timer > 0u) ...
        if isinstance(node, BinOp) and node.op in ('==', '!=', '<', '<=', '>', '>='):
            expr = None
            op = node.op
            const_val = None
            if isinstance(node.right, Const) and isinstance(node.right.value, int):
                expr = node.left
                const_val = int(node.right.value)
            elif isinstance(node.left, Const) and isinstance(node.left.value, int):
                expr = node.right
                const_val = int(node.left.value)
                swap = {'<': '>', '<=': '>=', '>': '<', '>=': '<=', '==': '==', '!=': '!='}
                op = swap[op]
            if expr is not None and const_val is not None:
                expr_ty = self._type_of(expr)
                direct_abs = self._direct_abs_int_symbol(expr)
                if self.opt_perf_lag_8 and direct_abs is not None and isinstance(expr_ty, IntType):
                    _, direct_label = direct_abs
                    sz = self.type_size(expr_ty)
                    if sz == 1:
                        if expr_ty.signed:
                            const_fits = (-128 <= const_val <= 127) and op in ('==', '!=')
                        else:
                            const_fits = 0 <= const_val <= 255
                        if const_fits:
                            if negate:
                                inv = {'==': '!=', '!=': '==', '<': '>=', '<=': '>', '>': '<=', '>=': '<'}
                                op = inv.get(op, op)
                            self.emit_instr(f'cpb  ({direct_label}), {const_val & 0xFF}')
                            # Phase P-2: cpb (mem), 0 vs unsigned mem-byte
                            # → carry flag never set; elide dead `jrl C` etc.
                            if (const_val & 0xFF) == 0 and not expr_ty.signed:
                                self._emit_cmp_after_zero_branch(
                                    op, label_target, signed=False,
                                )
                            else:
                                self._emit_cmp_after_cp_branch(op, label_target)
                            return
                    elif sz == 2:
                        if expr_ty.signed:
                            const_fits = (-32768 <= const_val <= 32767) and op in ('==', '!=')
                        else:
                            const_fits = 0 <= const_val <= 65535
                        if const_fits:
                            if negate:
                                inv = {'==': '!=', '!=': '==', '<': '>=', '<=': '>', '>': '<=', '>=': '<'}
                                op = inv.get(op, op)
                            self.emit_instr(f'cpw  ({direct_label}), {const_val & 0xFFFF}')
                            if (const_val & 0xFFFF) == 0 and not expr_ty.signed:
                                self._emit_cmp_after_zero_branch(
                                    op, label_target, signed=False,
                                )
                            else:
                                self._emit_cmp_after_cp_branch(op, label_target)
                            return
                if isinstance(expr_ty, IntType) and expr_ty.nbytes == 1:
                    const_fits = False
                    if expr_ty.signed:
                        const_fits = (-128 <= const_val <= 127) and op in ('==', '!=')
                    else:
                        const_fits = 0 <= const_val <= 255
                    if const_fits:
                        if negate:
                            inv = {'==': '!=', '!=': '==', '<': '>=', '<=': '>', '>': '<=', '>=': '<'}
                            op = inv.get(op, op)
                        # P-5.8 v7.3 byte-narrow load : the subsequent
                        # `_emit_cmp_u8_const_branch` emits `cp A, imm`
                        # or `or A, A` — neither reads W. So gen_expr
                        # for `expr` (a u8 value) can SKIP the `ld W, 0`
                        # zero-extend. Saves 2 B per u8 cmp-vs-const
                        # site (one of the highest-frequency patterns
                        # per comparative disasm).
                        prev_narrow = self._byte_narrow_load
                        self._byte_narrow_load = True
                        try:
                            self.gen_expr(expr)
                        finally:
                            self._byte_narrow_load = prev_narrow
                        # Phase P-2: pass signedness through so the zero
                        # case can elide dead unsigned `< 0` branches.
                        self._emit_cmp_u8_const_branch(
                            op, const_val, label_target,
                            signed=bool(expr_ty.signed),
                        )
                        return

        # Cheap compare-to-zero path for small scalar values.
        # This trims many hot checks like:
        #   if (u8_counter > 0) ...
        #   if (flag == 0) ...
        if isinstance(node, BinOp) and node.op in ('==', '!=', '<', '<=', '>', '>='):
            expr = None
            op = node.op
            if isinstance(node.right, Const) and node.right.value == 0:
                expr = node.left
            elif isinstance(node.left, Const) and node.left.value == 0:
                expr = node.right
                swap = {'<': '>', '<=': '>=', '>': '<', '>=': '<=', '==': '==', '!=': '!='}
                op = swap[op]
            if expr is not None:
                expr_ty = self._type_of(expr)
                if isinstance(expr_ty, IntType) and expr_ty.nbytes <= 2:
                    if op in ('==', '!=') or not expr_ty.signed:
                        # P-5.8 v7.3 byte-narrow load for u8 cmp-to-zero :
                        # if expr is u8, substitute `or A, W` → `or A, A`
                        # which only tests A (doesn't need W=0). Same
                        # size (2 B) but enables byte-narrow gen_expr.
                        # For u16 we still need `or A, W` and full WA.
                        is_byte = expr_ty.nbytes == 1
                        if is_byte:
                            prev_narrow = self._byte_narrow_load
                            self._byte_narrow_load = True
                            try:
                                self.gen_expr(expr)
                            finally:
                                self._byte_narrow_load = prev_narrow
                            self.emit_instr('or   A,  A')
                        else:
                            self.gen_expr(expr)
                            self.emit_instr('or   A,  W')
                        truth = None
                        if op == '==':
                            truth = 'Z'
                        elif op == '!=':
                            truth = 'NZ'
                        elif op == '>':
                            truth = 'NZ'
                        elif op == '<=':
                            truth = 'Z'
                        elif op == '>=' and not expr_ty.signed:
                            truth = 'ALWAYS'
                        elif op == '<' and not expr_ty.signed:
                            truth = 'NEVER'
                        if truth == 'ALWAYS':
                            if not negate:
                                self.emit_instr(f'jp   {label_target}')
                            return
                        if truth == 'NEVER':
                            if negate:
                                self.emit_instr(f'jp   {label_target}')
                            return
                        if truth is not None:
                            cc = truth
                            if negate:
                                cc = 'NZ' if truth == 'Z' else 'Z'
                            self.emit_instr(f'jrl  {cc},{label_target}')
                            return

        if isinstance(node, BinOp) and node.op in ('==', '!=', '<', '<=', '>', '>='):
            expr = None
            op = node.op
            const_imm = None
            const_ty = None
            if self._small_const_u16(node.right) is not None:
                expr = node.left
                const_imm = self._small_const_u16(node.right)
                const_ty = self.typeof_expr(node.right)
            elif self._small_const_u16(node.left) is not None:
                expr = node.right
                const_imm = self._small_const_u16(node.left)
                const_ty = self.typeof_expr(node.left)
                swap = {'<': '>', '<=': '>=', '>': '<', '>=': '<=', '==': '==', '!=': '!='}
                op = swap[op]
            if expr is not None and const_imm is not None:
                # Peek size BEFORE emitting expr: this fast path only handles
                # sz<=2. Without the peek, sz=4 falls through to the generic
                # path below which re-emits expr → duplicate LDW (same class
                # of bug as gen_unary / gen_binop imm_rhs path).
                expr_peekty = self.typeof_expr(expr)
                expr_peeksz = max(self.type_size(expr_peekty) if expr_peekty else 2, 2)
                if expr_peeksz <= 2:
                    left_ty = self.gen_expr(expr)
                    self.emit_instr(f'ld   HL, {const_imm}')
                    if negate:
                        inv = {'==': '!=', '!=': '==', '<': '>=', '<=': '>', '>': '<=', '>=': '<'}
                        op = inv.get(op, op)
                    signed_cmp = self._cmp_is_signed(left_ty, const_ty)
                    if signed_cmp and op in ('<', '<=', '>', '>='):
                        self._emit_cmp16_signed_branch(op, label_target)
                    else:
                        self._emit_alu16(op)
                        cc = self._CMP16_CC[op]
                        self.emit_instr(f'jrl  {cc}, {label_target}')
                    return

        # Special-case comparisons for better codegen
        if isinstance(node, BinOp) and node.op in ('==', '!=', '<', '<=', '>', '>='):
            if self.opt_perf_lag_1 and self._is_hl_safe_expr(node.left):
                left_peekty = self.typeof_expr(node.left)
                sz_peek = max(self.type_size(left_peekty) if left_peekty else 2, 2)
                if sz_peek <= 2 and not isinstance(left_peekty, PtrType):
                    right_ty = self.gen_expr(node.right)
                    self._emit_copy_wa_to_hl()
                    left_ty = self.gen_expr(node.left)
                    op = node.op
                    if negate:
                        inv = {'==': '!=', '!=': '==', '<': '>=', '<=': '>', '>': '<=', '>=': '<'}
                        op = inv.get(op, op)
                    signed_cmp = self._cmp_is_signed(left_ty, right_ty)
                    if signed_cmp and op in ('<', '<=', '>', '>='):
                        self._emit_cmp16_signed_branch(op, label_target)
                    else:
                        self._emit_alu16(op)
                        cc = self._CMP16_CC[op]
                        self.emit_instr(f'jrl  {cc}, {label_target}')
                    return
            left_ty  = self.gen_expr(node.left)
            sz = max(self.type_size(left_ty) if left_ty else 2, 2)
            self.emit_instr('push WA' if sz < 4 else 'push XWA')
            right_ty = self.gen_expr(node.right)
            if sz == 4:
                self._emit_copy_xwa_to_xhl()   # push XWA; pop XHL (long r+r LD broken too)
                self.emit_instr('pop  XWA')
            else:
                self._emit_copy_wa_to_hl()     # push WA; pop HL (word r+r LD broken)
                self.emit_instr('pop  WA')
            op = node.op
            if negate:
                inv = {'==': '!=', '!=': '==', '<': '>=', '<=': '>', '>': '<=', '>=': '<'}
                op = inv.get(op, op)
            signed_cmp = self._cmp_is_signed(left_ty, right_ty)
            if sz == 4:
                self.emit_instr('cp   XWA, XHL')
                cc = self._cmp_to_cc(op, signed=signed_cmp)
                self.emit_instr(f'jrl  {cc}, {label_target}')
            else:
                if signed_cmp and op in ('<', '<=', '>', '>='):
                    self._emit_cmp16_signed_branch(op, label_target)
                else:
                    self._emit_alu16(op)         # byte-split sets flags
                    cc = self._CMP16_CC[op]
                    self.emit_instr(f'jrl  {cc}, {label_target}')
            return

        # General case: evaluate, test for zero
        self.gen_expr(node)
        self.emit_instr('or   A,  W')
        if negate:
            self.emit_instr(f'jrl  Z,{label_target}')
        else:
            self.emit_instr(f'jrl  NZ,{label_target}')

    # -- Final output --

    def get_output(self, source_name: str) -> str:
        out = list(self.lines)

        # String constants in f_code
        if self.string_consts:
            out.append('')
            out.append('; String constants')
            for lab, s in self.string_consts:
                out.append(f'{lab}:')
                escaped = s.replace('\\', '\\\\').replace('"', '\\"')
                # Emit as bytes
                byte_vals = [str(ord(c)) for c in s] + ['0']
                out.append(f'    db {", ".join(byte_vals)}')

        # Const data section (ROM — arrays/tables initialized at compile time)
        if self.const_vars:
            out.append('')
            out.append('    f_const section data large')
            out.append('')
            for v in self.const_vars:
                if not v.is_static:
                    out.append(f'    public  _{v.name}')
                out.append(f'_{v.name}:')
                if isinstance(v.init_expr, InitList):
                    v.type_ = self._infer_unsized_array_type(v.type_, v.init_expr)
                    pieces = self._build_init_pieces(v.type_, v.init_expr)
                    self._emit_init_pieces(out, pieces)
                else:
                    # Scalar const (Const(N) or similar): emit db/dw/dl directly.
                    sz = self.type_size(v.type_)
                    try:
                        val = self._const_init_operand(v.init_expr, v.type_)
                    except Exception:
                        val = 0
                    dir_ = {1: 'db', 2: 'dw', 4: 'dl'}.get(sz, 'dw')
                    out.append(f'    {dir_} {val}')

        # BSS section
        if self.bss_vars:
            out.append('')
            out.append('    f_area section data large')
            out.append('')
            for v in self.bss_vars:
                if not v.is_static:
                    out.append(f'    public  _{v.name}')
                out.append(f'_{v.name}:')
                sz = self.type_size(v.type_)
                if isinstance(v.type_, StructType):
                    out.append(f'    dsb {sz}     ; struct {v.type_.tag} ({sz} bytes)')
                elif isinstance(v.type_, ArrayType):
                    out.append(f'    dsb {sz}     ; {v.type_}')
                elif sz == 1:
                    out.append(f'    dsb 1       ; u8')
                elif sz == 4:
                    out.append(f'    dsl 1       ; u32')
                else:
                    out.append(f'    dsw 1       ; u16')

        # Data section (initialized globals)
        if self.data_vars:
            out.append('')
            out.append('    f_data section data large')
            out.append('')
            for v in self.data_vars:
                if not v.is_static:
                    out.append(f'    public  _{v.name}')
                out.append(f'_{v.name}:')
                v.type_ = self._infer_unsized_array_type(v.type_, v.init_expr)
                sz = self.type_size(v.type_)
                if isinstance(v.init_expr, InitList):
                    pieces = self._build_init_pieces(v.type_, v.init_expr)
                    self._emit_init_pieces(out, pieces)
                else:
                    try:
                        val = self._const_init_operand(v.init_expr, v.type_)
                    except Exception:
                        val = None
                    if val is None:
                        # Non-constant init: emit zero + comment
                        out.append(f'    dw 0   ; NOTE: non-constant init not supported')
                    elif sz == 1:
                        out.append(f'    db {val}')
                    elif sz == 4:
                        out.append(f'    dl {val}')
                    else:
                        out.append(f'    dw {val}')

        out.append('')
        out.append('    end')
        return '\n'.join(out) + '\n'

# ---------------------------------------------------------------------------
# Preprocessor — #include inlining
# ---------------------------------------------------------------------------

def preprocess_includes(source: str, src_path: str, include_dirs=None, _seen=None) -> str:
    """Inline #include "filename" directives (relative to src file, then include_dirs).
    Tracks included paths to avoid duplicates (poor-man's include guard)."""
    import re
    if _seen is None:
        _seen = set()
    if include_dirs is None:
        include_dirs = []

    src_dir = os.path.dirname(os.path.abspath(src_path))
    search_dirs = [src_dir] + include_dirs

    lines = source.split('\n')
    result = []
    for line in lines:
        m = re.match(r'^\s*#\s*include\s+"([^"]+)"', line)
        if m:
            inc_name = m.group(1)
            inc_path = None
            for d in search_dirs:
                candidate = os.path.join(d, inc_name)
                if os.path.isfile(candidate):
                    inc_path = os.path.abspath(candidate)
                    break
            if inc_path and inc_path not in _seen:
                _seen.add(inc_path)
                try:
                    with open(inc_path, 'r', encoding='utf-8') as f:
                        inc_src = f.read()
                    inc_expanded = preprocess_includes(inc_src, inc_path, include_dirs, _seen)
                    result.append(f'/* begin {inc_name} */\n{inc_expanded}\n/* end {inc_name} */')
                except OSError:
                    result.append(line)  # file not found: keep line as comment fodder
            else:
                result.append(f'/* #include "{inc_name}" skipped */')
        else:
            result.append(line)
    return '\n'.join(result)

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compile_c(source: str, source_name: str, include_dirs=None) -> str:
    """Full pipeline: C source → assembly string."""
    # 0. Inline #include directives
    source = preprocess_includes(source, source_name, include_dirs)

    # 1. Lex
    tokens = lex(source, source_name)

    # 2. Parse
    parser = Parser(tokens)
    decls = parser.parse_program()

    # 3. Semantic pass
    sem = SemanticPass(decls)
    sem.run()

    # 4. Code generation
    cg = CodeGen(source_name, decls, sem, struct_defs=parser.struct_defs)
    cg.generate()

    # P-5.6.1 wiring: emit C5 pipeline stats to stderr when active.
    # Useful for tracking how many structured emits the migration
    # produced and how many functions had vreg spills under the
    # convex-hull liveness limitation.
    if cg._opt_c5_regalloc != '0' and os.environ.get('T900CC_C5_STATS', '0') != '0':
        import sys as _sys
        print(f't900cc[C5 stats] {source_name}: '
              f'fns={cg._c5_stats["functions_processed"]} '
              f'intervals={cg._c5_stats["intervals_total"]} '
              f'phys_runs={cg._c5_stats.get("phys_disjoint_runs", 0)} '
              f'spills={cg._c5_stats["spills_total"]} '
              f'shadow_mismatches={cg._c5_stats["shadow_mismatches"]} '
              f'structured_emits={cg._c5_stats["structured_emits"]} '
              f'p5_6_3={cg._c5_stats.get("p5_6_3_emits", 0)} '
              f'p5_6_3c={cg._c5_stats.get("p5_6_3c_emits", 0)} '
              f'p5_6_4={cg._c5_stats.get("p5_6_4_emits", 0)} '
              f'p5_6_4b={cg._c5_stats.get("p5_6_4b_emits", 0)} '
              f'fns_with_structured={cg._c5_stats["functions_with_structured_emits"]} '
              f'shadow_skipped_structured={cg._c5_stats["shadow_skipped_structured"]} '
              f'shadow_skipped_vreg_spilled={cg._c5_stats.get("shadow_skipped_vreg_spilled", 0)}',
              file=_sys.stderr)

    # 5. Emit
    return cg.get_output(source_name)


def main():
    ap = argparse.ArgumentParser(
        description='t900cc — C to TLCS-900 Assembly Compiler (NGPCraft Toolchain v1)'
    )
    ap.add_argument('input', help='Input .c file')
    ap.add_argument('-o', '--output', help='Output .asm file (default: stdout)')
    ap.add_argument('-I', dest='include_dirs', action='append', default=[],
                    metavar='DIR', help='Add include search directory')
    ap.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    ap.add_argument('--cdecl-legacy', action='store_true',
                    help='Force legacy cdecl ABI (all args pushed to stack). '
                         'Default since 2026-05-19 is __adecl v2 (args in XWA/XBC/XDE). '
                         'Use this for compatibility with hand-written cdecl ASM modules.')
    args = ap.parse_args()

    if args.cdecl_legacy:
        os.environ['T900CC_ABI_ADECL'] = '0'

    src_path = args.input
    if not os.path.isfile(src_path):
        print(f't900cc: error: file not found: {src_path}', file=sys.stderr)
        sys.exit(1)

    with open(src_path, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        asm = compile_c(source, src_path, include_dirs=args.include_dirs)
    except (LexError, ParseError, SemanticError, CodeGenError) as e:
        print(f't900cc: {e}', file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(asm)
        if args.verbose:
            print(f't900cc: wrote {args.output}', file=sys.stderr)
    else:
        sys.stdout.write(asm)


if __name__ == '__main__':
    main()
