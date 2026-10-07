# Ejecuta una fase de la migración dentro de Docker (Windows).
#   .\migrar.ps1 -Fase estructura -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos aqua_pruebas -Prefijo genbase_
#   .\migrar.ps1 -Fase datos      -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos aqua_pruebas -Prefijo genbase_
#   .\migrar.ps1 -Fase verificar  -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos aqua_pruebas -Prefijo genbase_
# El .mdb se COPIA al contenedor (el original no se modifica). MySQL se alcanza como host.docker.internal.
param(
    [Parameter(Mandatory)][ValidateSet('estructura', 'datos', 'verificar')][string]$Fase,
    [Parameter(Mandatory)][string]$Mdb,
    [string]$BaseDatos = 'aqua_pruebas',
    [string]$Prefijo = ''
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot

if (-not (Test-Path $Mdb)) { throw "No existe el archivo: $Mdb" }
if (-not (Test-Path "$repo\.env")) { throw "Falta .env: copia .env.example a .env y completa DB_PASSWORD" }

$script = switch ($Fase) { 'estructura' { 'crear_estructura.py' } 'datos' { 'cargar_datos.py' } 'verificar' { 'verificar_migracion.py' } }

docker build -t mdb-migrator $repo
if ($LASTEXITCODE -ne 0) { throw 'Falló docker build' }

$name = "mdb-migrator-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
docker run -d --name $name --env-file "$repo\.env" -e DB_HOST=host.docker.internal `
    --add-host=host.docker.internal:host-gateway mdb-migrator sleep infinity | Out-Null

try {
    $archivo = Split-Path $Mdb -Leaf
    docker cp $Mdb "${name}:/app/bases_datos/$archivo"
    docker exec $name python $script --db $BaseDatos --archivo $archivo "--prefijo=$Prefijo"

    $logs = Join-Path $repo 'logs'
    New-Item -ItemType Directory -Force $logs | Out-Null
    foreach ($l in 'errores_estructura.log', 'errores_carga_datos.log', 'rechazados_carga_datos.sql') {
        $destino = Join-Path $logs $l
        if (Test-Path $destino) { Remove-Item $destino -Force }   # evita mostrar el log de una corrida anterior
        docker cp "${name}:/app/$l" $destino 2>$null
    }
}
finally {
    docker rm -f $name | Out-Null
}
