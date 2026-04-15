"""
Little Duck - Entrega 3: Maquina virtual (quack-virtual-machine)

Programa INDEPENDIENTE del compilador. Recibe un archivo de representacion
intermedia EN DIRECCIONES (el que produce compiler.py como "<base>-direcciones.txt")
y lo ejecuta:

  - Carga el encabezado: lista de constantes, contadores de memoria por tipo y
    el bloque de memoria requerida por cada funcion.
  - Simula la memoria virtual segmentada en regiones (diccionarios), con acceso
    restringido SOLO a la memoria reservada (leer una celda sin valor es error).
  - Interpreta los cuadruplos ejecutando las instrucciones.
  - Maneja la pila de llamadas (call stack) con activation records: cada llamada
    a funcion tiene su propia copia de la memoria local y temporal, lo que
    permite recursion correcta. Las globales y constantes son compartidas; las
    funciones NO acceden a las globales (aislamiento de memoria).
  - Reporta errores de tiempo de ejecucion (division entre cero, acceso a
    memoria sin reservar, recursion maxima) y aborta.

No usa librerias externas (solo la biblioteca estandar). El formato del archivo
de entrada es el descrito en compiler.py (secciones .const, .global, .funcs,
.quads).
"""

import sys


# --- Convencion de regiones de memoria (debe coincidir con el compilador) ---
BASE = {
    'global_int': 1000, 'global_float': 2000, 'global_str': 3000,
    'global_void': 4000,
    'local_int': 7000, 'local_float': 8000, 'local_str': 9000,
    'temp_int': 12000, 'temp_float': 13000, 'temp_bool': 14000,
    'cte_int': 17000, 'cte_float': 18000, 'cte_str': 19000,
}
REGION_SIZE = 1000

# Limite de profundidad de la pila de llamadas (recursion maxima).
# Es un valor configurable; se mantiene moderado para evitar agotar la pila
# del interprete de Python antes de que la VM reporte el error.
RECURSION_LIMIT = 500


class RuntimeErrorVM(Exception):
    """Error en tiempo de ejecucion detectado por la VM."""
    def __init__(self, mensaje, quad_num=None):
        self.mensaje = mensaje
        self.quad_num = quad_num
        super().__init__(mensaje)


# --- Clasificacion de una direccion en (region, alcance) -------------------

def region_de_direccion(addr):
    """Devuelve el nombre de region ('global_int', 'temp_float', ...) a la que
    pertenece una direccion, o None si no cae en ninguna region conocida."""
    for region, base in BASE.items():
        if base <= addr < base + REGION_SIZE:
            return region
    return None


def es_local_o_temporal(addr):
    """True si la direccion pertenece a una region local o temporal (vive en el
    activation record actual); False si es global o constante (memoria global)."""
    region = region_de_direccion(addr)
    if region is None:
        return False
    return region.startswith('local_') or region.startswith('temp_')


# --- Carga del archivo de representacion intermedia ------------------------

class Programa:
    """Contiene todo lo necesario para ejecutar: constantes, contadores, el
    encabezado de funciones y la lista de cuadruplos."""
    def __init__(self):
        self.constantes = {}     # direccion -> valor (ya convertido a tipo)
        self.global_counts = {}  # region -> cantidad reservada
        self.funcs = {}          # nombre -> {'start','type','params','mem'}
        self.func_by_start = {}  # start_quad -> nombre de funcion
        self.quads = []          # lista de [op, argL, argR, res] (enteros)


def _interpretar_escapes(s):
    """Traduce las secuencias de escape de una cadena a sus caracteres reales.

    En el archivo de representacion intermedia las cadenas se guardan en una
    sola linea, asi que un salto de linea se almacena como los dos caracteres
    literales '\\' y 'n'. Al cargar la constante se convierten a su caracter
    real para que, por ejemplo, "\\n" se imprima como un salto de linea.
    """
    escapes = {
        'n': '\n', 't': '\t', 'r': '\r',
        '\\': '\\', '"': '"', '0': '\0',
    }
    out = []
    i = 0
    while i < len(s):
        if s[i] == '\\' and i + 1 < len(s):
            siguiente = s[i + 1]
            if siguiente in escapes:
                out.append(escapes[siguiente])
                i += 2
                continue
        out.append(s[i])
        i += 1
    return ''.join(out)


