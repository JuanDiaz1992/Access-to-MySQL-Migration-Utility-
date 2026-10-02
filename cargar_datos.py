import os
import sys
import subprocess
import re
import unicodedata
from rich.console import Console
from rich.prompt import Prompt

console = Console()

CARPETA_SQL = os.path.join(os.path.dirname(__file__), "bases_datos")

def quitar_tildes(texto):
    """Elimina tildes y caracteres especiales respetando Mayúsculas y Minúsculas."""
    texto_normalizado = unicodedata.normalize('NFD', texto)
    sin_tildes = ''.join(c for c in texto_normalizado if unicodedata.category(c) != 'Mn')
    return sin_tildes.replace('ñ', 'n').replace('Ñ', 'N')

def sanitizar_valores_insert(linea_insert):
    """
    Arregla el escape de comillas dentro de la cláusula VALUES (...).
    `mdb-export` muchas veces genera cadenas como 'D'AMICO' o 'OTRAS RETENCIONES',NULL)
    lo que rompe la sintaxis de MySQL.
    """
    pos_values = linea_insert.find("VALUES")
    if pos_values == -1:
        return linea_insert

    encabezado = linea_insert[:pos_values + 6]
    valores_raw = linea_insert[pos_values + 6:]

    # Corregir apóstrofes o comillas sueltas internas dentro de valores de texto entre comillas simples
    # Reemplaza ' en mitad de una palabra por \' para evitar romper la cadena en MySQL
    valores_corregidos = re.sub(r"(?<=\w)'(?=\w)", r"\'", valores_raw)

    return encabezado + valores_corregidos

def sanitizar_linea_insert(line, prefijo=""):
    """Limpia la sintaxis de INSERT INTO de mdb-export."""
    if not line.startswith("INSERT INTO"):
        return quitar_tildes(line)

    # 1. Renombrar la tabla con prefijo y sin tildes
    def corregir_tabla(m):
        nombre_tabla_raw = m.group(1)
        nombre_tabla_clean = quitar_tildes(nombre_tabla_raw)
        return f"INSERT INTO `{prefijo}{nombre_tabla_clean}`"

    line = re.sub(
        r'INSERT\s+INTO\s+[`"]?([^`"\s\(]+)[`"]?',
        corregir_tabla,
        line,
        flags=re.IGNORECASE
    )

    # 2. Corregir columnas duplicadas en la firma del INSERT (ej: `Numero`, `Número`)
    match_cols = re.search(r'\(([`"].*?[`"])\)\s+VALUES', line)
    if match_cols:
        cols_raw = match_cols.group(1).split(',')
        cols_vistas_lower = set()
        cols_nuevas = []

        for c in cols_raw:
            c_clean = c.strip().strip('`"').strip()
            c_sin_tildes = quitar_tildes(c_clean)
            c_key_lower = c_sin_tildes.lower()

            if c_key_lower in cols_vistas_lower:
                contador = 2
                col_final = f"{c_sin_tildes}_{contador}"
                while col_final.lower() in cols_vistas_lower:
                    contador += 1
                    col_final = f"{c_sin_tildes}_{contador}"
            else:
                col_final = c_sin_tildes

            cols_vistas_lower.add(col_final.lower())
            cols_nuevas.append(f"`{col_final}`")

        str_cols_nuevas = ", ".join(cols_nuevas)
        line = line.replace(match_cols.group(1), str_cols_nuevas)

    # 3. Sanitizar comillas dentro de los datos
    line = sanitizar_valores_insert(line)

    return line

def normalizar_inserts(contenido_sql, prefijo=""):
    """Limpia bytes nulos, tildes y arregla las sentencias INSERT."""
    sql_limpio = contenido_sql.replace('\x00', '')
    lines = []
    for line in sql_limpio.splitlines():
        if line.strip():
            lines.append(sanitizar_linea_insert(line, prefijo))
    return "\n".join(lines)

def track_tablas(tablas):
    """Progreso de extracción."""
    total = len(tablas)
    for i, t in enumerate(tablas, 1):
        if t.strip():
            console.print(f"  └─ Extrayendo datos tabla {i}/{total}: {t.strip()}")
            yield t.strip()

