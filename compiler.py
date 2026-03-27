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


def _columna(lexpos):
    if not source_text:
        return lexpos + 1
    last_nl = source_text.rfind('\n', 0, lexpos)
    if last_nl < 0:
        return lexpos + 1
    return lexpos - last_nl


def _linea(p, idx):
    """Numero de linea (base 1) del simbolo idx de la regla p.

    Con tracking=True PLY expone p.lineno(idx). Si por alguna razon no hay
    informacion de linea (idx que no es terminal), devuelve 0 para no romper.
    """
    try:
        ln = p.lineno(idx)
        if ln:
            return ln
    except (AttributeError, IndexError):
        pass
    return 0


def _en_linea(ln):
    """Sufijo ' (linea N)' para anexar a los mensajes de error semantico."""
    return (" (linea %d)" % ln) if ln else ""


# --- Programa --------------------------------------------------------------

def p_ProgHeader(p):
    "ProgHeader : PROGRAM IDENTIFIER"
    global program_name, goto_main_idx
    program_name = p[2]
    func_dir[program_name] = {
        'kind': 'program',
        'is_function': False,
        'return_type': 'void',
        'params': [],
        'vars': {},
        'start_quad': None,
    }
    scope_stack.append(program_name)
    # Cuadruplo 1: salto al inicio del main (se rellena en seen_main).
    emit('gotomain', None, None, '_', '-')
    goto_main_idx = len(quads) - 1


def p_seen_main(p):
    "seen_main :"
    if goto_main_idx is not None:
        fill(goto_main_idx, next_quad())
    if program_name in func_dir:
        func_dir[program_name]['start_quad'] = next_quad()


def p_Programa(p):
    "Programa : ProgHeader SEMICOL OptVars FuncList MAIN seen_main Body END"
    emit('end', None, None, None, '-')


# --- Variables globales opcionales ----------------------------------------

def p_OptVars_with(p):
    "OptVars : VARS"
    pass


def p_OptVars_empty(p):
    "OptVars : empty"
    pass


# --- Lista de funciones ----------------------------------------------------

def p_FuncList_more(p):
    "FuncList : FuncList FUNCS"
    pass


def p_FuncList_empty(p):
    "FuncList : empty"
    pass


# --- VARS ------------------------------------------------------------------

def p_VARS(p):
    "VARS : VAR VarDeclList"
    pass


def p_VarDeclList_more(p):
    "VarDeclList : VarDeclList VarDecl"
    pass


def p_VarDeclList_one(p):
    "VarDeclList : VarDecl"
    pass


def p_VarDecl(p):
    "VarDecl : IdList COLON TYPE SEMICOL"
    vtype = p[3]
    ln = _linea(p, 2)   # linea de los ':' (cercana a la declaracion)
    scope = scope_stack[-1] if scope_stack else program_name
    entry = func_dir.get(scope)
    if entry is None:
        current_id_list[:] = []
        return
    vtable = entry['vars']
    # Alcance de la region: 'global' si el scope es el programa, 'local' si es
    # una funcion (aislamiento de memoria de la entrega 3).
    scope_kind = 'global' if scope == program_name else 'local'
    for name in current_id_list:
        if name in vtable:
            semantic_errors.append(
                "Error semantico: la variable '%s' ya fue declarada en el "
                "scope '%s'%s" % (name, scope, _en_linea(ln)))
        elif name in func_dir and func_dir[name].get('is_function'):
            semantic_errors.append(
                "Error semantico: la variable '%s' no puede tener el mismo "
                "nombre que una funcion%s" % (name, _en_linea(ln)))
        else:
            addr = nueva_direccion(scope_kind, vtype, nombre=name)
            vtable[name] = {'type': vtype, 'scope': scope, 'is_param': False,
                            'addr': addr}
            # Contabiliza la variable en el encabezado de memoria del scope.
            key = '%s_%s' % (scope_kind,
                             'str' if vtype == 'string' else vtype)
            entry.setdefault('mem', {})
            entry['mem'][key] = entry['mem'].get(key, 0) + 1
    current_id_list[:] = []


def p_IdList_more(p):
    "IdList : IdList COMMA IDENTIFIER"
    current_id_list.append(p[3])


def p_IdList_one(p):
    "IdList : IDENTIFIER"
    current_id_list.append(p[1])


# --- TYPE (cada alternativa carga su nombre de tipo) -----------------------

def p_TYPE_int(p):
    "TYPE : INT"
    p[0] = 'int'


def p_TYPE_float(p):
    "TYPE : FLOAT"
    p[0] = 'float'


def p_TYPE_string(p):
    "TYPE : STRING"
    p[0] = 'string'


# --- Funciones -------------------------------------------------------------

