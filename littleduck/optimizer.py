"""An optimization pass over the generated quadruples.

The parser emits quadruples as it reduces, one per operation it recognizes,
without ever looking back at what it already produced. That is what keeps the
single pass simple, and it is also why the listing it leaves behind says more
than the program needs: an expression written entirely in constants is computed
at run time, a temporary that only ever holds one constant is stored and read
back, and code behind a condition that cannot hold is generated anyway.

This module runs after parsing, over the quadruples the parser left in the
context, and rewrites them in place. Five transformations feed each other and
are repeated until none of them finds anything left to do:

``fold_constants``
    an operation whose operands are all constants becomes the constant it
    produces.
``propagate_constants``
    a temporary assigned one constant and never assigned again is replaced by
    that constant everywhere it is read.
``simplify_branches``
    a conditional jump on a constant becomes an unconditional jump, or nothing
    at all.
``simplify_jumps``
    a jump to the next instruction is dropped, and a jump that lands on an
    unconditional jump is retargeted to where that one goes.
``remove_dead_code``
    an instruction whose result is a temporary nobody reads is dropped, and so
    is an instruction no path can reach.

Everything here is deliberately conservative about faults. Reading a variable
that was never assigned is a runtime error in this language, and so is dividing
by zero, so an instruction that could raise either is never removed and a
division by a zero constant is never folded: an optimization must make a
program faster, not make a broken program look correct.
"""

import operator as operators

from .quadruples import JUMP_OPERATORS

# How many times the transformations are repeated before giving up. Each round
# only reruns because the previous one changed something, and the changes are
# all removals or replacements of a name by a constant, so the process settles
# in a handful of rounds.
MAX_ROUNDS = 10

# Operators that compute a value from constants alone, and the Python function
# that computes it. Strings are left out: only their equality is defined, and
# folding it would mean interpreting the escape sequences of two literals to
# compare what they stand for rather than how they were written.
FOLDABLE_BINARY = {
    '+': operators.add, '-': operators.sub, '*': operators.mul,
    '/': operators.truediv,
    '>': operators.gt, '<': operators.lt,
    '>=': operators.ge, '<=': operators.le,
    '==': operators.eq, '!=': operators.ne,
}

FOLDABLE_UNARY = {
    'u+': operators.pos, 'u-': operators.neg, 'not': operators.not_,
}

# The fields each operator reads. Only these are rewritten when a constant is
# propagated, which is what keeps the pass away from the fields that hold
# something other than a value: a quadruple number, a function name, the base
# of an array, or the bounds of a check.
READ_FIELDS = {
    '=': ('left',),
    'print': ('left',),
    'gotof': ('left',), 'gotot': ('left',),
    'param': ('left',),
    'return': ('left',),
    'ver': ('left',),
    'arrayread': ('left', 'right'),
    'arraywrite': ('left', 'right'),
}
for _operator in FOLDABLE_BINARY:
    READ_FIELDS[_operator] = ('left', 'right')
for _operator in FOLDABLE_UNARY:
    READ_FIELDS[_operator] = ('left',)
del _operator

# Operators that produce a value and nothing else. Only these are candidates
# for removal when their result turns out to be unused: '/' can raise, and
# everything else either writes where this pass cannot see (a parameter of the
# callee, an element chosen at run time) or has an effect of its own.
PURE_OPERATORS = frozenset(
    set(FOLDABLE_BINARY) | set(FOLDABLE_UNARY) | {'='}) - {'/'}

# Jumps whose destination may be retargeted. 'gosub' is left out: its
# destination is the quadruple a function starts at, and the machine reads that
# same number from the function table to size the activation record.
RETARGETABLE = JUMP_OPERATORS - {'gosub'}

# A literal that could not be read as a foldable constant.
NOT_A_CONSTANT = object()


class OptimizationReport:
    """What one run of the optimizer changed."""

    def __init__(self):
        self.before = 0
        self.after = 0
        self.folded = 0
        self.propagated = 0
        self.branches = 0
        self.jumps = 0
        self.dead = 0
        self.unreachable = 0

    @property
    def removed(self):
        return self.before - self.after

    def summary(self):
        return ("%d quadruples in, %d out: %d folded, %d constants "
                "propagated, %d branches settled, %d jumps simplified, "
                "%d dead, %d unreachable"
                % (self.before, self.after, self.folded, self.propagated,
                   self.branches, self.jumps, self.dead, self.unreachable))


def optimize(context):
    """Rewrite the quadruples of a finished compilation and report on it."""
    return Optimizer(context).run()


