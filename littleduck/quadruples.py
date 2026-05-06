"""Quadruples: the intermediate representation the virtual machine executes.

A quadruple is ``(operator, left, right, result)`` plus the type of the result.
While the parser is running the operands are still names -- variables,
temporaries or literals -- and the scope each quadruple was emitted in is
recorded alongside it, so that ``littleduck.ir`` can later resolve every name to
a virtual address.
"""

# Operators whose numeric fields are quadruple numbers rather than addresses.
JUMP_OPERATORS = frozenset({'gotomain', 'goto', 'gotof', 'gotot', 'gosub'})


class Quadruple:
    """A single intermediate-representation instruction."""

    __slots__ = ('operator', 'left', 'right', 'result', 'result_type', 'scope')

    def __init__(self, operator, left, right, result, result_type, scope):
        self.operator = operator
        self.left = left
        self.right = right
        self.result = result
        self.result_type = result_type
        self.scope = scope

    def __repr__(self):
        return ("Quadruple(%r, %r, %r, %r)"
                % (self.operator, self.left, self.right, self.result))


class QuadrupleList:
    """The generated quadruples, numbered from 1 as the machine sees them."""

    def __init__(self):
        self._items = []

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, index):
        return self._items[index]

    def clear(self):
        self._items = []

    def emit(self, operator, left, right, result, result_type='-', scope=None):
        """Append a quadruple and return the index it was stored at."""
        self._items.append(
            Quadruple(operator, left, right, result, result_type, scope))
        return len(self._items) - 1

    def next_number(self):
        """The number (1-based) the next emitted quadruple will have."""
        return len(self._items) + 1

    def patch(self, index, target):
        """Fill in the pending destination of the jump stored at ``index``."""
        self._items[index].result = target

    def last_index(self):
        return len(self._items) - 1
