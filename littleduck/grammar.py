"""Syntax analysis: the LR grammar and the semantic actions attached to it.

The compiler is single pass. Besides recognizing the language, the rules below
fill the symbol table, consult the semantic cube and emit quadruples. Rules
that derive to epsilon (named ``Mark...``) exist only to run an action at a
precise point of a production -- when the condition of an ``if`` has just been
evaluated, when the body of a function is about to start, and so on.
"""

import ply.yacc as yacc

from .context import CONTEXT
from .errors import TooManyErrors
from .lexer import tokens  # noqa: F401  (yacc reads the token list from here)
from .semantics import result_type

start = 'Program'

# Cap on syntax errors. Past this point the parser is assumed to be stuck in a
# recovery loop and the run is abandoned.
MAX_SYNTAX_ERRORS = 50


def _column(lexpos):
    """Column (1-based) of a position in the source text."""
    source = CONTEXT.source
    if not source:
        return lexpos + 1
    line_start = source.rfind('\n', 0, lexpos)
    return lexpos + 1 if line_start < 0 else lexpos - line_start


def _line(p, index):
    """Line (1-based) of symbol ``index``, or 0 when PLY has no position."""
    try:
        return p.lineno(index) or 0
    except (AttributeError, IndexError):
        return 0


def _track_line(p, index):
    """Remember the line being parsed, for errors raised by epsilon markers."""
    line = _line(p, index)
    if line:
        CONTEXT.current_line = line
    return line


# --- Program ---------------------------------------------------------------

def p_ProgramHeader(p):
    "ProgramHeader : PROGRAM IDENTIFIER"
    CONTEXT.functions.declare_program(p[2])
    # The first quadruple jumps to the main body; its target is patched by
    # MarkMain, once the functions have been generated.
    CONTEXT.goto_main_index = CONTEXT.emit_pending_jump('gotomain')


def p_MarkMain(p):
    "MarkMain :"
    if CONTEXT.goto_main_index is not None:
        CONTEXT.patch(CONTEXT.goto_main_index, CONTEXT.next_quad())
    entry = CONTEXT.functions.program_entry
    if entry is not None:
        entry.start_quad = CONTEXT.next_quad()


def p_Program(p):
    ("Program : ProgramHeader SEMICOLON OptVars FunctionList "
     "MAIN MarkMain Body END")
    CONTEXT.emit('end', None, None, None, '-')


# --- Variable declarations -------------------------------------------------

def p_OptVars_present(p):
    "OptVars : VarSection"


def p_OptVars_empty(p):
    "OptVars : empty"


def p_VarSection(p):
    "VarSection : VAR VarDeclList"


def p_VarDeclList_more(p):
    "VarDeclList : VarDeclList VarDecl"


def p_VarDeclList_one(p):
    "VarDeclList : VarDecl"


def p_VarDecl(p):
    "VarDecl : IdList COLON Type SEMICOLON"
    CONTEXT.declare_variables(CONTEXT.pending_ids, p[3], _line(p, 2))
    CONTEXT.pending_ids = []


def p_IdList_more(p):
    "IdList : IdList COMMA IDENTIFIER"
    CONTEXT.pending_ids.append(p[3])


def p_IdList_one(p):
    "IdList : IDENTIFIER"
    CONTEXT.pending_ids.append(p[1])


# --- Types -----------------------------------------------------------------

def p_Type_int(p):
    "Type : INT"
    p[0] = 'int'


def p_Type_float(p):
    "Type : FLOAT"
    p[0] = 'float'


def p_Type_string(p):
    "Type : STRING"
    p[0] = 'string'


# --- Functions -------------------------------------------------------------

def p_FunctionList_more(p):
    "FunctionList : FunctionList Function"


def p_FunctionList_empty(p):
    "FunctionList : empty"


