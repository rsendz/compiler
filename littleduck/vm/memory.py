"""The memory the virtual machine runs on.

Memory is simulated with dictionaries and split in two: one shared block for
globals and constants, and a stack of activation records holding the locals and
temporaries of each active call. Which block an address belongs to follows from
the address itself, using the same region layout the compiler allocated from.

Cells are reserved but not initialised. Reading a cell that was never written
is a runtime error, which is what catches a variable used before it is assigned.
"""

from ..memory import REGION_BASE, is_frame_address, region_of
from .errors import VMRuntimeError


class RuntimeMemory:
    """Global memory plus the stack of activation records."""

    def __init__(self, constants, global_counts):
        self.reserved = dict(global_counts or {})
        # Globals and constants share one block for the whole run. Only the
        # constants start out with a value.
        self.globals = dict(constants)
        self.frames = []

    # -- Activation records ------------------------------------------------
    def push_frame(self, local_counts=None):
        """Open an activation record with its own local and temporary space."""
        self.frames.append({'cells': {}, 'counts': dict(local_counts or {})})

    def pop_frame(self):
        if self.frames:
            self.frames.pop()

    @property
    def current_frame(self):
        return self.frames[-1] if self.frames else None

    # -- Reads and writes --------------------------------------------------
    def read(self, address):
        cells = self._cells_for(address)
        if address in cells:
            return cells[address]
        region = region_of(address)
        if self._is_reserved(address):
            raise VMRuntimeError(
                "read of uninitialized memory (address %d, region %s): a "
                "variable was used before being assigned a value"
                % (address, region))
        raise VMRuntimeError(
            "read of unreserved memory (address %d, region %s)"
            % (address, region))

    def write(self, address, value):
        self._cells_for(address)[address] = value

    # -- Internals ---------------------------------------------------------
    def _cells_for(self, address):
        """The dictionary an address lives in: the frame's, or the globals."""
        if not is_frame_address(address):
            return self.globals
        if not self.frames:
            raise VMRuntimeError(
                "access to local/temporary memory with no active frame "
                "(address %d)" % address)
        return self.frames[-1]['cells']

    def _is_reserved(self, address):
        """True when the address falls inside the reserved part of its region."""
        region = region_of(address)
        if region is None:
            return False
        if is_frame_address(address):
            counts = self.frames[-1]['counts'] if self.frames else {}
            reserved = counts.get(region, 0)
        else:
            reserved = self.reserved.get(region, 0)
        return REGION_BASE[region] <= address < REGION_BASE[region] + reserved
