import os
import sys
import subprocess
import re
import unicodedata
from rich.console import Console

from config import CARPETA_SQL, LOTE_FILAS, comando_mysql, entorno_mysql, pedir_parametros

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

PREAMBULO_SQL = (
    "SET FOREIGN_KEY_CHECKS=0;\n"
    "SET UNIQUE_CHECKS=0;\n"
    f"{SQL_MODE_MIGRACION}\n"
    "SET autocommit=0;\n\n"
)
FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"
# Errores que afectan a toda la tabla (no a una fila concreta): reintentar fila a fila no sirve
ERRORES_ESTRUCTURA = {1146, 1054, 1136, 1049}

def exportar_tabla(ruta_mdb, tabla, lote):
    """mdb-export de una tabla con `lote` filas por INSERT. Devuelve (texto, error)."""
    # -S: filas por sentencia; cada sentencia sale en UNA línea
    # -b hex: los campos binarios (OLE/imágenes) salen como 0x... válido en MySQL
    # -e: escapa \ como \\ y los saltos de línea como \n (si no, MySQL se come las barras invertidas)
    res = subprocess.run(
        ["mdb-export", "-I", "mysql", "-S", str(lote), "-b", "hex", "-e", "-D", FORMATO_FECHA, ruta_mdb, tabla],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if res.returncode != 0:
        return None, res.stderr.decode("utf-8", errors="ignore").strip()
    return res.stdout.decode("utf-8", errors="ignore"), None

def extraer_datos(ruta_mdb, prefijo=""):
    """Extrae las instrucciones INSERT INTO. Devuelve (ruta_sql, bloques) donde bloques es
    [(tabla_access, linea_inicial, num_sentencias)] para poder localizar lotes fallidos."""
    ruta_sql = ruta_mdb.rsplit('.', 1)[0] + '_datos.sql'
    console.print(f"[bold yellow]Extrayendo únicamente DATOS (INSERT INTO)...[/bold yellow]")

    try:
        tables = subprocess.check_output(["mdb-tables", "-1", ruta_mdb]).decode("utf-8", errors="ignore").splitlines()

        # Con "mdb-export -e" las barras invertidas van escapadas: NO usar NO_BACKSLASH_ESCAPES.
        # Se conserva el modo estricto (los valores fuera de rango fallan en vez de truncarse), pero se permiten
        # fechas con día/mes cero: Access las guarda (p. ej. 1900-01-00) y el modo estricto rechazaría la fila.
        tablas_fallidas = []
        bloques = []
        # Cada tabla se sanea y se escribe a disco al terminar: no se acumula todo el SQL en memoria (bases de cientos de MB).
        with open(ruta_sql, "w", encoding="utf-8") as salida:
            salida.write(PREAMBULO_SQL)
            lineas_escritas = PREAMBULO_SQL.count("\n")

            for table in track_tablas(tables):
                datos, error = exportar_tabla(ruta_mdb, table, LOTE_FILAS)
                if error is not None:
                    tablas_fallidas.append(table)
                    console.print(f"[bold red]  ✖ No se pudo exportar la tabla '{table}': {error}[/bold red]")
                    continue
                texto = normalizar_inserts(datos, prefijo)
                if not texto.strip():
                    continue
                if not texto.endswith("\n"):
                    texto += "\n"
                n = texto.count("\n")
                bloques.append((table, lineas_escritas + 1, n))
                salida.write(texto + "COMMIT;\n")
                lineas_escritas += n + 1

            salida.write("\nSET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n")

        if tablas_fallidas:
            console.print(f"[bold red]⚠ Tablas NO exportadas ({len(tablas_fallidas)}): {', '.join(tablas_fallidas)}[/bold red]")

        return ruta_sql, bloques
    except FileNotFoundError:
        console.print("[bold red]Error:[bold red] 'mdbtools' no está instalado. Ejecuta: sudo apt install mdbtools")
        sys.exit(1)

PATRON_ERROR = re.compile(r"^ERROR (\d+) \(([^)]*)\) at line (\d+): (.*)$")
MAX_RECHAZADOS = 10000

def resumir_errores(ruta_log):
    """Lee el log por streaming: total de errores, los primeros 5 y {linea_del_sql: (codigo, mensaje)}."""
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
                por_linea[int(m.group(3))] = (int(m.group(1)), m.group(4))
    return total, primeros, por_linea

def guardar_rechazados(archivo_sql, por_linea, ruta_destino):
    """Guarda las sentencias (filas) que MySQL rechazó, para no perderlas sin rastro."""
    pendientes = set(por_linea)
    with open(archivo_sql, "r", encoding="utf-8", errors="ignore") as src, \
         open(ruta_destino, "w", encoding="utf-8") as dst:
        for n, linea in enumerate(src, 1):
            if n in pendientes:
                dst.write(f"-- {por_linea[n][1]}\n{linea.rstrip()}\n")
                pendientes.discard(n)
                if not pendientes:
                    break

def ejecutar_mysql(db_name, archivo_sql, ruta_log):
    """Ejecuta un .sql con el cliente mysql (stderr directo a disco)."""
    with open(archivo_sql, "r", encoding="utf-8", errors="ignore") as f, \
         open(ruta_log, "w", encoding="utf-8") as log_file:
        subprocess.run(
            comando_mysql(db_name),
            stdin=f,
            stdout=subprocess.DEVNULL,
            stderr=log_file,
            text=True,
            env=entorno_mysql()
        )

def preparar_reintento(ruta_mdb, prefijo, bloques, lineas_falladas, ruta_reintento):
    """Escribe un .sql con las filas de los lotes fallidos, una por sentencia. MySQL deshace por completo
    una sentencia fallida, así que reintentar esas filas no duplica nada. Devuelve el número de filas."""
    por_tabla = {}
    for n in sorted(lineas_falladas):
        for tabla, inicio, cantidad in bloques:
            if inicio <= n < inicio + cantidad:
                por_tabla.setdefault(tabla, []).append(n - inicio)
                break

    filas = 0
    with open(ruta_reintento, "w", encoding="utf-8") as salida:
        salida.write(PREAMBULO_SQL)
        for tabla, ordinales in por_tabla.items():
            datos, error = exportar_tabla(ruta_mdb, tabla, 1)
            if error is not None:
                console.print(f"[bold red]  ✖ No se pudo re-exportar '{tabla}' para el reintento: {error}[/bold red]")
                continue
            sentencias = datos.split("\n")
            for k in ordinales:
                for sentencia in sentencias[k * LOTE_FILAS:(k + 1) * LOTE_FILAS]:
                    if sentencia.strip():
                        salida.write(normalizar_inserts(sentencia, prefijo).strip() + "\n")
                        filas += 1
            salida.write("COMMIT;\n")
        salida.write("\nSET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n")
    return filas

def cargar_datos_native(db_name, archivo_sql, ruta_mdb, prefijo, bloques):
    """Inserta los registros en las tablas existentes (lotes de LOTE_FILAS filas; los lotes que MySQL rechaza se
    repiten fila a fila). Devuelve True si quedaron errores."""
    console.print(f"\n[bold blue]Insertando registros en MySQL (lotes de {LOTE_FILAS} filas)...[/bold blue]")

    carpeta = os.path.dirname(os.path.abspath(__file__))
    ruta_log = os.path.join(carpeta, "errores_carga_datos.log")
    ruta_rechazados = os.path.join(carpeta, "rechazados_carga_datos.sql")
    ruta_reintento = archivo_sql.rsplit('.', 1)[0] + '_reintento.sql'
    ruta_log_reintento = os.path.join(carpeta, "errores_reintento.tmp")
    hubo_errores = False

    try:
        ejecutar_mysql(db_name, archivo_sql, ruta_log)
        total, primeros, por_linea = resumir_errores(ruta_log)

        if total == 0:
            os.remove(ruta_log)
            console.print("\n[bold green]✔ ¡Carga de datos completada con éxito![/bold green]\n")
            return False

        # Errores de estructura (tabla/columna inexistente...): afectan a todo y reintentar no sirve
        estructura = {n: v for n, v in por_linea.items() if v[0] in ERRORES_ESTRUCTURA}
        reintentables = {n: v for n, v in por_linea.items() if v[0] not in ERRORES_ESTRUCTURA}
        console.print(f"\n[bold yellow]⚠ {total} lotes fueron rechazados por MySQL en el primer intento.[/bold yellow]")
        if estructura:
            hubo_errores = True
            console.print("[bold red]Hay errores de estructura (¿falta crear tablas o columnas con la fase 1?):[/bold red]")
            for err in primeros:
                console.print(f" • {err}")

        rechazadas = {}
        if reintentables:
            filas = preparar_reintento(ruta_mdb, prefijo, bloques, reintentables, ruta_reintento)
            console.print(f"Reintentando fila a fila {filas} filas de {len(reintentables)} lotes...")
            ejecutar_mysql(db_name, ruta_reintento, ruta_log_reintento)
            total2, primeros2, rechazadas = resumir_errores(ruta_log_reintento)
            with open(ruta_log, "a", encoding="utf-8") as log_file, \
                 open(ruta_log_reintento, "r", encoding="utf-8", errors="ignore") as log_reintento:
                log_file.write("\n-- Reintento fila a fila --\n")
                log_file.write(log_reintento.read())
            if rechazadas:
                hubo_errores = True
                guardar_rechazados(ruta_reintento, rechazadas, ruta_rechazados)
                console.print(f"[bold red]✖ {total2} filas rechazadas definitivamente[/bold red] (de {filas} reintentadas; "
                              f"las otras {filas - total2} se cargaron).")
                console.print(f"Filas rechazadas: [bold cyan]{ruta_rechazados}[/bold cyan]")
                for err in primeros2:
                    console.print(f" • {err}")
            else:
                console.print(f"[bold green]✔ Las {filas} filas se cargaron al reintentarlas una por una.[/bold green]")

        console.print(f"Detalle de los errores: [bold cyan]{ruta_log}[/bold cyan]\n")
        return hubo_errores

    except Exception as e:
        console.print(f"[bold red]Error ejecutando el cliente nativo de MySQL:[/bold red] {e}")
        return True
    finally:
        for ruta in (archivo_sql, ruta_reintento, ruta_log_reintento):
            if os.path.exists(ruta):
                os.remove(ruta)
        console.print(f"[bold dim]🗑 Archivos temporales borrados[/bold dim]\n")

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

    archivo_sql, bloques = extraer_datos(archivo, prefijo)
    if cargar_datos_native(db_name, archivo_sql, archivo, prefijo, bloques):
        sys.exit(1)

if __name__ == "__main__":
    main()
