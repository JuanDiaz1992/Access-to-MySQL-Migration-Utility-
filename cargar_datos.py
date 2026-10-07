import os
import sys
import subprocess
import re
import unicodedata
from rich.console import Console

from config import CARPETA_SQL, comando_mysql, entorno_mysql, pedir_parametros

console = Console()

# Modo del servidor + NO_AUTO_VALUE_ON_ZERO, sin NO_ZERO_IN_DATE / NO_ZERO_DATE (conserva el modo estricto)
SQL_MODE_MIGRACION = (
    "SET SESSION sql_mode=(SELECT TRIM(BOTH ',' FROM "
    "REPLACE(REPLACE(REPLACE(REPLACE(CONCAT(@@sql_mode, ',NO_AUTO_VALUE_ON_ZERO'), "
    "'NO_ZERO_IN_DATE', ''), 'NO_ZERO_DATE', ''), ',,,', ','), ',,', ',')));"
)

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
    """Limpia el encabezado (tabla y columnas) de un INSERT INTO de mdb-export."""
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

def sanitizar_sentencia_insert(sentencia, prefijo=""):
    """Sanea solo el encabezado del INSERT; los valores (con sus tildes y saltos de línea) no se tocan."""
    pos = sentencia.find(" VALUES")
    if pos == -1:
        return sentencia
    resto = sentencia[pos + len(" VALUES"):]
    encabezado = sanitizar_linea_insert(sentencia[:pos] + " VALUES", prefijo)
    return encabezado + sanitizar_valores_insert("VALUES" + resto)[len("VALUES"):]

