"""The interpreter: executes a loaded program quadruple by quadruple."""

import operator as operators

from .errors import VMRuntimeError
from .memory import RuntimeMemory

# How deep the call stack may get before recursion is considered runaway. Kept
# well under Python's own limit so the machine reports the error itself.
RECURSION_LIMIT = 500

EMPTY_FIELD = -1


class VirtualMachine:
    """Runs a program loaded by :func:`littleduck.vm.loader.load_program`."""

    def __init__(self, program):
        self.program = program
        self.memory = RuntimeMemory(program.constants, program.global_counts)
        self.instruction_pointer = 0
        self.depth = 1                 # call depth, counting the main program
        self._return_addresses = []    # quadruple to resume at after a call
        self._pending_params = []      # arguments of the call being prepared
        self._result_slots = []        # caller temporary awaiting the result
        self._function_slots = []      # global slot the callee returns through
        self._output = []
        self._operations = self._build_operation_table()

    # -- Execution ---------------------------------------------------------
    def run(self):
        """Execute the whole program, starting at the jump to ``main``."""
        self.memory.push_frame(self._main_memory())
        quads = self.program.quads
        while self.instruction_pointer < len(quads):
            operator, left, right, result = quads[self.instruction_pointer]
            operation = self._operations.get(operator)
            if operation is None:
                raise VMRuntimeError("unknown operator '%s'" % operator,
                                     self.current_quad_number)
            try:
                jump = operation(left, right, result)
            except VMRuntimeError as error:
                if error.quad_number is None:
                    error.quad_number = self.current_quad_number
                raise
            # An operation either returns the quadruple to jump to (1-based) or
            # None, meaning "carry on with the next one".
            self.instruction_pointer = (self.instruction_pointer + 1
                                        if jump is None else jump - 1)

    @property
    def current_quad_number(self):
        return self.instruction_pointer + 1

    def output_text(self):
        return ''.join(self._output)

    def _main_memory(self):
        """The temporaries of the main program, which lives in its own frame."""
        return {region: self.program.global_counts.get(region, 0)
                for region in ('temp_int', 'temp_float', 'temp_str',
                               'temp_bool')}

    def _build_operation_table(self):
        return {
            '+': self._arithmetic(operators.add),
            '-': self._arithmetic(operators.sub),
            '*': self._arithmetic(operators.mul),
            '/': self._divide,
            'u+': self._unary(operators.pos),
            'u-': self._unary(operators.neg),
            'not': self._unary(operators.not_),
            '>': self._arithmetic(operators.gt),
            '<': self._arithmetic(operators.lt),
            '>=': self._arithmetic(operators.ge),
            '<=': self._arithmetic(operators.le),
            '==': self._arithmetic(operators.eq),
            '!=': self._arithmetic(operators.ne),
            '=': self._assign,
            'gotomain': self._goto,
            'goto': self._goto,
            'gotof': self._goto_false,
            'gotot': self._goto_true,
            'sub': self._begin_call,
            'param': self._pass_param,
            'gosub': self._call,
            'return': self._return,
            'endfun': self._end_function,
            'print': self._print,
            'newline': self._newline,
            'end': self._end,
        }

    # -- Arithmetic, comparison and assignment -----------------------------
    def _arithmetic(self, apply):
        def run(left, right, result):
            self.memory.write(result, apply(self.memory.read(left),
                                            self.memory.read(right)))
        return run

    def _unary(self, apply):
        def run(left, right, result):
            self.memory.write(result, apply(self.memory.read(left)))
        return run

    def _divide(self, left, right, result):
        divisor = self.memory.read(right)
        if divisor == 0:
            raise VMRuntimeError("division by zero", self.current_quad_number)
        # Division always yields a float, as the language defines it.
        self.memory.write(result, self.memory.read(left) / divisor)

    def _assign(self, left, right, result):
        self.memory.write(result, self.memory.read(left))

    # -- Jumps -------------------------------------------------------------
    def _goto(self, left, right, result):
        return result

    def _goto_false(self, left, right, result):
        return result if not self.memory.read(left) else None

    def _goto_true(self, left, right, result):
        return result if self.memory.read(left) else None

    # -- Calls -------------------------------------------------------------
    def _begin_call(self, left, right, result):
        """Open the argument set the following ``param`` quadruples fill in."""
        self._pending_params.append({})

    def _pass_param(self, left, right, result):
        """Read an argument in the caller and stage it for the callee's slot."""
        self._pending_params[-1][result] = self.memory.read(left)

    def _call(self, left, right, result):
        """Enter a function: new activation record, arguments copied in.

        ``left`` is the callee's global return slot, ``right`` the caller
        temporary that will receive the value, ``result`` the first quadruple
        of the function.
        """
        if self.depth + 1 > RECURSION_LIMIT:
            raise VMRuntimeError(
                "maximum recursion depth exceeded (%d)" % RECURSION_LIMIT,
                self.current_quad_number)
        arguments = self._pending_params.pop()
        name = self.program.function_at.get(result)
        local_counts = self.program.functions[name]['memory'] if name else {}
        self.memory.push_frame(local_counts)
        for address, value in arguments.items():
            self.memory.current_frame['cells'][address] = value
        # Resume at the quadruple after this one, which is 1-based
        # (instruction_pointer + 1) + 1.
        self._return_addresses.append(self.instruction_pointer + 2)
        self._result_slots.append(right)
        self._function_slots.append(left)
        self.depth += 1
        return result

    def _return(self, left, right, result):
        """Park the returned value in the function's global slot."""
        self.memory.write(result, self.memory.read(left))
        # The goto emitted right after the return leads to the endfun.
        return None

    def _end_function(self, left, right, result):
        """Leave a function: drop its frame and hand the value to the caller."""
        self.memory.pop_frame()
        self.depth -= 1
        resume_at = self._return_addresses.pop()
        result_slot = self._result_slots.pop()
        function_slot = self._function_slots.pop()
        if (result_slot != EMPTY_FIELD
                and function_slot in self.memory.globals):
            self.memory.write(result_slot, self.memory.globals[function_slot])
        return resume_at

    # -- Output ------------------------------------------------------------
    def _print(self, left, right, result):
        self._output.append(format_value(self.memory.read(left)))

    def _newline(self, left, right, result):
        self._output.append('\n')

    def _end(self, left, right, result):
        self.instruction_pointer = len(self.program.quads)
        return None


def format_value(value):
    """Render a value the way the language prints it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        # Keeps the decimal point without trailing noise.
        return repr(value)
    return str(value)
