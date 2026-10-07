# Access to MySQL Migration Utility (MDB Migrator)

Un conjunto de herramientas y scripts en Python diseñados para automatizar la extracción, normalización, sanitización y carga masiva de esquemas DDL e instrucciones DML desde archivos de Microsoft Access (`.mdb` / `.accdb`) hacia un motor **MySQL / MariaDB**.

Especialmente diseñado para lidiar con problemas comunes en migraciones legadas:
- Sanitización de tildes, caracteres especiales y símbolos dialectales en nombres de tablas y columnas.
- Desambiguación automática de nombres de columnas e índices duplicados por tabla.
- Limpieza y escape seguro de comillas simples (`'`) dentro de datos de texto en bloques `INSERT INTO`.
- Carga de alto rendimiento directamente por socket/CLI nativo de MySQL evitando cuellos de botella en drivers ORM.

## Uso en Windows con Docker (recomendado)

El proceso sigue en **dos fases independientes**: `crear_estructura.py` (DDL) y `cargar_datos.py` (DML).
En producción, donde las tablas ya existen, basta con ejecutar solo la fase de datos.

1. Copia `.env.example` a `.env` y completa `DB_PASSWORD` (el `.env` no se sube a git):
   `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.
2. Ejecuta cada fase con `migrar.ps1` (construye la imagen, **copia** el `.mdb` al contenedor sin modificar
   el original y alcanza el MySQL de Windows como `host.docker.internal`):

```powershell
.\migrar.ps1 -Fase estructura -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos aqua_pruebas -Prefijo genbase_
.\migrar.ps1 -Fase datos      -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos aqua_pruebas -Prefijo genbase_
.\migrar.ps1 -Fase verificar  -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos aqua_pruebas -Prefijo genbase_
```

`verificar_conteos.py` compara las filas de cada tabla entre Access y MySQL (cuenta las filas realmente
exportadas, no `mdb-count`, que puede estar desfasado). Devuelve código de salida 1 si hay diferencias.
Los scripts aceptan `--db`, `--archivo` y `--prefijo`; lo que falte se pregunta de forma interactiva.
La conexión se configura con variables de entorno / `.env` (`DB_SOCKET` solo para Linux sin Docker).

Notas aprendidas:
- El contenedor necesita `LANG=C.UTF-8` (ya está en el `Dockerfile`); sin él `mdb-export` falla en las tablas
  con tildes en el nombre y esas tablas se omiten.
- `mdb-export` se llama con `-b hex` (campos binarios/OLE como `0x...`) y `-e` (escapa `\` y saltos de línea).
  Los valores del INSERT no se modifican: las tildes de los **datos** se conservan; solo se sanean los
  nombres de tablas y columnas.
- Access `Byte` (0-255) se crea como `tinyint unsigned` (mdb-schema lo daba con signo, máx. 127).
- `migrador.py` (todo en uno) es la versión antigua: quita tildes de todo el SQL, incluidos los datos. Usar las dos fases.

Requisitos Previos (ejecución directa en Linux, sin Docker)
1. Dependencias del Sistema (Linux / Debian / Ubuntu)
Es necesario contar con los clientes nativos de MySQL y la suite de herramientas CLI mdbtools:
    sudo apt update
    sudo apt install -y mdbtools mysql-client
    
Entorno de Python
Se recomienda el uso de un entorno virtual (venv):
# Crear el entorno virtual
python3 -m venv venv

# Activar el entorno virtual
source venv/bin/activate

# Instalar dependencias requeridas
pip install rich mysql-connector-python


Estructura del Proyecto:
migrador_mysql/
├── bases_datos/              # Carpeta para colocar los archivos .mdb de origen
│   └── .gitkeep
├── crear_estructura.py       # Extrae el DDL (CREATE TABLE, ALTER TABLE) y crea el esquema
├── cargar_datos.py           # Extrae el DML (INSERT INTO), sanitiza y carga la data
├── errores_estructura.log    # Log generado en caso de fallos DDL (Ignorado en Git)
├── errores_carga_datos.log   # Log generado en caso de fallos DML (Ignorado en Git)
├── .gitignore                # Reglas para excluir temporales y datos sensibles
└── README.md                 # Documentación del proyecto



Uso del Migrador
El proceso de migración se ejecuta en dos pasos secuenciales: Estructura (DDL) primero y Datos (DML) después.

Paso 1: Generar la Estructura de Tablas (crear_estructura.py)
Copia tu archivo .mdb dentro de la carpeta bases_datos/.

Ejecuta el script interactivo:

python3 crear_estructura.py

Ingresa los parámetros requeridos cuando el prompt lo solicite:

    Nombre de la base de datos destino: (ej. aquamovil_core)

    Nombre del archivo MDB: (ej. PtoBase.mdb)

    Prefijo para las tablas (Opcional): (ej. ptobase_)
    
El script se encargará de crear la base de datos destino en MySQL si no existe, ajustar los tipos de datos compatibles, resolver colisiones de índices y crear las tablas limpias.


Paso 2: Migrar e Insertar Registros (cargar_datos.py)
Una vez creadas las tablas destino, procede con la carga de los datos:
python3 cargar_datos.py


Ingresa los mismos parámetros de base de datos y prefijo utilizados en el Paso 1.

El script extraerá secuencialmente los datos de cada tabla, corregirá problemas de sintaxis o caracteres especiales en textos (ej: 'OTRAS RETENCIONES') e insertará de forma masiva los registros.

Si deseas auditar la cantidad de registros migrados de una tabla específica entre Access y MySQL, puedes correr las siguientes verificaciones en la terminal:
# Conteo de registros en el archivo original de Access
mdb-export bases_datos/TuArchivo.mdb NombreTabla | wc -l

-- Conteo de registros en MySQL
SELECT COUNT(*) FROM `nombre_tabla`;


Solución de Problemas Frecuentes
ERROR 1061 (42000): Duplicate key name ...:
Ocurre cuando el archivo .mdb original tiene índices o claves primarias nombradas con la misma clave en la misma tabla. La función interna deduplicar_indices_cuerpo() le asigna automáticamente un correlativo _2, _3, etc.

ERROR 1064 (42000): Syntax Error ... en cadenas de texto:
Asegúrate de ejecutar la última versión de cargar_datos.py, la cual aplica sanitización con la regex sanitizar_valores_insert() para escapar adecuadamente apóstrofes internos sin romper los límites del INSERT.
# Access-to-MySQL-Migration-Utility-
