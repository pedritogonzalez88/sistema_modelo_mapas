# Pruebas locales del Validador EET en WSL 2 + Docker.
#
# Ejemplos:
#   powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1 -SkipBuild
#   powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1 -SkipIntegration
#   powershell -ExecutionPolicy Bypass -File scripts/test-local.ps1 -SkipSync
#
# Requisitos: WSL 2 con la distro Ubuntu registrada y Docker dentro de Ubuntu.
[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [switch]$SkipIntegration,
    [switch]$SkipSync
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL no está disponible. Instálelo con: wsl --install -d Ubuntu"
}

$distros = ((& wsl.exe -l -q) | Out-String) -replace "`0", ""
if ($distros -notmatch 'Ubuntu') {
    throw "No hay una distro Ubuntu registrada. Ejecute: wsl --install -d Ubuntu"
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script = Join-Path $PSScriptRoot 'test-local.sh'
if (-not (Test-Path $script)) {
    throw "No se encontró $script"
}

$wslSource = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $repo) | Out-String).Trim()
$wslScript = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $script) | Out-String).Trim()
if (-not $wslSource -or -not $wslScript) {
    throw "No se pudieron convertir las rutas a formato WSL."
}

$skipBuildArg = if ($SkipBuild) { '1' } else { '0' }
$skipIntegrationArg = if ($SkipIntegration) { '1' } else { '0' }
$skipSyncArg = if ($SkipSync) { '1' } else { '0' }

Write-Host "Repositorio: $wslSource"
Write-Host "Destino WSL: /opt/sistema_modelo_mapas"
Write-Host ""

& wsl.exe -d Ubuntu -u root --exec bash $wslScript `
    $wslSource '/opt/sistema_modelo_mapas' `
    $skipBuildArg $skipIntegrationArg $skipSyncArg

if ($LASTEXITCODE -ne 0) {
    throw "Las pruebas locales fallaron (exit $LASTEXITCODE)."
}
Write-Host "Pruebas locales completadas correctamente."
