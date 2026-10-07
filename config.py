"""Configuración compartida: conexión a MySQL por variables de entorno o archivo .env."""
import argparse
import os

from rich.prompt import Prompt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _cargar_env():
    ruta = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(ruta):
        return
    with open(ruta, encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, valor = linea.split("=", 1)
            os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env()

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_SOCKET = os.environ.get("DB_SOCKET", "")  # Linux: /var/run/mysqld/mysqld.sock
DB_NAME_DEFAULT = os.environ.get("DB_NAME", "aquamovil_core")
CARPETA_SQL = os.environ.get("CARPETA_BASES", os.path.join(BASE_DIR, "bases_datos"))


def conexion_servidor():
    """Conexión al servidor MySQL (sin base de datos seleccionada)."""
    import mysql.connector

    kwargs = {"user": DB_USER, "password": DB_PASSWORD}
    if DB_SOCKET:
        kwargs["unix_socket"] = DB_SOCKET
    else:
        kwargs.update(host=DB_HOST, port=DB_PORT)
    return mysql.connector.connect(**kwargs)


def comando_mysql(db_name):
    """Comando del cliente nativo `mysql` (la contraseña va por MYSQL_PWD, no por argumentos)."""
    cmd = ["mysql", "-u", DB_USER, "--force", "--default-character-set=utf8mb4"]
    if DB_SOCKET:
        cmd.append(f"--socket={DB_SOCKET}")
    else:
        cmd += ["-h", DB_HOST, "-P", str(DB_PORT), "--protocol=tcp"]
    cmd.append(db_name)
    return cmd


def entorno_mysql():
    env = os.environ.copy()
    if DB_PASSWORD:
        env["MYSQL_PWD"] = DB_PASSWORD
    return env


def pedir_parametros(titulo, texto_prefijo):
    """Lee --db, --archivo y --prefijo; lo que falte se pregunta de forma interactiva."""
    parser = argparse.ArgumentParser(description=titulo)
    parser.add_argument("--db", help="Nombre de la base de datos destino")
    parser.add_argument("--archivo", help="Archivo .mdb (nombre dentro de la carpeta de bases o ruta)")
    parser.add_argument("--prefijo", help="Prefijo de las tablas (usa '' para ninguno)")
    args = parser.parse_args()

    db_name = args.db or Prompt.ask("\n[bold]Nombre de la base de datos destino[/bold]", default=DB_NAME_DEFAULT)
    archivo = args.archivo or Prompt.ask("[bold]Ingresa el nombre del archivo en bases_datos[/bold] (ej: AQuaBase.mdb)")
    if args.prefijo is not None:
        prefijo = args.prefijo
    else:
        prefijo = Prompt.ask(texto_prefijo, default="")
    return db_name, archivo, prefijo