def _convertir_constante(texto):
    """Convierte el texto de una constante a su valor Python.

    Las cadenas vienen entre comillas dobles; los flotantes tienen punto; el
    resto se interpreta como entero.
    """
    if len(texto) >= 2 and texto[0] == '"' and texto[-1] == '"':
        return _interpretar_escapes(texto[1:-1])
    try:
        if '.' in texto:
            return float(texto)
        return int(texto)
    except ValueError:
        return texto


def cargar_programa(path):
    """Lee el archivo de IR en direcciones y construye un objeto Programa."""
    prog = Programa()
    seccion = None
    func_actual = None

    # Nombres de las secciones del archivo de IR (sin punto, como en clase).
    SECCIONES = {'const', 'global', 'funcs', 'quads'}

    with open(path, 'r') as f:
        for raw in f:
            linea = raw.rstrip('\n')
            if not linea.strip():
                continue
            # Una linea es un marcador de seccion si su contenido completo es
            # uno de los nombres reservados de seccion.
            if linea.strip() in SECCIONES:
                seccion = linea.strip()
                continue

            if seccion == 'const':
                # 'valor   direccion'  (el valor puede contener espacios, p.ej.
                # un string "c = "). La direccion es el ultimo token; el valor
                # es todo lo anterior. Se usa rsplit con maxsplit=1 para separar
                # solo el ultimo bloque de espacios y tolerar la alineacion.
                valor_txt, addr_txt = linea.rstrip().rsplit(None, 1)
                addr = int(addr_txt)
                prog.constantes[addr] = _convertir_constante(valor_txt.strip())

            elif seccion == 'global':
                region, num = linea.split()
                prog.global_counts[region] = int(num)

            elif seccion == 'funcs':
                partes = linea.split()
                if partes[0] == 'func':
                    # func <nombre> <start_quad> <tipo>
                    func_actual = {
                        'name': partes[1],
                        'start': int(partes[2]),
                        'type': partes[3],
                        'params': 0,
                        'mem': {},
                    }
                elif partes[0] == 'params':
                    func_actual['params'] = int(partes[1])
                elif partes[0] == 'endfunc':
                    prog.funcs[func_actual['name']] = func_actual
                    prog.func_by_start[func_actual['start']] = \
                        func_actual['name']
                    func_actual = None
                else:
                    # region <num>  (local_int, temp_int, ...)
                    func_actual['mem'][partes[0]] = int(partes[1])

            elif seccion == 'quads':
                # num  op  argL  argR  res   (separados por espacios o tabs)
                partes = linea.split()
                # La linea de encabezado de columnas ('# op argL argR res') y
                # cualquier linea cuyo primer campo no sea un numero de quad se
                # ignoran: no son cuadruplos.
                if not partes[0].lstrip('-').isdigit():
                    continue
                # partes[0] = numero de quad (1-based), se ignora (es el indice)
                op = partes[1]
                aL = int(partes[2])
                aR = int(partes[3])
                res = int(partes[4])
                prog.quads.append([op, aL, aR, res])

    return prog


# --- Memoria de la VM ------------------------------------------------------

def _valor_default(region):
    """Valor por defecto al reservar una celda, segun el tipo de la region."""
    if region.endswith('_int'):
        return 0
    if region.endswith('_float'):
        return 0.0
    if region.endswith('_str'):
        return ""
    if region.endswith('_bool'):
        return False
    return 0


