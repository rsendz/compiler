"""Lexical analysis: token definitions and the PLY lexer built from them."""

import ply.lex as lex

from .context import CONTEXT

# Reserved words, mapped from lexeme to token type. ``t_IDENTIFIER`` reclassifies
# an identifier whose text happens to be one of these.
RESERVED = {
    'program': 'PROGRAM',
    'var': 'VAR',
    'main': 'MAIN',
    'end': 'END',
    'int': 'INT',
    'float': 'FLOAT',
    'string': 'STRING',
    'bool': 'BOOL',
    'void': 'VOID',
    'if': 'IF',
    'else': 'ELSE',
    'do': 'DO',
    'while': 'WHILE',
    'print': 'PRINT',
    'return': 'RETURN',
    'break': 'BREAK',
    'true': 'TRUE',
    'false': 'FALSE',
    'and': 'OP_AND',
    'or': 'OP_OR',
    'not': 'OP_NOT',
}

tokens = [
    'IDENTIFIER',
    'CONST_INT',
    'CONST_FLOAT',
    'CONST_STR',
    'OP_GE',       # >=
    'OP_LE',       # <=
    'OP_EQ',       # ==
    'OP_NE',       # !=
    'OP_GT',       # >
    'OP_LT',       # <
    'OP_ASSIGN',   # =
    'OP_PLUS',     # +
    'OP_MINUS',    # -
    'OP_MULT',     # *
    'OP_DIV',      # /
    'SEMICOLON',   # ;
    'COMMA',       # ,
    'COLON',       # :
    'LBRACE',      # {
    'RBRACE',      # }
    'LBRACKET',    # [
    'RBRACKET',    # ]
    'LPAREN',      # (
    'RPAREN',      # )
] + list(RESERVED.values())

# Two-character operators are listed first so they win over their prefixes.
t_OP_GE = r'>='
t_OP_LE = r'<='
t_OP_EQ = r'=='
t_OP_NE = r'!='
t_OP_GT = r'>'
t_OP_LT = r'<'
t_OP_ASSIGN = r'='
t_OP_PLUS = r'\+'
t_OP_MINUS = r'-'
t_OP_MULT = r'\*'
t_OP_DIV = r'/'
t_SEMICOLON = r';'
t_COMMA = r','
t_COLON = r':'
t_LBRACE = r'\{'
t_RBRACE = r'\}'
t_LBRACKET = r'\['
t_RBRACKET = r'\]'
t_LPAREN = r'\('
t_RPAREN = r'\)'

t_ignore = ' \t'


def t_BAD_IDENTIFIER(t):
    # An identifier that starts with a digit or an underscore. This rule comes
    # before CONST_INT so that "12abc" is one bad identifier and not an integer
    # followed by a name.
    r'[0-9]+[a-zA-Z_][a-zA-Z0-9_]*|_[a-zA-Z0-9_]*'
    CONTEXT.errors.add_lexical(
        "Lexical error at line %d: invalid identifier '%s' at position %d "
        "(identifiers must start with a letter)"
        % (t.lexer.lineno, t.value, t.lexpos))
    # No token is returned: the lexeme is dropped and scanning continues.


def t_CONST_FLOAT(t):
    r'[0-9]+\.[0-9]+'
    t.value = float(t.value)
    return t


def t_CONST_INT(t):
    r'[0-9]+'
    t.value = int(t.value)
    return t


def t_CONST_STR(t):
    r'"[^"]*"'
    return t


def t_IDENTIFIER(t):
    r'[a-zA-Z][a-zA-Z0-9_]*'
    t.type = RESERVED.get(t.value, 'IDENTIFIER')
    return t


def t_COMMENT(t):
    r'\#.*'


def t_newline(t):
    r'\n+'
    t.lexer.lineno += len(t.value)


def t_error(t):
    CONTEXT.errors.add_lexical(
        "Lexical error at line %d: unrecognized symbol '%s' at position %d"
        % (t.lexer.lineno, t.value[0], t.lexpos))
    t.lexer.skip(1)


def build_lexer():
    return lex.lex()


lexer = build_lexer()
