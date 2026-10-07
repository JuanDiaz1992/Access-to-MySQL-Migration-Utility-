# Migracion Access -> MySQL dentro de Docker (Windows): una base o varias.
#
# Modo interactivo (recomendado):   .\migrar.ps1
#   Pregunta fase, bases de Access, base MySQL de destino y prefijo, con las opciones visibles; consulta el destino
#   para no duplicar datos, muestra un resumen y pide confirmacion antes de hacer nada.
#
# Modo directo (sin preguntas):
#   .\migrar.ps1 -Fase todo       -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos mi_base -Prefijo genbase_
#   .\migrar.ps1 -Fase estructura -Mdb C:\BasesActivas\GenBase.mdb,C:\BasesActivas\PtoBase.mdb -BaseDatos mi_base
#   .\migrar.ps1 -Fase datos      -Todas -BaseDatos mi_base
#   .\migrar.ps1 -Fase verificar  -Mdb C:\BasesActivas\GenBase.mdb -BaseDatos mi_base -Prefijo genbase_
#
# Fases: estructura | datos | verificar | todo (= las tres en ese orden, por cada base).
# Con varias bases el prefijo es automatico: nombre del archivo en minusculas + "_" (genbase_, ptobase_...).
# Los .mdb se COPIAN al contenedor (los originales no se modifican). MySQL se alcanza como host.docker.internal.
# Nunca duplica: omite las bases cuyas tablas ya existen o ya tienen datos (salvo -Forzar).
param(
    [ValidateSet('estructura', 'datos', 'verificar', 'todo')][string]$Fase,
    [string[]]$Mdb,
    [switch]$Todas,
    [string]$BaseDatos,
    [string]$Prefijo,
    [string]$CarpetaBases = 'C:\BasesActivas',
    [switch]$Forzar
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot

function Titulo($texto) { Write-Host ''; Write-Host $texto -ForegroundColor Cyan }
function PrefijoAuto($ruta) { return ([IO.Path]::GetFileNameWithoutExtension($ruta)).ToLower() + '_' }

function ResolverSeleccion($texto, $lista) {
    $res = @()
    foreach ($t in ($texto -split ',')) {
        $t = $t.Trim().Trim('"')
        if (-not $t) { continue }
        if ($t -match '^(t|todas|todos)$') { return @($lista | ForEach-Object { $_.FullName }) }
        if ($t -match '^(\d+)-(\d+)$') {
            $a = [int]$Matches[1]; $b = [int]$Matches[2]
            if ($a -lt 1 -or $b -gt $lista.Count -or $a -gt $b) { return $null }
            $res += @($lista[($a - 1)..($b - 1)] | ForEach-Object { $_.FullName })
        }
        elseif ($t -match '^\d+$') {
            $n = [int]$t
            if ($n -lt 1 -or $n -gt $lista.Count) { return $null }
            $res += $lista[$n - 1].FullName
        }
        elseif (Test-Path -LiteralPath $t -PathType Leaf) { $res += (Resolve-Path -LiteralPath $t).Path }
        else { return $null }
    }
    if ($res.Count -eq 0) { return $null }
    return @($res | Select-Object -Unique)
}

$interactivo = $false

# 1) Fase
if (-not $Fase) {
    $interactivo = $true
    Titulo 'FASE a ejecutar:'
    Write-Host '  1) estructura  Crea las tablas vacias en MySQL (primera vez)'
    Write-Host '  2) datos       Carga los datos en tablas que YA existen y estan vacias'
    Write-Host '  3) verificar   Compara Access contra MySQL fila por fila y celda por celda (solo lectura)'
    Write-Host '  4) todo        Las tres anteriores, en orden, por cada base'
    do { $op = Read-Host 'Elige 1, 2, 3 o 4' } until ($op -in '1', '2', '3', '4')
    $Fase = @('estructura', 'datos', 'verificar', 'todo')[[int]$op - 1]
}

# 2) Bases de Access (una o varias)
$archivos = @()
if ($Todas) {
    if (-not (Test-Path $CarpetaBases)) { throw "No existe la carpeta: $CarpetaBases" }
    $archivos = @(Get-ChildItem $CarpetaBases -Filter *.mdb | ForEach-Object { $_.FullName })
}
elseif ($Mdb) { $archivos = @($Mdb | ForEach-Object { $_.Trim().Trim('"') }) }
else {
    $interactivo = $true
    $lista = @()
    if (Test-Path $CarpetaBases) { $lista = @(Get-ChildItem $CarpetaBases -Filter *.mdb | Sort-Object Name) }
    Titulo "BASES de Access a migrar (carpeta $CarpetaBases):"
    for ($i = 0; $i -lt $lista.Count; $i++) {
        Write-Host ('  {0,2}) {1,-24} {2,8:N1} MB' -f ($i + 1), $lista[$i].Name, ($lista[$i].Length / 1MB))
    }
    Write-Host '  Una sola:        escribe su numero            ej: 6'
    Write-Host '  Varias:          separa con comas o rangos    ej: 1,3,5   o   1-4'
    Write-Host '  Todas:           escribe T'
    Write-Host '  Otro archivo:    escribe la ruta completa de un .mdb'
    do {
        $archivos = ResolverSeleccion (Read-Host 'Tu seleccion') $lista
        if (-not $archivos) { Write-Host '  Seleccion no valida: revisa los numeros o la ruta.' -ForegroundColor Yellow }
    } until ($archivos)
}
foreach ($a in $archivos) { if (-not (Test-Path -LiteralPath $a -PathType Leaf)) { throw "No existe el archivo: $a" } }
# Las mas pequenas primero: los resultados llegan antes
$archivos = @($archivos | Sort-Object { (Get-Item -LiteralPath $_).Length })

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
if ($BaseDatos -notmatch '^[A-Za-z0-9_]+$') { throw 'La base de datos solo puede tener letras, numeros y guion bajo (_).' }

# 4) Prefijo: con una sola base se elige; con varias es automatico por archivo
$prefijos = @{}
if ($archivos.Count -gt 1) {
    if ($PSBoundParameters.ContainsKey('Prefijo')) { throw 'Con varias bases el prefijo es automatico (nombre del archivo + _); no uses -Prefijo.' }
    foreach ($a in $archivos) { $prefijos[$a] = PrefijoAuto $a }
}
else {
    $a = $archivos[0]
    if (-not $PSBoundParameters.ContainsKey('Prefijo')) {
        $interactivo = $true
        $sugerido = PrefijoAuto $a
        Titulo 'PREFIJO de las tablas (evita mezclar bases de Access distintas dentro de la misma base MySQL):'
        Write-Host "  Enter           -> usa el sugerido: $sugerido"
        Write-Host '  otro texto      -> usa ese prefijo'
        Write-Host '  -               -> sin prefijo (las tablas conservan su nombre original)'
        $r = Read-Host 'Prefijo'
        $Prefijo = if ($r -eq '') { $sugerido } elseif ($r -eq '-') { '' } else { $r.Trim() }
    }
    if ($Prefijo -notmatch '^[A-Za-z0-9_]*$') { throw 'El prefijo solo puede tener letras, numeros y guion bajo (_).' }
    $prefijos[$a] = $Prefijo
}

if (-not (Test-Path "$repo\.env")) { throw 'Falta .env: copia .env.example a .env y completa DB_PASSWORD' }

# Imagen de Docker (la primera vez tarda unos minutos; despues usa la cache)
Titulo 'Preparando la imagen de Docker (la primera vez tarda unos minutos)...'
docker build -q -t mdb-migrator $repo | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Fallo docker build. Revisa que Docker Desktop este abierto y listo.' }

# Estado del destino y plan por base
function EstadoDestino($prefijo) {
    $salida = docker run --rm --env-file "$repo\.env" -e DB_HOST=host.docker.internal `
        --add-host=host.docker.internal:host-gateway mdb-migrator python estado_destino.py --db $BaseDatos "--prefijo=$prefijo" 2>&1
    $texto = ($salida | Out-String)
    if ($texto -notmatch 'ESTADO tablas=(\d+) filas=(\d+) existe=(\d)') {
        throw "No se pudo consultar MySQL (esta corriendo el servicio?):`n$texto"
    }
    return @{ Tablas = [int]$Matches[1]; Filas = [int]$Matches[2] }
}

