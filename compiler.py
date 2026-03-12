"""
Little Duck - Entrega 3: Memoria virtual y representacion intermedia

Compilador de Little Duck (Python + PLY). Esta entrega extiende el lexer, el
parser LR y el analisis semantico de las entregas previas con:
  - Asignacion de DIRECCIONES DE MEMORIA VIRTUAL a variables, parametros,
    funciones, temporales y constantes, segun una convencion de regiones por
    alcance (global / local / temporal / constante) y tipo.
  - Aislamiento de scope: las funciones NO acceden a las variables globales;
    cada funcion tiene su propia region local y temporal.
  - Representacion intermedia con dos formatos de salida:
      * "ir-nombres.txt": legible, con nombres (para depuracion).
      * "ir-direcciones.txt": SOLO direcciones, apegado a la convencion de
        clase, que es el que ejecuta la maquina virtual.
  - Encabezado de memoria: lista de constantes con su direccion, contadores de
    memoria por tipo, y un bloque por funcion con su memoria local requerida.

Este modulo es el COMPILADOR. La ejecucion la realiza un programa
independiente (virtual_machine.py). El integrador main.py abre "input.txt",
compila y, si no hay errores, ejecuta la representacion intermedia con la VM.

Convencion de regiones (indices base, material de clase):
  global_int 1000  global_float 2000  global_str 3000  global_void 4000
  local_int  7000  local_float  8000  local_str  9000
  temp_int  12000  temp_float  13000  temp_bool 14000
  cte_int   17000  cte_float   18000  cte_str   19000
"""

import ply.lex as lex
import ply.yacc as yacc


# ----- LEXER -----

# Palabras reservadas: lexema -> tipo de token. t_IDENTIFIER las reclasifica.
reserved = {
    'program': 'PROGRAM',
    'var': 'VAR',
    'main': 'MAIN',
    'end': 'END',
    'int': 'INT',
    'float': 'FLOAT',
    'string': 'STRING',
    'void': 'VOID',
    'if': 'IF',
    'else': 'ELSE',
    'do': 'DO',
    'while': 'WHILE',
    'print': 'PRINT',
    'return': 'RETURN',
    'break': 'BREAK',
}

tokens = [
    'IDENTIFIER',
    'CONST_INT',
    'CONST_FLOAT',
    'CONST_STR',
    'OP_GE',       # >=
    'OP_LE',       # <=
    'OP_EQ',       # ==
    'OP_NE',       # !=
    'OP_GT',       # >
    'OP_LT',       # <
    'OP_ASIGNA',   # =
    'OP_PLUS',     # +
    'OP_MINUS',    # -
    'OP_MULT',     # *
    'OP_DIV',      # /
    'SEMICOL',     # ;
    'COMMA',       # ,
    'COLON',       # :
    'LBRACE',      # {
    'RBRACE',      # }
    'LBRACKET',    # [
    'RBRACKET',    # ]
    'LPAREN',      # (
    'RPAREN',      # )
] + list(reserved.values())

# Operadores de dos caracteres antes que los de uno.
t_OP_GE = r'>='
t_OP_LE = r'<='
t_OP_EQ = r'=='
t_OP_NE = r'!='
t_OP_GT = r'>'
t_OP_LT = r'<'
t_OP_ASIGNA = r'='
t_OP_PLUS = r'\+'
t_OP_MINUS = r'-'
t_OP_MULT = r'\*'
t_OP_DIV = r'/'
t_SEMICOL = r';'
t_COMMA = r','
t_COLON = r':'
t_LBRACE = r'\{'
t_RBRACE = r'\}'
t_LBRACKET = r'\['
t_RBRACKET = r'\]'
t_LPAREN = r'\('
t_RPAREN = r'\)'

lex_errors = []


