import csv
import io
import os
import subprocess
import sys

from rich.console import Console
from rich.table import Table

from config import CARPETA_SQL, conexion_servidor, pedir_parametros
from crear_estructura import quitar_tildes

console = Console()


def tablas_access(ruta_mdb):
    salida = subprocess.check_output(["mdb-tables", "-1", ruta_mdb]).decode("utf-8", errors="ignore")
    return [t.strip() for t in salida.splitlines() if t.strip()]


def contar_filas_access(ruta_mdb, tabla):
    """Cuenta las filas realmente exportadas (mdb-count puede estar desfasado)."""
    salida = subprocess.check_output(["mdb-export", ruta_mdb, tabla]).decode("utf-8", errors="ignore")
    return max(sum(1 for _ in csv.reader(io.StringIO(salida))) - 1, 0)


def main():
    console.rule("[bold cyan]Verificación de conteos Access vs MySQL[/bold cyan]")
    db_name, archivo, prefijo = pedir_parametros(
        "Compara filas por tabla entre un .mdb y MySQL",
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

    resultado = Table(show_lines=False)
    for col in ("Tabla Access", "Access", "MySQL", "Estado"):
        resultado.add_column(col)
    total_a = total_m = 0
    problemas = 0

    for tabla in tablas_access(archivo):
        if "MSys" in tabla:
            continue
        filas_a = contar_filas_access(archivo, tabla)
        real = existentes.get((prefijo + quitar_tildes(tabla)).lower())
        if real is None:
            resultado.add_row(tabla, str(filas_a), "-", "[red]NO EXISTE[/red]")
            problemas += 1
            continue
        cursor.execute(f"SELECT COUNT(*) FROM `{db_name}`.`{real}`")
        filas_m = cursor.fetchone()[0]
        total_a += filas_a
        total_m += filas_m
        if filas_a != filas_m:
            problemas += 1
            resultado.add_row(tabla, str(filas_a), str(filas_m), "[red]DIFERENTE[/red]")

    cursor.close()
    conn.close()
    console.print(resultado if problemas else "[green]Todas las tablas coinciden.[/green]")
    console.print(f"Filas totales: Access={total_a} MySQL={total_m} | tablas con problemas: {problemas}")
    sys.exit(1 if problemas else 0)


if __name__ == "__main__":
    main()
