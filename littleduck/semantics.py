"""The semantic cube: which operand types each operator accepts.

``CUBE[left][right][operator]`` yields the type of the result, or ``'error'``
when the combination is not allowed. Assignment is modelled as an operator too,
with the destination type on the left, which is what makes ``float = int`` legal
and ``int = float`` illegal.
"""

TYPES = ('int', 'float', 'string', 'bool')
NUMERIC = ('int', 'float')
OPERATORS = ('+', '-', '*', '/', '<', '>', '<=', '>=', '==', '!=', '=',
             'and', 'or')


def build_cube():
    """Build the operand-type compatibility table."""
    cube = {left: {right: {op: 'error' for op in OPERATORS}
                   for right in TYPES}
            for left in TYPES}

    # Arithmetic + - * : numeric operands, float wins over int.
    for op in ('+', '-', '*'):
        cube['int']['int'][op] = 'int'
        cube['int']['float'][op] = 'float'
        cube['float']['int'][op] = 'float'
        cube['float']['float'][op] = 'float'

    # Division between numeric operands always yields a float.
    for left in NUMERIC:
        for right in NUMERIC:
            cube[left][right]['/'] = 'float'

    # Ordering comparisons: numeric operands only.
    for op in ('<', '>', '<=', '>='):
        for left in NUMERIC:
            for right in NUMERIC:
                cube[left][right][op] = 'bool'

    # Equality: numeric against numeric, string against string, or two
    # booleans.
    for op in ('==', '!='):
        for left in NUMERIC:
            for right in NUMERIC:
                cube[left][right][op] = 'bool'
        cube['string']['string'][op] = 'bool'
        cube['bool']['bool'][op] = 'bool'

    # Logical conjunction and disjunction: booleans only. There is no implicit
    # conversion from a number, so 'if (n and m)' is rejected rather than
    # quietly reading zero as false.
    for op in ('and', 'or'):
        cube['bool']['bool'][op] = 'bool'

    # Assignment. int = float stays an error: it would lose precision.
    cube['int']['int']['='] = 'int'
    cube['float']['float']['='] = 'float'
    cube['float']['int']['='] = 'float'
    cube['string']['string']['='] = 'string'
    cube['bool']['bool']['='] = 'bool'

    return cube


CUBE = build_cube()


def result_type(left_type, operator, right_type):
    """Look up the result type of an operation, propagating previous errors.

    An operand that already failed carries the type ``'error'``; the error is
    propagated silently so a single mistake is only reported once.
    """
    if left_type == 'error' or right_type == 'error':
        return 'error'
    try:
        return CUBE[left_type][right_type][operator]
    except KeyError:
        return 'error'


def negation_type(operand_type):
    """The result of ``not`` over ``operand_type``, or ``'error'``.

    ``not`` is the only logical operator with a single operand, so it does not
    fit the cube and gets its own rule.
    """
    if operand_type == 'error':
        return 'error'
    return 'bool' if operand_type == 'bool' else 'error'
