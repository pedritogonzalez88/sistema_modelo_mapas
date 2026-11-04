# Backup local con verificación de restauración.
#
#   powershell -ExecutionPolicy Bypass -File scripts/backup.ps1 `
#       -Destino "D:\EET-Backups" -Verify
#
# El destino debe ser un disco/NAS externo, no el mismo volumen de datos.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destino,
    [int]$Dias = 30,
    [string]$KeyFile = '',
    [string]$Container = '',
    [switch]$Verify
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL no está disponible."
}

$script = Join-Path $PSScriptRoot 'backup-local.sh'
if (-not (Test-Path $script)) {
    throw "No se encontró $script"
}

# Crear el destino en Windows si no existe (también crea la carpeta montada).
if (-not (Test-Path $Destino)) {
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
}
$destinoWsl = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $Destino) |
    Out-String).Trim()
$scriptWsl = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $script) |
    Out-String).Trim()

$argumentos = @($scriptWsl, '--dest', $destinoWsl,
                '--retention-days', "$Dias")
if ($KeyFile) {
    $argumentos += @('--key-file', $KeyFile)
}
if ($Container) {
    $argumentos += @('--container', $Container)
}
if ($Verify) {
    $argumentos += '--verify'
}

& wsl.exe -d Ubuntu -u root --exec bash @argumentos
if ($LASTEXITCODE -ne 0) {
    throw "El backup falló (exit $LASTEXITCODE)."
}