class Memoria:
    """Memoria simulada con diccionarios. Se separa en:
      - glob: globales + constantes (compartidas durante toda la ejecucion)
      - frames de activacion: cada uno con su copia de local y temporal

    Cada region se RESERVA con un tamano (cuantas celdas declaro el compilador),
    pero las celdas NO se inicializan: una variable debe asignarse antes de
    usarse. Leer una direccion que no ha sido escrita es un error de tiempo de
    ejecucion (memoria sin inicializar si esta dentro del rango reservado, o
    memoria sin reservar si esta fuera de todo rango).
    """
    def __init__(self, constantes, global_counts):
        # Limites reservados por region: region -> cantidad de celdas.
        self.reservado = dict(global_counts) if global_counts else {}
        # Memoria global: un solo diccionario direccion -> valor. Solo se llena
        # con las constantes; las globales se escriben durante la ejecucion.
        self.glob = {}
        for addr, val in constantes.items():
            self.glob[addr] = val
        # Pila de frames; cada frame: {'celdas': dict, 'counts': dict}.
        self.frames = []

    def push_frame(self, local_counts=None):
        """Crea un activation record. Registra los limites reservados de las
        regiones local/temp de la funcion, pero NO inicializa sus celdas; cada
        local/temporal debe escribirse antes de leerse."""
        counts = dict(local_counts) if local_counts else {}
        self.frames.append({'celdas': {}, 'counts': counts})

    def pop_frame(self):
        if self.frames:
            self.frames.pop()

    def _contenedor(self, addr):
        """Devuelve el diccionario de celdas donde vive la direccion."""
        if es_local_o_temporal(addr):
            if not self.frames:
                raise RuntimeErrorVM(
                    "acceso a memoria local/temporal sin un frame activo "
                    "(direccion %d)" % addr)
            return self.frames[-1]['celdas']
        return self.glob

    def _esta_reservada(self, addr):
        """True si la direccion cae dentro del rango reservado de su region."""
        region = region_de_direccion(addr)
        if region is None:
            return False
        base = BASE[region]
        if es_local_o_temporal(addr):
            counts = self.frames[-1]['counts'] if self.frames else {}
            count = counts.get(region, 0)
        else:
            count = self.reservado.get(region, 0)
        return base <= addr < base + count

    def leer(self, addr):
        cont = self._contenedor(addr)
        if addr in cont:
            return cont[addr]
        # La celda no ha sido escrita. Se distingue el motivo del error:
        region = region_de_direccion(addr)
        if self._esta_reservada(addr):
            # Reservada por su declaracion, pero usada antes de asignarsele
            # un valor: error de uso de variable sin inicializar.
            raise RuntimeErrorVM(
                "acceso a memoria virtual sin inicializar (direccion %d, "
                "region %s): se uso una variable antes de asignarle un valor"
                % (addr, region))
        # Fuera de todo rango reservado: acceso a memoria no reservada.
        raise RuntimeErrorVM(
            "acceso a memoria virtual sin reservar (direccion %d, region %s)"
            % (addr, region))

    def escribir(self, addr, valor):
        cont = self._contenedor(addr)
        cont[addr] = valor


# --- Interprete ------------------------------------------------------------