def p_FuncHeader(p):
    "FuncHeader : ReturnType IDENTIFIER"
    # Punto neuralgico: al ver el tipo de retorno y el id de la funcion,
    # se agrega al directorio (error si ya existe) y se cambia el scope.
    rt = p[1]
    name = p[2]
    ln = _linea(p, 2)
    p[0] = name
    if name in func_dir:
        # Puede ser otra funcion con el mismo nombre, o el nombre del programa,
        # o una variable global registrada en el scope global.
        if func_dir[name].get('is_function') or name == program_name:
            semantic_errors.append(
                "Error semantico: la funcion '%s' ya fue declarada%s"
                % (name, _en_linea(ln)))
        scope_stack.append(name)   # se cambia scope igual para no romper
        return
    # Correccion: una funcion no puede llamarse igual que una variable global.
    gvars = func_dir[program_name]['vars'] if program_name in func_dir else {}
    if name in gvars:
        semantic_errors.append(
            "Error semantico: la funcion '%s' no puede tener el mismo nombre "
            "que una variable%s" % (name, _en_linea(ln)))
    # La funcion ocupa una celda en la region global de su tipo de retorno,
    # que sirve para alojar el valor de retorno (convencion: global_<tipo>).
    func_addr = nueva_direccion('global', rt, nombre=name)
    func_dir[name] = {
        'kind': 'function',
        'is_function': True,
        'return_type': rt,
        'params': [],
        'vars': {},
        'start_quad': None,
        'has_return': False,       # se marca True al ver un return con valor
        'decl_line': ln,           # linea de la firma (para el error de return)
        'addr': func_addr,         # direccion del slot de retorno
        'mem': {},                 # contadores de memoria local de la funcion
    }
    scope_stack.append(name)
    # Aislamiento de memoria: cada funcion inicia sus regiones local y temporal
    # desde el inicio (7000.. / 12000..), de modo que las locales y temporales
    # son propias de la funcion. Se guardan los contadores previos.
    entrar_scope_local_y_temporales()


def p_ReturnType_void(p):
    "ReturnType : VOID"
    p[0] = 'void'


def p_ReturnType_typed(p):
    "ReturnType : TYPE"
    p[0] = p[1]


def p_OptParams_with(p):
    "OptParams : ParamList"
    pass


def p_OptParams_empty(p):
    "OptParams : empty"
    pass


def p_ParamList_more(p):
    "ParamList : ParamList COMMA Param"
    pass


def p_ParamList_one(p):
    "ParamList : Param"
    pass


def p_Param(p):
    "Param : IDENTIFIER COLON TYPE"
    name = p[1]
    ptype = p[3]
    ln = _linea(p, 1)
    scope = scope_stack[-1]
    entry = func_dir.get(scope)
    if entry is None:
        return
    if name in entry['vars']:
        semantic_errors.append(
            "Error semantico: el parametro '%s' ya existe en la funcion "
            "'%s'%s" % (name, scope, _en_linea(ln)))
    elif name in func_dir and func_dir[name].get('is_function'):
        semantic_errors.append(
            "Error semantico: el parametro '%s' no puede tener el mismo "
            "nombre que una funcion%s" % (name, _en_linea(ln)))
    else:
        addr = nueva_direccion('local', ptype, nombre=name)
        entry['vars'][name] = {'type': ptype, 'scope': scope,
                               'is_param': True, 'addr': addr}
        entry['params'].append((name, ptype))
        # Los parametros ocupan memoria local del tipo correspondiente.
        key = 'local_%s' % ('str' if ptype == 'string' else ptype)
        entry.setdefault('mem', {})
        entry['mem'][key] = entry['mem'].get(key, 0) + 1


def p_seen_func_start(p):
    "seen_func_start :"
    # Tras procesar params y vars locales: el proximo quad es el inicio del
    # cuerpo de la funcion -> se guarda como start_quad.
    scope = scope_stack[-1]
    if scope in func_dir:
        func_dir[scope]['start_quad'] = next_quad()
    return_jumps_stack.append([])


def p_FUNCS(p):
    ("FUNCS : FuncHeader LPAREN OptParams RPAREN LBRACKET OptVars "
     "seen_func_start Body RBRACKET SEMICOL")
    # Fin de la funcion: cuadruplo endfunc y parchado de los 'return'.
    scope = scope_stack[-1] if scope_stack else None
    entry = func_dir.get(scope)
    # Correccion: una funcion con tipo de retorno (no void) debe tener return.
    if entry is not None and entry.get('is_function'):
        if entry['return_type'] != 'void' and not entry.get('has_return'):
            ln = entry.get('decl_line', 0)
            semantic_errors.append(
                "Error semantico: la funcion '%s' de tipo %s debe tener al "
                "menos un 'return' con valor%s"
                % (scope, entry['return_type'], _en_linea(ln)))
    endq = next_quad()
    emit('endfun', None, None, None, '-')
    if return_jumps_stack:
        for idx in return_jumps_stack.pop():
            fill(idx, endq)
    if len(scope_stack) > 1:
        scope_stack.pop()
    # Restaura los contadores de region local/temporal del scope anterior.
    salir_scope_local_y_temporales()