$plan = @()
foreach ($a in $archivos) {
    $p = $prefijos[$a]
    $e = EstadoDestino $p
    $fases = @(); $omitida = ''
    switch ($Fase) {
        'estructura' {
            if ($e.Tablas -gt 0 -and -not $Forzar) { $omitida = "ya hay $($e.Tablas) tablas con ese prefijo" } else { $fases = @('estructura') }
        }
        'datos' {
            if ($e.Tablas -eq 0) { $omitida = "no hay tablas: ejecuta antes la fase estructura" }
            elseif ($e.Filas -gt 0 -and -not $Forzar) { $omitida = "las tablas ya tienen datos (~$($e.Filas) filas): se duplicarian" }
            else { $fases = @('datos') }
        }
        'verificar' {
            if ($e.Tablas -eq 0) { $omitida = 'no hay tablas que verificar' } else { $fases = @('verificar') }
        }
        'todo' {
            if ($e.Tablas -gt 0 -and -not $Forzar) { $omitida = "ya hay $($e.Tablas) tablas con ese prefijo (se omite para no duplicar)" }
            else { $fases = @('estructura', 'datos', 'verificar') }
        }
    }
    $plan += [pscustomobject]@{ Ruta = $a; Nombre = [IO.Path]::GetFileName($a); Prefijo = $p; Fases = $fases; Omitida = $omitida }
}

