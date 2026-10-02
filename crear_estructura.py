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
    """Elimina tildes y caracteres especiales respetando Mayúsculas y Minúsculas."""
    texto_normalizado = unicodedata.normalize('NFD', texto)
    sin_tildes = ''.join(c for c in texto_normalizado if unicodedata.category(c) != 'Mn')
    return sin_tildes.replace('ñ', 'n').replace('Ñ', 'N')

def normalizar_schema(contenido_sql, prefijo=""):
    """Limpia tildes, desambigua columnas y arregla alter tables / índices duplicados."""
    sql = contenido_sql.replace('\x00', '').replace('\xa0', ' ')

    # 1. Eliminar tablas del sistema de Access
    lineas_sql = []
    for line in sql.splitlines():
        if "MSys" in line:
            continue
        lineas_sql.append(line)
    sql = "\n".join(lineas_sql)

    # 2. Renombrar CREATE TABLE con prefijo y sin tildes
    def reemplazar_create_tabla(match):
        nombre_tabla_raw = match.group(1)
        nombre_tabla_clean = quitar_tildes(nombre_tabla_raw)
        return f"CREATE TABLE IF NOT EXISTS `{prefijo}{nombre_tabla_clean}`"

    sql = re.sub(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"]?([^`"\s\(]+)[`"]?',
        reemplazar_create_tabla,
        sql,
        flags=re.IGNORECASE
    )

    # 3. Procesar las columnas duplicadas en el cuerpo de cada CREATE TABLE
    def procesar_cuerpo_tabla(match):
        encabezado = match.group(1)
        cuerpo = match.group(2)
        cierre = match.group(3)

        lineas = cuerpo.splitlines()
        columnas_vistas_lower = set()
        lineas_filtradas = []

        for linea in lineas:
            match_col = re.match(r'^(\s*[`"]?)([^`"\s]+)([`"]?\s+.*)', linea)

            if match_col and not re.match(r'^\s*(PRIMARY\s+KEY|KEY|UNIQUE|CONSTRAINT|INDEX|FOREIGN\s+KEY)', linea, re.IGNORECASE):
                prefijo_col, col_original, sufijo_col = match_col.groups()
                col_sin_tildes = quitar_tildes(col_original)
                col_key_lower = col_sin_tildes.lower()

                if col_key_lower in columnas_vistas_lower:
                    contador = 2
                    col_final = f"{col_sin_tildes}_{contador}"
                    while col_final.lower() in columnas_vistas_lower:
                        contador += 1
                        col_final = f"{col_sin_tildes}_{contador}"
                else:
                    col_final = col_sin_tildes

                columnas_vistas_lower.add(col_final.lower())
                linea = f"{prefijo_col}{col_final}{sufijo_col}"
            else:
                linea = quitar_tildes(linea)

            lineas_filtradas.append(linea)

        return f"{encabezado}\n" + "\n".join(lineas_filtradas) + f"\n{cierre}"

    sql = re.sub(
        r'(CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`[^`]+`\s*\()(.*?\n)(\s*\);)',
        procesar_cuerpo_tabla,
        sql,
        flags=re.DOTALL | re.IGNORECASE
    )

    # 4. Arreglar ALTER TABLE (Renombrar tabla y Deduplicar Nombres de Índices)
    lineas_procesadas = []
    indices_por_tabla = {}  # { 'nombre_tabla': set('nombre_indice_lower') }

    for linea in sql.splitlines():
        linea_clean = quitar_tildes(linea)

        # Detectar comandos: ALTER TABLE `NombreTabla` ADD INDEX `NombreIndice` ...
        match_alter = re.search(
            r'ALTER\s+TABLE\s+[`"]?([^`"\s]+)[`"]?\s+ADD\s+(INDEX|KEY|UNIQUE INDEX)\s+[`"]?([^`"\s\(]+)[`"]?(.*)',
            linea_clean,
            re.IGNORECASE
        )

        if match_alter:
            tabla_raw = match_alter.group(1)
            tipo_idx = match_alter.group(2)
            nombre_idx = match_alter.group(3)
            resto_cmd = match_alter.group(4)

            tabla_final = f"{prefijo}{tabla_raw}"
            if tabla_final not in indices_por_tabla:
                indices_por_tabla[tabla_final] = set()

            idx_set = indices_por_tabla[tabla_final]
            idx_lower = nombre_idx.lower()

            # Si el índice ya existe para esa tabla, se le asigna un sufijo (_2, _3)
            if idx_lower in idx_set:
                contador = 2
                nuevo_idx = f"{nombre_idx}_{contador}"
                while nuevo_idx.lower() in idx_set:
                    contador += 1
                    nuevo_idx = f"{nombre_idx}_{contador}"
                idx_final = nuevo_idx
            else:
                idx_final = nombre_idx

            idx_set.add(idx_final.lower())

            # Reconstruir la sentencia ALTER TABLE libre de colisiones
            linea_clean = f"ALTER TABLE `{tabla_final}` ADD {tipo_idx} `{idx_final}`{resto_cmd}"

        elif linea_clean.strip().startswith("ALTER TABLE"):
            # Si es un ALTER TABLE que agrega PRIMARY KEY u otra cosa, solo le aplicamos el prefijo
            linea_clean = re.sub(
                r'ALTER\s+TABLE\s+[`"]?([^`"\s]+)[`"]?',
                lambda m: f"ALTER TABLE `{prefijo}{m.group(1)}`",
                linea_clean,
                flags=re.IGNORECASE
            )

        lineas_procesadas.append(linea_clean)

    return "\n".join(lineas_procesadas)

def crear_bd_si_no_existe(db_name):
    """Crea la base de datos destino si no existe."""
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

def extraer_estructura(ruta_mdb, prefijo=""):
    """Genera el SQL únicamente con la estructura DDL."""
    ruta_sql = ruta_mdb.rsplit('.', 1)[0] + '_estructura.sql'
    console.print(f"[bold yellow]Extrayendo únicamente ESTRUCTURA (CREATE TABLE)...[/bold yellow]")

    try:
        schema = subprocess.check_output(["mdb-schema", ruta_mdb, "mysql"]).decode("utf-8", errors="ignore")

        raw_sql = "SET FOREIGN_KEY_CHECKS=0;\nSET UNIQUE_CHECKS=0;\n\n"
        raw_sql += schema + "\n\n"
        raw_sql += "SET FOREIGN_KEY_CHECKS=1;\nSET UNIQUE_CHECKS=1;\n"

        sql_procesado = normalizar_schema(raw_sql, prefijo)

        with open(ruta_sql, "w", encoding="utf-8") as f:
            f.write(sql_procesado)

        return ruta_sql
    except FileNotFoundError:
        console.print("[bold red]Error:[bold red] 'mdbtools' no está instalado. Ejecuta: sudo apt install mdbtools")
        sys.exit(1)

def ejecutar_sql_native(db_name, archivo_sql):
    """Ejecuta la creación de tablas en MySQL."""
    console.print(f"\n[bold blue]Creando tablas en MySQL...[/bold blue]")
    ruta_log = os.path.join(os.path.dirname(__file__), "errores_estructura.log")
    hubo_errores = False

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
            console.print("\n[bold green]✔ ¡Estructura creada con éxito![/bold green]\n")
        else:
            hubo_errores = True
            console.print(f"\n[bold yellow]⚠ Se presentaron algunos errores al crear tablas. Revisa log: {ruta_log}[/bold yellow]\n")
            for err in lineas_error[:5]:
                console.print(f" • {err}")

    except Exception as e:
        hubo_errores = True
        console.print(f"[bold red]Error al ejecutar en MySQL:[/bold red] {e}")
    finally:
        if os.path.exists(archivo_sql):
            if not hubo_errores:
                os.remove(archivo_sql)
                console.print(f"[bold dim]🗑 Archivo temporal borrado: {os.path.basename(archivo_sql)}[/bold dim]\n")
            else:
                console.print(f"[bold yellow]📄 Archivo SQL conservado para inspección: {os.path.basename(archivo_sql)}[/bold yellow]\n")

def main():
    console.rule("[bold cyan]Creador de Estructuras DDL (MySQL)[/bold cyan]")

    db_name = Prompt.ask("\n[bold]Nombre de la base de datos destino[/bold]", default="aquamovil_core")
    archivo = Prompt.ask("[bold]Ingresa el nombre del archivo en bases_datos[/bold] (ej: AQuaBase.mdb)")
    prefijo = Prompt.ask("[bold]Ingresa el prefijo para las tablas[/bold] (ej: aquabase_ o Enter para ninguno)", default="")

    if not os.path.dirname(archivo):
        archivo = os.path.join(CARPETA_SQL, archivo)

    if not os.path.exists(archivo):
        archivo_mdb = archivo + ".mdb" if not archivo.endswith(".mdb") else archivo
        if os.path.exists(archivo_mdb):
            archivo = archivo_mdb
        else:
            console.print(f"[bold red]Error:[bold red] El archivo '{archivo}' no existe.")
            return

    crear_bd_si_no_existe(db_name)
    archivo_sql = extraer_estructura(archivo, prefijo)
    ejecutar_sql_native(db_name, archivo_sql)

if __name__ == "__main__":
    main()
