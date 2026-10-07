"""Compara una base .mdb de Access con lo migrado a MySQL: filas y contenido celda por celda."""
import csv
import hashlib
import io
import os
import re
import struct
import subprocess
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation

from rich.console import Console
from rich.table import Table

from config import CARPETA_SQL, conexion_servidor, pedir_parametros
from crear_estructura import leer_tipos_access, quitar_tildes

csv.field_size_limit(sys.maxsize)
console = Console()

FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"
TIPOS_BINARIOS = {"tinyblob", "blob", "mediumblob", "longblob", "binary", "varbinary"}
NUMERO = re.compile(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$")
BLOQUE = 10000


def normalizar(valor, tipo="otro"):
    """Forma canónica de una celda (texto CSV de Access o valor crudo de MySQL) para poder compararlas.

    tipo: "bin" (hex), "float"/"double" (se compara a la precisión real del tipo) u "otro" (exacto).
    """
    if valor is None:
        return ""
    if isinstance(valor, (bytes, bytearray)):
        if tipo == "bin":
            return bytes(valor).hex().upper()
        valor = bytes(valor).decode("utf-8", errors="replace")
    elif tipo == "bin":
        texto = valor[2:] if valor[:2].lower() == "0x" else valor
        return texto.upper()
    if NUMERO.match(valor):
        try:
            if tipo == "float":
                # Single de Access = 32 bits: se redondea a float32 antes de comparar (MySQL lo muestra con 6 dígitos)
                texto = format(struct.unpack("f", struct.pack("f", float(valor)))[0], ".6g")
            elif tipo == "double":
                texto = format(float(valor), ".15g")
            else:
                texto = format(Decimal(valor).normalize(), "f")
            return "0" if texto in ("-0", "-0.0") else texto
        except (ValueError, InvalidOperation, OverflowError):
            return valor
    return valor


def tipo_columna(data_type):
    data_type = str(data_type).lower()
    if data_type in TIPOS_BINARIOS:
        return "bin"
    if data_type in ("float", "real"):
        return "float"
    if data_type == "double":
        return "double"
    return "otro"


def huella(celdas):
    h = hashlib.blake2b("\x1f".join(celdas).encode("utf-8", errors="replace"), digest_size=8)
    return int.from_bytes(h.digest(), "big")


def filas_access(ruta_mdb, tabla, tipos):
    """Genera las filas de Access ya normalizadas (streaming; sirve para tablas de millones de filas)."""
    proceso = subprocess.Popen(
        ["mdb-export", "-D", FORMATO_FECHA, "-T", FORMATO_FECHA, "-b", "hex", ruta_mdb, tabla],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    texto = io.TextIOWrapper(proceso.stdout, encoding="utf-8", errors="replace", newline="")
    lector = csv.reader(texto)
    next(lector, None)
    for fila in lector:
        yield [normalizar(c, tipos[i] if i < len(tipos) else "otro") for i, c in enumerate(fila)]
    proceso.stdout.close()
    if proceso.wait() != 0:
        raise RuntimeError(proceso.stderr.read().decode("utf-8", errors="ignore").strip())


def filas_mysql(conn, db_name, tabla_real, tipos):
    cursor = conn.cursor(raw=True)
    cursor.execute(f"SELECT * FROM `{db_name}`.`{tabla_real}`")
    while True:
        bloque = cursor.fetchmany(BLOQUE)
        if not bloque:
            break
        for fila in bloque:
            yield [normalizar(c, tipos[i] if i < len(tipos) else "otro") for i, c in enumerate(fila)]
    cursor.close()


def comparar_tabla(conn, db_name, ruta_mdb, tabla, tabla_real, tipos):
    """Devuelve (filas_access, filas_mysql, faltan_en_mysql, sobran_en_mysql, ejemplos)."""
    en_access = Counter()
    n_access = 0
    for fila in filas_access(ruta_mdb, tabla, tipos):
        en_access[huella(fila)] += 1
        n_access += 1

    en_mysql = Counter()
    n_mysql = 0
    for fila in filas_mysql(conn, db_name, tabla_real, tipos):
        en_mysql[huella(fila)] += 1
        n_mysql += 1

    faltan = en_access - en_mysql
    sobran = en_mysql - en_access
    ejemplos = []
    if faltan:
        for fila in filas_access(ruta_mdb, tabla, tipos):
            if huella(fila) in faltan:
                ejemplos.append(" | ".join(c[:40] for c in fila[:8]))
                if len(ejemplos) == 3:
                    break
    return n_access, n_mysql, sum(faltan.values()), sum(sobran.values()), ejemplos


def auditar_tipos(conn, db_name, ruta_mdb, prefijo, existentes):
    """Detecta columnas con tipo MySQL que pierde información respecto al original de Access."""
    esquema = subprocess.check_output(["mdb-schema", ruta_mdb, "access"]).decode("utf-8", errors="ignore")
    cursor = conn.cursor()
    problemas = []
    for (tabla, columna), tipo_a in leer_tipos_access(esquema).items():
        real = existentes.get((prefijo + quitar_tildes(tabla)).lower())
        if real is None or tipo_a not in ("double", "currency", "byte", "memo"):
            continue
        cursor.execute(
            "SELECT data_type, column_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
            (db_name, real, quitar_tildes(columna)))
        fila = cursor.fetchone()
        if not fila:
            continue
        data_type, column_type = str(fila[0]).lower(), str(fila[1]).lower()
        if tipo_a == "double" and data_type != "double":
            problemas.append(f"{tabla}.{columna}: Access Double -> MySQL {column_type} (pierde decimales)")
        elif tipo_a == "currency" and data_type not in ("decimal", "double"):
            problemas.append(f"{tabla}.{columna}: Access Currency -> MySQL {column_type} (pierde decimales)")
        elif tipo_a == "byte" and "unsigned" not in column_type:
            problemas.append(f"{tabla}.{columna}: Access Byte -> MySQL {column_type} (no admite 128-255)")
        elif tipo_a == "memo" and data_type in ("tinytext", "text"):
            problemas.append(f"{tabla}.{columna}: Access Memo -> MySQL {column_type} (límite de 64 KB)")
    cursor.close()
    return problemas


def tablas_access(ruta_mdb):
    salida = subprocess.check_output(["mdb-tables", "-1", ruta_mdb]).decode("utf-8", errors="ignore")
    return [t.strip() for t in salida.splitlines() if t.strip() and "MSys" not in t]


def main():
    console.rule("[bold cyan]Verificación Access vs MySQL (filas y contenido)[/bold cyan]")
    db_name, archivo, prefijo = pedir_parametros(
        "Compara cada tabla de un .mdb con lo migrado en MySQL, celda por celda",
        "[bold]Ingresa el prefijo de las tablas[/bold] (Enter para ninguno)",
    )
    if not os.path.dirname(archivo):
        archivo = os.path.join(CARPETA_SQL, archivo)
    if not os.path.exists(archivo):
        console.print(f"[bold red]Error:[/bold red] El archivo '{archivo}' no existe.")
        sys.exit(2)

    conn = conexion_servidor()
    cursor = conn.cursor()
    cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (db_name,))
    existentes = {r[0].lower(): r[0] for r in cursor.fetchall()}

    problemas = []
    total_a = total_m = 0

    tipos_malos = auditar_tipos(conn, db_name, archivo, prefijo, existentes)
    if tipos_malos:
        console.print(f"[bold red]⚠ {len(tipos_malos)} columnas con tipo que pierde información:[/bold red]")
        for t in tipos_malos[:15]:
            console.print(f"   • {t}")
        if len(tipos_malos) > 15:
            console.print(f"   ... y {len(tipos_malos) - 15} más")
        problemas.append(("(estructura)", "-", "-", f"{len(tipos_malos)} columnas con tipo incorrecto", tipos_malos[:3]))
    else:
        console.print("[green]Tipos de columna: sin pérdida de información (Double, Currency, Byte, Memo).[/green]")
    tablas = tablas_access(archivo)
    for i, tabla in enumerate(tablas, 1):
        real = existentes.get((prefijo + quitar_tildes(tabla)).lower())
        if real is None:
            problemas.append((tabla, "-", "-", "La tabla NO existe en MySQL", []))
            console.print(f"  [{i}/{len(tablas)}] {tabla}: [red]NO EXISTE[/red]")
            continue

        cursor.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position", (db_name, real))
        tipos = [tipo_columna(t) for (t,) in cursor.fetchall()]

        try:
            n_a, n_m, faltan, sobran, ejemplos = comparar_tabla(conn, db_name, archivo, tabla, real, tipos)
        except Exception as e:
            problemas.append((tabla, "?", "?", f"Error al comparar: {e}", []))
            console.print(f"  [{i}/{len(tablas)}] {tabla}: [red]ERROR {e}[/red]")
            continue

        total_a += n_a
        total_m += n_m
        if faltan or sobran or n_a != n_m:
            detalle = f"{faltan} filas de Access sin igual en MySQL, {sobran} filas de MySQL sin igual en Access"
            problemas.append((tabla, str(n_a), str(n_m), detalle, ejemplos))
            console.print(f"  [{i}/{len(tablas)}] {tabla}: [red]DIFERENTE[/red] ({n_a} vs {n_m})")
        else:
            console.print(f"  [{i}/{len(tablas)}] {tabla}: [green]OK[/green] ({n_a} filas)")

    cursor.close()
    conn.close()

    if problemas:
        resultado = Table(title="Diferencias")
        for col in ("Tabla Access", "Access", "MySQL", "Detalle"):
            resultado.add_column(col)
        for tabla, a, m, detalle, ejemplos in problemas:
            resultado.add_row(tabla, a, m, detalle + ("\n" + "\n".join(ejemplos) if ejemplos else ""))
        console.print(resultado)
    else:
        console.print("[bold green]✔ Todas las tablas coinciden fila por fila y celda por celda.[/bold green]")
    console.print(f"Filas totales: Access={total_a} MySQL={total_m} | tablas con problemas: {len(problemas)}")
    sys.exit(1 if problemas else 0)


if __name__ == "__main__":
    main()
