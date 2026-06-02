"""Reachability over the generated quadruples.

The compiler builds no syntax tree -- it emits quadruples as it parses -- so a
question like "can this function reach its end without returning a value?"
cannot be answered from the grammar alone. It can be answered from the
quadruples themselves: the jumps between them already describe the control flow
of the function, so walking them from its first instruction says which paths
exist.
"""

# Quadruples that end a path: 'return' has produced the value, 'endfun' and
# 'end' close the function and the program.
TERMINATORS = frozenset({'return', 'endfun', 'end'})

# Jumps that always transfer control, and jumps that may fall through instead.
UNCONDITIONAL_JUMPS = frozenset({'goto', 'gotomain'})
CONDITIONAL_JUMPS = frozenset({'gotof', 'gotot'})


def successors(quads, index):
    """The instructions control may reach directly from the one at ``index``.

    Indices are 0-based positions in ``quads``. A conditional jump has two
    successors -- its target and the instruction after it -- while a terminator
    has none.
    """
    quad = quads[index]
    operator = quad.operator
    if operator in TERMINATORS:
        return ()
    if operator in UNCONDITIONAL_JUMPS:
        return (quad.result - 1,)
    if operator in CONDITIONAL_JUMPS:
        return (quad.result - 1, index + 1)
    return (index + 1,)


def has_unresolved_jump(quads, start, end):
    """True when a jump in ``[start, end]`` never got a numeric destination.

    A jump left unpatched means the function did not parse cleanly, and its
    control flow cannot be trusted. Callers use this to stay quiet rather than
    report a path that may not exist.
    """
    for index in range(start, end + 1):
        quad = quads[index]
        if quad.operator in UNCONDITIONAL_JUMPS or \
                quad.operator in CONDITIONAL_JUMPS:
            if not isinstance(quad.result, int):
                return True
    return False


def reaches_end_without_returning(quads, start, end):
    """True when some path from ``start`` arrives at ``end`` without returning.

    ``start`` is the function's first instruction and ``end`` its ``endfun``,
    both 0-based. Paths stop at every ``return``, so arriving at ``end`` means
    control fell off the function without producing a value.
    """
    if start > end:
        return False
    pending = [start]
    seen = set()
    while pending:
        index = pending.pop()
        if index == end:
            return True
        if index in seen or not start <= index <= end:
            continue
        seen.add(index)
        pending.extend(successors(quads, index))
    return False
