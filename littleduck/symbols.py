"""Symbol table: the function directory and the variable tables it contains.

A function sees, in order, the blocks it has open, then its own parameters and
top-level locals, then the global variables. It does not see another function's
names, which is what allows every function to reuse the same local address
range.

Blocks nest. Every ``{ }`` may open a ``var`` section of its own, and the names
it declares are visible until its closing brace. A block may reuse a name that
an enclosing block already declared; the inner one wins while it is open, and
the outer one is reachable again afterwards. Each entry keeps two views of its
variables for that reason: ``blocks``, the stack of the ones currently in
scope, and ``variables``, every variable it ever declared -- which is what the
memory header is counted from.
"""


class Variable:
    """A declared variable or parameter, together with its virtual address.

    An array is the same thing with a ``size``: its address is that of its
    first element, and the remaining ones follow immediately after it.

    A variable is also what the quadruples carry: the parser puts the object
    itself in the operand field rather than its name, so two variables that
    share a name -- one shadowing the other, or two sibling blocks each
    declaring their own -- never have to be told apart by name later on.
    """

    __slots__ = ('name', 'type', 'scope', 'is_parameter', 'address', 'size',
                 'depth')

    def __init__(self, name, var_type, scope, address, is_parameter=False,
                 size=None, depth=0):
        self.name = name
        self.type = var_type
        self.scope = scope
        self.address = address
        self.is_parameter = is_parameter
        self.size = size
        # 0 for a parameter or a top-level local of the scope; deeper for one
        # declared inside a nested block.
        self.depth = depth

    def __repr__(self):
        return "Variable(%r, %r)" % (self.name, self.address)

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
        self.parameters = []      # the parameters, in declaration order
        self.variables = []       # every variable ever declared in this scope
        # The blocks currently open, innermost last. The first one is the
        # scope's own level: its parameters and its top-level locals.
        self.blocks = [{}]
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

    # -- Blocks ------------------------------------------------------------
    def add(self, variable):
        """Record a new variable, both in the open block and in the scope."""
        self.variables.append(variable)
        self.blocks[-1][variable.name] = variable

    def declared_here(self, name):
        """True when the innermost open block already declares ``name``."""
        return name in self.blocks[-1]

    def find(self, name):
        """The variable ``name`` refers to right now, innermost block first."""
        for block in reversed(self.blocks):
            variable = block.get(name)
            if variable is not None:
                return variable
        return None

    def top_level(self, name):
        """The variable ``name`` refers to at the scope's own level.

        Only these are reachable from elsewhere: a global is one of the
        program's top-level variables, never one that a block inside ``main``
        introduced and closed again.
        """
        return self.blocks[0].get(name)

    @property
    def depth(self):
        """How many blocks are open inside this scope."""
        return len(self.blocks) - 1


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
            entry = self.current_entry
            if entry is not None:
                # A body abandoned by error recovery can leave blocks open.
                # They belong to a scope that is ending, so they go with it.
                del entry.blocks[1:]
            self.scope_stack.pop()

    # -- Blocks ------------------------------------------------------------
    def open_block(self):
        """Start a nested block in the current scope."""
        entry = self.current_entry
        if entry is not None:
            entry.blocks.append({})

    def close_block(self):
        """End the innermost block, taking the names it declared out of scope."""
        entry = self.current_entry
        if entry is not None and len(entry.blocks) > 1:
            entry.blocks.pop()

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
        """Find what ``name`` refers to here: block, then scope, then global.

        A function that declares no ``name`` of its own falls through to the
        global variables. One that does declare it hides the global for as long
        as its own declaration is in scope.
        """
        entry = self.current_entry
        if entry is None:
            return None
        variable = entry.find(name)
        if variable is not None:
            return variable
        if entry is self.program_entry:
            return None
        return self.global_variable(name)

    def global_variable(self, name):
        """The global variable called ``name``, if there is one."""
        program = self.program_entry
        return program.top_level(name) if program else None

    def global_variables(self):
        """Every variable of the program scope, blocks in ``main`` included."""
        entry = self.program_entry
        return entry.variables if entry else []