def normalizar_inserts(contenido_sql, prefijo=""):
    """Limpia bytes nulos y sanea cada sentencia INSERT completa (mdb-export reparte las filas en varias líneas)."""
    sql_limpio = contenido_sql.replace('\x00', '')
    sentencias = re.split(r'(?m)^(?=INSERT INTO )', sql_limpio)
    salida = []
    for s in sentencias:
        if s.startswith("INSERT INTO "):
            salida.append(sanitizar_sentencia_insert(s, prefijo))
        else:
            salida.append(s)
    return "".join(salida)

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

        # Con "mdb-export -e" las barras invertidas van escapadas: NO usar NO_BACKSLASH_ESCAPES.
        # Se conserva el modo estricto (los valores fuera de rango fallan en vez de truncarse), pero se permiten
        # fechas con día/mes cero: Access las guarda (p. ej. 1900-01-00) y el modo estricto rechazaría la fila.
        tablas_fallidas = []
        # Cada tabla se sanea y se escribe a disco al terminar: no se acumula todo el SQL en memoria (bases de cientos de MB).
        with open(ruta_sql, "w", encoding="utf-8") as salida:
            salida.write(
                "SET FOREIGN_KEY_CHECKS=0;\n"
                "SET UNIQUE_CHECKS=0;\n"
                f"{SQL_MODE_MIGRACION}\n"
                "SET autocommit=0;\n\n"
            )

            for table in track_tablas(tables):
                # -S 1: una fila por sentencia, así un valor inválido solo afecta a su fila y no a todo un lote
                # -b hex: los campos binarios (OLE/imágenes) salen como 0x... válido en MySQL
                # -e: escapa \ como \\ y los saltos de línea como \n (si no, MySQL se come las barras invertidas)
                res = subprocess.run(
                    ["mdb-export", "-I", "mysql", "-S", "1", "-b", "hex", "-e", "-D", "%Y-%m-%d %H:%M:%S", ruta_mdb, table],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                if res.returncode != 0:
                    tablas_fallidas.append(table)
                    detalle = res.stderr.decode("utf-8", errors="ignore").strip()
                    console.print(f"[bold red]  ✖ No se pudo exportar la tabla '{table}': {detalle}[/bold red]")
                    continue
                datos = res.stdout.decode("utf-8", errors="ignore")
                salida.write(normalizar_inserts(datos, prefijo) + "\nCOMMIT;\n")

            salida.write("\nSET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n")

        if tablas_fallidas:
            console.print(f"[bold red]⚠ Tablas NO exportadas ({len(tablas_fallidas)}): {', '.join(tablas_fallidas)}[/bold red]")

        return ruta_sql
    except FileNotFoundError:
        console.print("[bold red]Error:[bold red] 'mdbtools' no está instalado. Ejecuta: sudo apt install mdbtools")
        sys.exit(1)

PATRON_ERROR = re.compile(r"^ERROR (\d+) \(([^)]*)\) at line (\d+): (.*)$")
MAX_RECHAZADOS = 10000

def resumir_errores(ruta_log):
    """Lee el log por streaming: total de errores, los primeros 5 y {linea_del_sql: mensaje}."""
    total, primeros, por_linea = 0, [], {}
    with open(ruta_log, "r", encoding="utf-8", errors="ignore") as f:
        for linea in f:
            linea = linea.strip()
            if "ERROR" not in linea:
                continue
            total += 1
            if len(primeros) < 5:
                primeros.append(linea)
            m = PATRON_ERROR.match(linea)
            if m and len(por_linea) < MAX_RECHAZADOS:
                por_linea[int(m.group(3))] = m.group(4)
    return total, primeros, por_linea

def guardar_rechazados(archivo_sql, por_linea, ruta_destino):
    """Guarda las sentencias (filas) que MySQL rechazó, para no perderlas sin rastro."""
    pendientes = set(por_linea)
    with open(archivo_sql, "r", encoding="utf-8", errors="ignore") as src, \
         open(ruta_destino, "w", encoding="utf-8") as dst:
        for n, linea in enumerate(src, 1):
            if n in pendientes:
                dst.write(f"-- {por_linea[n]}\n{linea.rstrip()}\n")
                pendientes.discard(n)
                if not pendientes:
                    break

def cargar_datos_native(db_name, archivo_sql):
    """Inserta los registros en las tablas existentes. Devuelve True si hubo errores."""
    console.print(f"\n[bold blue]Insertando registros en MySQL...[/bold blue]")

    carpeta = os.path.dirname(os.path.abspath(__file__))
    ruta_log = os.path.join(carpeta, "errores_carga_datos.log")
    ruta_rechazados = os.path.join(carpeta, "rechazados_carga_datos.sql")
    hubo_errores = False

    comando = comando_mysql(db_name)

    try:
        # stderr va directo a disco: un error repetido en millones de filas no debe llenar la memoria
        with open(archivo_sql, "r", encoding="utf-8", errors="ignore") as f, \
             open(ruta_log, "w", encoding="utf-8") as log_file:
            subprocess.run(
                comando,
                stdin=f,
                stdout=subprocess.DEVNULL,
                stderr=log_file,
                text=True,
                env=entorno_mysql()
            )

        total, primeros, por_linea = resumir_errores(ruta_log)
        if total == 0:
            os.remove(ruta_log)
            console.print("\n[bold green]✔ ¡Carga de datos completada con éxito![/bold green]\n")
        else:
            hubo_errores = True
            guardar_rechazados(archivo_sql, por_linea, ruta_rechazados)
            console.print(f"\n[bold yellow]⚠ La carga terminó con {total} errores en registros específicos.[/bold yellow]")
            console.print(f"Detalle de los errores: [bold cyan]{ruta_log}[/bold cyan]")
            console.print(f"Filas rechazadas (para revisarlas o recargarlas): [bold cyan]{ruta_rechazados}[/bold cyan]")
            if total > MAX_RECHAZADOS:
                console.print(f"[bold yellow]Solo se guardaron las primeras {MAX_RECHAZADOS} filas rechazadas.[/bold yellow]")
            console.print("[bold red]Primeros errores encontrados:[/bold red]")
            for err in primeros:
                console.print(f" • {err}")

    except Exception as e:
        hubo_errores = True
        console.print(f"[bold red]Error ejecutando el cliente nativo de MySQL:[/bold red] {e}")
    finally:
        if os.path.exists(archivo_sql):
            os.remove(archivo_sql)
            console.print(f"[bold dim]🗑 Archivo temporal borrado: {os.path.basename(archivo_sql)}[/bold dim]\n")
    return hubo_errores

def main():
    console.rule("[bold cyan]Cargador de Datos (MySQL)[/bold cyan]")

    db_name, archivo, prefijo = pedir_parametros(
        "Fase 2: carga los datos (DML) de un .mdb en tablas MySQL que ya existen",
        "[bold]Ingresa el prefijo correspondiente[/bold] (ej: aquabase_ o Enter para ninguno)",
    )

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
    if cargar_datos_native(db_name, archivo_sql):
        sys.exit(1)

if __name__ == "__main__":
    main()