# --- Body y statements -----------------------------------------------------

def p_Body(p):
    "Body : LBRACE StmtList RBRACE"
    pass


def p_StmtList_more(p):
    "StmtList : StmtList STATEMENT"
    pass


def p_StmtList_empty(p):
    "StmtList : empty"
    pass


def p_STATEMENT_ASSIGN(p):
    "STATEMENT : ASSIGN"
    pass


def p_STATEMENT_CONDITION(p):
    "STATEMENT : CONDITION"
    pass


def p_STATEMENT_CYCLE(p):
    "STATEMENT : CYCLE"
    pass


def p_STATEMENT_F_Call(p):
    "STATEMENT : F_Call"
    pass


def p_STATEMENT_Print(p):
    "STATEMENT : Print"
    pass


def p_STATEMENT_Return(p):
    "STATEMENT : Return_Statement"
    pass


def p_STATEMENT_Break(p):
    "STATEMENT : Break_Statement"
    pass


# --- ASSIGN ----------------------------------------------------------------

def p_ASSIGN(p):
    "ASSIGN : IDENTIFIER OP_ASIGNA EXPRESION SEMICOL"
    target = p[1]
    ln = _linea(p, 1)
    info = lookup_var(target)
    if info is None:
        semantic_errors.append(
            "Error semantico: la variable '%s' no esta declarada%s"
            % (target, _en_linea(ln)))
        if operand_stack:
            operand_stack.pop()
        return
    if not operand_stack:
        return
    val, vtype = operand_stack.pop()
    ttype = info['type']
    res = tipo_cubo(ttype, '=', vtype)
    if res == 'error':
        if vtype != 'error':
            semantic_errors.append(
                "Error semantico: no se puede asignar %s a '%s' (%s)%s"
                % (vtype, target, ttype, _en_linea(ln)))
        return
    emit('=', val, None, target, ttype)


# --- CONDITION (if / if-else) ----------------------------------------------

def p_seen_if_cond(p):
    "seen_if_cond :"
    # Tras evaluar la expresion del if: debe ser bool. Se emite gotof.
    if not operand_stack:
        return
    cond, t = operand_stack.pop()
    if t != 'bool' and t != 'error':
        semantic_errors.append(
            "Error semantico: la condicion del 'if' debe ser booleana%s"
            % _en_linea(current_line))
    emit('gotof', cond, None, '_', '-')
    pila_saltos.append(len(quads) - 1)


def p_CONDITION_if(p):
    "CONDITION : IF LPAREN EXPRESION RPAREN seen_if_cond Body SEMICOL"
    if pila_saltos:
        idx = pila_saltos.pop()
        fill(idx, next_quad())


def p_seen_else(p):
    "seen_else :"
    # Fin del bloque verdadero: se emite goto (salta el else) y se parcha
    # el gotof para que apunte al inicio del else.
    emit('goto', None, None, '_', '-')
    goto_idx = len(quads) - 1
    if pila_saltos:
        false_idx = pila_saltos.pop()
        fill(false_idx, next_quad())
    pila_saltos.append(goto_idx)


def p_CONDITION_if_else(p):
    ("CONDITION : IF LPAREN EXPRESION RPAREN seen_if_cond Body seen_else "
     "ELSE Body SEMICOL")
    if pila_saltos:
        idx = pila_saltos.pop()
        fill(idx, next_quad())


# --- CYCLE (do-while) ------------------------------------------------------

def p_seen_do(p):
    "seen_do :"
    # El proximo quad es el primero del cuerpo del ciclo (destino del gotot).
    pila_saltos.append(next_quad())
    break_stack.append([])


def p_seen_while(p):
    "seen_while :"
    # Tras evaluar la expresion del while: debe ser bool. gotot regresa al
    # inicio del ciclo si la condicion es verdadera.
    if not operand_stack:
        if pila_saltos:
            pila_saltos.pop()
        if break_stack:
            break_stack.pop()
        return
    cond, t = operand_stack.pop()
    if t != 'bool' and t != 'error':
        semantic_errors.append(
            "Error semantico: la condicion del 'while' debe ser booleana%s"
            % _en_linea(current_line))
    start = pila_saltos.pop() if pila_saltos else None
    emit('gotot', cond, None, start, '-')
    exit_q = next_quad()
    brks = break_stack.pop() if break_stack else []
    for idx in brks:
        fill(idx, exit_q)


def p_CYCLE(p):
    ("CYCLE : DO seen_do Body WHILE LPAREN EXPRESION RPAREN "
     "seen_while SEMICOL")
    pass


# --- Break -----------------------------------------------------------------

def p_Break_Statement(p):
    "Break_Statement : BREAK SEMICOL"
    if not break_stack:
        semantic_errors.append(
            "Error semantico: 'break' fuera de un ciclo%s"
            % _en_linea(_linea(p, 1)))
        return
    emit('goto', None, None, '_', '-')
    break_stack[-1].append(len(quads) - 1)


