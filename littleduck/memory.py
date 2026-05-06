"""Virtual memory layout and address allocation.

Every variable, parameter, temporary and constant is given a virtual address
that encodes both its scope and its type. Addresses are grouped in regions of
:data:`REGION_SIZE` consecutive slots, so the region a value belongs to can be
recovered from its address alone -- which is what lets the virtual machine
route a read or a write to the right block of memory without a symbol table.

The same layout is shared by the virtual machine (see ``littleduck.vm``), so
this module is the single source of truth for the address convention.
"""

# First address of every region, keyed by "<scope>_<type>".
REGION_BASE = {
    'global_int': 1000, 'global_float': 2000, 'global_str': 3000,
    'global_void': 4000,
    'local_int': 7000, 'local_float': 8000, 'local_str': 9000,
    'temp_int': 12000, 'temp_float': 13000, 'temp_bool': 14000,
    'cte_int': 17000, 'cte_float': 18000, 'cte_str': 19000,
}

# Number of addresses each region can hand out before invading the next one.
REGION_SIZE = 1000

# Regions saved and restored around a function, so that locals and temporaries
# of every function start at the beginning of their region.
FUNCTION_REGIONS = ('local_int', 'local_float', 'local_str',
                    'temp_int', 'temp_float', 'temp_bool')


def region_name(scope_kind, value_type):
    """Build a region key from a scope kind and a value type.

    ``scope_kind`` is one of 'global', 'local', 'temp' or 'cte'; ``value_type``
    is one of 'int', 'float', 'string', 'bool' or 'void'.
    """
    short = 'str' if value_type == 'string' else value_type
    return '%s_%s' % (scope_kind, short)


def region_of(address):
    """Return the region an address belongs to, or None if it belongs to none."""
    for region, base in REGION_BASE.items():
        if base <= address < base + REGION_SIZE:
            return region
    return None


def is_frame_address(address):
    """True when the address lives in an activation record (local/temporary).

    Global and constant addresses are shared by the whole run; local and
    temporary ones belong to whichever call is currently executing.
    """
    region = region_of(address)
    return region is not None and (region.startswith('local_')
                                   or region.startswith('temp_'))


class MemorySpace:
    """Hands out virtual addresses and keeps track of what was handed out."""

    def __init__(self, errors):
        self._errors = errors
        self.counters = {}
        self.constants = {}      # (type, value) -> {'address', 'type', 'value'}
        self.names = {}          # address -> readable name, for the debug IR
        self.temporaries = {}    # temporary name -> {'address', 'type'}
        self.temp_counter = 0
        self._saved_frames = []
        self.reset()

    def reset(self):
        self.counters = {region: 0 for region in REGION_BASE}
        self.constants.clear()
        self.names.clear()
        self.temporaries.clear()
        self.temp_counter = 0
        self._saved_frames = []

    # -- Allocation --------------------------------------------------------
    def allocate(self, scope_kind, value_type, name=None):
        """Reserve the next address of a region and return it."""
        region = region_name(scope_kind, value_type)
        if region not in REGION_BASE:
            # Unsupported scope/type combination: fall back to a void region so
            # that translation can continue instead of crashing.
            region = 'global_void'
        offset = self.counters[region]
        if offset >= REGION_SIZE:
            self._errors.add_semantic(
                "Semantic error: memory region '%s' ran out of addresses"
                % region)
            return REGION_BASE[region]
        self.counters[region] += 1
        address = REGION_BASE[region] + offset
        if name is not None:
            self.names[address] = name
        return address

    def constant(self, value, value_type):
        """Return the address of a constant, allocating it the first time."""
        key = (value_type, value)
        if key in self.constants:
            return self.constants[key]['address']
        address = self.allocate('cte', value_type, name=repr(value))
        self.constants[key] = {'address': address, 'type': value_type,
                               'value': value}
        return address

    def new_temporary(self, value_type, scope):
        """Allocate a temporary and return its (unique) name.

        Temporary numbering restarts inside every function, so the name is
        qualified with the scope to stay unique across the whole program while
        the printed name stays short.
        """
        self.temp_counter += 1
        short_name = 't%d' % self.temp_counter
        unique_name = '%s_%s' % (short_name, scope)
        region_type = 'bool' if value_type == 'bool' else value_type
        address = self.allocate('temp', region_type, name=short_name)
        self.temporaries[unique_name] = {'address': address,
                                         'type': value_type}
        return unique_name

    def is_temporary(self, name):
        return isinstance(name, str) and name in self.temporaries

    def address_of_temporary(self, name):
        return self.temporaries[name]['address']

    def sorted_constants(self):
        return sorted(self.constants.values(), key=lambda c: c['address'])

    def constant_counts(self):
        counts = {'cte_int': 0, 'cte_float': 0, 'cte_str': 0}
        for constant in self.constants.values():
            counts[region_name('cte', constant['type'])] += 1
        return counts

    # -- Per-function isolation -------------------------------------------
    def enter_function(self):
        """Restart the local and temporary regions for a new function.

        Each function owns its whole local and temporary space, so its
        addresses always start at the base of the region. The counters of the
        enclosing scope are saved and restored on the way out.
        """
        self._saved_frames.append(
            (self.temp_counter,
             {region: self.counters[region] for region in FUNCTION_REGIONS}))
        self.temp_counter = 0
        for region in FUNCTION_REGIONS:
            self.counters[region] = 0

    def exit_function(self):
        """Restore the counters saved by :meth:`enter_function`."""
        if not self._saved_frames:
            return
        self.temp_counter, saved = self._saved_frames.pop()
        self.counters.update(saved)
