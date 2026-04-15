"""
Little Duck - Entrega 3: Integrador principal

Punto de entrada unico de la entrega. Abre automaticamente "input.txt"
(o, alternativamente, un nombre de archivo dado por la terminal), compila el
codigo fuente (lexico, sintaxis, semantica y generacion de representacion
intermedia) y, si no hubo errores de compilacion, ejecuta la representacion
intermedia con la maquina virtual.

Salida:
  - Si hay errores de compilacion: se imprimen con su numero de linea y se
    aborta (no se ejecuta nada mas).
  - Si la compilacion es valida: NO se imprimen mensajes adicionales; se
    escriben los dos archivos de representacion intermedia (uno con nombres
    para depuracion y uno en direcciones para la VM) y se ejecuta el programa,
    mostrando su salida.
  - Si hay un error en tiempo de ejecucion: se reporta y se aborta.
"""

import sys

import compiler
import virtual_machine as vm


def main():
    # Nombre de archivo: por defecto "input.txt"; o el primer argumento.
    nombre = sys.argv[1] if len(sys.argv) > 1 else "input.txt"

    try:
        codigo = open(nombre).read()
    except OSError as e:
        print("No se pudo abrir el archivo de entrada '%s': %s" % (nombre, e))
        return 1

    # Fase 1: compilacion. base_salida controla los nombres de los .txt.
    ok, archivo_dir = compiler.compilar(codigo, base_salida="ir")
    if not ok:
        # Los errores de compilacion ya se imprimieron, con su numero de linea.
        return 1

    # Fase 2: ejecucion en la maquina virtual (programa independiente).
    try:
        programa = vm.cargar_programa(archivo_dir)
        maquina = vm.VM(programa)
        maquina.ejecutar()
    except vm.RuntimeErrorVM as e:
        # Error en tiempo de ejecucion: se reporta y se aborta.
        # Si ya se habia impreso algo del programa, se vuelca antes del error.
        salida_parcial = maquina.texto_salida() if 'maquina' in dir() else ""
        if salida_parcial:
            sys.stdout.write(salida_parcial)
            if not salida_parcial.endswith('\n'):
                sys.stdout.write('\n')
        if e.quad_num is not None:
            print("Error en tiempo de ejecucion (cuadruplo %d): %s"
                  % (e.quad_num, e.mensaje))
        else:
            print("Error en tiempo de ejecucion: %s" % e.mensaje)
        return 1

    # Programa terminado correctamente: mostrar su salida.
    sys.stdout.write(maquina.texto_salida())
    return 0


if __name__ == '__main__':
    sys.exit(main())