# --- Return ----------------------------------------------------------------

def p_Return_Statement_expr(p):
    "Return_Statement : RETURN EXPRESION SEMICOL"
    ln = _linea(p, 1)
    scope = scope_stack[-1] if scope_stack else None
    entry = func_dir.get(scope)
    is_func = entry is not None and entry.get('is_function')
    if not is_func:
        semantic_errors.append(
            "Error semantico: 'return' fuera de una funcion%s" % _en_linea(ln))
        if operand_stack:
            operand_stack.pop()
        return
    rt = entry['return_type']
    if not operand_stack:
        return
    val, t = operand_stack.pop()
    if rt == 'void':
        semantic_errors.append(
            "Error semantico: una funcion 'void' no debe retornar un valor%s"
            % _en_linea(ln))
        return
    if tipo_cubo(rt, '=', t) == 'error':
        if t != 'error':
            semantic_errors.append(
                "Error semantico: tipo de retorno incompatible, se esperaba "
                "%s y se obtuvo %s%s" % (rt, t, _en_linea(ln)))
        return
    # Se registro un return con valor compatible: la funcion tipada cumple.
    entry['has_return'] = True
    # El valor de retorno se copia al slot global de la funcion (su direccion);
    # luego un goto salta al endfun (parchado al cerrar la funcion).
    emit('return', val, None, scope, rt)
    emit('goto', None, None, '_', '-')
    if return_jumps_stack:
        return_jumps_stack[-1].append(len(quads) - 1)


def p_Return_Statement_void(p):
    "Return_Statement : RETURN SEMICOL"
    ln = _linea(p, 1)
    scope = scope_stack[-1] if scope_stack else None
    entry = func_dir.get(scope)
    is_func = entry is not None and entry.get('is_function')
    if not is_func:
        semantic_errors.append(
            "Error semantico: 'return' fuera de una funcion%s" % _en_linea(ln))
        return
    rt = entry['return_type']
    if rt != 'void':
        semantic_errors.append(
            "Error semantico: la funcion debe retornar un valor de tipo %s%s"
            % (rt, _en_linea(ln)))
        return
    emit('goto', None, None, '_', '-')
    if return_jumps_stack:
        return_jumps_stack[-1].append(len(quads) - 1)


# --- Print -----------------------------------------------------------------

def p_Print(p):
    "Print : PRINT LPAREN PrintArgList RPAREN SEMICOL"
    # Cada print termina implicitamente con un salto de linea (op 'newline').
    emit('newline', None, None, None, '-')


def p_PrintArgList_more(p):
    "PrintArgList : PrintArgList COMMA PrintArg"
    pass


def p_PrintArgList_one(p):
    "PrintArgList : PrintArg"
    pass


def p_PrintArg_expr(p):
    "PrintArg : EXPRESION"
    if operand_stack:
        val, t = operand_stack.pop()
        emit('print', val, None, None, '-')


# --- Llamada a funcion (statement) -----------------------------------------

def p_start_call(p):
    "start_call :"
    funcname = p[-2]
    call_stack.append({'args': []})
    emit('sub', funcname, None, None, '-')


def p_F_Call(p):
    "F_Call : IDENTIFIER LPAREN start_call OptArgs RPAREN SEMICOL"
    cerrar_llamada(p[1], ln=_linea(p, 1))


def p_OptArgs_with(p):
    "OptArgs : ArgList"
    pass


def p_OptArgs_empty(p):
    "OptArgs : empty"
    pass


def p_ArgList_more(p):
    "ArgList : ArgList COMMA EXPRESION"
    if call_stack and operand_stack:
        call_stack[-1]['args'].append(operand_stack.pop())


def p_ArgList_one(p):
    "ArgList : EXPRESION"
    if call_stack and operand_stack:
        call_stack[-1]['args'].append(operand_stack.pop())


# --- EXPRESION (con o sin operador relacional) -----------------------------

def p_EXPRESION_relop(p):
    "EXPRESION : EXP RelOp EXP"
    aplicar_binaria(p[2])


def p_EXPRESION_no_relop(p):
    "EXPRESION : EXP"
    pass


def p_RelOp_gt(p):
    "RelOp : OP_GT"
    p[0] = '>'


def p_RelOp_lt(p):
    "RelOp : OP_LT"
    p[0] = '<'


def p_RelOp_ge(p):
    "RelOp : OP_GE"
    p[0] = '>='


def p_RelOp_le(p):
    "RelOp : OP_LE"
    p[0] = '<='


def p_RelOp_eq(p):
    "RelOp : OP_EQ"
    p[0] = '=='


def p_RelOp_ne(p):
    "RelOp : OP_NE"
    p[0] = '!='


# --- EXP / TERMINO (precedencia embebida en la gramatica) ------------------

def p_EXP_plus(p):
    "EXP : EXP OP_PLUS TERMINO"
    aplicar_binaria('+')


def p_EXP_minus(p):
    "EXP : EXP OP_MINUS TERMINO"
    aplicar_binaria('-')


