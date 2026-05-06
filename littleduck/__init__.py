"""Little Duck: a compiler and virtual machine for a small imperative language.

The pipeline is:

``littleduck.lexer``     turns source text into tokens.
``littleduck.grammar``   parses, type-checks and emits quadruples in one pass.
``littleduck.ir``        resolves names to virtual addresses and writes the files.
``littleduck.vm``        loads the address file and executes it.
"""

from .compiler import CompilationResult, compile_file, compile_source
from .vm import VirtualMachine, VMRuntimeError, load_program

__all__ = [
    'CompilationResult',
    'compile_source',
    'compile_file',
    'VirtualMachine',
    'VMRuntimeError',
    'load_program',
]