def p_FunctionHeader(p):
    "FunctionHeader : ReturnType IDENTIFIER"
    # As soon as the return type and the name are known the function is added
    # to the directory and becomes the current scope, so its parameters and
    # locals land in its own tables.
    return_type, name = p[1], p[2]
    line = _line(p, 2)
    p[0] = name
    directory = CONTEXT.functions

    if name in directory.entries:
        if directory.is_function(name) or name == directory.program_name:
            CONTEXT.semantic_error(
                "Semantic error: function '%s' is already declared" % name,
                line)
        # The scope is switched anyway, so the body still parses.
        directory.push_scope(name)
        return

    if name in directory.global_variables():
        CONTEXT.semantic_error(
            "Semantic error: function '%s' cannot share its name with a "
            "variable" % name, line)

    # A function owns one global slot of its return type, used to hand the
    # returned value back to the caller.
    address = CONTEXT.memory.allocate('global', return_type, name=name)
    directory.declare_function(name, return_type, address, line)
    directory.push_scope(name)
    # Locals and temporaries of this function start at the base of their
    # region; the counters of the enclosing scope are saved until it ends.
    CONTEXT.memory.enter_function()


def p_ReturnType_void(p):
    "ReturnType : VOID"
    p[0] = 'void'


def p_ReturnType_typed(p):
    "ReturnType : Type"
    p[0] = p[1]


def p_OptParams_present(p):
    "OptParams : ParamList"


def p_OptParams_empty(p):
    "OptParams : empty"


def p_ParamList_more(p):
    "ParamList : ParamList COMMA Param"


def p_ParamList_one(p):
    "ParamList : Param"


def p_Param(p):
    "Param : IDENTIFIER COLON Type"
    CONTEXT.declare_parameter(p[1], p[3], _line(p, 1))


def p_MarkFunctionBody(p):
    "MarkFunctionBody :"
    # Parameters and locals are in place, so the next quadruple is the first
    # one of the body: that is where a call to this function jumps.
    entry = CONTEXT.functions.current_entry
    if entry is not None:
        entry.start_quad = CONTEXT.next_quad()
    CONTEXT.return_jumps.append([])


def p_Function(p):
    ("Function : FunctionHeader LPAREN OptParams RPAREN LBRACKET OptVars "
     "MarkFunctionBody Body RBRACKET SEMICOLON")
    entry = CONTEXT.functions.current_entry
    if entry is not None and entry.is_function:
        if entry.return_type != 'void' and not entry.has_return:
            CONTEXT.semantic_error(
                "Semantic error: function '%s' returns %s and must have at "
                "least one 'return' with a value"
                % (entry.name, entry.return_type), entry.declaration_line)

    end_quad = CONTEXT.next_quad()
    CONTEXT.emit('endfun', None, None, None, '-')
    if CONTEXT.return_jumps:
        for index in CONTEXT.return_jumps.pop():
            CONTEXT.patch(index, end_quad)
    CONTEXT.functions.pop_scope()
    CONTEXT.memory.exit_function()


# --- Statements ------------------------------------------------------------

def p_Body(p):
    "Body : LBRACE StatementList RBRACE"


def p_StatementList_more(p):
    "StatementList : StatementList Statement"


def p_StatementList_empty(p):
    "StatementList : empty"


def p_Statement(p):
    """Statement : Assignment
                 | Condition
                 | Loop
                 | Call
                 | Print
                 | ReturnStatement
                 | BreakStatement"""


# --- Assignment ------------------------------------------------------------

def p_Assignment(p):
    "Assignment : IDENTIFIER OP_ASSIGN Expression SEMICOLON"
    target = p[1]
    line = _line(p, 1)
    variable = CONTEXT.functions.lookup_variable(target)
    if variable is None:
        CONTEXT.semantic_error(
            "Semantic error: variable '%s' is not declared" % target, line)
        CONTEXT.pop_operand()
        return
    operand = CONTEXT.pop_operand()
    if operand is None:
        return
    value, value_type = operand
    if result_type(variable.type, '=', value_type) == 'error':
        if value_type != 'error':
            CONTEXT.semantic_error(
                "Semantic error: cannot assign %s to '%s' (%s)"
                % (value_type, target, variable.type), line)
        return
    CONTEXT.emit('=', value, None, target, variable.type)