def extraer_datos(ruta_mdb, prefijo=""):
    """Extrae las instrucciones INSERT INTO."""
    ruta_sql = ruta_mdb.rsplit('.', 1)[0] + '_datos.sql'
    console.print(f"[bold yellow]Extrayendo únicamente DATOS (INSERT INTO)...[/bold yellow]")

    try:
        tables = subprocess.check_output(["mdb-tables", "-1", ruta_mdb]).decode("utf-8", errors="ignore").splitlines()

        # Desactivamos comprobaciones de sintaxis estricta temporalmente
        raw_sql = (
            "SET FOREIGN_KEY_CHECKS=0;\n"
            "SET UNIQUE_CHECKS=0;\n"
            "SET SQL_MODE='NO_AUTO_VALUE_ON_ZERO,NO_BACKSLASH_ESCAPES';\n\n"
        )

        for table in track_tablas(tables):
            if table:
                try:
                    # mdb-export nativo de MySQL
                    data = subprocess.check_output(
                        ["mdb-export", "-I", "mysql", "-D", "%Y-%m-%d %H:%M:%S", ruta_mdb, table]
                    ).decode("utf-8", errors="ignore")
                    raw_sql += data + "\n"
                except Exception:
                    pass

        raw_sql += "\nSET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n"

        console.print("[bold yellow]Sanitizando datos, tildes y prefijos...[/bold yellow]")
        sql_procesado = normalizar_inserts(raw_sql, prefijo)

        with open(ruta_sql, "w", encoding="utf-8") as f:
            f.write(sql_procesado)

        return ruta_sql
    except FileNotFoundError:
        console.print("[bold red]Error:[bold red] 'mdbtools' no está instalado. Ejecuta: sudo apt install mdbtools")
        sys.exit(1)

def cargar_datos_native(db_name, archivo_sql):
    """Inserta los registros en las tablas existentes."""
    console.print(f"\n[bold blue]Insertando registros en MySQL...[/bold blue]")

    ruta_log = os.path.join(os.path.dirname(__file__), "errores_carga_datos.log")

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

        if proceso.stderr:
            with open(ruta_log, "w", encoding="utf-8") as log_file:
                log_file.write(proceso.stderr)

        lineas_error = [linea for linea in proceso.stderr.splitlines() if "ERROR" in linea]
        if not lineas_error:
            console.print("\n[bold green]✔ ¡Carga de datos completada con éxito![/bold green]\n")
        else:
            console.print("\n[bold yellow]⚠ La carga terminó con algunos errores en registros específicos.[/bold yellow]")
            console.print(f"Revisa el detalle completo en: [bold cyan]{ruta_log}[/bold cyan]\n")
            console.print("[bold red]Primeros errores encontrados:[/bold red]")
            for err in lineas_error[:5]:
                console.print(f" • {err}")

    except Exception as e:
        console.print(f"[bold red]Error ejecutando el cliente nativo de MySQL:[/bold red] {e}")
    finally:
        if os.path.exists(archivo_sql):
            os.remove(archivo_sql)
            console.print(f"[bold dim]🗑 Archivo temporal borrado: {os.path.basename(archivo_sql)}[/bold dim]\n")

def main():
    console.rule("[bold cyan]Cargador de Datos (MySQL)[/bold cyan]")

    db_name = Prompt.ask("\n[bold]Nombre de la base de datos destino[/bold]", default="aquamovil_core")
    archivo = Prompt.ask("[bold]Ingresa el nombre del archivo en bases_datos[/bold] (ej: AQuaBase.mdb)")
    prefijo = Prompt.ask("[bold]Ingresa el prefijo correspondiente[/bold] (ej: aquabase_ o Enter para ninguno)", default="")

    if not os.path.dirname(archivo):
        archivo = os.path.join(CARPETA_SQL, archivo)

    if not os.path.exists(archivo):
        archivo_mdb = archivo + ".mdb" if not archivo.endswith(".mdb") else archivo
        if os.path.exists(archivo_mdb):
            archivo = archivo_mdb
        else:
            console.print(f"[bold red]Error:[bold red] El archivo '{archivo}' no existe.")
            return

    archivo_sql = extraer_datos(archivo, prefijo)
    cargar_datos_native(db_name, archivo_sql)

if __name__ == "__main__":
    main()
