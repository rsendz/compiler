"""Check the C++ virtual machine against the executable Little Duck suites.

The Python compiler remains the producer of the address-only IR. This script
compiles every program that reaches execution, runs the C++ VM on that output,
and compares both its exit status and output to the existing golden files.

    python3 cpp-vm/test_against_suite.py cpp-vm/build/littleduck-vm
"""

import argparse
import difflib
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SUITES = {
    "programs": (0, []),
    "optimized": (0, ["--optimize-report"]),
    "runtime-errors": (1, []),
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check the C++ VM against Little Duck's executable tests.")
    parser.add_argument("vm", type=pathlib.Path,
                        help="path to the C++ littleduck-vm executable")
    arguments = parser.parse_args(argv)
    vm = arguments.vm.resolve()
    if not vm.is_file():
        parser.error("C++ VM executable does not exist: %s" % vm)

    passed, failed = 0, []
    with tempfile.TemporaryDirectory() as temporary:
        output_base = pathlib.Path(temporary) / "ir"
        for suite, (expected_status, compiler_args) in SUITES.items():
            for source in sorted((ROOT / "tests" / suite).glob("*.txt")):
                compiled = subprocess.run(
                    [sys.executable, str(ROOT / "main.py"), str(source),
                     "--ir-base", str(output_base)] + compiler_args,
                    cwd=ROOT, capture_output=True, text=True)
                ir_path = output_base.with_name(output_base.name + "-addresses.txt")
                name = "%s/%s" % (suite, source.stem)
                if not ir_path.exists():
                    failed.append((name, "the Python compiler did not produce IR\n" +
                                   compiled.stdout + compiled.stderr))
                    continue

                completed = subprocess.run([str(vm), str(ir_path)], cwd=ROOT,
                                           capture_output=True, text=True)
                actual = completed.stdout + completed.stderr
                expected = source.with_suffix(".expected").read_text()
                if suite == "optimized":
                    # The optimizer report comes from Python's compiler
                    # driver, before either VM begins execution.
                    expected = "".join(
                        line for line in expected.splitlines(keepends=True)
                        if not line.startswith("Optimizer:"))
                if completed.returncode != expected_status:
                    failed.append((name, "expected exit status %d, got %d\n%s"
                                   % (expected_status, completed.returncode, actual)))
                elif actual != expected:
                    diff = difflib.unified_diff(
                        expected.splitlines(keepends=True),
                        actual.splitlines(keepends=True),
                        fromfile="expected", tofile="C++ VM")
                    failed.append((name, "output differs:\n" + "".join(diff)))
                else:
                    print("ok   %s" % name)
                    passed += 1

    print("\n%d passed, %d failed" % (passed, len(failed)))
    for name, details in failed:
        print("FAIL %s\n%s" % (name, _indent(details)))
    return 1 if failed else 0


def _indent(text):
    return "\n".join("     " + line for line in text.splitlines())


if __name__ == "__main__":
    sys.exit(main())