# --- Conditionals ----------------------------------------------------------

def p_MarkIfCondition(p):
    "MarkIfCondition :"
    if not CONTEXT.operands:
        return
    condition = CONTEXT.check_condition('if')
    CONTEXT.jumps.append(CONTEXT.emit_pending_jump('gotof', condition))


def p_Condition_if(p):
    "Condition : IF LPAREN Expression RPAREN MarkIfCondition Body SEMICOLON"
    if CONTEXT.jumps:
        CONTEXT.patch(CONTEXT.jumps.pop(), CONTEXT.next_quad())


def p_MarkElse(p):
    "MarkElse :"
    # End of the true branch: jump over the else, and point the pending gotof
    # at the first quadruple of the else.
    goto_index = CONTEXT.emit_pending_jump('goto')
    if CONTEXT.jumps:
        CONTEXT.patch(CONTEXT.jumps.pop(), CONTEXT.next_quad())
    CONTEXT.jumps.append(goto_index)


def p_Condition_if_else(p):
    ("Condition : IF LPAREN Expression RPAREN MarkIfCondition Body MarkElse "
     "ELSE Body SEMICOLON")
    if CONTEXT.jumps:
        CONTEXT.patch(CONTEXT.jumps.pop(), CONTEXT.next_quad())


# --- Loop ------------------------------------------------------------------

def p_MarkLoopStart(p):
    "MarkLoopStart :"
    # The next quadruple is the first one of the body: where gotot comes back to.
    CONTEXT.jumps.append(CONTEXT.next_quad())
    CONTEXT.break_jumps.append([])


def p_MarkLoopCondition(p):
    "MarkLoopCondition :"
    if not CONTEXT.operands:
        if CONTEXT.jumps:
            CONTEXT.jumps.pop()
        if CONTEXT.break_jumps:
            CONTEXT.break_jumps.pop()
        return
    condition = CONTEXT.check_condition('while')
    start = CONTEXT.jumps.pop() if CONTEXT.jumps else None
    CONTEXT.emit('gotot', condition, None, start, '-')
    exit_quad = CONTEXT.next_quad()
    for index in (CONTEXT.break_jumps.pop() if CONTEXT.break_jumps else []):
        CONTEXT.patch(index, exit_quad)


def p_Loop(p):
    ("Loop : DO MarkLoopStart Body WHILE LPAREN Expression RPAREN "
     "MarkLoopCondition SEMICOLON")


def p_BreakStatement(p):
    "BreakStatement : BREAK SEMICOLON"
    if not CONTEXT.break_jumps:
        CONTEXT.semantic_error("Semantic error: 'break' outside of a loop",
                               _line(p, 1))
        return
    CONTEXT.break_jumps[-1].append(CONTEXT.emit_pending_jump('goto'))


# --- Return ----------------------------------------------------------------

def p_ReturnStatement_value(p):
    "ReturnStatement : RETURN Expression SEMICOLON"
    line = _line(p, 1)
    entry = CONTEXT.functions.current_entry
    if entry is None or not entry.is_function:
        CONTEXT.semantic_error(
            "Semantic error: 'return' outside of a function", line)
        CONTEXT.pop_operand()
        return
    operand = CONTEXT.pop_operand()
    if operand is None:
        return
    value, value_type = operand
    if entry.return_type == 'void':
        CONTEXT.semantic_error(
            "Semantic error: a 'void' function must not return a value", line)
        return
    if result_type(entry.return_type, '=', value_type) == 'error':
        if value_type != 'error':
            CONTEXT.semantic_error(
                "Semantic error: incompatible return type, expected %s but "
                "got %s" % (entry.return_type, value_type), line)
        return
    entry.has_return = True
    # The value is copied into the function's global slot and control jumps to
    # the endfun, whose position is only known once the function is closed.
    CONTEXT.emit('return', value, None, entry.name, entry.return_type)
    _push_return_jump()