def t_BAD_IDENTIFIER(t):
    # Identificador invalido (empieza con digito o '_'). Va antes que CONST_INT
    # para que "12abc" no se parta en INT + ID.
    r'[0-9]+[a-zA-Z_][a-zA-Z0-9_]*|_[a-zA-Z0-9_]*'
    msg = ("Error lexico linea %d: identificador invalido '%s' en lexpos %d "
           "(debe empezar con letra)" % (t.lexer.lineno, t.value, t.lexpos))
    lex_errors.append(msg)
    # No se retorna token: el simbolo se descarta y el lexer continua.


def t_CONST_FLOAT(t):
    r'[0-9]+\.[0-9]+'
    t.value = float(t.value)
    return t


def t_CONST_INT(t):
    r'[0-9]+'
    t.value = int(t.value)
    return t


def t_CONST_STR(t):
    r'"[^"]*"'
    return t


def t_IDENTIFIER(t):
    r'[a-zA-Z][a-zA-Z0-9_]*'
    t.type = reserved.get(t.value, 'IDENTIFIER')
    return t


def t_COMMENT(t):
    r'\#.*'
    pass


def t_newline(t):
    r'\n+'
    t.lexer.lineno += len(t.value)


t_ignore = ' \t'


def t_error(t):
    msg = ("Error lexico linea %d: simbolo no reconocido '%s' en lexpos %d"
           % (t.lexer.lineno, t.value[0], t.lexpos))
    lex_errors.append(msg)
    t.lexer.skip(1)


lexer = lex.lex()


# ----- ESTRUCTURAS SEMANTICAS Y DE GENERACION DE CODIGO -------

# Texto fuente (para calcular columnas en errores) y listas de errores.
source_text = ""
parse_errors = []
recovery_notes = []
semantic_errors = []

# Tabla de simbolos: el directorio de funciones. Cada entrada es un dict.
#   func_dir[nombre] = {
#       'kind': 'program' | 'function',
#       'is_function': bool,
#       'return_type': 'void'|'int'|'float'|'string',
#       'params': [(nombre, tipo), ...],   # orden de declaracion
#       'vars':   { nombre: {'type','scope','is_param'} },  # tabla de variables
#       'start_quad': int|None,
#   }
func_dir = {}

program_name = None        # nombre del programa = scope global
scope_stack = []           # pila de scopes; el tope es el scope actual

# Pila de operandos: cada elemento es (direccion/nombre, tipo).
operand_stack = []

# Pila de saltos (pending jumps): guarda indices de cuadruplos por rellenar
# (if/else) o el numero de cuadruplo de inicio de un ciclo (do-while).
pila_saltos = []

# Una pila de listas para los 'break': una lista por cada ciclo activo.
break_stack = []

# Una pila de listas para los 'return': una lista por funcion en compilacion.
return_jumps_stack = []

# Pila de "frames" de llamada: una por cada llamada a funcion en curso.
call_stack = []

# Lista temporal de ids mientras se procesa una declaracion de variables.
current_id_list = []

# Linea aproximada en curso: se actualiza en reglas con token y la usan los
# marcadores neuralgicos (que derivan en epsilon y no tienen token propio)
# y los helpers aplicar_binaria/cerrar_llamada para reportar con linea.
current_line = 0

# Lista de cuadruplos. Cada quad: [op, argL, argR, res, tipo_res].
quads = []
# Scope vigente al emitir cada quad (paralelo a quads), para resolver vars.
quad_scope = []
temp_counter = 0           # numero de variables temporales (t1, t2, ...)
goto_main_idx = None       # indice del goto inicial hacia el main


# --- Memoria virtual: regiones, contadores y asignador de direcciones ------

# Indices base de cada region (alcance + tipo). Convencion del material.
BASE = {
    'global_int': 1000, 'global_float': 2000, 'global_str': 3000,
    'global_void': 4000,
    'local_int': 7000, 'local_float': 8000, 'local_str': 9000,
    'temp_int': 12000, 'temp_float': 13000, 'temp_bool': 14000,
    'cte_int': 17000, 'cte_float': 18000, 'cte_str': 19000,
}

