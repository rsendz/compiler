# Metrics

Numbers describing the compiler: how much language it accepts, how fast the
front end runs, and what the optimization pass actually removes.

Everything here is produced by `metrics.py`, over the same programs the test
suite runs. Nothing was written for the benchmark, so the figures describe the
compiler as it is rather than a workload chosen to flatter it.

```bash
python3 metrics.py                      # every section
python3 metrics.py --section optimizer  # one of them
```

The measurements below were taken on an Apple M1, macOS 26.5.1, CPython
3.13.15 (arm64), against 29 programs: the 21 in `tests/programs/`, the one in
`tests/optimized/`, and the 7 in `tests/runtime-errors/`, all of which compile
cleanly. The 18 programs in `tests/compile-errors/` are excluded from the
timing and optimizer sections, since they never reach code generation.

## Language and grammar

| | |
| --- | --- |
| Grammar productions | 105 |
| - of which semantic markers (epsilon rules that only run an action) | 9 |
| - of which error recovery | 4 |
| Nonterminals | 52 |
| Token types | 45 |
| - of which reserved words | 21 |
| Quadruple operators emitted across the suite | 29 |

The productions are counted from the tables PLY built, not from the source, so
the number is what the parser really accepts. The nine `Mark...` rules are the
ones that exist only to run a semantic action at a precise point of a
production: when the condition of an `if` has just been evaluated, when a
function body is about to start. The four error rules are the recovery points
that let one run report more than the first syntax error.

## Compile throughput

| | |
| --- | --- |
| Programs compiled | 29 |
| Source lines (blank and comment lines excluded) | 706 |
| Tokens | 3,623 |
| Mean compile time | 0.8 – 1.1 ms |
| Lines per second | 22,000 – 29,500 |
| Tokens per second | 113,000 – 151,000 |
| Bytes per second | 603,000 – 809,000 |
| Largest program | `arrays`, 66 lines at 22,400 – 27,600 lines/s |

The ranges are the spread over five consecutive runs on an otherwise idle
machine; a single run reports one point inside them. Call it **~26,000 lines
per second**, and do not read more precision than that into any one number.

Each figure covers the whole front end: lexing, parsing, semantic checks,
address allocation, quadruple emission, the optimization pass and writing both
intermediate-representation files. Every program is compiled 20 times and the
first compilation of each is discarded, so the cost of importing PLY and
building the parser tables is not counted against the programs.

Every compilation still pays a fixed cost, resetting the context, starting the
parser, so the smaller a program is, the more of its time goes there. The
largest program in the suite is the closest thing here to a steady-state rate,
and it is the more honest number of the two.

These programs are small, and none of them is large enough to say what happens
at ten thousand lines. The rates are a floor for programs of this size, not an
extrapolation.

## Optimizer impact

| | |
| --- | --- |
| Quadruples before | 1,182 |
| Quadruples after | 1,113 |
| **Reduction** | **5.8%** |
| Programs that shrank | 18 of 29 |
| Largest reduction | `constant_folding`, 63 → 41 (34.9%) |

Broken down by transformation, across the whole suite:

| Transformation | Instructions |
| --- | --- |
| Constants folded | 21 |
| Constants propagated | 21 |
| Branches settled | 5 |
| Jumps simplified | 35 |
| Dead results removed | 21 |
| Unreachable instructions removed | 16 |

Per program, sorted by how much came off:

| Program | Before | After | Reduction |
| --- | ---: | ---: | ---: |
| `optimized/constant_folding` | 63 | 41 | 35% |
| `programs/all_paths_return` | 56 | 48 | 14% |
| `programs/early_return` | 39 | 35 | 10% |
| `programs/return_types` | 39 | 35 | 10% |
| `runtime-errors/folded_division_by_zero` | 10 | 9 | 10% |
| `runtime-errors/uninitialized_local` | 12 | 11 | 8% |
| `programs/operators` | 53 | 49 | 8% |
| `programs/strings_and_flow` | 40 | 37 | 8% |
| `programs/functions_and_recursion` | 45 | 42 | 7% |
| `programs/nested_calls` | 47 | 44 | 6% |
| `runtime-errors/runaway_recursion` | 17 | 16 | 6% |
| `programs/fibonacci` | 38 | 36 | 5% |
| `programs/scopes` | 63 | 60 | 5% |
| `programs/full_program` | 46 | 44 | 4% |
| `programs/nested_control_flow` | 83 | 80 | 4% |
| `programs/calls_in_expressions` | 59 | 57 | 3% |
| `programs/booleans` | 121 | 119 | 2% |
| `programs/arrays` | 161 | 160 | 1% |
| the remaining 11 programs | | | 0% |