def p_ReturnStatement_void(p):
    "ReturnStatement : RETURN SEMICOLON"
    line = _line(p, 1)
    entry = CONTEXT.functions.current_entry
    if entry is None or not entry.is_function:
        CONTEXT.semantic_error(
            "Semantic error: 'return' outside of a function", line)
        return
    if entry.return_type != 'void':
        CONTEXT.semantic_error(
            "Semantic error: the function must return a value of type %s"
            % entry.return_type, line)
        return
    _push_return_jump()


def _push_return_jump():
    """Emit the jump to the end of the function and leave it to be patched."""
    index = CONTEXT.emit_pending_jump('goto')
    if CONTEXT.return_jumps:
        CONTEXT.return_jumps[-1].append(index)


# --- Print -----------------------------------------------------------------

def p_Print(p):
    "Print : PRINT LPAREN PrintArgList RPAREN SEMICOLON"
    # Every print ends with a line break of its own.
    CONTEXT.emit('newline', None, None, None, '-')


def p_PrintArgList_more(p):
    "PrintArgList : PrintArgList COMMA PrintArg"


def p_PrintArgList_one(p):
    "PrintArgList : PrintArg"


def p_PrintArg(p):
    "PrintArg : Expression"
    operand = CONTEXT.pop_operand()
    if operand is not None:
        CONTEXT.emit('print', operand[0], None, None, '-')


# --- Function calls --------------------------------------------------------

def p_MarkCallStart(p):
    "MarkCallStart :"
    # p[-2] is the IDENTIFIER two symbols back, i.e. the name being called.
    CONTEXT.start_call(p[-2])


def p_Call(p):
    "Call : IDENTIFIER LPAREN MarkCallStart OptArgs RPAREN SEMICOLON"
    CONTEXT.finish_call(p[1], line=_line(p, 1))


def p_OptArgs_present(p):
    "OptArgs : ArgList"


def p_OptArgs_empty(p):
    "OptArgs : empty"


def p_ArgList_more(p):
    "ArgList : ArgList COMMA Expression"
    CONTEXT.collect_argument()


def p_ArgList_one(p):
    "ArgList : Expression"
    CONTEXT.collect_argument()


# --- Expressions -----------------------------------------------------------

def p_Expression_relational(p):
    "Expression : Exp RelOp Exp"
    CONTEXT.apply_binary(p[2])


def p_Expression_simple(p):
    "Expression : Exp"


def p_RelOp(p):
    """RelOp : OP_GT
             | OP_LT
             | OP_GE
             | OP_LE
             | OP_EQ
             | OP_NE"""
    p[0] = p[1]


def p_Exp_plus(p):
    "Exp : Exp OP_PLUS Term"
    CONTEXT.apply_binary('+')


def p_Exp_minus(p):
    "Exp : Exp OP_MINUS Term"
    CONTEXT.apply_binary('-')


def p_Exp_term(p):
    "Exp : Term"


def p_Term_mult(p):
    "Term : Term OP_MULT Factor"
    CONTEXT.apply_binary('*')


def p_Term_div(p):
    "Term : Term OP_DIV Factor"
    CONTEXT.apply_binary('/')


def p_Term_factor(p):
    "Term : Factor"


def p_Factor_parenthesized(p):
    "Factor : LPAREN Expression RPAREN"


def p_Factor_unary_plus(p):
    "Factor : OP_PLUS Atom"
    CONTEXT.apply_unary('u+')


def p_Factor_unary_minus(p):
    "Factor : OP_MINUS Atom"
    CONTEXT.apply_unary('u-')


