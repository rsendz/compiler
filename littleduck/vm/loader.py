"""Reading the address-based intermediate representation back into memory.

The file is a plain-text listing produced by :mod:`littleduck.ir`, made of four
sections: ``const``, ``global``, ``funcs`` and ``quads``.
"""

ESCAPE_SEQUENCES = {
    'n': '\n', 't': '\t', 'r': '\r', '\\': '\\', '"': '"', '0': '\0',
}

SECTION_NAMES = frozenset({'const', 'global', 'funcs', 'quads'})


class Program:
    """Everything needed to run: constants, memory sizes and quadruples."""

    def __init__(self):
        self.constants = {}         # address -> value
        self.global_counts = {}     # region -> slots reserved
        self.functions = {}         # name -> {'start', 'type', 'params', 'memory'}
        self.function_at = {}       # start quadruple -> function name
        self.quads = []             # [operator, left, right, result]
        self.lines = []             # source line of each quadruple


def unescape(text):
    """Turn the escape sequences of a stored string into real characters.

    Constants are written on a single line, so a line break reaches this point
    as the two characters ``\\`` and ``n``.
    """
    out = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == '\\' and index + 1 < len(text):
            replacement = ESCAPE_SEQUENCES.get(text[index + 1])
            if replacement is not None:
                out.append(replacement)
                index += 2
                continue
        out.append(character)
        index += 1
    return ''.join(out)


def parse_constant(text):
    """Convert the stored text of a constant into a Python value."""
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return unescape(text[1:-1])
    if text == 'true':
        return True
    if text == 'false':
        return False
    try:
        return float(text) if '.' in text else int(text)
    except ValueError:
        return text


def load_program(path):
    """Read an address-based intermediate representation file."""
    program = Program()
    section = None
    function = None

    with open(path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line in SECTION_NAMES:
                section = line
                continue
            if section == 'const':
                _read_constant(program, line)
            elif section == 'global':
                region, count = line.split()
                program.global_counts[region] = int(count)
            elif section == 'funcs':
                function = _read_function_line(program, function, line.split())
            elif section == 'quads':
                _read_quadruple(program, line.split())

    return program


def _read_constant(program, line):
    # 'value  address'. The value itself may contain spaces, so only the last
    # field is split off.
    value_text, address_text = line.rsplit(None, 1)
    program.constants[int(address_text)] = parse_constant(value_text.strip())


def _read_function_line(program, function, fields):
    keyword = fields[0]
    if keyword == 'func':
        # func <name> <start quadruple> <return type>
        return {'name': fields[1], 'start': int(fields[2]), 'type': fields[3],
                'params': 0, 'memory': {}}
    if keyword == 'params':
        function['params'] = int(fields[1])
        return function
    if keyword == 'endfunc':
        program.functions[function['name']] = function
        program.function_at[function['start']] = function['name']
        return None
    # '<region> <slots>' for the function's local and temporary memory.
    function['memory'][fields[0]] = int(fields[1])
    return function


def _read_quadruple(program, fields):
    # Skip the column header: its first field is not a quadruple number.
    if not fields[0].lstrip('-').isdigit():
        return
    operator, left, right, result = fields[1], fields[2], fields[3], fields[4]
    program.quads.append([operator, int(left), int(right), int(result)])
    # The source line is kept beside the quadruples rather than inside them,
    # so the machine can keep unpacking a quadruple into its four fields.
    program.lines.append(int(fields[5]) if len(fields) > 5 else 0)
