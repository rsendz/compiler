"""Run the virtual machine on its own:

    python -m littleduck.vm [ir-file]

Executes an address-based intermediate representation file
(``ir-addresses.txt`` by default) without going through the compiler.
"""

import argparse
import sys

from .errors import VMRuntimeError
from .loader import load_program
from .machine import VirtualMachine

DEFAULT_IR_FILE = "ir-addresses.txt"


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m littleduck.vm",
        description="Execute an intermediate representation file.")
    parser.add_argument("ir_file", nargs="?", default=DEFAULT_IR_FILE,
                        help="file to execute (default: %(default)s)")
    arguments = parser.parse_args(argv)

    try:
        machine = VirtualMachine(load_program(arguments.ir_file))
    except OSError as error:
        print("Could not open the intermediate representation file '%s': %s"
              % (arguments.ir_file, error))
        return 1

    try:
        machine.run()
    except VMRuntimeError as error:
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
