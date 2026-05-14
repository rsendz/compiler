"""Run every program under tests/ and compare its output with the expected one.

    python run_tests.py             run the whole suite
    python run_tests.py string      run only the tests whose name matches
    python run_tests.py --update    rewrite the .expected files

Each ``<name>.txt`` under a suite directory is compiled and run, and everything
it prints is compared against ``<name>.expected``. The suite a program lives in
also says how it must finish: programs run to completion, while the two error
suites must fail. Intermediate representation files are written to a temporary
directory so a run leaves nothing behind.
"""

import argparse
import difflib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
TESTS = ROOT / "tests"

# Suite directory -> exit status every program in it must produce.
SUITES = {
    "programs": 0,
    "compile-errors": 1,
    "runtime-errors": 1,
}

EXPECTED_SUFFIX = ".expected"


class Failure(Exception):
    """A test whose output or exit status did not match."""


def discover(pattern=None):
    """Yield ``(suite, source path)`` for every test, in a stable order."""
    for suite in SUITES:
        directory = TESTS / suite
        if not directory.is_dir():
            continue
        for source in sorted(directory.glob("*.txt")):
            if pattern is None or pattern in source.stem:
                yield suite, source


def run(source, workdir):
    """Compile and run one program, returning its output and exit status."""
    completed = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), str(source),
         "--ir-base", str(workdir / "ir")],
        cwd=ROOT, capture_output=True, text=True)
    return completed.stdout + completed.stderr, completed.returncode


def check(suite, source, output, status):
    """Raise :class:`Failure` when the run does not match what was recorded."""
    wanted_status = SUITES[suite]
    if status != wanted_status:
        raise Failure("expected exit status %d, got %d\n%s"
                      % (wanted_status, status, output))

    expected_file = source.with_suffix(EXPECTED_SUFFIX)
    if not expected_file.exists():
        raise Failure("no %s file; run with --update to record one"
                      % expected_file.name)

    expected = expected_file.read_text()
    if output != expected:
        diff = difflib.unified_diff(
            expected.splitlines(keepends=True), output.splitlines(keepends=True),
            fromfile="expected", tofile="actual")
        raise Failure("output differs:\n" + "".join(diff))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="run_tests.py",
        description="Run the Little Duck test suite.")
    parser.add_argument("pattern", nargs="?",
                        help="only run tests whose name contains this text")
    parser.add_argument("--update", action="store_true",
                        help="record the current output as the expected one")
    arguments = parser.parse_args(argv)

    passed, failed, updated = 0, [], 0
    with tempfile.TemporaryDirectory() as temporary:
        workdir = pathlib.Path(temporary)
        for suite, source in discover(arguments.pattern):
            name = "%s/%s" % (suite, source.stem)
            output, status = run(source, workdir)

            if arguments.update:
                source.with_suffix(EXPECTED_SUFFIX).write_text(output)
                print("recorded %s" % name)
                updated += 1
                continue

            try:
                check(suite, source, output, status)
            except Failure as failure:
                print("FAIL %s\n%s" % (name, _indent(str(failure))))
                failed.append(name)
            else:
                print("ok   %s" % name)
                passed += 1

    if arguments.update:
        print("\nrecorded %d expected output(s)" % updated)
        return 0

    print("\n%d passed, %d failed" % (passed, len(failed)))
    for name in failed:
        print("  - %s" % name)
    return 1 if failed else 0


def _indent(text):
    return "\n".join("     " + line for line in text.splitlines())


if __name__ == "__main__":
    sys.exit(main())
