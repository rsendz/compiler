"""Driving a full compilation: source text in, intermediate representation out.

The four phases -- lexical, syntax, semantic and code generation -- all run in
the single parsing pass driven from here. Errors are collected rather than
raised, so one run reports everything it can find.

Optimization is the one thing that does not fit in that pass: it needs the
whole quadruple list in front of it. It runs afterwards, on a program that
already compiled cleanly, and can be turned off.
"""

from .context import CONTEXT
from .errors import TooManyErrors
from .grammar import parser
from .ir import IntermediateCode
from .lexer import lexer
from .optimizer import optimize

DEFAULT_OUTPUT_BASE = "ir"


class CompilationResult:
    """What a compilation produced: either an IR file or a list of errors."""

    def __init__(self, ok, ir_path=None, report=(), summary="",
                 optimization=None):
        self.ok = ok
        self.ir_path = ir_path
        self.report = list(report)
        self.summary = summary
        # What the optimizer changed, or None when it did not run.
        self.optimization = optimization

    def __bool__(self):
        return self.ok

    def print_errors(self):
        """Print the collected errors, each one with its line number."""
        print("Compilation errors:")
        for line in self.report:
            print(line)
        print("")
        print(self.summary)


def compile_source(source, output_base=DEFAULT_OUTPUT_BASE, optimize_ir=True):
    """Compile Little Duck source text.

    On success both intermediate-representation files are written and the
    result points at the one the virtual machine runs. On failure nothing is
    written and the result carries the error report.
    """
    CONTEXT.reset()
    CONTEXT.source = source
    lexer.lineno = 1

    try:
        parser.parse(source, lexer=lexer, tracking=True)
    except TooManyErrors:
        # Parsing was abandoned to break a recovery loop; the errors that led
        # there are already recorded.
        pass

    if CONTEXT.errors.has_errors:
        return CompilationResult(False, report=CONTEXT.errors.report(),
                                 summary=CONTEXT.errors.summary())

    # Only a program that compiled cleanly is optimized: the quadruples of one
    # that did not are incomplete, and rewriting them would say nothing useful.
    optimization = optimize(CONTEXT) if optimize_ir else None
    ir_path = IntermediateCode(CONTEXT).write(output_base, optimization)
    return CompilationResult(True, ir_path=ir_path, optimization=optimization)


def compile_file(path, output_base=DEFAULT_OUTPUT_BASE, optimize_ir=True):
    """Compile the source file at ``path``."""
    with open(path) as handle:
        return compile_source(handle.read(), output_base, optimize_ir)
