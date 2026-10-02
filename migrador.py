import os
import sys
import subprocess
import re
import unicodedata
import mysql.connector
from rich.console import Console
from rich.prompt import Prompt

console = Console()

CARPETA_SQL = os.path.join(os.path.dirname(__file__), "bases_datos")

def quitar_tildes(texto):
    """Elimina tildes y caracteres especiales (á -> a, ó -> o, ñ -> n, etc.)."""
    texto_normalizado = unicodedata.normalize('NFD', texto)
    sin_tildes = ''.join(c for c in texto_normalizado if unicodedata.category(c) != 'Mn')
    return sin_tildes.replace('ñ', 'n').replace('Ñ', 'N')

def normalizar_sql(contenido_sql, prefijo=""):
    """Quita tildes de todo el SQL y añade el prefijo a CREATE e INSERT."""
    # 1. Quitar tildes
    sql_limpio = quitar_tildes(contenido_sql)

    # 2. Aplicar prefijo si existe
    if prefijo:
        # Poner prefijo a CREATE TABLE [IF NOT EXISTS] `nombre`
        sql_limpio = re.sub(
            r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?"?([a-zA-Z0-9_]+)`?"?',
            rf'CREATE TABLE `{prefijo}\1`',
            sql_limpio,
            flags=re.IGNORECASE
        )
        # Poner prefijo a INSERT INTO `nombre`
        sql_limpio = re.sub(
            r'INSERT\s+INTO\s+`?"?([a-zA-Z0-9_]+)`?"?',
            rf'INSERT INTO `{prefijo}\1`',
            sql_limpio,
            flags=re.IGNORECASE
        )
        # Poner prefijo a DROP TABLE [IF EXISTS] `nombre`
        sql_limpio = re.sub(
            r'DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?`?"?([a-zA-Z0-9_]+)`?"?',
            rf'DROP TABLE IF EXISTS `{prefijo}\1`',
            sql_limpio,
            flags=re.IGNORECASE
        )

    return sql_limpio

def crear_bd_si_no_existe(db_name):
    """Crea la base de datos de destino si no existe."""
    try:
        conn = mysql.connector.connect(
            host="localhost", user="root", password="", unix_socket="/var/run/mysqld/mysqld.sock"
        )
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        cursor.close()
        conn.close()
    except Exception as e:
        console.print(f"[bold red]Error al verificar/crear la BD:[/bold red] {e}")

def track_tablas(tablas):
    """Muestra avance simple al extraer tablas."""
    total = len(tablas)
    for i, t in enumerate(tablas, 1):
        if t.strip():
            console.print(f"  └─ Extrayendo tabla {i}/{total}: {t.strip()}")
            yield t.strip()

def convertir_mdb_a_sql(ruta_mdb, prefijo=""):
    """Convierte el .mdb a un .sql sanitizado para MySQL."""
    ruta_sql = ruta_mdb.rsplit('.', 1)[0] + '.sql'
    console.print(f"[bold yellow]Extrayendo estructura y datos de Access...[/bold yellow]")

    try:
        schema = subprocess.check_output(["mdb-schema", ruta_mdb, "mysql"]).decode("utf-8", errors="ignore")
        tables = subprocess.check_output(["mdb-tables", "-1", ruta_mdb]).decode("utf-8", errors="ignore").splitlines()

        raw_sql = "SET FOREIGN_KEY_CHECKS=0;\nSET UNIQUE_CHECKS=0;\n\n"
        raw_sql += schema + "\n\n"

        for table in track_tablas(tables):
            if table:
                try:
                    data = subprocess.check_output(
                        ["mdb-export", "-I", "mysql", "-D", "%Y-%m-%d %H:%M:%S", ruta_mdb, table]
                    ).decode("utf-8", errors="ignore")
                    raw_sql += data + "\n"
                except Exception:
                    pass

        raw_sql += "\nSET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n"

        console.print("[bold yellow]Limpiando tildes y aplicando prefijos...[/bold yellow]")
        sql_procesado = normalizar_sql(raw_sql, prefijo)

        with open(ruta_sql, "w", encoding="utf-8") as f:
            f.write(sql_procesado)

        return ruta_sql
    except FileNotFoundError:
        console.print("[bold red]Error:[bold red] 'mdbtools' no está instalado. Ejecuta: sudo apt install mdbtools")
        sys.exit(1)

def ejecutar_migracion_native(db_name, archivo_sql):
    """Ejecuta el archivo SQL con la CLI de MySQL usando --force para no detenerse y guarda logs de errores."""
    console.print(f"\n[bold blue]Importando en MySQL con el cliente nativo...[/bold blue]")

    ruta_log = os.path.join(os.path.dirname(__file__), "errores_migracion.log")

    # Flag -f (--force) obliga a continuar aunque una consulta falle
    comando = [
        "mysql",
        "-u", "root",
        "--force",
        "--socket=/var/run/mysqld/mysqld.sock",
        "--default-character-set=utf8mb4",
        db_name
    ]

    try:
        with open(archivo_sql, "r", encoding="utf-8", errors="ignore") as f:
            proceso = subprocess.run(
                comando,
                stdin=f,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

        # Guardar todos los avisos y errores en un archivo log
        if proceso.stderr:
            with open(ruta_log, "w", encoding="utf-8") as log_file:
                log_file.write(proceso.stderr)

        if proceso.returncode == 0 or "ERROR" not in proceso.stderr:
            console.print("\n[bold green]✔ ¡Migración completada con éxito![/bold green]\n")
        else:
            console.print("\n[bold yellow]⚠ La migración terminó con algunas advertencias o errores.[/bold yellow]")
            console.print(f"Puedes revisar el detalle completo de los errores en: [bold cyan]{ruta_log}[/bold cyan]\n")

            # Mostrar los primeros 5 errores
            lineas_error = [linea for linea in proceso.stderr.splitlines() if "ERROR" in linea]
            console.print("[bold red]Resumen de primeros errores encontrados:[/bold red]")
            for err in lineas_error[:5]:
                console.print(f" • {err}")

    except Exception as e:
        console.print(f"[bold red]Error ejecutando el cliente nativo de MySQL:[/bold red] {e}")

def main():
    console.rule("[bold cyan]Herramienta de Migración MySQL / Access[/bold cyan]")

    db_name = Prompt.ask("\n[bold]Nombre de la base de datos destino[/bold]", default="aquamovil_core")
    archivo = Prompt.ask("[bold]Ingresa el nombre del archivo en bases_datos[/bold] (ej: AQuaBase.mdb)")
    prefijo = Prompt.ask("[bold]Ingresa el prefijo para las tablas[/bold] (ej: aquabase_ o presiona Enter para ninguno)", default="")

    if not os.path.dirname(archivo):
        archivo = os.path.join(CARPETA_SQL, archivo)

    if not os.path.exists(archivo):
        console.print(f"[bold red]Error:[bold red] El archivo '{archivo}' no existe.")
        return

    crear_bd_si_no_existe(db_name)

    if archivo.lower().endswith(".mdb"):
        archivo_sql = convertir_mdb_a_sql(archivo, prefijo)
    else:
        archivo_sql = archivo
        # Normalizar si ya es SQL
        with open(archivo_sql, 'r', encoding='utf-8', errors='ignore') as f:
            contenido = f.read()
        contenido_limpio = normalizar_sql(contenido, prefijo)
        with open(archivo_sql, 'w', encoding='utf-8') as f:
            f.write(contenido_limpio)

    ejecutar_migracion_native(db_name, archivo_sql)

if __name__ == "__main__":
    main()
