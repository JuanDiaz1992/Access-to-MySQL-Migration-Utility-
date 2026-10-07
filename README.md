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
2. Ejecuta `migrar.ps1` (construye la imagen de Docker, **copia** los `.mdb` al contenedor sin modificar los
   originales y alcanza el MySQL de Windows como `host.docker.internal`). Migra **una base o varias**.

**Modo interactivo (recomendado):** ejecuta `.\migrar.ps1` sin parametros. Pregunta, con las opciones visibles:
la fase (`estructura`, `datos`, `verificar` o `todo` = las tres en orden), las bases de Access (lista numerada de
`C:\BasesActivas`: un numero, varias con `1,3,5` o `1-4`, `T` para todas, o la ruta de otro `.mdb`), la base MySQL
de destino y el prefijo (con varias bases es automatico: nombre del archivo en minusculas + `_`). Antes de ejecutar
consulta el destino, muestra un resumen y pide confirmacion. No hay valores por defecto para la base ni el prefijo.

**Nunca duplica:** omite (y lo avisa) las bases cuyas tablas ya existen (`estructura`, `todo`) o ya tienen datos
(`datos`), y `datos` se niega si faltan las tablas. `-Forzar` desactiva estas protecciones. Al terminar muestra una
tabla con OK/FALLO por base y fase; el detalle de errores queda en `logs\<base>\`.

**Modo directo** (sin preguntas):

```powershell
.\migrar.ps1 -Fase todo       -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos mi_base -Prefijo genbase_
.\migrar.ps1 -Fase estructura -Mdb C:\BasesActivas\GenBase.mdb,C:\BasesActivas\PtoBase.mdb -BaseDatos mi_base
.\migrar.ps1 -Fase datos      -Todas -BaseDatos mi_base
.\migrar.ps1 -Fase verificar  -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos mi_base -Prefijo genbase_
```

`verificar_migracion.py` compara cada tabla entre Access y MySQL **fila por fila y celda por celda** (no solo
cantidades; los `float` se comparan a su precisión real, los binarios por su contenido hexadecimal). Si hay
diferencias lista las tablas, cuántas filas faltan o sobran y algunos ejemplos, y termina con código de salida 1.
Los scripts aceptan `--db`, `--archivo` y `--prefijo`; lo que falte se pregunta de forma interactiva.
La conexión se configura con variables de entorno / `.env` (`DB_SOCKET` solo para Linux sin Docker).

Notas aprendidas:
- El contenedor necesita `LANG=C.UTF-8` (ya está en el `Dockerfile`); sin él `mdb-export` falla en las tablas
  con tildes en el nombre y esas tablas se omiten.
- `mdb-export` se llama con `-b hex` (campos binarios/OLE como `0x...`) y `-e` (escapa `\` y saltos de línea).
  Los valores del INSERT no se modifican: las tildes de los **datos** se conservan; solo se sanean los
  nombres de tablas y columnas.
- Claves foráneas: la tabla referenciada (`REFERENCES`) y el nombre de la restricción llevan el prefijo; antes
  apuntaban a tablas inexistentes y las inserciones normales en esas tablas fallaban.
- Una columna `unique` ya crea en MySQL un índice con el nombre de la columna: un `ADD INDEX` con ese nombre
  recibe sufijo `_2` en vez de fallar con `ERROR 1061`.
- Carga en lotes de `LOTE_FILAS` filas por `INSERT` (100 por defecto, `mdb-export -S`) con `autocommit=0` y `COMMIT`
  por tabla. Si MySQL rechaza un lote (deshace la sentencia completa, sin dejar filas a medias), esas filas se
  repiten una por una: solo se rechazan las filas realmente inválidas, que quedan con su motivo en
  `rechazados_carga_datos.sql` (detalle en `errores_carga_datos.log`). Los errores de estructura (tabla o columna
  inexistente) se avisan sin reintentar. `cargar_datos.py` termina con código 1 si quedó algún error.
  Referencia medida con `AQuaBase` (2.5 M de filas): carga ~4 min y verificación ~5 min.
- La carga usa el modo SQL estricto del servidor **sin** `NO_ZERO_IN_DATE`/`NO_ZERO_DATE`: Access permite fechas
  como `1900-01-00` y MySQL las rechazaba. Se guardan tal cual, sin alterar el dato.
- Los datos se escriben a disco tabla por tabla (no se acumula todo el SQL en memoria), necesario para `.mdb`
  de cientos de MB; el cliente `mysql` usa `--max-allowed-packet=64M`.
- Tipos numéricos: `mdb-schema` convierte Access `Double` y `Currency` en `FLOAT` de 4 bytes (~7 dígitos), que
  pierde decimales en importes grandes. Se crean como `DOUBLE` y `DECIMAL(19,4)`; los memos como `LONGTEXT`
  (`TEXT` se limita a 64 KB). `verificar_migracion.py` audita estos tipos también en estructuras ya creadas con
  versiones anteriores de la herramienta: si aparecen columnas `FLOAT` donde Access tenía `Double`, hay que
  corregirlas (`ALTER TABLE ... MODIFY ... DOUBLE`) antes de cargar datos.
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
