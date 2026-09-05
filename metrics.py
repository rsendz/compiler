"""Measure the compiler: grammar size, throughput and what the optimizer saves.

    python metrics.py               print every section
    python metrics.py --section optimizer

The numbers reported here come from the same programs the test suite runs, so
they describe the compiler as it is rather than a benchmark written to flatter
it. Everything is measured in this process: the grammar is read from the parser
tables PLY built, the timings come from repeated compilations of each test
program, and the optimizer's effect is the difference between compiling the
same program with the pass on and with it off.

The equivalence section is the one that matters most for the optimizer: every
program is run twice, unoptimized and optimized, and the two runs have to print
the same thing and fail in the same way. A pass that removed an instruction
that faults would show up here as a program that stops failing.
"""

import argparse
import io
import pathlib
import sys
import tempfile
import time
from contextlib import redirect_stdout

from littleduck import (VirtualMachine, VMRuntimeError, compile_source,
                       load_program, optimizer)
from littleduck.context import CONTEXT
from littleduck.symbols import FunctionDirectory
from littleduck.grammar import parser
from littleduck.lexer import RESERVED, lexer, tokens

ROOT = pathlib.Path(__file__).resolve().parent
TESTS = ROOT / "tests"

# Suites whose programs compile cleanly, and so can be timed and optimized.
COMPILING_SUITES = ("programs", "optimized", "runtime-errors")

# How many times each program is compiled when timing. The first compilation of
# a run is discarded: it pays for imports and for the caches PLY fills.
REPEATS = 20


def sources(suites=COMPILING_SUITES):
    """Every test program of the given suites, in a stable order."""
    for suite in suites:
        for path in sorted((TESTS / suite).glob("*.txt")):
            yield suite, path


def source_lines(text):
    """Lines of actual program text: blanks and comments do not count."""
    return sum(1 for line in text.splitlines()
               if line.strip() and not line.strip().startswith('#'))


def token_count(text):
    """How many tokens the lexer produces for a source text."""
    lexer.lineno = 1
    lexer.input(text)
    return sum(1 for _ in lexer)


def compile_quietly(text, output_base, optimize):
    """Compile a source text, keeping whatever it prints out of the report."""
    with redirect_stdout(io.StringIO()):
        return compile_source(text, output_base, optimize)


# --- Grammar ---------------------------------------------------------------

def grammar_metrics():
    """Size of the language the parser accepts."""
    # The first production is the augmented start rule PLY adds itself.
    productions = parser.productions[1:]
    markers = [p for p in productions if p.name.startswith('Mark')]
    recovery = [p for p in productions if 'error' in p.prod]
    nonterminals = {p.name for p in productions}
    return {
        "Grammar productions": len(productions),
        "  of which semantic markers": len(markers),
        "  of which error recovery": len(recovery),
        "Nonterminals": len(nonterminals),
        "Token types": len(tokens),
        "  of which reserved words": len(RESERVED),
        "Quadruple operators": len(sorted(quadruple_operators())),
    }


def quadruple_operators():
    """Every operator the compiler emits across the test programs."""
    operators = set()
    with tempfile.TemporaryDirectory() as temporary:
        base = str(pathlib.Path(temporary) / "ir")
        for _, path in sources():
            result = compile_quietly(path.read_text(), base, False)
            if result.ok:
                operators.update(quad.operator for quad in CONTEXT.quads)
    return operators


# --- Throughput ------------------------------------------------------------

def throughput_metrics():
    """How fast the front end turns source text into quadruples."""
    total_lines = total_tokens = total_bytes = 0
    total_seconds = 0.0
    largest = None

    with tempfile.TemporaryDirectory() as temporary:
        base = str(pathlib.Path(temporary) / "ir")
        for _, path in sources():
            text = path.read_text()
            lines = source_lines(text)
            compile_quietly(text, base, True)  # warm up, and discard

            start = time.perf_counter()
            for _ in range(REPEATS):
                compile_quietly(text, base, True)
            seconds = (time.perf_counter() - start) / REPEATS

            total_lines += lines
            total_tokens += token_count(text)
            total_bytes += len(text)
            total_seconds += seconds
            if largest is None or lines > largest[1]:
                largest = (path.stem, lines, lines / seconds)

    return {
        "Programs compiled": len(list(sources())),
        "Source lines": total_lines,
        "Tokens": total_tokens,
        "Mean compile time": "%.2f ms" % (
            total_seconds / len(list(sources())) * 1000),
        "Lines per second": "%.0f" % (total_lines / total_seconds),
        "Tokens per second": "%.0f" % (total_tokens / total_seconds),
        "Bytes per second": "%.0f" % (total_bytes / total_seconds),
        # Every compilation pays a fixed cost -- resetting the context and
        # starting the parser -- so the smaller a program is, the more of its
        # time goes there. The largest program is the closest thing here to a
        # steady-state rate.
        "Largest program": "%s, %d lines at %.0f lines/s" % largest,
    }