def p_EXP_term(p):
    "EXP : TERMINO"
    pass


def p_TERMINO_mult(p):
    "TERMINO : TERMINO OP_MULT FACTOR"
    aplicar_binaria('*')


def p_TERMINO_div(p):
    "TERMINO : TERMINO OP_DIV FACTOR"
    aplicar_binaria('/')


def p_TERMINO_factor(p):
    "TERMINO : FACTOR"
    pass


# --- FACTOR ----------------------------------------------------------------

def p_FACTOR_paren(p):
    "FACTOR : LPAREN EXPRESION RPAREN"
    pass


def p_FACTOR_plus_atom(p):
    "FACTOR : OP_PLUS Atom"
    global temp_counter
    if not operand_stack:
        return
    val, t = operand_stack.pop()
    if t == 'error':
        operand_stack.append(('error', 'error'))
        return
    if t == 'string' or t == 'bool':
        semantic_errors.append(
            "Error semantico: el operador unario '+' no aplica a %s%s"
            % (t, _en_linea(current_line)))
        operand_stack.append(('error', 'error'))
        return
    # Correccion: la suma unaria tambien se materializa como un cuadruplo 'u+'
    # (operador distinto), de forma analoga a la resta unaria.
    temp = nuevo_temporal(t)
    emit('u+', val, None, temp, t)
    operand_stack.append((temp, t))


def p_FACTOR_minus_atom(p):
    "FACTOR : OP_MINUS Atom"
    global temp_counter
    if not operand_stack:
        return
    val, t = operand_stack.pop()
    if t == 'error':
        operand_stack.append(('error', 'error'))
        return
    if t == 'string' or t == 'bool':
        semantic_errors.append(
            "Error semantico: el operador unario '-' no aplica a %s%s"
            % (t, _en_linea(current_line)))
        operand_stack.append(('error', 'error'))
        return
    # Correccion: -5 NO es una constante negativa plegada. Siempre se genera
    # un cuadruplo de negacion unaria 'u-' con su temporal, tanto para
    # constantes como para variables/temporales.
    temp = nuevo_temporal(t)
    emit('u-', val, None, temp, t)
    operand_stack.append((temp, t))


def p_FACTOR_atom(p):
    "FACTOR : Atom"
    pass


# --- Atom ------------------------------------------------------------------

def p_Atom_id(p):
    "Atom : IDENTIFIER"
    global current_line
    current_line = _linea(p, 1) or current_line
    info = lookup_var(p[1])
    if info is None:
        semantic_errors.append(
            "Error semantico: la variable '%s' no esta declarada%s"
            % (p[1], _en_linea(_linea(p, 1))))
        operand_stack.append(('error', 'error'))
    else:
        operand_stack.append((p[1], info['type']))


def p_Atom_cte(p):
    "Atom : CTE"
    pass


def p_Atom_fcall(p):
    "Atom : F_CallExpr"
    pass


def p_F_CallExpr(p):
    "F_CallExpr : IDENTIFIER LPAREN start_call OptArgs RPAREN"
    # Se valida primero existencia/tipo de retorno; si procede, se crea el
    # temporal que recibira el valor de retorno y se pasa a cerrar_llamada.
    global current_line
    ln = _linea(p, 1)
    current_line = ln or current_line
    entry = func_dir.get(p[1])
    if entry is None or not entry.get('is_function'):
        cerrar_llamada(p[1], ln=ln)
        operand_stack.append(('error', 'error'))
        return
    if entry['return_type'] == 'void':
        semantic_errors.append(
            "Error semantico: la funcion void '%s' no produce un valor "
            "utilizable en una expresion%s" % (p[1], _en_linea(ln)))
        cerrar_llamada(p[1], ln=ln)
        operand_stack.append(('error', 'error'))
        return
    rt = entry['return_type']
    temp = nuevo_temporal(rt)
    _, ok = cerrar_llamada(p[1], result_temp=temp, ln=ln)
    if not ok:
        operand_stack.append(('error', 'error'))
    else:
        operand_stack.append((temp, rt))


# --- CTE (cada tipo de constante empuja su valor y tipo) -------------------

def p_CTE_int(p):
    "CTE : CONST_INT"
    global current_line
    current_line = _linea(p, 1) or current_line
    direccion_constante(p[1], 'int')
    operand_stack.append((p[1], 'int'))


def p_CTE_float(p):
    "CTE : CONST_FLOAT"
    global current_line
    current_line = _linea(p, 1) or current_line
    direccion_constante(p[1], 'float')
    operand_stack.append((p[1], 'float'))


def p_CTE_str(p):
    "CTE : CONST_STR"
    global current_line
    current_line = _linea(p, 1) or current_line
    direccion_constante(p[1], 'string')
    operand_stack.append((p[1], 'string'))


def p_empty(p):
    "empty :"
    pass


# --- Recuperacion de errores de sintaxis -----------------------------------

