# Little Duck

A compiler and virtual machine for **Little Duck**, a small imperative
language, written in Python with [PLY](https://github.com/dabeaz/ply)
(lex/yacc).

The compiler runs in a single pass: while the LR parser reduces the grammar it
also fills the symbol table, checks types against a semantic cube, allocates
virtual memory addresses and emits quadruples. The result is an intermediate
representation written entirely in addresses, which a separate virtual machine
loads and executes.

## Grammar diagram

<img width="1453" height="684" alt="Little Duck grammar diagram" src="https://github.com/user-attachments/assets/fd0d3d05-5bc6-42d8-9b6c-b2a695c19682" />

## Quick start

```bash
pip install ply
```

Compile and run a program:

```bash
python main.py tests/programs/arithmetic.txt
```

With no arguments the entry point reads `input.txt`:

```bash
python main.py
```

Two files are written next to the program: `ir-names.txt`, a readable listing
meant for inspection, and `ir-addresses.txt`, the address-only listing the
machine executes. Use `--ir-base NAME` to change the base name.

The virtual machine is a program of its own and can run a listing directly,
without going through the compiler again:

```bash
python -m littleduck.vm ir-addresses.txt
```

The exit status is `0` when the program ran to completion, and `1` when
compilation failed or the program hit a runtime error.

## Project layout

```
main.py                    command-line entry point: compile, then run
littleduck/
    lexer.py               tokens and the PLY lexer
    grammar.py             the LR grammar with its semantic actions
    semantics.py           the semantic cube
    symbols.py             function directory and variable tables
    memory.py              virtual memory layout and address allocation
    quadruples.py          the intermediate representation
    context.py             the state shared by every phase
    ir.py                  address resolution and the output files
    compiler.py            the compilation driver
    errors.py              error collection and reporting
    vm/
        loader.py          reads an address listing back into memory
        memory.py          simulated memory and activation records
        machine.py         the interpreter
        errors.py          runtime errors
        __main__.py        `python -m littleduck.vm`
run_tests.py               runs every program under tests/ and checks its output
tests/
    programs/              programs that compile and run
    compile-errors/        programs rejected at compile time
    runtime-errors/        programs that compile but fail while running
```

## The language

```
program name;
var a, b : int;
    x : float;
    s : string;

int add(p : int, q : int) [
    var scratch : int;
    {
        scratch = p + q;
        return scratch;
    }
];

main {
    a = (2 + 3) * 4;
    x = a / 2;

    if (x >= 1.0) {
        print("x is ", x);
    } else {
        print("x is small");
    };

    do {
        a = a - 1;
        if (a == 5) { break; };
    } while (a > 0);

    b = add(a, 3);
}
end
```

Types are `int`, `float` and `string`; functions may also be `void`. Function
bodies are delimited by `[ ]`, blocks by `{ }`, and every statement — including
`if` and `do/while` — ends with a semicolon. Comments start with `#`.

## How it works

### 1. Lexical analysis

Reserved words: `program`, `var`, `main`, `end`, `int`, `float`, `string`,
`void`, `if`, `else`, `do`, `while`, `print`, `return`, `break`.

Operators and delimiters: `+ - * / = < > <= >= == != , ; : { } [ ] ( )`.
Constants are `CONST_INT`, `CONST_FLOAT` and `CONST_STR`; identifiers start
with a letter.

Two rules exist purely to report problems: `t_BAD_IDENTIFIER` catches names
that start with a digit or an underscore (`12abc`, `_x`) before the integer
rule can split them in two, and `t_error` reports unrecognized symbols. Neither
one stops the scan.

### 2. Syntax analysis

The start symbol is `Program`. Precedence is built into the grammar through the
`Exp` / `Term` / `Factor` chain rather than through PLY precedence
declarations, and `Expression` accepts at most one relational operator, so
`a < b < c` is rejected.

```
Program       -> ProgramHeader ; OptVars FunctionList main Body end
Body          -> { StatementList }
StatementList -> StatementList Statement | empty
Statement     -> Assignment | Condition | Loop | Call
               | Print | ReturnStatement | BreakStatement
```

Recovery rules (`Statement : error SEMICOLON`, `Body : LBRACE error RBRACE`,
and their siblings) let the parser resynchronize at the next `;` or `}` and
keep reporting. Each recovery point is noted in the error report. Past 50
syntax errors the parser is assumed to be looping and the run is abandoned.

### 3. Semantic analysis

**Function directory.** Every scope — the program itself and each function —
is one `FunctionEntry` holding its return type, its parameters in declaration
order, its variable table, the quadruple it starts at, and how much memory it
needs.

**Scopes are isolated.** A function sees only its own parameters and locals;
the main program sees only the globals. Nothing reaches across, which is what
lets every function reuse the same range of local addresses.

**Semantic cube.** `CUBE[left][right][operator]` gives the result type or
`error`. The rules that matter:

- `+ - *` between `int`/`float`, with `float` winning.
- `/` between numerics always produces `float`.
- Comparisons produce `bool`; `==` and `!=` also accept two strings.
- Assignment allows `float = int` but not `int = float`.

Assignment is modelled as an operator with the destination type on the left,
so the same table checks assignments, arguments and return values.

**Checks performed.** Variables declared before use; compatible types in
operations and assignments; calls matching their signature in arity and type;
boolean conditions in `if` and `while`; `return` only inside a function and
with the right type; a non-`void` function having at least one `return` with a
value; `break` only inside a loop; and names not colliding between variables
and functions.

### 4. Virtual memory

Every variable, parameter, temporary and constant gets an address that encodes
both its scope and its type:

| Region   | int   | float | string | bool  | void  |
| -------- | ----- | ----- | ------ | ----- | ----- |
| Global   | 1000  | 2000  | 3000   | —     | 4000  |
| Local    | 7000  | 8000  | 9000   | —     | —     |
| Temporal | 12000 | 13000 | —      | 14000 | —     |
| Constant | 17000 | 18000 | 19000  | —     | —     |

Each region holds 1000 addresses. Because the region follows from the address
alone, the machine can route a read or a write without a symbol table — which
is why the executable listing carries no names at all.

A function that returns a value owns one global slot of its return type, used
to park the value until the caller picks it up. On entering a function the
local and temporary counters restart at the base of their region and the
enclosing scope's counters are saved, so every function's memory starts from
zero.

### 5. Intermediate representation

A quadruple is `operator, left, right, result`. The operators are:

| Group      | Operators                                     |
| ---------- | --------------------------------------------- |
| Arithmetic | `+` `-` `*` `/` `u+` `u-`                     |
| Relational | `<` `>` `<=` `>=` `==` `!=`                   |
| Data       | `=`                                           |
| Control    | `gotomain` `goto` `gotof` `gotot`             |
| Calls      | `sub` `param` `gosub` `return` `endfun`       |
| Output     | `print` `newline`                             |
| End        | `end`                                         |

Jumps whose destination is not yet known are emitted with a placeholder and
patched later. The parser keeps one stack per kind of pending jump: `jumps` for
`if`/`else` and the top of a loop, `break_jumps` for the `break`s of each open
loop, and `return_jumps` for the `return`s of the function being compiled.

A unary minus is never folded into a negative literal: `-5` emits its own `u-`
quadruple, so the listing mirrors the source expression.

#### Worked example

```
program expressions;
var a : int;
    b : float;

main {
    a = (2 + 3) * 4;
    b = a + 1.5;
    b = a / 2;
    if (b >= 1.0) {
        print("b is ", b);
    };
}
end
```

`ir-names.txt`:

| #   | op       | left    | right | result | type  |
| --- | -------- | ------- | ----- | ------ | ----- |
| 1   | gotomain | -       | -     | 2      | -     |
| 2   | +        | 2       | 3     | t1     | int   |
| 3   | \*       | t1      | 4     | t2     | int   |
| 4   | =        | t2      | -     | a      | int   |
| 5   | +        | a       | 1.5   | t3     | float |
| 6   | =        | t3      | -     | b      | float |
| 7   | /        | a       | 2     | t4     | float |
| 8   | =        | t4      | -     | b      | float |
| 9   | >=       | b       | 1.0   | t5     | bool  |
| 10  | gotof    | t5      | -     | 14     | -     |
| 11  | print    | "b is " | -     | -      | -     |
| 12  | print    | b       | -     | -      | -     |
| 13  | newline  | -       | -     | -      | -     |
| 14  | end      | -       | -     | -      | -     |

The same program in `ir-addresses.txt`, preceded by its memory header:

```
const
2                        17000
3                        17001
4                        17002
1.5                      18000
1.0                      18001
"b is "                  19000

global
global_int     1
global_float   1
...

quads
1    gotomain  -1       -1       2
2    +         17000    17001    12000
3    *         12000    17002    12001
4    =         12001    -1       1000
...
```

The file has four sections. `const` lists every constant with its address,
`global` the number of slots the program needs per region, `funcs` one block
per function (where it starts, how many parameters it takes, how much local
and temporary memory it needs) and `quads` the instructions themselves, with
`-1` for an unused field.

### 6. Execution

The machine simulates memory with dictionaries, split in two: one shared block
for globals and constants, and a stack of activation records holding the locals
and temporaries of each active call. Because every call gets its own record,
recursion works as expected.

Cells are reserved but never initialised, so reading a variable before it is
assigned is a runtime error rather than a silent zero. The machine also reports
division by zero, access to memory outside any reserved region, and recursion
past its depth limit. In every case it prints whatever the program had already
produced before the fault and stops.

## Tests

```bash
python run_tests.py
```

Every `<name>.txt` under `tests/` is compiled, run, and compared against the
`<name>.expected` file beside it. The directory a program lives in also says
how it must finish, so a program that was supposed to fail but succeeded is a
failure even if its output looks right.

- `tests/programs/` — programs that compile and run: arithmetic, control flow,
  nested loops, `break`, early `return`, recursion (including a two-call
  `fib`), calls nested inside other calls, string comparison, printing every
  type, and one program exercising every construct at once.
- `tests/compile-errors/` — one file per class of rejection: bad identifiers,
  syntax errors and their recovery, type mismatches, undeclared names, name
  collisions, calls that do not match their signature, misplaced `break` and
  `return`, and a function reaching for a global variable.
- `tests/runtime-errors/` — programs that compile cleanly and then fail:
  division by zero (int and float), reading an uninitialized global or local,
  and runaway recursion.

Pass a fragment of a name to run part of the suite, and `--update` to record
the current output as the expected one after an intentional change:

```bash
python run_tests.py recursion
python run_tests.py --update
```

Review what `--update` writes before committing it: it records whatever the
compiler currently does, which is only correct if the change was intended.

Individual programs can still be run by hand:

```bash
python main.py tests/programs/full_program.txt
python main.py tests/compile-errors/type_mismatch.txt
python main.py tests/runtime-errors/division_by_zero.txt
```

## Current limitations

- No arrays or compound data structures.
- No declarable booleans; `bool` only arises from comparisons.
- Functions cannot read or write global variables.
- No short-circuit boolean operators (`and`, `or`, `not`).

## Possible extensions

- Arrays with bounds checking.
- Boolean operators and declarable `bool` variables.
- Constant folding and dead-code elimination over the quadruples.
- Richer diagnostics showing the offending source line in context.
