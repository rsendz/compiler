# Little Duck Compiler (Entrega 2)

Este proyecto implementa un compilador de Little Duck en Python usando PLY (lex/yacc). La entrega 2 agrega analisis semantico y generacion de representacion intermedia (cuadruplos) sobre el lexer y parser de la entrega 1.

## Diagrama de gramatica

<img width="1453" height="684" alt="image" src="https://github.com/user-attachments/assets/fd0d3d05-5bc6-42d8-9b6c-b2a695c19682" />

## Resumen rapido

- Entrada fija: `prueba.txt`.
- Salida: imprime errores o la representacion intermedia + tabla de simbolos.
- Archivo generado: `prueba-ir.txt`.
- Parser LR con puntos neuralgicos para validar semantica y emitir cuadruplos.

## Estructura del compilador

El flujo principal vive en `compiler.py` y esta separado en cuatro fases:

1. **Lexico** (PLY lex)
2. **Sintactico** (PLY yacc)
3. **Semantico** (tabla de simbolos + cubo semantico)
4. **IR** (cuadruplos)

### 1) Lexico

- Palabras reservadas: `program`, `var`, `main`, `end`, `int`, `float`, `string`, `void`, `if`, `else`, `do`, `while`, `print`, `return`, `break`.
- Tokens para operadores y delimitadores: `+ - * / = < > <= >= == != , ; : { } [ ] ( )`.
- Constantes: `CONST_INT`, `CONST_FLOAT`, `CONST_STR`.
- Identificadores: `IDENTIFIER` (letras y numeros, sin iniciar con digito).

Errores lexico importantes:

- `t_BAD_IDENTIFIER` captura identificadores invalidos (ej. `12abc`, `_x`).
- `t_error` reporta simbolos no reconocidos.

### 2) Sintactico

- Simbolo inicial: `Programa`.
- La gramatica implementa precedencia de operadores con reglas `EXP`, `TERMINO`, `FACTOR`.
- `EXPRESSION` soporta un operador relacional opcional para producir un `bool`.
- Se definen reglas de recuperacion (por ejemplo `STATEMENT : error SEMICOL`).

Estructura base del lenguaje (resumen):

```
Programa -> PROGRAM id ; OptVars FuncList MAIN Body END
Body     -> { StmtList }
StmtList -> StmtList STATEMENT | empty
STATEMENT -> ASSIGN | CONDITION | CYCLE | F_Call | Print | Return_Statement | Break_Statement
```

### 3) Semantica

#### Directorio de funciones

La tabla de simbolos se maneja con `func_dir`:

```
func_dir[nombre] = {
  kind: 'program' | 'function',
  is_function: bool,
  return_type: 'void'|'int'|'float'|'string',
  params: [(nombre, tipo), ...],
  vars: { nombre: {type, scope, is_param} },
  start_quad: int|None
}
```

#### Manejo de scopes

- `program_name` guarda el scope global.
- `scope_stack` se usa para alternar entre global y funciones.
- `lookup_var` busca primero en el scope actual y luego en el global.

#### Cubo semantico

- `build_cube()` crea la matriz de compatibilidad `CUBE[izq][der][op]`.
- Reglas clave:
  - Aritmeticos `+ - *` entre `int/float`.
  - Division siempre produce `float`.
  - Comparaciones producen `bool`.
  - Asignacion permite `float = int`, pero no `int = float`.

#### Pila de operandos

- `operand_stack` guarda pares `(valor, tipo)`.
- `aplicar_binaria(op)` valida tipos y genera temporales `t1, t2, ...`.

#### Validaciones principales

- Variables declaradas antes de usar.
- Tipos compatibles en operaciones y asignaciones.
- Llamadas a funciones con parametros correctos (numero y tipo).
- `if` y `while` requieren expresiones booleanas.
- `return` valido solo dentro de funciones, con tipo correcto.
- `break` valido solo dentro de ciclos.

### 4) Representacion intermedia (cuadruplos)

Cada cuadruplo es: `[op, argL, argR, res, tipo_res]`.

Operaciones clave:

- `=` asignacion
- `+ - * /` aritmeticos
- `< > <= >= == !=` comparaciones
- `gotof`, `gotot`, `goto` para control de flujo
- `param`, `gosub` para llamadas
- `print` para salida
- `endfun`, `end` como cierres

Estructuras de apoyo:

- `pila_saltos`: indices a rellenar (if/else, do-while).
- `break_stack`: lista de gotos pendientes por ciclo.
- `return_jumps_stack`: gotos a parchar al terminar cada funcion.
- `call_stack`: argumentos de una llamada en progreso.

## Como se ejecuta

