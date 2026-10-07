"""Cuenta cuántas tablas y filas hay en MySQL con un prefijo. migrar.ps1 lo usa para no duplicar datos."""
import argparse

from config import conexion_servidor


def main():
    parser = argparse.ArgumentParser(description="Estado del destino: tablas y filas con un prefijo")
    parser.add_argument("--db", required=True, help="Base de datos MySQL")
    parser.add_argument("--prefijo", default="", help="Prefijo de las tablas (vacío = todas)")
    args = parser.parse_args()

    conn = conexion_servidor()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = %s", (args.db,))
    if cursor.fetchone()[0] == 0:
        print("ESTADO tablas=0 filas=0 existe=0")
        return

    cursor.execute("SET SESSION information_schema_stats_expiry=0")
    patron = args.prefijo.replace("\\", "\\\\").replace("_", "\\_").replace("%", "\\%") + "%"
    cursor.execute(
        "SELECT COUNT(*), COALESCE(SUM(table_rows), 0) FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name LIKE %s", (args.db, patron))
    tablas, filas = cursor.fetchone()
    print(f"ESTADO tablas={tablas} filas={int(filas)} existe=1")


if __name__ == "__main__":
    main()
