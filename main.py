"""Command-line entry point: compile a Little Duck program and run it.

    python main.py [source-file] [--ir-base NAME] [--no-optimize]

Reads the given source file (``input.txt`` by default), compiles it and, if
there were no errors, executes the intermediate representation on the virtual
machine.

Exit status is 0 when the program ran to completion, and 1 when compilation
failed or the program hit a runtime error.
"""

import argparse
import sys

from littleduck import (VirtualMachine, VMRuntimeError, compile_file,
                        load_program)
from littleduck.compiler import DEFAULT_OUTPUT_BASE

DEFAULT_SOURCE = "input.txt"


def parse_arguments(argv):
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Compile a Little Duck program and run it.")
    parser.add_argument("source", nargs="?", default=DEFAULT_SOURCE,
                        help="source file to compile (default: %(default)s)")
    parser.add_argument("--ir-base", default=DEFAULT_OUTPUT_BASE,
                        metavar="NAME",
                        help="base name of the generated intermediate "
                             "representation files (default: %(default)s)")
    parser.add_argument("--no-optimize", dest="optimize",
                        action="store_false",
                        help="emit the quadruples exactly as the parser "
                             "produced them, without the optimization pass")
    parser.add_argument("--optimize-report", action="store_true",
                        help="print what the optimization pass changed")
    return parser.parse_args(argv)


def main(argv=None):
    arguments = parse_arguments(argv)

    try:
        result = compile_file(arguments.source, arguments.ir_base,
                              arguments.optimize)
    except OSError as error:
        print("Could not open the source file '%s': %s"
              % (arguments.source, error))
        return 1

    if not result.ok:
        result.print_errors()
        return 1

    if arguments.optimize_report and result.optimization is not None:
        print("Optimizer: " + result.optimization.summary())

    machine = VirtualMachine(load_program(result.ir_path))
    try:
        machine.run()
    except VMRuntimeError as error:
        # Whatever the program printed before the fault is still its output.
        _write_output(machine.output_text(), end_with_newline=True)
        print(error.describe())
        return 1

    _write_output(machine.output_text())
    return 0


def _write_output(text, end_with_newline=False):
    if not text:
        return
    sys.stdout.write(text)
    if end_with_newline and not text.endswith('\n'):
        sys.stdout.write('\n')


if __name__ == '__main__':
    sys.exit(main())
