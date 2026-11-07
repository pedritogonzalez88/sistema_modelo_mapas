# Crea el administrador inicial de producción (TOTP obligatorio).
# El comando solicita usuario, nombre, correo y contraseña de forma interactiva.
[CmdletBinding()]
param(
    [string]$EnvFile = '.env.production',
    [string]$ComposeFile = 'docker-compose.prod.yml'
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repo $EnvFile
$composePath = Join-Path $repo $ComposeFile

if (-not (Test-Path $envPath) -or -not (Test-Path $composePath)) {
    throw "Faltan $envPath o $composePath."
}
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL no está disponible."
}

$wslRepo = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $repo) | Out-String).Trim()
$wslEnv = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $envPath) | Out-String).Trim()
$wslCompose = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $composePath) | Out-String).Trim()

& wsl.exe -d Ubuntu -u root --exec bash -lc `
    "cd '$wslRepo' && docker compose --env-file '$wslEnv' -f '$wslCompose' run --rm --no-deps app .venv/bin/python scripts/bootstrap_admin.py"
if ($LASTEXITCODE -ne 0) {
    throw "No se pudo crear el administrador (exit $LASTEXITCODE)."
}