def p_VarDecl_error(p):
    "VarDecl : error SEMICOL"
    recovery_notes.append(
        "  -> Recuperado en ';' (dentro de una declaracion de variables)")


def p_STATEMENT_error(p):
    "STATEMENT : error SEMICOL"
    recovery_notes.append(
        "  -> Recuperado en ';' (dentro de un statement)")


def p_Body_error(p):
    "Body : LBRACE error RBRACE"
    recovery_notes.append(
        "  -> Recuperado en '}' (dentro de un bloque)")


def p_FUNCS_error(p):
    "FUNCS : error SEMICOL"
    recovery_notes.append(
        "  -> Recuperado en ';' (dentro de la firma o cuerpo de una funcion)")
    # Defensa: si una funcion fallo, restablecer scope.
    if len(scope_stack) > 1:
        scope_stack.pop()


MAX_PARSE_ERRORS = 50   # tope de errores de sintaxis para evitar bucles


def p_error(p):
    if p:
        col = _columna(p.lexpos)
        msg = ("Error sintactico en linea %d, columna %d: token inesperado "
               "%s ('%s')" % (p.lineno, col, p.type, p.value))
        parse_errors.append(msg)
        # Si ya hay demasiados errores, se asume un ciclo de recuperacion y se
        # aborta el parsing para no agotar memoria.
        if len(parse_errors) >= MAX_PARSE_ERRORS:
            raise SyntaxError("demasiados errores de sintaxis; se aborta")
        # Descartar el token problematico para forzar el avance del parser y
        # evitar que la recuperacion de errores entre en un bucle infinito.
        parser.errok()
        return parser.token()
    else:
        parse_errors.append("Error sintactico: fin de archivo inesperado")


parser = yacc.yacc(debug=False, write_tables=False)


# ------ SALIDA: TRADUCCION A DIRECCIONES Y REPRESENTACION INTERMEDIA --------

# Operadores cuyos campos argL/argR/res son NUMEROS DE CUADRUPLO (saltos), no
# direcciones de memoria. No deben traducirse a direcciones.
SALTO_RES = {'gotomain', 'gotof', 'goto', 'gotot', 'gosub'}


def _es_nombre_temporal(x):
    return isinstance(x, str) and x in temp_addrs


def resolver_operando(x, scope):
    """Traduce un operando de quad (nombre/literal) a su direccion virtual.

    - Temporales: se buscan en temp_addrs (su nombre ya es unico).
    - Variables/parametros: se buscan en la tabla del scope del quad; si no,
      en la tabla global (para el main).
    - Constantes literales (int/float/str): se buscan en const_table.
    - None o '_' -> -1 (campo vacio).
    """
    if x is None or x == '_':
        return -1
    # Temporal
    if _es_nombre_temporal(x):
        return temp_addrs[x]['addr']
    # Nombre de funcion (su slot de retorno)
    if isinstance(x, str) and x in func_dir and func_dir[x].get('is_function'):
        return func_dir[x]['addr']
    # Variable o parametro en el scope del quad
    if isinstance(x, str):
        entry = func_dir.get(scope)
        if entry and x in entry['vars']:
            return entry['vars'][x]['addr']
        # Fallback: tabla global (programa)
        gentry = func_dir.get(program_name)
        if gentry and x in gentry['vars']:
            return gentry['vars'][x]['addr']
    # Constante literal: deducir su tipo por el valor de Python
    if isinstance(x, bool):
        return direccion_constante(x, 'int')
    if isinstance(x, int):
        return direccion_constante(x, 'int')
    if isinstance(x, float):
        return direccion_constante(x, 'float')
    if isinstance(x, str) and x.startswith('"') and x.endswith('"'):
        return direccion_constante(x, 'string')
    # Cualquier otra cosa (no deberia ocurrir): se deja como -1.
    return -1


def traducir_quads():
    """Devuelve la lista de quads en DIRECCIONES (para la VM).

    Cada quad: [op, dirL, dirR, dirRes]. Los saltos conservan el numero de
    cuadruplo en el campo correspondiente. Los campos vacios son -1.
    """
    out = []
    for i, (op, aL, aR, res, rt) in enumerate(quads):
        scope = quad_scope[i] if i < len(quad_scope) else program_name
        if op == 'gotomain':
            out.append([op, -1, -1, res if isinstance(res, int) else -1])
        elif op in ('goto',):
            out.append([op, -1, -1, res if isinstance(res, int) else -1])
        elif op in ('gotof', 'gotot'):
            dl = resolver_operando(aL, scope)
            tgt = res if isinstance(res, int) else -1
            out.append([op, dl, -1, tgt])
        elif op == 'gosub':
            # argL = nombre funcion (no se traduce a memoria), argR = temporal
            # que recibe el retorno, res = quad de inicio.
            dr = resolver_operando(aR, scope) if aR is not None else -1
            start = res if isinstance(res, int) else -1
            # Se guarda tambien la direccion del slot de retorno de la funcion.
            faddr = func_dir[aL]['addr'] if aL in func_dir else -1
            out.append([op, faddr, dr, start])
        elif op == 'sub':
            faddr = func_dir[aL]['addr'] if aL in func_dir else -1
            out.append([op, faddr, -1, -1])
        elif op == 'param':
            dl = resolver_operando(aL, scope)
            dres = resolver_operando(res, func_dir_scope_of_callee(i, res))
            out.append([op, dl, -1, dres])
        elif op in ('endfun', 'end', 'newline'):
            out.append([op, -1, -1, -1])
        elif op == 'print':
            out.append([op, resolver_operando(aL, scope), -1, -1])
        elif op == 'return':
            out.append([op, resolver_operando(aL, scope), -1,
                        resolver_operando(res, scope)])
        else:
            # Operaciones: =, +, -, *, /, u-, u+, relacionales.
            dl = resolver_operando(aL, scope)
            dr = resolver_operando(aR, scope)
            dres = resolver_operando(res, scope)
            out.append([op, dl, dr, dres])
    return out