# --- Optimizer -------------------------------------------------------------

def optimizer_rows():
    """One row per program: quadruples before and after the pass."""
    rows = []
    with tempfile.TemporaryDirectory() as temporary:
        base = str(pathlib.Path(temporary) / "ir")
        for suite, path in sources():
            result = compile_quietly(path.read_text(), base, True)
            if not result.ok or result.optimization is None:
                continue
            rows.append((suite, path.stem, result.optimization))
    return rows


def optimizer_metrics(rows):
    """What the pass removed across the whole suite."""
    before = sum(report.before for _, _, report in rows)
    after = sum(report.after for _, _, report in rows)
    improved = [row for row in rows if row[2].after < row[2].before]
    best = max(rows, key=lambda row: 1 - row[2].after / row[2].before)
    return {
        "Programs optimized": len(rows),
        "Quadruples before": before,
        "Quadruples after": after,
        "Reduction": "%.1f%%" % ((before - after) / before * 100),
        "Programs that shrank": "%d of %d" % (len(improved), len(rows)),
        "Largest reduction": "%s (%d -> %d, %.1f%%)" % (
            best[1], best[2].before, best[2].after,
            (best[2].before - best[2].after) / best[2].before * 100),
        "Constants folded": sum(r.folded for _, _, r in rows),
        "Constants propagated": sum(r.propagated for _, _, r in rows),
        "Branches settled": sum(r.branches for _, _, r in rows),
        "Jumps simplified": sum(r.jumps for _, _, r in rows),
        "Dead instructions": sum(r.dead for _, _, r in rows),
        "Unreachable instructions": sum(r.unreachable for _, _, r in rows),
    }


# --- Equivalence -----------------------------------------------------------

def run_program(text, base, optimize):
    """Compile and run one program, returning what it printed and how it ended.

    A runtime error is part of the answer: two runs of the same program are
    only equivalent when they fail in the same way, at the same source line.
    """
    result = compile_quietly(text, base, optimize)
    if not result.ok:
        return None, "compile error", None
    machine = VirtualMachine(load_program(result.ir_path))
    try:
        machine.run()
    except VMRuntimeError as error:
        # The quadruple number is left out on purpose: the pass renumbers what
        # it keeps, so the same fault legitimately carries a different number.
        # What has to match is the failure and the line it came from.
        return machine.output_text(), error.message, error.source_line
    return machine.output_text(), "ok", None


def equivalence_metrics():
    """Check that optimizing changes nothing a program can observe."""
    same, different, faulting = 0, [], 0
    with tempfile.TemporaryDirectory() as temporary:
        directory = pathlib.Path(temporary)
        for _, path in sources():
            text = path.read_text()
            plain = run_program(text, str(directory / "plain"), False)
            optimized = run_program(text, str(directory / "opt"), True)
            if plain[1] != "ok":
                faulting += 1
            if plain == optimized:
                same += 1
            else:
                different.append((path.stem, plain, optimized))

    metrics = {
        "Programs run both ways": same + len(different),
        "Identical output and outcome": same,
        "Divergent": len(different),
        "  of which fault at run time": faulting,
    }
    for name, plain, optimized in different:
        metrics["DIVERGED: " + name] = "%r vs %r" % (plain, optimized)
    return metrics



# --- Safety guards ---------------------------------------------------------
#
# The optimizer refuses a handful of rewrites that a less careful pass would
# make. Each guard below is switched off in turn and the whole suite is run
# again: a guard that is holding something up shows as programs whose output or
# outcome changes once it is gone.