class VM:
    def __init__(self, programa):
        self.prog = programa
        self.mem = Memoria(programa.constantes, programa.global_counts)
        self.ip = 0                 # apuntador de instruccion (indice 0-based)
        # Pila de retorno: a que quad (1-based) volver tras un endfun/return.
        self.return_stack = []
        # Argumentos pendientes de la llamada en preparacion: lista de
        # (valor, direccion_destino_en_callee).
        self.pending_params = []
        # Pila de slots de resultado: a donde copiar el retorno en el caller.
        self.result_slot_stack = []
        # Pila de slots globales de retorno de la funcion en curso (aL de gosub).
        self.func_slot_stack = []
        # Profundidad actual de la pila de llamadas (incluye el main).
        self.depth = 1
        self.salida = []            # lineas de salida del programa

    def ejecutar(self):
        quads = self.prog.quads
        n = len(quads)
        # El main (programa principal) es la base de la pila de llamadas. Sus
        # temporales son locales a su frame; sus contadores estan en el
        # encabezado global (temp_int/temp_float/temp_bool).
        main_counts = {
            'temp_int': self.prog.global_counts.get('temp_int', 0),
            'temp_float': self.prog.global_counts.get('temp_float', 0),
            'temp_bool': self.prog.global_counts.get('temp_bool', 0),
        }
        self.mem.push_frame(main_counts)
        # El primer quad es gotomain: saltar al main.
        # ip arranca en 0 (quad 1).
        while self.ip < n:
            op, aL, aR, res = quads[self.ip]
            metodo = getattr(self, 'op_' + self._nombre_op(op), None)
            if metodo is None:
                raise RuntimeErrorVM("operador desconocido '%s'" % op,
                                     self.ip + 1)
            try:
                salto = metodo(aL, aR, res)
            except RuntimeErrorVM as e:
                if e.quad_num is None:
                    e.quad_num = self.ip + 1
                raise
            if salto is None:
                self.ip += 1
            else:
                # salto es un numero de quad 1-based: convertir a indice.
                self.ip = salto - 1

    def _nombre_op(self, op):
        """Mapea el simbolo del operador al sufijo del metodo op_*."""
        tabla = {
            '+': 'suma', '-': 'resta', '*': 'mult', '/': 'div',
            '=': 'asigna',
            '>': 'mayor', '<': 'menor', '>=': 'mayoreq', '<=': 'menoreq',
            '==': 'igual', '!=': 'distinto',
            'u-': 'umenos', 'u+': 'umas',
            'gotomain': 'gotomain', 'goto': 'goto', 'gotof': 'gotof',
            'gotot': 'gotot',
            'sub': 'sub', 'param': 'param', 'gosub': 'gosub',
            'return': 'return_', 'endfun': 'endfun',
            'print': 'print_', 'newline': 'newline', 'end': 'end',
        }
        return tabla.get(op, op)

    # ---- Operaciones aritmeticas -----------------------------------------
    def _bin(self, aL, aR):
        return self.mem.leer(aL), self.mem.leer(aR)

    def op_suma(self, aL, aR, res):
        a, b = self._bin(aL, aR)
        self.mem.escribir(res, a + b)

    def op_resta(self, aL, aR, res):
        a, b = self._bin(aL, aR)
        self.mem.escribir(res, a - b)

    def op_mult(self, aL, aR, res):
        a, b = self._bin(aL, aR)
        self.mem.escribir(res, a * b)

    def op_div(self, aL, aR, res):
        a, b = self._bin(aL, aR)
        if b == 0:
            raise RuntimeErrorVM("division entre cero", self.ip + 1)
        # La division entre numericos produce float (convencion del lenguaje).
        self.mem.escribir(res, a / b)

    def op_umenos(self, aL, aR, res):
        self.mem.escribir(res, -self.mem.leer(aL))

    def op_umas(self, aL, aR, res):
        self.mem.escribir(res, +self.mem.leer(aL))

    # ---- Relacionales ----------------------------------------------------
    def op_mayor(self, aL, aR, res):
        a, b = self._bin(aL, aR); self.mem.escribir(res, a > b)

    def op_menor(self, aL, aR, res):
        a, b = self._bin(aL, aR); self.mem.escribir(res, a < b)

    def op_mayoreq(self, aL, aR, res):
        a, b = self._bin(aL, aR); self.mem.escribir(res, a >= b)

    def op_menoreq(self, aL, aR, res):
        a, b = self._bin(aL, aR); self.mem.escribir(res, a <= b)

    def op_igual(self, aL, aR, res):
        a, b = self._bin(aL, aR); self.mem.escribir(res, a == b)

    def op_distinto(self, aL, aR, res):
        a, b = self._bin(aL, aR); self.mem.escribir(res, a != b)

    # ---- Asignacion ------------------------------------------------------
    def op_asigna(self, aL, aR, res):
        self.mem.escribir(res, self.mem.leer(aL))

    # ---- Saltos ----------------------------------------------------------
    def op_gotomain(self, aL, aR, res):
        return res   # salta al quad del main

    def op_goto(self, aL, aR, res):
        return res

    def op_gotof(self, aL, aR, res):
        if not self.mem.leer(aL):
            return res
        return None

    def op_gotot(self, aL, aR, res):
        if self.mem.leer(aL):
            return res
        return None

    # ---- Llamadas a funcion ----------------------------------------------
    def op_sub(self, aL, aR, res):
        # Señaliza el inicio de una llamada. Se prepara un diccionario de
        # parametros pendientes que los 'param' iran llenando (direccion->valor).
        self.pending_params.append({})

    def op_param(self, aL, aR, res):
        # Copia el valor del argumento (aL, en el frame del caller) al slot del
        # parametro (res), que se guarda en el conjunto de params pendientes.
        valor = self.mem.leer(aL)
        self.pending_params[-1][res] = valor

    def op_gosub(self, aL, aR, res):
        # aL = direccion del slot de retorno de la funcion (global)
        # aR = direccion del temporal (en el caller) que recibira el resultado
        # res = quad de inicio de la funcion
        if self.depth + 1 > RECURSION_LIMIT:
            raise RuntimeErrorVM(
                "se excedio la profundidad maxima de recursion (%d)"
                % RECURSION_LIMIT, self.ip + 1)
        params = self.pending_params.pop()
        # Construir el activation record de la funcion llamada con su memoria
        # local/temporal reservada (segun el encabezado), luego copiar los
        # parametros recibidos sobre sus celdas.
        funcname = self.prog.func_by_start.get(res)
        local_counts = self.prog.funcs[funcname]['mem'] if funcname else {}
        self.mem.push_frame(local_counts)
        for addr, valor in params.items():
            self.mem.frames[-1]['celdas'][addr] = valor
        # Guardar a donde regresar, el slot de resultado y el slot global de la
        # funcion. self.ip es indice 0-based del gosub; el quad siguiente en
        # 1-based es (self.ip + 1) + 1 = self.ip + 2.
        self.return_stack.append(self.ip + 2)
        self.result_slot_stack.append(aR)
        self.func_slot_stack.append(aL)
        self.depth += 1
        return res   # salta al primer quad de la funcion

    def op_return_(self, aL, aR, res):
        # Copia el valor de retorno (aL) al slot global de la funcion (res).
        valor = self.mem.leer(aL)
        self.mem.escribir(res, valor)
        # El goto que sigue al return lleva al endfun.
        return None

    def op_endfun(self, aL, aR, res):
        # Fin de la funcion: se destruye el frame y se regresa al caller.
        self.mem.pop_frame()
        self.depth -= 1
        destino = self.return_stack.pop()
        result_slot = self.result_slot_stack.pop()
        func_slot = self.func_slot_stack.pop()
        # Copiar el valor de retorno (que quedo en el slot global de la funcion)
        # al temporal del caller, si la llamada esperaba un resultado.
        if (result_slot is not None and result_slot != -1
                and func_slot in self.mem.glob):
            self.mem.escribir(result_slot, self.mem.glob[func_slot])
        return destino

    # ---- Impresion -------------------------------------------------------
    def op_print_(self, aL, aR, res):
        valor = self.mem.leer(aL)
        self._emitir(self._fmt_valor(valor), salto=False)

    def op_newline(self, aL, aR, res):
        self._emitir("", salto=True)

    def _fmt_valor(self, v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, float):
            # Imprime sin ceros sobrantes pero conservando el punto si aplica.
            return repr(v)
        return str(v)

    def _emitir(self, texto, salto):
        if salto:
            # Cierra la linea actual.
            if self.salida and not self.salida[-1].endswith('\n'):
                self.salida[-1] = self.salida[-1] + '\n'
            else:
                self.salida.append('\n')
        else:
            if self.salida and not self.salida[-1].endswith('\n'):
                self.salida[-1] = self.salida[-1] + texto
            else:
                self.salida.append(texto)

    # ---- Fin -------------------------------------------------------------
    def op_end(self, aL, aR, res):
        self.ip = len(self.prog.quads)   # termina el bucle
        return None

    def texto_salida(self):
        return ''.join(self.salida)

if __name__ == '__main__':
    # Modo independiente: ejecuta directamente un archivo de representacion
    # intermedia en direcciones (por defecto "ir-direcciones.txt", o el nombre
    # dado como primer argumento de la terminal).
    nombre = sys.argv[1] if len(sys.argv) > 1 else "ir-direcciones.txt"
    try:
        programa = cargar_programa(nombre)
        maquina = VM(programa)
        maquina.ejecutar()
        sys.stdout.write(maquina.texto_salida())
    except OSError as e:
        print("No se pudo abrir el archivo de IR '%s': %s" % (nombre, e))
        sys.exit(1)
    except RuntimeErrorVM as e:
        salida = maquina.texto_salida() if 'maquina' in dir() else ""
        if salida:
            sys.stdout.write(salida)
            if not salida.endswith('\n'):
                sys.stdout.write('\n')
        if e.quad_num is not None:
            print("Error en tiempo de ejecucion (cuadruplo %d): %s"
                  % (e.quad_num, e.mensaje))
        else:
            print("Error en tiempo de ejecucion: %s" % e.mensaje)
        sys.exit(1)