class Optimizer:
    """Rewrites the quadruple list of a compilation context in place."""

    def __init__(self, context):
        self.context = context
        self.quads = list(context.quads)
        # Removal is deferred: an index dropped here stays in the list until
        # the end, so that the numbers the jumps carry keep meaning the same
        # thing while the transformations run.
        self.removed = set()
        # Jumps are counted by which quadruple was touched, not by how often:
        # one jump can be shortened again in a later round, once a removal has
        # moved what it used to land on.
        self.simplified = set()
        self.report = OptimizationReport()

    def run(self):
        self.report.before = len(self.quads)
        for _ in range(MAX_ROUNDS):
            changed = False
            for transformation in (self.fold_constants,
                                   self.propagate_constants,
                                   self.simplify_branches,
                                   self.simplify_jumps,
                                   self.remove_dead_results,
                                   self.remove_unreachable):
                changed = transformation() or changed
            if not changed:
                break
        self._compact()
        self.report.after = len(self.context.quads)
        return self.report

    # -- Constants ---------------------------------------------------------
    def fold_constants(self):
        """Replace an operation over constants with the constant it yields."""
        changed = False
        for _, quad in self._live():
            value = self._folded_value(quad)
            if value is NOT_A_CONSTANT:
                continue
            # The result is a value the source never spelled out, so it needs
            # an address of its own before anything can refer to it.
            self.context.memory.constant(value, quad.result_type)
            quad.operator = '='
            quad.left = value
            quad.right = None
            self.report.folded += 1
            changed = True
        return changed

    def _folded_value(self, quad):
        """The value ``quad`` computes, or ``NOT_A_CONSTANT``."""
        if quad.operator in FOLDABLE_UNARY:
            operand = _constant(quad.left)
            if operand is NOT_A_CONSTANT:
                return NOT_A_CONSTANT
            return FOLDABLE_UNARY[quad.operator](operand)
        if quad.operator not in FOLDABLE_BINARY:
            return NOT_A_CONSTANT
        left, right = _constant(quad.left), _constant(quad.right)
        if left is NOT_A_CONSTANT or right is NOT_A_CONSTANT:
            return NOT_A_CONSTANT
        if quad.operator == '/' and right == 0:
            # Dividing by zero is a runtime error, and it stays one.
            return NOT_A_CONSTANT
        return FOLDABLE_BINARY[quad.operator](left, right)

    def propagate_constants(self):
        """Replace reads of a single-valued temporary with its constant.

        Only temporaries qualify, and only those written exactly once: a
        variable can be assigned again further down, and a temporary inside a
        short-circuit is written on each of the two paths through it.
        """
        values = self._constant_temporaries()
        if not values:
            return False
        changed = False
        for _, quad in self._live():
            for field in READ_FIELDS.get(quad.operator, ()):
                operand = getattr(quad, field)
                if isinstance(operand, str) and operand in values:
                    setattr(quad, field, values[operand])
                    self.report.propagated += 1
                    changed = True
        return changed

    def _constant_temporaries(self):
        """Map each temporary that only ever holds one constant to its value."""
        definitions = {}
        for _, quad in self._live():
            target = _written_temporary(quad)
            if target is not None and self.context.memory.is_temporary(target):
                definitions.setdefault(target, []).append(quad)
        values = {}
        for name, quads in definitions.items():
            if len(quads) != 1:
                continue
            quad = quads[0]
            if quad.operator == '=' and _constant(quad.left) is not \
                    NOT_A_CONSTANT:
                values[name] = quad.left
        return values

    # -- Jumps -------------------------------------------------------------
    def simplify_branches(self):
        """Settle a conditional jump whose condition is a constant."""
        changed = False
        for index, quad in self._live():
            if quad.operator not in ('gotof', 'gotot'):
                continue
            condition = _constant(quad.left)
            if condition is NOT_A_CONSTANT:
                continue
            jumps = (not condition) if quad.operator == 'gotof' \
                else bool(condition)
            if jumps:
                quad.operator = 'goto'
                quad.left = None
            else:
                self._remove(index)
            self.report.branches += 1
            changed = True
        return changed

    def simplify_jumps(self):
        """Drop jumps that go nowhere and shorten those that go through another."""
        changed = False
        for index, quad in self._live():
            if quad.operator not in RETARGETABLE:
                continue
            target = quad.result
            if not isinstance(target, int):
                continue
            landing = self._retarget(target)
            if landing != target:
                quad.result = landing
                self.simplified.add(index)
                changed = True
            if quad.operator == 'goto' and self._first_live(quad.result - 1) \
                    == self._first_live(index + 1):
                # The jump lands exactly where control would have gone anyway.
                self._remove(index)
                self.simplified.add(index)
                changed = True
        self.report.jumps = len(self.simplified)
        return changed

    def _retarget(self, target):
        """Follow a chain of unconditional jumps to where it really ends."""
        seen = set()
        while True:
            index = self._first_live(target - 1)
            if index in seen or index >= len(self.quads):
                return target
            quad = self.quads[index]
            if quad.operator != 'goto' or not isinstance(quad.result, int):
                return index + 1
            seen.add(index)
            target = quad.result

    # -- Dead code ---------------------------------------------------------
    def remove_dead_results(self):
        """Drop an instruction whose result is a temporary nobody reads.

        An instruction only qualifies when everything it reads is a constant or
        another temporary. Reading a variable that was never assigned is a
        runtime error, so dropping an instruction that reads one could turn a
        program that faults into one that does not.
        """
        read = self._read_names()
        changed = False
        for index, quad in self._live():
            if quad.operator not in PURE_OPERATORS:
                continue
            target = _written_temporary(quad)
            if target is None or not self.context.memory.is_temporary(target):
                continue
            if target in read:
                continue
            if not all(self._is_safe_to_drop(getattr(quad, field))
                       for field in READ_FIELDS.get(quad.operator, ())):
                continue
            self._remove(index)
            self.report.dead += 1
            changed = True
        return changed

    def _is_safe_to_drop(self, operand):
        """True when reading ``operand`` cannot fail on its own."""
        return (_constant(operand) is not NOT_A_CONSTANT
                or _is_string_literal(operand)
                or self.context.memory.is_temporary(operand))

    def _read_names(self):
        """Every name read by a surviving quadruple."""
        names = set()
        for _, quad in self._live():
            for field in READ_FIELDS.get(quad.operator, ()):
                operand = getattr(quad, field)
                if isinstance(operand, str):
                    names.add(operand)
        return names

    def remove_unreachable(self):
        """Drop the instructions no path arrives at.

        The walk starts at the first quadruple and at the first quadruple of
        every function. Functions are seeded rather than discovered through
        their calls so that an uncalled one keeps its body: the function table
        in the generated file names the quadruple each function starts at, and
        that number has to keep pointing at real code.
        """
        pending = [0]
        for entry in self.context.functions.functions():
            if entry.start_quad is not None:
                pending.append(entry.start_quad - 1)
        reached = set()
        while pending:
            index = self._first_live(pending.pop())
            if index in reached or index >= len(self.quads):
                continue
            reached.add(index)
            pending.extend(self._successors(index))

        changed = False
        for index, _ in self._live():
            if index not in reached:
                self._remove(index)
                self.report.unreachable += 1
                changed = True
        return changed

    def _successors(self, index):
        """The instructions control may reach directly from the one at ``index``.

        A ``gosub`` continues at the instruction after it: the call returns
        there, and the body it enters is walked from its own starting point.
        """
        quad = self.quads[index]
        operator = quad.operator
        if operator in ('end', 'endfun'):
            return ()
        if operator in ('goto', 'gotomain'):
            return (quad.result - 1,) if isinstance(quad.result, int) else ()
        if operator in ('gotof', 'gotot'):
            if isinstance(quad.result, int):
                return (quad.result - 1, index + 1)
            return (index + 1,)
        return (index + 1,)

    # -- Bookkeeping -------------------------------------------------------
    def _live(self):
        """The surviving quadruples, as ``(index, quadruple)`` pairs."""
        return [(index, quad) for index, quad in enumerate(self.quads)
                if index not in self.removed]

    def _remove(self, index):
        self.removed.add(index)

    def _first_live(self, index):
        """The first surviving quadruple at or after ``index``."""
        while index < len(self.quads) and index in self.removed:
            index += 1
        return max(index, 0)

    def _compact(self):
        """Drop the removed quadruples and renumber everything that points at one.

        A jump to a quadruple that is gone is aimed at the next surviving one,
        which is where control would have arrived anyway.
        """
        survivors = [index for index in range(len(self.quads))
                     if index not in self.removed]
        numbers = self._renumbering(survivors)

        for index in survivors:
            quad = self.quads[index]
            if quad.operator in JUMP_OPERATORS and isinstance(quad.result, int):
                quad.result = numbers[quad.result - 1]

        for entry in self.context.functions.entries.values():
            if entry.start_quad is not None:
                entry.start_quad = numbers[entry.start_quad - 1]

        self.context.quads.replace([self.quads[index] for index in survivors])

    def _renumbering(self, survivors):
        """Map every old 0-based index to its new 1-based quadruple number."""
        numbers = {}
        next_number = 1
        for index in range(len(self.quads)):
            if index in self.removed:
                continue
            numbers[index] = next_number
            next_number += 1
        # A removed quadruple, and the position just past the end, both stand
        # for "wherever the next surviving instruction is".
        for index in range(len(self.quads), -1, -1):
            if index not in numbers:
                numbers[index] = numbers.get(index + 1, len(survivors) + 1)
        return numbers


def _constant(operand):
    """The Python value of a foldable literal, or ``NOT_A_CONSTANT``.

    Booleans and numbers qualify. A name -- a variable, a temporary, a function
    -- does not, and neither does a string literal.
    """
    if isinstance(operand, (bool, int, float)):
        return operand
    return NOT_A_CONSTANT


def _is_string_literal(operand):
    return (isinstance(operand, str) and len(operand) >= 2
            and operand.startswith('"') and operand.endswith('"'))


def _written_temporary(quad):
    """The name a quadruple writes its result to, when it has one."""
    if quad.operator in PURE_OPERATORS or quad.operator == '/':
        return quad.result
    return None