The 5.8% figure is the honest one and it is not impressive, because most of
these programs were written to exercise a language feature rather than to give
an optimizer something to chew on. The shape of the result is the interesting
part: the pass does almost nothing to a program written in variables
(`arithmetic`, `nested_loops`, `print_types` all come out unchanged), because
it reasons about what a *temporary* holds and never about what a *variable*
holds. Give it constants and dead branches and it takes a third of the program
away.

## Optimized against unoptimized

Every program is compiled twice, once with the pass and once without, run on
the virtual machine both times, and the two runs compared:

| | |
| --- | --- |
| Programs run both ways | 29 |
| Identical output and outcome | **29** |
| Divergent | 0 |
| - of which fault at run time | 7 |

The seven that fault are the point of the check. A program that divides by
zero, reads uninitialized memory, indexes past the end of an array or recurses
away has to fail the same way and at the same source line whether it was
optimized or not. The quadruple number a fault reports is excluded from the
comparison: the pass renumbers what it keeps, so the same fault legitimately
carries a different number. The message and the source line are not excluded.

## What the safety guards are holding up

The pass refuses a handful of rewrites that a less careful one would make.
Switching each refusal off in turn and rerunning the suite says which of them
are load-bearing and which are defensive:

| Guard, switched off | Result |
| --- | --- |
| Division by a zero constant is not folded | **1 of 29 programs breaks** |
| Every function entry seeds the reachability walk | **16 of 29 programs break** |
| An unread result that reads a variable stays | 0 break (defensive) |
| Division is never treated as pure | 0 break (defensive) |
| `end` and `endfun` survive unreachability | 0 break (defensive) |

The last three are guards against constructs the current code generator does
not produce. They are worth keeping, since the thing that would produce them is
one new grammar rule away, but they are not catching anything today. The first
two are.

### Bug class 1: folding a fault out of existence

`tests/runtime-errors/folded_division_by_zero.txt`:

```
quotient = 8 / (2 - 2);
print("never reached = ", quotient);
```

The pass does fold `2 - 2` to `0`, propagates it into the division and drops
the subtraction that is now dead. The program goes from ten quadruples to
nine:

```
             unoptimized                           optimized
 4  -      2    2    t1                  4  /      8    0    t2
 5  /      8    t1   t2                  5  =      t2   -    quotient
 6  =      t2   -    quotient
```

But the division itself stays, because `8 / 0` is a runtime error in this
language and it has to remain one. Fold it and the compiler is claiming to know
an answer the machine is required to refuse.

Removing the guard and folding it like any other operation gives, over the same
program:

| Version | Prints | Ends with |
| --- | --- | --- |
| Unoptimized | `before the division` | division by zero at line 9 |
| Optimized, as shipped | `before the division` | division by zero at line 9 |
| Folding `l / 0` in Python | -- | **compiler crash**, `ZeroDivisionError` |
| Folding `l / 0` to infinity | `before the division`<br>`never reached = inf` | **exit 0** |

The last row is the one that matters. It does not crash, it does not warn, and
it does not look wrong: the program prints a plausible-looking answer and
reports success, and the fault the language guarantees is simply gone. That is
the bug class the guard exists for. An optimization is allowed to make a
program faster and never allowed to make a broken program look correct.

### Bug class 2: losing every function body

The unreachable-code walk seeds itself from the first quadruple *and* from the
first quadruple of every declared function, rather than discovering bodies by
following calls. That looks redundant until you notice that `gosub` continues
at the instruction after the call: the walk never enters a function through the
call that invokes it.

Seed only from the first quadruple, and every function body in the program is
unreachable and gets removed, while the function table in the generated file
still names the quadruple each one starts at. The machine jumps into whatever
survived at that number, and 16 of the 29 programs break, mostly as reads of
uninitialized temporaries in a region that was never really entered:

```
$ # with the seeding removed
fibonacci   plain=('fib(10) = 55\nfib(0) = 0\nfib(1) = 1\n', 'ok')
            opt=('', 'read of uninitialized memory (address 12000, region temp_int)')
```

An uncalled function is the same problem in miniature: its body is genuinely
unreachable by control flow, and it still cannot be removed, because the entry
in the function table has to keep pointing at real code.

## Test suite

47 end-to-end programs, all passing: 21 that run to completion, 1 compiled with
the optimizer's report recorded, 18 that must fail to compile, and 7 that
compile cleanly and then fail at run time. See the "Tests" section of the
[README](README.md) for what each suite covers.

## What is not measured here

- Execution speed. The Python virtual machine and the C++ one in `cpp-vm/` are
  checked against each other for agreement, not for throughput.
- Compilation of large inputs. The largest program in the suite is 66 lines.
- Memory. Nothing here reports how much the compiler or the machine allocates.
