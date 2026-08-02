"""Turning quadruples into the two intermediate-representation files.

The parser leaves quadruples whose operands are still names. This module
resolves each name to a virtual address and writes two files:

``<base>-names.txt``
    the same quadruples with readable names, meant for inspection only.
``<base>-addresses.txt``
    addresses only, preceded by the memory header. This is what the virtual
    machine loads and runs.

Both files carry the source line each quadruple came from, in a last column.
The machine ignores it while running and quotes it when something goes wrong,
which is what lets a fault name the line of the program rather than only the
number of a quadruple.
"""

from .memory import format_constant, region_name
from .symbols import Variable

# Sections of the address file, in the order they are written.
SECTIONS = ('const', 'global', 'funcs', 'quads')

GLOBAL_REGIONS = ('global_int', 'global_float', 'global_str', 'global_bool',
                  'global_void')
TEMP_REGIONS = ('temp_int', 'temp_float', 'temp_str', 'temp_bool')
CONST_REGIONS = ('cte_int', 'cte_float', 'cte_str', 'cte_bool')
LOCAL_REGIONS = ('local_int', 'local_float', 'local_str', 'local_bool')

EMPTY_FIELD = -1


class IntermediateCode:
    """Renders the quadruples of a finished compilation."""

    def __init__(self, context):
        self.context = context

    # -- Operand resolution ------------------------------------------------
    def resolve(self, operand, scope):
        """Translate a quadruple operand into its virtual address.

        A variable carries its own address, so there is nothing to look up.
        Temporaries and constants have their own tables, and a function name
        stands for the slot its return value is parked in. An empty field
        resolves to -1.
        """
        context = self.context
        if operand is None or operand == '_':
            return EMPTY_FIELD

        if isinstance(operand, Variable):
            return operand.address

        if context.memory.is_temporary(operand):
            return context.memory.address_of_temporary(operand)

        if isinstance(operand, str) and context.functions.is_function(operand):
            return context.functions.get(operand).address

        # Anything left is a literal; its type follows from the Python value.
        # bool is checked first: in Python it is a subclass of int.
        if isinstance(operand, bool):
            return context.memory.constant(operand, 'bool')
        if isinstance(operand, int):
            return context.memory.constant(operand, 'int')
        if isinstance(operand, float):
            return context.memory.constant(operand, 'float')
        if isinstance(operand, str) and operand.startswith('"') \
                and operand.endswith('"'):
            return context.memory.constant(operand, 'string')
        return EMPTY_FIELD

    def to_addresses(self):
        """Return the quadruples as ``[operator, left, right, result]`` rows.

        Jump operators keep a quadruple number in the field that holds their
        destination; every other field is a virtual address.
        """
        rows = []
        for index, quad in enumerate(self.context.quads):
            rows.append(self._translate(index, quad))
        return rows

    def _translate(self, index, quad):
        operator, scope = quad.operator, quad.scope
        target = quad.result if isinstance(quad.result, int) else EMPTY_FIELD

        if operator in ('gotomain', 'goto'):
            return [operator, EMPTY_FIELD, EMPTY_FIELD, target]
        if operator in ('gotof', 'gotot'):
            return [operator, self.resolve(quad.left, scope), EMPTY_FIELD,
                    target]
        if operator == 'gosub':
            # left: the callee's return slot; right: the caller's temporary
            # that receives the value; result: the quadruple to jump to.
            entry = self.context.functions.get(quad.left)
            return [operator,
                    entry.address if entry else EMPTY_FIELD,
                    self.resolve(quad.right, scope)
                    if quad.right is not None else EMPTY_FIELD,
                    target]
        if operator == 'sub':
            entry = self.context.functions.get(quad.left)
            return [operator, entry.address if entry else EMPTY_FIELD,
                    EMPTY_FIELD, EMPTY_FIELD]
        if operator == 'param':
            return [operator, self.resolve(quad.left, scope), EMPTY_FIELD,
                    self.resolve(quad.result, scope)]
        if operator in ('endfun', 'end', 'newline'):
            return [operator, EMPTY_FIELD, EMPTY_FIELD, EMPTY_FIELD]
        if operator == 'ver':
            # The bounds are plain numbers fixed by the declaration, not
            # addresses: only the index has to be read from memory.
            return [operator, self.resolve(quad.left, scope), quad.right,
                    quad.result]
        if operator == 'print':
            return [operator, self.resolve(quad.left, scope), EMPTY_FIELD,
                    EMPTY_FIELD]
        if operator == 'return':
            return [operator, self.resolve(quad.left, scope), EMPTY_FIELD,
                    self.resolve(quad.result, scope)]
        # Assignment, arithmetic, unary, logical, relational and array
        # operators. An array name resolves to the address of its first
        # element, which is the base the index is added to.
        return [operator,
                self.resolve(quad.left, scope),
                self.resolve(quad.right, scope),
                self.resolve(quad.result, scope)]

    # -- Memory header -----------------------------------------------------
    def _global_counts(self):
        """How many global slots each type needs.

        Global variables are counted from the program's table; each function
        adds one more slot of its return type for the value it hands back.
        """
        counts = {}
        for variable in self.context.functions.global_variables():
            region = region_name('global', variable.type)
            counts[region] = counts.get(region, 0) + variable.slots
        for entry in self.context.functions.functions():
            region = region_name('global', entry.return_type)
            counts[region] = counts.get(region, 0) + 1
        return counts

    def _constant_lines(self):
        """One ``value  address`` line per constant, ordered by address.

        The columns are padded for readability; the loader splits on
        whitespace, so the padding is harmless.
        """
        return ["%-24s %d" % (format_constant(constant['value']),
                              constant['address'])
                for constant in self.context.memory.sorted_constants()]

    def _global_lines(self):
        """Global, main-program temporary and constant slot counts."""
        counts = self._global_counts()
        lines = ["%-14s %d" % (region, counts.get(region, 0))
                 for region in GLOBAL_REGIONS]
        program = self.context.functions.program_entry
        program_memory = program.memory if program else {}
        lines += ["%-14s %d" % (region, program_memory.get(region, 0))
                  for region in TEMP_REGIONS]
        constant_counts = self.context.memory.constant_counts()
        lines += ["%-14s %d" % (region, constant_counts[region])
                  for region in CONST_REGIONS]
        return lines

    def _function_lines(self):
        """One block per function: signature, arity and local memory needs."""
        lines = []
        for entry in self.context.functions.functions():
            lines.append("func %s %d %s"
                         % (entry.name, entry.start_quad, entry.return_type))
            lines.append("params %d" % len(entry.parameters))
            lines += ["%-14s %d" % (region, entry.memory.get(region, 0))
                      for region in LOCAL_REGIONS + TEMP_REGIONS]
            lines.append("endfunc")
        return lines

    # -- Rendering ---------------------------------------------------------
    def display_names(self):
        """A readable name per variable, unique inside its scope.

        Blocks let one scope hold several variables of the same name -- one
        hiding another, or two sibling blocks each declaring their own. They
        have different addresses, so the executable listing tells them apart on
        its own; the readable one numbers them in declaration order.
        """
        names = {}
        for entry in self.context.functions.entries.values():
            by_name = {}
            for variable in entry.variables:
                by_name.setdefault(variable.name, []).append(variable)
            for name, group in by_name.items():
                if len(group) == 1:
                    names[id(group[0])] = name
                    continue
                for number, variable in enumerate(group, start=1):
                    names[id(variable)] = "%s~%d" % (name, number)
        return names

    def as_names(self, optimization=None):
        """The readable listing: quadruples with names, for inspection."""
        lines = ["# Intermediate representation (names) - for inspection only"]
        if optimization is not None:
            lines.append("# Optimizer: " + optimization.summary())
        lines.append("# Constants: value  address")
        for constant in self.context.memory.sorted_constants():
            lines.append("const\t%s\t%d"
                         % (format_constant(constant['value']),
                            constant['address']))
        lines.append("# Quadruples")
        lines.append("%-4s %-9s %-14s %-14s %-14s %-8s %s"
                     % ("#", "op", "left", "right", "result", "type", "line"))
        names = self.display_names()
        for number, quad in enumerate(self.context.quads, start=1):
            lines.append("%-4d %-9s %-14s %-14s %-14s %-8s %d"
                         % (number, quad.operator, _field(quad.left, names),
                            _field(quad.right, names),
                            _field(quad.result, names),
                            _field(quad.result_type, names), quad.line))
        return lines

    def as_addresses(self):
        """The executable listing: memory header plus quadruples in addresses.

        The quadruples are translated first even though they are written last:
        resolving an operand can register a constant that was not seen while
        parsing, and the ``const`` section has to list it.
        """
        rows = self.to_addresses()

        lines = ["const"]
        lines += self._constant_lines()
        lines += ["", "global"]
        lines += self._global_lines()
        lines += ["", "funcs"]
        lines += self._function_lines()
        lines += ["", "quads"]
        # A column header the loader skips, since its first field is not a
        # quadruple number.
        lines.append("%-4s %-9s %-8s %-8s %-8s %s"
                     % ("#", "op", "left", "right", "result", "line"))
        for number, (row, quad) in enumerate(zip(rows, self.context.quads),
                                             start=1):
            lines.append("%-4d %-9s %-8d %-8d %-8d %d"
                         % (number, row[0], row[1], row[2], row[3], quad.line))
        return lines

    def write(self, base_name, optimization=None):
        """Write both files and return the path of the executable one.

        The address listing is rendered first: translating an operand can
        register a constant, and the readable listing prints the constants too.
        """
        names_path = base_name + "-names.txt"
        addresses_path = base_name + "-addresses.txt"
        address_lines = self.as_addresses()
        _write_lines(names_path, self.as_names(optimization))
        _write_lines(addresses_path, address_lines)
        return addresses_path


def _field(value, names):
    """Render a quadruple field for the readable listing.

    An empty field becomes a dash, a variable is spelled the way ``names``
    spells it, and a constant the way the source does -- 'true' rather than
    Python's 'True'.
    """
    if value is None or value == '_':
        return '-'
    if isinstance(value, Variable):
        return names.get(id(value), value.name)
    return format_constant(value)


def _write_lines(path, lines):
    with open(path, 'w') as handle:
        handle.write("\n".join(lines) + "\n")