def func_dir_scope_of_callee(quad_index, param_name):
    """Para un quad 'param', el destino (res) es un parametro de la funcion que
    se esta por llamar. Se busca hacia adelante el 'gosub' que cierra esta
    llamada para saber a que funcion pertenece el parametro."""
    for j in range(quad_index + 1, len(quads)):
        if quads[j][0] == 'gosub':
            funcname = quads[j][1]
            if funcname in func_dir:
                return funcname
            break
        if quads[j][0] == 'sub':
            break
    return program_name


def fmt(v):
    if v is None or v == '_':
        return '-'
    return str(v)


# --- Encabezado de memoria (constantes, contadores, funciones) -------------

def contar_memoria_global():
    """Cuenta variables globales por tipo (las del scope del programa)."""
    counts = {}
    gentry = func_dir.get(program_name)
    if gentry:
        for v in gentry['vars'].values():
            key = 'global_%s' % ('str' if v['type'] == 'string'
                                 else v['type'])
            counts[key] = counts.get(key, 0) + 1
    # Las funciones tambien ocupan un slot global de su tipo de retorno.
    for name, entry in func_dir.items():
        if entry.get('is_function'):
            key = 'global_%s' % ('str' if entry['return_type'] == 'string'
                                 else entry['return_type'])
            counts[key] = counts.get(key, 0) + 1
    return counts


def lineas_constantes():
    """Lista de constantes: 'valor  direccion', ordenadas por direccion.

    Se alinean en dos columnas de ancho fijo para que el archivo sea legible.
    La VM lee con .split(), asi que los espacios no afectan la interpretacion.
    """
    items = sorted(const_table.values(), key=lambda c: c['addr'])
    lines = []
    for c in items:
        v = str(c['value'])
        # Para strings se imprime con comillas tal como estan.
        lines.append("%-24s %d" % (v, c['addr']))
    return lines


def lineas_contadores_globales():
    """Contadores de memoria global y de constantes (encabezado del programa)."""
    g = contar_memoria_global()
    lines = []
    for region in ['global_int', 'global_float', 'global_str', 'global_void']:
        lines.append("%-14s %d" % (region, g.get(region, 0)))
    # Temporales del programa principal y constantes.
    main_mem = func_dir.get(program_name, {}).get('mem', {})
    for region in ['temp_int', 'temp_float', 'temp_bool']:
        lines.append("%-14s %d" % (region, main_mem.get(region, 0)))
    cte_counts = {'cte_int': 0, 'cte_float': 0, 'cte_str': 0}
    for c in const_table.values():
        key = 'cte_%s' % ('str' if c['type'] == 'string' else c['type'])
        cte_counts[key] += 1
    for region in ['cte_int', 'cte_float', 'cte_str']:
        lines.append("%-14s %d" % (region, cte_counts[region]))
    return lines


def lineas_encabezado_funciones():
    """Bloque por funcion: nombre, start_quad, tipo, params y memoria local."""
    lines = []
    for name, entry in func_dir.items():
        if not entry.get('is_function'):
            continue
        n_params = len(entry['params'])
        lines.append("func %s %d %s" % (name, entry['start_quad'],
                                        entry['return_type']))
        lines.append("params %d" % n_params)
        mem = entry.get('mem', {})
        for region in ['local_int', 'local_float', 'local_str',
                       'temp_int', 'temp_float', 'temp_bool']:
            lines.append("%-14s %d" % (region, mem.get(region, 0)))
        lines.append("endfunc")
    return lines


# --- Construccion de los dos archivos de representacion intermedia ----------

