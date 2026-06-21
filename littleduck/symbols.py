"""Symbol table: the function directory and the variable tables it contains.

Scopes are deliberately flat and isolated. The main program sees only the
global variables; a function sees only its own parameters and locals. Nothing
reaches across, which is what allows every function to reuse the same local
address range.
"""


class Variable:
    """A declared variable or parameter, together with its virtual address.

    An array is the same thing with a ``size``: its address is that of its
    first element, and the remaining ones follow immediately after it.
    """

    __slots__ = ('name', 'type', 'scope', 'is_parameter', 'address', 'size')

    def __init__(self, name, var_type, scope, address, is_parameter=False,
                 size=None):
        self.name = name
        self.type = var_type
        self.scope = scope
        self.address = address
        self.is_parameter = is_parameter
        self.size = size

    @property
    def is_array(self):
        return self.size is not None

    @property
    def slots(self):
        """How many consecutive addresses this variable occupies."""
        return self.size if self.size is not None else 1


class FunctionEntry:
    """One entry of the function directory: the program itself or a function."""

    def __init__(self, name, return_type, is_function,
                 address=None, declaration_line=0):
        self.name = name
        self.return_type = return_type
        self.is_function = is_function
        # Functions own a global slot of their return type where the returned
        # value is parked until the caller picks it up.
        self.address = address
        self.declaration_line = declaration_line
        self.parameters = []      # [(name, type)] in declaration order
        self.variables = {}       # name -> Variable
        self.start_quad = None
        # A 'return' with a value was written somewhere in the body...
        self.has_return = False
        # ...and every one of them type-checked. Only then is the body's
        # control flow worth analysing: a return that failed to type emits no
        # quadruple, so the flow graph would be missing paths that the source
        # actually has.
        self.all_returns_valid = True
        self.memory = {}          # region name -> number of slots needed

    def reserve(self, region, slots=1):
        """Record that this scope needs ``slots`` more addresses in ``region``."""
        self.memory[region] = self.memory.get(region, 0) + slots


class FunctionDirectory:
    """All the declared scopes plus the stack of the ones currently open."""

    def __init__(self):
        self.entries = {}
        self.program_name = None
        self.scope_stack = []

    def clear(self):
        self.entries.clear()
        self.program_name = None
        self.scope_stack = []

    # -- Declaration -------------------------------------------------------
    def declare_program(self, name):
        entry = FunctionEntry(name, 'void', is_function=False)
        self.entries[name] = entry
        self.program_name = name
        self.scope_stack.append(name)
        return entry

    def declare_function(self, name, return_type, address, declaration_line):
        entry = FunctionEntry(name, return_type, is_function=True,
                              address=address,
                              declaration_line=declaration_line)
        self.entries[name] = entry
        return entry

    # -- Scope handling ----------------------------------------------------
    def push_scope(self, name):
        self.scope_stack.append(name)

    def pop_scope(self):
        # The global scope stays at the bottom of the stack for the whole run.
        if len(self.scope_stack) > 1:
            self.scope_stack.pop()

    @property
    def current_scope(self):
        return self.scope_stack[-1] if self.scope_stack else self.program_name

    @property
    def current_entry(self):
        return self.entries.get(self.current_scope)

    @property
    def program_entry(self):
        return self.entries.get(self.program_name)

    # -- Lookups -----------------------------------------------------------
    def get(self, name):
        return self.entries.get(name)

    def is_function(self, name):
        entry = self.entries.get(name)
        return entry is not None and entry.is_function

    def functions(self):
        """Iterate over the declared functions, skipping the program itself."""
        return [entry for entry in self.entries.values() if entry.is_function]

    def lookup_variable(self, name):
        """Find a variable in the current scope only.

        There is no fallback to the global table on purpose: functions cannot
        read or write global variables.
        """
        entry = self.current_entry
        if entry is not None:
            return entry.variables.get(name)
        return None

    def global_variables(self):
        entry = self.program_entry
        return entry.variables if entry else {}
