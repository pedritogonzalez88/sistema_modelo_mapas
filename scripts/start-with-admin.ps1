# Levanta el Validador EET y garantiza un usuario administrador.
#
# Uso:
#   # Entorno local (dev)
#   powershell -ExecutionPolicy Bypass -File scripts/start-with-admin.ps1
#
#   # Produccion (requiere .env.production)
#   powershell -ExecutionPolicy Bypass -File scripts/start-with-admin.ps1 -Production
#
# Si no se pasan credenciales, se solicitan de forma interactiva (la
# contrasena no se muestra). El script es idempotente: si el administrador
# ya existe, no lo duplica.
param(
    [switch]$Production,
    [string]$AdminUser = "",
    [string]$AdminNombre = "",
    [string]$AdminEmail = "",
    [string]$AdminPassword = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $repo

if ($Production) {
    $envFile = Join-Path $repo '.env.production'
    $composeFile = Join-Path $repo 'docker-compose.prod.yml'
    if (-not (Test-Path $envFile)) {
        throw "No existe $envFile. Genere la configuracion con prepare-production.ps1."
    }
    if (-not (Test-Path $composeFile)) {
        throw "No existe $composeFile."
    }
} else {
    $envFile = Join-Path $repo '.env.local'
    $composeFile = Join-Path $repo 'docker-compose.yml'
    if (-not (Test-Path $envFile)) {
        throw "No existe $envFile. Ejecute primero scripts/start-local.ps1 o genere la configuracion local."
    }
}

# Cargar variables del entorno elegido.
$vars = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $vars[$matches[1]] = $matches[2].Trim().Trim('"').Trim("'")
    }
}

$buildArg = if ($SkipBuild) { @() } else { @('--build') }

Write-Host "Levantando stack ($envFile)..."
& docker compose --env-file $envFile -f $composeFile up -d @buildArg --wait --wait-timeout 240
if ($LASTEXITCODE -ne 0) { throw "docker compose up fallo (exit $LASTEXITCODE)." }

# Pedir credenciales si faltan.
if (-not $AdminUser) { $AdminUser = Read-Host "Usuario administrador" }
if (-not $AdminNombre) { $AdminNombre = Read-Host "Nombre completo" }
if (-not $AdminEmail) { $AdminEmail = Read-Host "Correo institucional" }
if (-not $AdminPassword) {
    $secure = Read-Host "Contrasena inicial" -AsSecureString
    $AdminPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

if (-not $AdminUser -or -not $AdminNombre -or -not $AdminEmail -or -not $AdminPassword) {
    throw "Faltan datos del administrador."
}

if ($Production) {
    & docker compose --env-file $envFile -f $composeFile run --rm -e "EET_SUPER_ADMIN_USERNAME=$AdminUser" -e "EET_SUPER_ADMIN_NOMBRE=$AdminNombre" -e "EET_SUPER_ADMIN_EMAIL=$AdminEmail" -e "EET_SUPER_ADMIN_PASSWORD=$AdminPassword" app .venv/bin/python scripts/seed_super_admin.py
    if ($LASTEXITCODE -ne 0) { throw "seed_super_admin fallo (exit $LASTEXITCODE)." }
} else {
    $env:EET_SUPER_ADMIN_USERNAME = $AdminUser
    $env:EET_SUPER_ADMIN_NOMBRE = $AdminNombre
    $env:EET_SUPER_ADMIN_EMAIL = $AdminEmail
    $env:EET_SUPER_ADMIN_PASSWORD = $AdminPassword
    $env:EET_DATABASE_URL = "postgresql+psycopg://eet:eet@localhost:5432/eet"
    $env:EET_ENVIRONMENT = "dev"
    & python scripts/seed_super_admin.py
    if ($LASTEXITCODE -ne 0) { throw "seed_super_admin fallo (exit $LASTEXITCODE)." }
}

$puerto = if ($Production) { "443" } else { "8000" }
$esquema = if ($Production) { "https" } else { "http" }
$hostName = if ($Production) { ($vars['EET_PUBLIC_BASE_URL'] -replace '^https?://','' -replace '/$','') } else { "localhost" }
Write-Host ""
Write-Host ("Sistema levantado: {0}://{1}{2}" -f $esquema, $hostName, (if ($Production) { "" } else { ":8000" }))
Write-Host "Usuario administrador: $AdminUser"
Write-Host "El primer login exigira cambiar la contrasena y enrolar TOTP."