def ir_con_nombres():
    """Representacion intermedia legible (con nombres) para depuracion."""
    lines = []
    lines.append("# Representacion intermedia (NOMBRES) - solo depuracion")
    lines.append("# Constantes: valor  direccion")
    for c in sorted(const_table.values(), key=lambda c: c['addr']):
        lines.append("const\t%s\t%d" % (c['value'], c['addr']))
    lines.append("# Cuadruplos")
    header = "%-4s %-9s %-14s %-14s %-14s %-8s" % (
        "#", "op", "argL", "argR", "res", "tipo")
    lines.append(header)
    for i, (op, aL, aR, res, rt) in enumerate(quads, start=1):
        lines.append("%-4d %-9s %-14s %-14s %-14s %-8s" % (
            i, op, fmt(aL), fmt(aR), fmt(res), fmt(rt)))
    return lines


def ir_con_direcciones():
    """Representacion intermedia EN DIRECCIONES (la que ejecuta la VM).

    Formato (estricto, por secciones, convencion de clase):
      const         -> lista 'valor  direccion'
      global        -> contadores de memoria global / temporal / constante
      funcs         -> func ... endfunc por cada funcion
      quads         -> cuadruplos en direcciones, un quad por linea:
                       num op argL argR res
    """
    lines = []
    lines.append("const")
    for ln in lineas_constantes():
        lines.append(ln)
    lines.append("")
    lines.append("global")
    for ln in lineas_contadores_globales():
        lines.append(ln)
    lines.append("")
    lines.append("funcs")
    for ln in lineas_encabezado_funciones():
        lines.append(ln)
    lines.append("")
    lines.append("quads")
    tq = traducir_quads()
    # Encabezado de columnas (comentado con # para que la VM lo ignore, ya que
    # cualquier linea cuyos campos no sean numericos no es un cuadruplo valido).
    lines.append("%-4s %-9s %-8s %-8s %-8s" % ("#", "op", "argL", "argR", "res"))
    for i, q in enumerate(tq, start=1):
        op = q[0]
        aL, aR, res = q[1], q[2], q[3]
        lines.append("%-4d %-9s %-8d %-8d %-8d" % (i, op, aL, aR, res))
    return lines


def imprimir_errores():
    if lex_errors:
        print("\n%d error(es) lexico(s):" % len(lex_errors))
        for e in lex_errors:
            print("  - " + e)
    if parse_errors:
        print("\n%d error(es) sintactico(s):" % len(parse_errors))
        for e in parse_errors:
            print("  - " + e)
        if recovery_notes:
            for nota in recovery_notes:
                print(nota)
    if semantic_errors:
        print("\n%d error(es) semantico(s):" % len(semantic_errors))
        for e in semantic_errors:
            print("  - " + e)


def reset_estado():
    global source_text, program_name, goto_main_idx, temp_counter, current_line
    lex_errors[:] = []
    parse_errors[:] = []
    recovery_notes[:] = []
    semantic_errors[:] = []
    func_dir.clear()
    scope_stack[:] = []
    operand_stack[:] = []
    pila_saltos[:] = []
    break_stack[:] = []
    return_jumps_stack[:] = []
    call_stack[:] = []
    current_id_list[:] = []
    quads[:] = []
    quad_scope[:] = []
    temp_counter = 0
    program_name = None
    goto_main_idx = None
    source_text = ""
    current_line = 0
    reset_memoria()


def compilar(codigo, base_salida="ir"):
    """Compila el codigo fuente de Little Duck.

    Si no hay errores, escribe dos archivos:
      <base>-nombres.txt    (legible, depuracion)
      <base>-direcciones.txt (en direcciones, lo ejecuta la VM)
    Devuelve (ok, archivo_direcciones). Si hay errores de compilacion,
    devuelve (False, None) tras imprimirlos con su numero de linea.
    """
    global source_text
    reset_estado()
    source_text = codigo
    lexer.lineno = 1

    try:
        parser.parse(codigo, lexer=lexer, tracking=True)
    except SyntaxError:
        # Se aborto el parsing por exceso de errores (posible ciclo de
        # recuperacion). Los errores ya estan registrados en parse_errors.
        pass

    hay_errores = bool(lex_errors or parse_errors or semantic_errors)
    if hay_errores:
        print("Errores de compilacion:")
        imprimir_errores()
        print("\nTotal: %d lexico(s) + %d sintactico(s) + %d semantico(s)"
              % (len(lex_errors), len(parse_errors), len(semantic_errors)))
        return (False, None)

    # Si el analisis es valido NO se imprimen mensajes adicionales.
    nombres = ir_con_nombres()
    direcciones = ir_con_direcciones()

    archivo_nombres = base_salida + "-nombres.txt"
    archivo_dir = base_salida + "-direcciones.txt"
    with open(archivo_nombres, "w") as f:
        f.write("\n".join(nombres) + "\n")
    with open(archivo_dir, "w") as f:
        f.write("\n".join(direcciones) + "\n")

    return (True, archivo_dir)


if __name__ == '__main__':
    # Modo de prueba directa del compilador (sin VM).
    codigo = open("input.txt").read()
    ok, archivo = compilar(codigo)
    if ok:
        print("Compilacion exitosa. IR en direcciones:", archivo)