"""The state shared by every compilation phase.

The parser is a single pass: it checks types, fills the symbol table and emits
quadruples while it reduces. All of that state lives here so the grammar rules
in :mod:`littleduck.grammar` stay short and readable, and so a fresh
compilation starts from a clean slate by calling :meth:`CompilationContext.reset`.
"""

from .errors import ErrorLog
from .memory import MemorySpace
from .quadruples import QuadrupleList
from .semantics import negation_type, result_type
from .symbols import FunctionDirectory, Variable

# An operand whose type could not be determined. It is pushed instead of a real
# value so that parsing can continue, and it never triggers a second error.
ERROR_OPERAND = ('error', 'error')


class CompilationContext:
    """Symbol table, memory, quadruples and the parser's working stacks."""

    def __init__(self):
        self.errors = ErrorLog()
        self.memory = MemorySpace(self.errors)
        self.functions = FunctionDirectory()
        self.quads = QuadrupleList()

        self.source = ""
        self.current_line = 0

        self.operands = []        # (name or literal, type)
        self.jumps = []           # indices of quadruples waiting for a target
        self.break_jumps = []     # one list of pending breaks per open loop
        self.return_jumps = []    # one list of pending returns per function
        self.calls = []           # one frame per call being parsed
        self.pending_ids = []     # declarators of the declaration being parsed
        self.short_circuits = []  # one frame per 'and'/'or' being parsed
        self.goto_main_index = None

    def reset(self):
        """Wipe every phase's state so the context can be reused."""
        self.errors.clear()
        self.memory.reset()
        self.functions.clear()
        self.quads.clear()
        self.source = ""
        self.current_line = 0
        self.operands = []
        self.jumps = []
        self.break_jumps = []
        self.return_jumps = []
        self.calls = []
        self.pending_ids = []
        self.short_circuits = []
        self.goto_main_index = None

    # -- Quadruple emission -----------------------------------------------
    def emit(self, operator, left, right, result, result_type='-'):
        return self.quads.emit(operator, left, right, result, result_type,
                               self.functions.current_scope)

    def next_quad(self):
        return self.quads.next_number()

    def patch(self, index, target):
        self.quads.patch(index, target)

    def emit_pending_jump(self, operator, condition=None):
        """Emit a jump whose destination is not known yet, and return its index."""
        return self.emit(operator, condition, None, '_', '-')

    # -- Operand stack -----------------------------------------------------
    def push_operand(self, value, value_type):
        self.operands.append((value, value_type))

    def push_error_operand(self):
        self.operands.append(ERROR_OPERAND)

    def pop_operand(self):
        return self.operands.pop() if self.operands else None

    # -- Errors ------------------------------------------------------------
    def semantic_error(self, message, line=None):
        self.errors.add_semantic(
            message, self.current_line if line is None else line)

    # -- Memory ------------------------------------------------------------
    def new_temporary(self, value_type):
        """Create a temporary of ``value_type`` inside the current scope."""
        scope = self.functions.current_scope
        name = self.memory.new_temporary(value_type, scope)
        entry = self.functions.get(scope)
        if entry is not None:
            entry.reserve('temp_%s' % ('str' if value_type == 'string'
                                       else value_type))
        return name

    def scope_kind(self):
        """'global' inside the main program, 'local' inside a function."""
        return ('global' if self.functions.current_scope
                == self.functions.program_name else 'local')

    # -- Declarations ------------------------------------------------------
    def declare_variables(self, names, var_type, line):
        """Add the identifiers collected by a declaration to the current scope."""
        entry = self.functions.current_entry
        if entry is None:
            return
        kind = self.scope_kind()
        for name in names:
            if name in entry.variables:
                self.semantic_error(
                    "Semantic error: variable '%s' is already declared in "
                    "scope '%s'" % (name, entry.name), line)
            elif self.functions.is_function(name):
                self.semantic_error(
                    "Semantic error: variable '%s' cannot share its name with "
                    "a function" % name, line)
            else:
                address = self.memory.allocate(kind, var_type, name=name)
                entry.variables[name] = Variable(name, var_type, entry.name,
                                                 address)
                entry.reserve('%s_%s' % (kind, 'str' if var_type == 'string'
                                         else var_type))

    def declare_parameter(self, name, param_type, line):
        entry = self.functions.current_entry
        if entry is None:
            return
        if name in entry.variables:
            self.semantic_error(
                "Semantic error: parameter '%s' already exists in function "
                "'%s'" % (name, entry.name), line)
        elif self.functions.is_function(name):
            self.semantic_error(
                "Semantic error: parameter '%s' cannot share its name with a "
                "function" % name, line)
        else:
            address = self.memory.allocate('local', param_type, name=name)
            entry.variables[name] = Variable(name, param_type, entry.name,
                                             address, is_parameter=True)
            entry.parameters.append((name, param_type))
            entry.reserve('local_%s' % ('str' if param_type == 'string'
                                        else param_type))

    # -- Expressions -------------------------------------------------------
    def apply_binary(self, operator):
        """Pop two operands, type-check them and emit the operation."""
        if len(self.operands) < 2:
            return
        right, right_type = self.operands.pop()
        left, left_type = self.operands.pop()
        outcome = result_type(left_type, operator, right_type)
        if outcome == 'error':
            if 'error' not in (left_type, right_type):
                self.semantic_error(
                    "Semantic error: invalid operation '%s %s %s'"
                    % (left_type, operator, right_type))
            self.push_error_operand()
            return
        temporary = self.new_temporary(outcome)
        self.emit(operator, left, right, temporary, outcome)
        self.push_operand(temporary, outcome)

    def apply_unary(self, operator):
        """Emit a unary plus or minus over the operand on top of the stack.

        A leading minus is never folded into a negative literal: it always
        produces its own quadruple, so the intermediate representation mirrors
        the source expression.
        """
        if not self.operands:
            return
        value, value_type = self.operands.pop()
        if value_type == 'error':
            self.push_error_operand()
            return
        if value_type in ('string', 'bool'):
            self.semantic_error(
                "Semantic error: unary '%s' does not apply to %s"
                % (operator[-1], value_type))
            self.push_error_operand()
            return
        temporary = self.new_temporary(value_type)
        self.emit(operator, value, None, temporary, value_type)
        self.push_operand(temporary, value_type)

    def apply_not(self):
        """Negate the boolean on top of the operand stack."""
        if not self.operands:
            return
        value, value_type = self.operands.pop()
        outcome = negation_type(value_type)
        if outcome == 'error':
            if value_type != 'error':
                self.semantic_error(
                    "Semantic error: 'not' expects bool but got %s"
                    % value_type)
            self.push_error_operand()
            return
        temporary = self.new_temporary('bool')
        self.emit('not', value, None, temporary, 'bool')
        self.push_operand(temporary, 'bool')

    # -- Logical operators -------------------------------------------------
    def begin_short_circuit(self, operator):
        """Emit the first half of an ``and``/``or``, before its right operand.

        ``and`` and ``or`` do not become quadruples of their own. The left
        operand is copied into the temporary that will hold the result, and a
        jump over the right operand is emitted: ``gotof`` for ``and``, which
        skips when the left side is already false, and ``gotot`` for ``or``,
        which skips when it is already true. That jump is what makes the
        operators short-circuiting -- the right operand is only evaluated when
        the left one did not settle the answer.
        """
        left = self.pop_operand()
        if left is None:
            self.short_circuits.append(None)
            return
        value, value_type = left
        if value_type not in ('bool', 'error'):
            self.semantic_error(
                "Semantic error: the left operand of '%s' must be bool, not %s"
                % (operator, value_type))
        if value_type != 'bool':
            self.short_circuits.append(None)
            return
        temporary = self.new_temporary('bool')
        self.emit('=', value, None, temporary, 'bool')
        jump = 'gotof' if operator == 'and' else 'gotot'
        self.short_circuits.append(
            {'temporary': temporary,
             'jump': self.emit_pending_jump(jump, temporary)})

    def finish_short_circuit(self, operator):
        """Emit the second half of an ``and``/``or``, once its right operand is in.

        The right operand overwrites the temporary, and the jump opened by
        :meth:`begin_short_circuit` is patched to land just after it.
        """
        right = self.pop_operand()
        frame = self.short_circuits.pop() if self.short_circuits else None
        if frame is None or right is None:
            self.push_error_operand()
            return
        value, value_type = right
        if value_type != 'bool':
            if value_type != 'error':
                self.semantic_error(
                    "Semantic error: the right operand of '%s' must be bool, "
                    "not %s" % (operator, value_type))
            # The jump still needs a destination, or the control flow left
            # behind would not be walkable by the return analysis.
            self.patch(frame['jump'], self.next_quad())
            self.push_error_operand()
            return
        self.emit('=', value, None, frame['temporary'], 'bool')
        self.patch(frame['jump'], self.next_quad())
        self.push_operand(frame['temporary'], 'bool')

    def check_condition(self, keyword):
        """Pop the condition of an ``if``/``while`` and require it to be boolean."""
        if not self.operands:
            return None
        condition, condition_type = self.operands.pop()
        if condition_type not in ('bool', 'error'):
            self.semantic_error(
                "Semantic error: the '%s' condition must be boolean" % keyword)
        return condition

    # -- Function calls ----------------------------------------------------
    def start_call(self, name):
        """Open a call frame and mark the start of the call in the quadruples."""
        self.calls.append({'arguments': []})
        self.emit('sub', name, None, None, '-')

    def collect_argument(self):
        if self.calls and self.operands:
            self.calls[-1]['arguments'].append(self.operands.pop())

    def finish_call(self, name, result_temporary=None, line=None):
        """Validate a call and emit its ``param``/``gosub`` quadruples.

        ``result_temporary`` is the temporary that receives the returned value
        when the call appears inside an expression. Returns ``(entry, ok)``.
        """
        line = line or self.current_line
        frame = self.calls.pop() if self.calls else {'arguments': []}
        arguments = frame['arguments']
        entry = self.functions.get(name)
        if entry is None or not entry.is_function:
            self.semantic_error(
                "Semantic error: function '%s' is not declared" % name, line)
            return (None, False)

        parameters = entry.parameters
        ok = True
        if len(arguments) != len(parameters):
            self.semantic_error(
                "Semantic error: function '%s' expects %d argument(s) but got "
                "%d" % (name, len(parameters), len(arguments)), line)
            # Without matching arity there is no sensible code to emit.
            return (entry, False)

        for position, ((value, value_type), (_, param_type)) in enumerate(
                zip(arguments, parameters), start=1):
            if result_type(param_type, '=', value_type) == 'error':
                if value_type != 'error':
                    self.semantic_error(
                        "Semantic error: argument %d of '%s' expects %s but "
                        "got %s" % (position, name, param_type, value_type),
                        line)
                ok = False

        for (value, _), (param_name, param_type) in zip(arguments, parameters):
            # param: the evaluated argument on the left, the destination
            # parameter (in the callee's local memory) as the result.
            self.emit('param', value, None, param_name, param_type)
        # gosub: the function name on the left (readable in the debug IR), the
        # temporary that receives the return value on the right, and the
        # quadruple the function starts at as the result.
        self.emit('gosub', name, result_temporary, entry.start_quad, '-')
        return (entry, ok)


# The context the grammar rules operate on. It is reset, never replaced, so the
# rules can bind to it once at import time.
CONTEXT = CompilationContext()