# Limite de direcciones por region (para no invadir la siguiente). 1000 celdas.
REGION_SIZE = 1000

# Contadores globales de cuantas direcciones se han asignado por region.
# Las temporales se cuentan POR FUNCION (se reinician al entrar a cada scope),
# pero el contador 'global' aqui sirve solo para la asignacion en compilacion;
# el numero por funcion se guarda en cada entry de func_dir.
mem_counters = {}

# Tabla de constantes: valor -> {'addr', 'type'}. Una constante no se duplica.
const_table = {}

# Mapa auxiliar para imprimir la IR con nombres: direccion -> nombre legible.
addr_to_name = {}

# Direcciones de las temporales: nombre temporal (t1, t2, ...) -> {addr, type}.
# Las temporales son LOCALES a cada scope, por lo que su contador de region se
# reinicia al cambiar de scope (ver entrar_scope / salir_scope).
temp_addrs = {}

# Pila para guardar/restaurar los contadores de region temporal por scope,
# de modo que las temporales sean locales a cada funcion.
_temp_counter_stack = []


def reset_memoria():
    """Reinicia contadores y tablas de memoria virtual."""
    mem_counters.clear()
    for region in BASE:
        mem_counters[region] = 0
    const_table.clear()
    addr_to_name.clear()
    temp_addrs.clear()
    _temp_counter_stack[:] = []


def _region(scope_kind, tipo):
    """Construye el nombre de region a partir del alcance y el tipo.

    scope_kind: 'global' | 'local' | 'temp' | 'cte'
    tipo: 'int' | 'float' | 'string' | 'bool' | 'void'
    """
    t = 'str' if tipo == 'string' else tipo
    return '%s_%s' % (scope_kind, t)


def nueva_direccion(scope_kind, tipo, nombre=None):
    """Asigna y devuelve una nueva direccion en la region (scope_kind, tipo).

    Lleva el contador de la region, valida que no se exceda el tamano y,
    si se da un nombre, lo registra en addr_to_name para la IR legible.
    """
    region = _region(scope_kind, tipo)
    if region not in BASE:
        # Tipo/alcance no soportado: se usa una region 'void' como salvaguarda.
        region = _region(scope_kind, 'void') if (scope_kind + '_void') in BASE \
            else 'global_void'
    offset = mem_counters[region]
    if offset >= REGION_SIZE:
        semantic_errors.append(
            "Error semantico: se excedio la capacidad de la region de memoria "
            "'%s'" % region)
        return BASE[region]  # se devuelve la base para no romper la traduccion
    mem_counters[region] += 1
    addr = BASE[region] + offset
    if nombre is not None:
        addr_to_name[addr] = nombre
    return addr


def direccion_constante(valor, tipo):
    """Devuelve la direccion de una constante, asignandola la primera vez."""
    clave = (tipo, valor)
    if clave in const_table:
        return const_table[clave]['addr']
    addr = nueva_direccion('cte', tipo, nombre=repr(valor))
    const_table[clave] = {'addr': addr, 'type': tipo, 'value': valor}
    return addr


def nuevo_temporal(tipo):
    """Crea una temporal local del tipo dado, le asigna direccion y la registra.

    Devuelve el nombre ('t1', 't2', ...). La direccion queda en temp_addrs.
    El nombre del temporal es unico por scope porque temp_counter se reinicia
    al entrar a cada funcion (ver entrar_scope_temporales).
    """
    global temp_counter
    temp_counter += 1
    scope = scope_stack[-1] if scope_stack else program_name
    name = 't%d_%s' % (temp_counter, scope)   # unico globalmente para el mapa
    region_tipo = 'bool' if tipo == 'bool' else tipo
    addr = nueva_direccion('temp', region_tipo, nombre='t%d' % temp_counter)
    temp_addrs[name] = {'addr': addr, 'type': tipo}
    # Contabiliza la temporal en la funcion actual (para su encabezado de mem).
    entry = func_dir.get(scope)
    if entry is not None:
        key = 'temp_%s' % ('str' if tipo == 'string' else tipo)
        entry.setdefault('mem', {})
        entry['mem'][key] = entry['mem'].get(key, 0) + 1
    return name