def p_Factor_atom(p):
    "Factor : Atom"


def p_Atom_identifier(p):
    "Atom : IDENTIFIER"
    line = _track_line(p, 1)
    variable = CONTEXT.functions.lookup_variable(p[1])
    if variable is None:
        CONTEXT.semantic_error(
            "Semantic error: variable '%s' is not declared" % p[1], line)
        CONTEXT.push_error_operand()
    else:
        CONTEXT.push_operand(p[1], variable.type)


def p_Atom_constant(p):
    "Atom : Constant"


def p_Atom_call(p):
    "Atom : CallExpression"


def p_CallExpression(p):
    "CallExpression : IDENTIFIER LPAREN MarkCallStart OptArgs RPAREN"
    # The function must exist and return a value before a temporary is created
    # to hold the result of the call.
    name = p[1]
    line = _track_line(p, 1)
    entry = CONTEXT.functions.get(name)
    if entry is None or not entry.is_function:
        CONTEXT.finish_call(name, line=line)
        CONTEXT.push_error_operand()
        return
    if entry.return_type == 'void':
        CONTEXT.semantic_error(
            "Semantic error: void function '%s' does not produce a value "
            "usable in an expression" % name, line)
        CONTEXT.finish_call(name, line=line)
        CONTEXT.push_error_operand()
        return
    temporary = CONTEXT.new_temporary(entry.return_type)
    _, ok = CONTEXT.finish_call(name, result_temporary=temporary, line=line)
    if ok:
        CONTEXT.push_operand(temporary, entry.return_type)
    else:
        CONTEXT.push_error_operand()


# --- Constants -------------------------------------------------------------

def p_Constant_int(p):
    "Constant : CONST_INT"
    _track_line(p, 1)
    CONTEXT.memory.constant(p[1], 'int')
    CONTEXT.push_operand(p[1], 'int')


def p_Constant_float(p):
    "Constant : CONST_FLOAT"
    _track_line(p, 1)
    CONTEXT.memory.constant(p[1], 'float')
    CONTEXT.push_operand(p[1], 'float')


def p_Constant_string(p):
    "Constant : CONST_STR"
    _track_line(p, 1)
    CONTEXT.memory.constant(p[1], 'string')
    CONTEXT.push_operand(p[1], 'string')


def p_empty(p):
    "empty :"


# --- Syntax error recovery -------------------------------------------------

def p_VarDecl_error(p):
    "VarDecl : error SEMICOLON"
    CONTEXT.errors.add_recovery_note(
        "  -> recovered at ';' (inside a variable declaration)")


def p_Statement_error(p):
    "Statement : error SEMICOLON"
    CONTEXT.errors.add_recovery_note(
        "  -> recovered at ';' (inside a statement)")


def p_Body_error(p):
    "Body : LBRACE error RBRACE"
    CONTEXT.errors.add_recovery_note(
        "  -> recovered at '}' (inside a block)")


def p_Function_error(p):
    "Function : error SEMICOLON"
    CONTEXT.errors.add_recovery_note(
        "  -> recovered at ';' (inside a function signature or body)")
    # The failed function may have left its scope open.
    CONTEXT.functions.pop_scope()


def p_error(p):
    if p is None:
        CONTEXT.errors.add_syntax("Syntax error: unexpected end of file")
        return None
    CONTEXT.errors.add_syntax(
        "Syntax error at line %d, column %d: unexpected token %s ('%s')"
        % (p.lineno, _column(p.lexpos), p.type, p.value))
    if len(CONTEXT.errors.syntax) >= MAX_SYNTAX_ERRORS:
        raise TooManyErrors("too many syntax errors; aborting")
    # Drop the offending token so the parser makes progress instead of looping
    # through the same recovery point.
    parser.errok()
    return parser.token()


def build_parser():
    return yacc.yacc(debug=False, write_tables=False)


parser = build_parser()