```
python compiler.py
```

- Lee `prueba.txt`.
- Si hay errores: los imprime con contexto.
- Si no hay errores: imprime IR y tabla de simbolos, y escribe `prueba-ir.txt`.

## Rundown completo de `prueba.txt`

Contenido actual:

```
program expresiones;
var a : int;
    b : float;

main {
    a = (2 + 3) * 4;
    b = a + 1.5;
    b = a / 2;
    if (b >= 1.0) {
        print("b es ", b);
    };
}
end
```

### Paso a paso

1. **`program expresiones;`**

- Crea el scope global `expresiones`.
- Emite `gotomain` y lo deja pendiente para apuntar al inicio de `main`.

2. **`var a : int; b : float;`**

- Inserta `a` como `int` global.
- Inserta `b` como `float` global.

3. **`main { ... }`**

- El `seen_main` parcha `gotomain` al inicio del bloque principal.

4. **`a = (2 + 3) * 4;`**

- `2 + 3` produce `t1` (int).
- `t1 * 4` produce `t2` (int).
- `a = t2` valida `int = int`.

5. **`b = a + 1.5;`**

- `a` es `int`, `1.5` es `float`.
- `a + 1.5` produce `t3` (float).
- `b = t3` valida `float = float`.

6. **`b = a / 2;`**

- `a / 2` produce `t4` (float) por regla de division.
- `b = t4` valida `float = float`.

7. **`if (b >= 1.0) { print("b es ", b); };`**

- `b >= 1.0` produce `t5` (bool).
- Se emite `gotof t5` al final del `if`.
- `print("b es ", b)` genera:
  - `print "b es "`
  - `print b`
  - `print "\\n"` (salto de linea automatico)

8. **`end`**

- Emite `end` como cierre del programa.

### IR esperado

| #   | op       | argL    | argR | res | tipo  |
| --- | -------- | ------- | ---- | --- | ----- |
| 1   | gotomain | -       | -    | 2   | -     |
| 2   | +        | 2       | 3    | t1  | int   |
| 3   | \*       | t1      | 4    | t2  | int   |
| 4   | =        | t2      | -    | a   | int   |
| 5   | +        | a       | 1.5  | t3  | float |
| 6   | =        | t3      | -    | b   | float |
| 7   | /        | a       | 2    | t4  | float |
| 8   | =        | t4      | -    | b   | float |
| 9   | >=       | b       | 1.0  | t5  | bool  |
| 10  | gotof    | t5      | -    | 14  | -     |
| 11  | print    | "b es " | -    | -   | -     |
| 12  | print    | b       | -    | -   | -     |
| 13  | print    | "\\n"   | -    | -   | -     |
| 14  | end      | -       | -    | -   | -     |

Nota: el numero exacto de temporales puede variar si cambias el orden o la forma
de las expresiones, pero la secuencia logica es la misma.

## Detalles de implementacion (por bloques)

### Seccion de helpers

- `emit(op, argL, argR, res, tipo)` agrega un cuadruplo.
- `next_quad()` calcula el indice del siguiente cuadruplo (base 1).
- `fill(idx, target)` parcha saltos pendientes.

### Llamadas a funcion

- `start_call` emite `sub` al iniciar una llamada.
- Se almacenan argumentos en `call_stack`.
- `cerrar_llamada` valida parametros, emite `param` y `gosub`.
- Si la llamada es expresion, se copia el retorno a un temporal.

### Return

- Para funciones no-void, el valor se asigna a un simbolo con el nombre
  de la funcion y se emite un `goto` al `endfun`.
- Los `goto` de retorno se parchan cuando termina la funcion.

### Break

- `break` emite un `goto` pendiente que se resuelve al cerrar el ciclo.

### Recuperacion de errores

- Reglas `error` permiten continuar parsing despues de `;` o `}`.
- `recovery_notes` documenta los puntos donde se recupero el parser.

## Archivos relevantes

- `compiler.py`: implementacion completa.
- `prueba.txt`: programa de entrada actual.
- `prueba-ir.txt`: salida con cuadruplos y tabla de simbolos.
- `parsetab.py`: tabla LR generada por PLY.
- `tests/`: casos de prueba de la materia.

## Limitaciones actuales

- No hay maquina virtual ni ejecucion del IR.
- No hay manejo de arreglos ni estructuras compuestas.
- No hay booleanos declarables (solo valores booleanos generados por comparaciones).

## Sugerencias de extension

- Agregar un runtime para ejecutar cuadruplos.
- Implementar memoria virtual para variables globales, locales y temporales.
- Agregar arrays y verificacion de indices.
- Mejorar diagnosticos con contexto de linea completa.
