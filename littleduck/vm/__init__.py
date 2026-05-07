"""The virtual machine: loads an address-based intermediate representation and
executes it.

It is independent of the compiler: give it a file produced by
:mod:`littleduck.ir` and it runs, with no access to the source or the symbol
table. The only thing the two share is the memory layout in
:mod:`littleduck.memory`.
"""

from .errors import VMRuntimeError
from .loader import Program, load_program
from .machine import VirtualMachine

__all__ = ['VMRuntimeError', 'Program', 'load_program', 'VirtualMachine']
