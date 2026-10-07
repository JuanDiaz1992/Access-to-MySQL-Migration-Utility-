# Ejecuta UNA fase de la migracion Access -> MySQL dentro de Docker (Windows).
#
# Modo interactivo (recomendado):   .\migrar.ps1
#   Pregunta cada dato mostrando las opciones, resume lo que va a hacer y pide confirmacion.
#
# Modo directo (los 4 datos juntos, sin preguntas):
#   .\migrar.ps1 -Fase estructura -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos mi_base -Prefijo genbase_
#   .\migrar.ps1 -Fase datos      -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos mi_base -Prefijo genbase_
#   .\migrar.ps1 -Fase verificar  -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos mi_base -Prefijo genbase_
#
# El .mdb se COPIA al contenedor (el original no se modifica). MySQL se alcanza como host.docker.internal.
# No hay valores por defecto para la base de datos ni para el prefijo: siempre se eligen de forma explicita.
param(
    [ValidateSet('estructura', 'datos', 'verificar')][string]$Fase,
    [string]$Mdb,
    [string]$BaseDatos,
    [string]$Prefijo,
    [string]$CarpetaBases = 'C:\BasesActivas'
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot

function Titulo($texto) { Write-Host ''; Write-Host $texto -ForegroundColor Cyan }

$interactivo = $false

# 1) Fase
if (-not $Fase) {
    $interactivo = $true
    Titulo 'FASE a ejecutar (se hacen en este orden):'
    Write-Host '  1) estructura  Crea las tablas vacias en MySQL (primera vez)'
    Write-Host '  2) datos       Carga los datos en tablas que YA existen y estan vacias'
    Write-Host '  3) verificar   Compara Access contra MySQL fila por fila y celda por celda'
    do { $op = Read-Host 'Elige 1, 2 o 3' } until ($op -in '1', '2', '3')
    $Fase = @('estructura', 'datos', 'verificar')[[int]$op - 1]
}

# 2) Archivo de Access
if (-not $Mdb) {
    $interactivo = $true
    $lista = @()
    if (Test-Path $CarpetaBases) { $lista = @(Get-ChildItem $CarpetaBases -Filter *.mdb | Sort-Object Name) }
    Titulo "ARCHIVO de Access a migrar (carpeta $CarpetaBases):"
    for ($i = 0; $i -lt $lista.Count; $i++) {
        Write-Host ('  {0,2}) {1,-24} {2,8:N1} MB' -f ($i + 1), $lista[$i].Name, ($lista[$i].Length / 1MB))
    }
    Write-Host '  (o escribe la ruta completa de otro archivo .mdb)'
    do {
        $r = (Read-Host 'Numero de la lista o ruta').Trim().Trim('"')
        if ($r -match '^\d+$' -and [int]$r -ge 1 -and [int]$r -le $lista.Count) { $Mdb = $lista[[int]$r - 1].FullName }
        elseif ($r -and (Test-Path -LiteralPath $r -PathType Leaf)) { $Mdb = $r }
        else { Write-Host '  Opcion no valida: elige un numero de la lista o una ruta de archivo existente.' -ForegroundColor Yellow }
    } until ($Mdb)
}

# 3) Base de datos MySQL de destino (sin valor por defecto)
if (-not $BaseDatos) {
    $interactivo = $true
    Titulo 'BASE DE DATOS MySQL de destino (obligatoria; la fase "estructura" la crea si no existe):'
    do {
        $BaseDatos = (Read-Host 'Nombre de la base de datos').Trim()
        if ($BaseDatos -notmatch '^[A-Za-z0-9_]+$') {
            Write-Host '  Usa solo letras, numeros y guion bajo (_).' -ForegroundColor Yellow
            $BaseDatos = $null
        }
    } until ($BaseDatos)
}

# 4) Prefijo de las tablas (sin valor por defecto silencioso)
if (-not $PSBoundParameters.ContainsKey('Prefijo')) {
    $interactivo = $true
    $sugerido = ([IO.Path]::GetFileNameWithoutExtension($Mdb)).ToLower() + '_'
    Titulo 'PREFIJO de las tablas (evita mezclar bases de Access distintas dentro de la misma base MySQL):'
    Write-Host "  Enter           -> usa el sugerido: $sugerido"
    Write-Host '  otro texto      -> usa ese prefijo'
    Write-Host '  -               -> sin prefijo (las tablas conservan su nombre original)'
    $r = Read-Host 'Prefijo'
    $Prefijo = if ($r -eq '') { $sugerido } elseif ($r -eq '-') { '' } else { $r.Trim() }
}
if ($Prefijo -notmatch '^[A-Za-z0-9_]*$') { throw 'El prefijo solo puede tener letras, numeros y guion bajo (_).' }

if (-not (Test-Path -LiteralPath $Mdb)) { throw "No existe el archivo: $Mdb" }
if (-not (Test-Path "$repo\.env")) { throw 'Falta .env: copia .env.example a .env y completa DB_PASSWORD' }

# Resumen y confirmacion
Titulo 'RESUMEN - esto es lo que se va a ejecutar:'
Write-Host "  Fase:             $Fase"
Write-Host "  Archivo Access:   $Mdb  (se copia; el original no se modifica)"
Write-Host "  Base MySQL:       $BaseDatos"
Write-Host ('  Prefijo tablas:   ' + $(if ($Prefijo) { $Prefijo } else { '(ninguno)' }))
switch ($Fase) {
    'estructura' { Write-Host '  Efecto: CREA tablas en esa base (si ya existen, no las sobrescribe).' -ForegroundColor DarkYellow }
    'datos'      { Write-Host '  Efecto: AGREGA filas. Si las tablas ya tienen datos se duplicaran.' -ForegroundColor DarkYellow }
    'verificar'  { Write-Host '  Efecto: solo lectura, no modifica nada.' -ForegroundColor DarkYellow }
}
if ($interactivo) {
    $confirma = Read-Host 'Continuar? (S/N)'
    if ($confirma -notmatch '^[sSyY]') { Write-Host 'Cancelado, no se hizo ningun cambio.'; return }
}

$script = switch ($Fase) { 'estructura' { 'crear_estructura.py' } 'datos' { 'cargar_datos.py' } 'verificar' { 'verificar_migracion.py' } }

docker build -t mdb-migrator $repo
if ($LASTEXITCODE -ne 0) { throw 'Fallo docker build' }

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
