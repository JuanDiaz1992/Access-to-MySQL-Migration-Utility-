# Access to MySQL Migration Utility (MDB Migrator)

Un conjunto de herramientas y scripts en Python diseñados para automatizar la extracción, normalización, sanitización y carga masiva de esquemas DDL e instrucciones DML desde archivos de Microsoft Access (`.mdb` / `.accdb`) hacia un motor **MySQL / MariaDB**.

Especialmente diseñado para lidiar con problemas comunes en migraciones legadas:
- Sanitización de tildes, caracteres especiales y símbolos dialectales en nombres de tablas y columnas.
- Desambiguación automática de nombres de columnas e índices duplicados por tabla.
- Limpieza y escape seguro de comillas simples (`'`) dentro de datos de texto en bloques `INSERT INTO`.
- Carga de alto rendimiento directamente por socket/CLI nativo de MySQL evitando cuellos de botella en drivers ORM.

Requisitos Previos
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