def entrar_scope_local_y_temporales():
    """Al entrar a una funcion: guarda y reinicia los contadores de las
    regiones local_* y temp_*, para que las locales/temporales de la funcion
    empiecen al inicio de su region (aislamiento de memoria por funcion)."""
    global temp_counter
    _temp_counter_stack.append((
        temp_counter,
        mem_counters['temp_int'],
        mem_counters['temp_float'],
        mem_counters['temp_bool'],
        mem_counters['local_int'],
        mem_counters['local_float'],
        mem_counters['local_str'],
    ))
    temp_counter = 0
    mem_counters['temp_int'] = 0
    mem_counters['temp_float'] = 0
    mem_counters['temp_bool'] = 0
    mem_counters['local_int'] = 0
    mem_counters['local_float'] = 0
    mem_counters['local_str'] = 0


def salir_scope_local_y_temporales():
    """Al salir de la funcion: restaura los contadores guardados."""
    global temp_counter
    if _temp_counter_stack:
        (temp_counter,
         mem_counters['temp_int'],
         mem_counters['temp_float'],
         mem_counters['temp_bool'],
         mem_counters['local_int'],
         mem_counters['local_float'],
         mem_counters['local_str']) = _temp_counter_stack.pop()


# --- Cubo semantico --------------------------------------------------------

def build_cube():
    """Construye el cubo semantico cube[izq][der][op] -> tipo | 'error'."""
    types = ['int', 'float', 'string', 'bool']
    ops = ['+', '-', '*', '/', '<', '>', '<=', '>=', '==', '!=', '=']
    cube = {}
    for lt in types:
        cube[lt] = {}
        for rt in types:
            cube[lt][rt] = {}
            for op in ops:
                cube[lt][rt][op] = 'error'

    numeric = ['int', 'float']

    # Aritmeticos + - *  (la division se trata aparte).
    for op in ['+', '-', '*']:
        cube['int']['int'][op] = 'int'
        cube['int']['float'][op] = 'float'
        cube['float']['int'][op] = 'float'
        cube['float']['float'][op] = 'float'

    # Division: entre numericos siempre produce float.
    for lt in numeric:
        for rt in numeric:
            cube[lt][rt]['/'] = 'float'

    # Comparaciones de orden: solo numericos -> bool.
    for op in ['<', '>', '<=', '>=']:
        for lt in numeric:
            for rt in numeric:
                cube[lt][rt][op] = 'bool'

    # Igualdad: numerico-numerico y string-string -> bool.
    for op in ['==', '!=']:
        for lt in numeric:
            for rt in numeric:
                cube[lt][rt][op] = 'bool'
        cube['string']['string'][op] = 'bool'

    # Asignacion: el izquierdo es el destino, el derecho el valor.
    cube['int']['int']['='] = 'int'
    cube['float']['float']['='] = 'float'
    cube['float']['int']['='] = 'float'      # int -> float es valido
    cube['string']['string']['='] = 'string'
    # int = float queda 'error' (perdida de precision).

    return cube


CUBE = build_cube()


def tipo_cubo(lt, op, rt):
    """Consulta el cubo. Propaga 'error' sin volver a reportar."""
    if lt == 'error' or rt == 'error':
        return 'error'
    try:
        return CUBE[lt][rt][op]
    except KeyError:
        return 'error'


# --- Helpers de cuadruplos -------------------------------------------------

def emit(op, argL, argR, res, res_type='-'):
    """Agrega un cuadruplo. El numero del quad es len(quads) (base 1).

    Se registra tambien el scope vigente al emitir el quad, para poder
    resolver despues las direcciones de variables/parametros locales.
    """
    quads.append([op, argL, argR, res, res_type])
    quad_scope.append(scope_stack[-1] if scope_stack else program_name)