def without_zero_division_guard():
    """Fold a division by a zero constant like any other operation."""
    original = optimizer.Optimizer._folded_value

    def fold(self, quad):
        if quad.operator == '/':
            left = optimizer._constant(quad.left)
            right = optimizer._constant(quad.right)
            if optimizer.NOT_A_CONSTANT not in (left, right):
                return left / right
        return original(self, quad)

    optimizer.Optimizer._folded_value = fold
    return lambda: setattr(optimizer.Optimizer, '_folded_value', original)


def without_faulting_operand_guard():
    """Drop an unread result even when what it reads could fault."""
    original = optimizer.Optimizer._is_safe_to_drop
    optimizer.Optimizer._is_safe_to_drop = lambda self, operand: True
    return lambda: setattr(optimizer.Optimizer, '_is_safe_to_drop', original)


def without_impure_division_guard():
    """Count division as pure, so an unread one may be dropped."""
    original = optimizer.PURE_OPERATORS
    optimizer.PURE_OPERATORS = frozenset(set(original) | {'/'})
    return lambda: setattr(optimizer, 'PURE_OPERATORS', original)


def without_structural_guard():
    """Let an unreachable 'end' or 'endfun' be removed with the rest."""
    original = optimizer.STRUCTURAL
    optimizer.STRUCTURAL = frozenset()
    return lambda: setattr(optimizer, 'STRUCTURAL', original)


def without_function_seeding():
    """Walk reachability from the first quadruple alone.

    'gosub' continues at the instruction after the call, so a walk that is not
    told where the functions start never enters one.
    """
    original = FunctionDirectory.functions
    FunctionDirectory.functions = lambda self: []
    return lambda: setattr(FunctionDirectory, 'functions', original)


GUARDS = (
    ("division by a zero constant is not folded", without_zero_division_guard),
    ("an unread result that reads a variable stays",
     without_faulting_operand_guard),
    ("division is never treated as pure", without_impure_division_guard),
    ("'end' and 'endfun' survive unreachability",
     without_structural_guard),
    ("every function entry seeds the reachability walk",
     without_function_seeding),
)


def guard_metrics():
    """Run the suite once per guard, with that guard switched off."""
    metrics = {}
    for description, disable in GUARDS:
        restore = disable()
        try:
            broken = _programs_broken_by_optimizing()
        finally:
            restore()
        metrics[description] = _describe_breakage(broken)
    return metrics


def _programs_broken_by_optimizing():
    """Programs the optimizer changes the behaviour of, as it is right now."""
    broken = []
    with tempfile.TemporaryDirectory() as temporary:
        directory = pathlib.Path(temporary)
        for _, path in sources():
            text = path.read_text()
            plain = run_program(text, str(directory / "plain"), False)
            try:
                optimized = run_program(text, str(directory / "opt"), True)
            except Exception as error:            # noqa: BLE001
                # A rewrite the guard was refusing can fail in the compiler
                # itself, which is a breakage like any other.
                optimized = ("compiler crash: %s" % type(error).__name__,)
            if plain != optimized:
                broken.append((path.stem, plain, optimized))
    return broken


def _describe_breakage(broken):
    if not broken:
        return "0 of %d programs break (guard is defensive)" % len(
            list(sources()))
    names = ", ".join(name for name, _, _ in broken[:3])
    if len(broken) > 3:
        names += ", ..."
    return "%d of %d programs break: %s" % (
        len(broken), len(list(sources())), names)


SECTIONS = {
    "grammar": ("Language and grammar", grammar_metrics),
    "throughput": ("Compile throughput", throughput_metrics),
    "optimizer": ("Optimizer impact",
                  lambda: optimizer_metrics(optimizer_rows())),
    "equivalence": ("Optimized against unoptimized", equivalence_metrics),
    "guards": ("Safety guards, switched off one at a time", guard_metrics),
}


def main(argv=None):
    arguments = argparse.ArgumentParser(
        prog="metrics.py",
        description="Measure the Little Duck compiler.")
    arguments.add_argument("--section", action="append", choices=SECTIONS,
                           help="only report this section (repeatable)")
    options = arguments.parse_args(argv)

    for key in options.section or SECTIONS:
        title, measure = SECTIONS[key]
        print(title)
        print("-" * len(title))
        results = measure()
        width = max(len(name) for name in results)
        for name, value in results.items():
            print("%-*s  %s" % (width, name, value))
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