# Resumen y confirmacion
Titulo 'RESUMEN - esto es lo que se va a ejecutar:'
Write-Host "  Fase:        $Fase"
Write-Host "  Base MySQL:  $BaseDatos"
Write-Host '  Los archivos Access se copian; los originales no se modifican.'
Write-Host ''
foreach ($b in $plan) {
    $pref = if ($b.Prefijo) { $b.Prefijo } else { '(sin prefijo)' }
    if ($b.Omitida) { Write-Host ('  OMITIDA   {0,-22} {1,-18} {2}' -f $b.Nombre, $pref, $b.Omitida) -ForegroundColor Yellow }
    else { Write-Host ('  EJECUTA   {0,-22} {1,-18} {2}' -f $b.Nombre, $pref, ($b.Fases -join ' + ')) }
}
$aEjecutar = @($plan | Where-Object { -not $_.Omitida })
if ($aEjecutar.Count -eq 0) { Write-Host ''; Write-Host 'No hay nada que ejecutar. Usa -Forzar solo si estas seguro.' -ForegroundColor Yellow; return }
if ($interactivo) {
    Write-Host ''
    $confirma = Read-Host "Continuar con $($aEjecutar.Count) base(s)? (S/N)"
    if ($confirma -notmatch '^[sSyY]') { Write-Host 'Cancelado, no se hizo ningun cambio.'; return }
}

$scripts = @{ estructura = 'crear_estructura.py'; datos = 'cargar_datos.py'; verificar = 'verificar_migracion.py' }
$resultados = @()
$inicio = Get-Date
foreach ($b in $plan) {
    $fila = [ordered]@{ Base = $b.Nombre; Prefijo = $b.Prefijo; estructura = '-'; datos = '-'; verificar = '-' }
    if ($b.Omitida) { $fila['Nota'] = $b.Omitida; $resultados += [pscustomobject]$fila; continue }

    Titulo ("===== {0}  ({1}) =====" -f $b.Nombre, ($b.Fases -join ' + '))
    $name = "mdb-migrator-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
    docker run -d --name $name --env-file "$repo\.env" -e DB_HOST=host.docker.internal `
        --add-host=host.docker.internal:host-gateway mdb-migrator sleep infinity | Out-Null
    try {
        $archivo = Split-Path $b.Ruta -Leaf
        docker cp $b.Ruta "${name}:/app/bases_datos/$archivo"
        $logs = Join-Path $repo ('logs\' + [IO.Path]::GetFileNameWithoutExtension($archivo))
        New-Item -ItemType Directory -Force $logs | Out-Null

        foreach ($f in $b.Fases) {
            Write-Host ("--- fase: {0} ---" -f $f) -ForegroundColor DarkCyan
            docker exec $name python $scripts[$f] --db $BaseDatos --archivo $archivo "--prefijo=$($b.Prefijo)"
            $codigo = $LASTEXITCODE
            $fila[$f] = if ($codigo -eq 0) { 'OK' } else { 'FALLO' }
            foreach ($l in 'errores_estructura.log', 'errores_carga_datos.log', 'rechazados_carga_datos.sql') {
                $destino = Join-Path $logs $l
                if (Test-Path $destino) { Remove-Item $destino -Force }   # no mostrar logs de corridas anteriores
                docker cp "${name}:/app/$l" $destino 2>$null
            }
            if ($f -eq 'estructura' -and $codigo -ne 0) {
                $fila['Nota'] = 'la estructura fallo: no se cargaron datos'
                break
            }
        }
    }
    finally {
        docker rm -f $name | Out-Null
    }
    $resultados += [pscustomobject]$fila
}

Titulo ("RESULTADO FINAL ({0:N1} min)" -f ((Get-Date) - $inicio).TotalMinutes)
$resultados | Format-Table -AutoSize
$hayFallo = @($resultados | Where-Object { $_.estructura -eq 'FALLO' -or $_.datos -eq 'FALLO' -or $_.verificar -eq 'FALLO' }).Count
if ($hayFallo) { Write-Host "Hubo fallos: revisa la carpeta logs\<base>\ y el detalle impreso arriba." -ForegroundColor Red }
else { Write-Host 'Sin fallos.' -ForegroundColor Green }