def next_quad():
    """Numero (base 1) que tendra el proximo cuadruplo generado."""
    return len(quads) + 1


def fill(idx, target):
    """Rellena el campo 'res' del cuadruplo en el indice idx (base 0)."""
    quads[idx][3] = target


def lookup_var(name):
    """Busca una variable SOLO en el scope actual (aislamiento de memoria).

    Decision de diseno (entrega 3): las funciones NO acceden a las variables
    globales; cada funcion ve unicamente su propia tabla local (parametros y
    locales). El programa principal (main) ve unicamente las globales.
    """
    if scope_stack:
        scope = scope_stack[-1]
        entry = func_dir.get(scope)
        if entry and name in entry['vars']:
            return entry['vars'][name]
    return None


def aplicar_binaria(op):
    """Saca dos operandos, valida con el cubo, genera el quad y empuja temp."""
    global temp_counter
    if len(operand_stack) < 2:
        return
    arg_r, type_r = operand_stack.pop()
    arg_l, type_l = operand_stack.pop()
    res_type = tipo_cubo(type_l, op, type_r)
    if res_type == 'error':
        if type_l != 'error' and type_r != 'error':
            semantic_errors.append(
                "Error semantico: operacion no valida '%s %s %s'%s"
                % (type_l, op, type_r, _en_linea(current_line)))
        operand_stack.append(('error', 'error'))
        return
    temp = nuevo_temporal(res_type)
    emit(op, arg_l, arg_r, temp, res_type)
    operand_stack.append((temp, res_type))


def cerrar_llamada(funcname, result_temp=None, ln=None):
    """Valida una llamada y emite param... + gosub. Retorna (entry, ok).

    result_temp: nombre del temporal que recibira el valor de retorno (solo
    cuando la llamada aparece dentro de una expresion). Se coloca en argR del
    gosub para que la VM sepa donde copiar el retorno.
    ln: numero de linea del IDENTIFIER de la llamada (para los mensajes de
    error); si no se da, se usa current_line como respaldo.
    """
    if not ln:
        ln = current_line
    frame = call_stack.pop() if call_stack else {'args': []}
    args = frame['args']
    entry = func_dir.get(funcname)
    if entry is None or not entry.get('is_function'):
        semantic_errors.append(
            "Error semantico: la funcion '%s' no esta declarada%s"
            % (funcname, _en_linea(ln)))
        return (None, False)
    params = entry['params']
    ok = True
    if len(args) != len(params):
        semantic_errors.append(
            "Error semantico: la funcion '%s' espera %d argumento(s) y "
            "recibio %d%s" % (funcname, len(params), len(args),
                              _en_linea(ln)))
        ok = False
    else:
        i = 1
        for (aval, atype), (pname, ptype) in zip(args, params):
            if tipo_cubo(ptype, '=', atype) == 'error':
                if atype != 'error':
                    semantic_errors.append(
                        "Error semantico: el argumento %d de '%s' espera %s y "
                        "recibio %s%s" % (i, funcname, ptype, atype,
                                          _en_linea(ln)))
                ok = False
            i += 1
    # Se emiten los cuadruplos solo si coincide el numero de argumentos,
    # para mantener la representacion intermedia coherente.
    if len(args) == len(params):
        for (aval, atype), (pname, ptype) in zip(args, params):
            # param: argL = argumento evaluado; res = parametro destino (en la
            # memoria local de la funcion llamada).
            emit('param', aval, None, pname, ptype)
        # gosub: argL = nombre de la funcion (para depurar), argR = temporal
        # que recibe el retorno (o '-'), res = quad de inicio de la funcion.
        emit('gosub', funcname, result_temp if result_temp else None,
             entry.get('start_quad'), '-')
    return (entry, ok)


# ----- PARSER LR (yacc) CON ACCIONES SEMANTICAS Y GENERACION DE CUADRUPLOS -----

start = 'Programa'
