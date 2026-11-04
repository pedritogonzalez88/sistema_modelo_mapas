# Genera deploy/.env.easypanel con secretos aleatorios y valores de producción
# para pegarlos en Easypanel → Environment.
#
# Uso (Compose de Easypanel, el host de la base es el servicio "db"):
#   powershell -ExecutionPolicy Bypass -File scripts/prepare-easypanel-env.ps1 `
#       -Dominio "eet.aep.gov.py" -SmtpHost "smtp.aep.gov.py" `
#       -SmtpUser "validador" -AdminEmail "admin@aep.gov.py"
#
# Uso (App/Dockerfile + base PostGIS aparte, host = nombre del servicio de base):
#   ... -DbHost "eet-db"
#
# No imprime secretos. El archivo queda gitignored.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Dominio,
    [Parameter(Mandatory = $true)][string]$SmtpHost,
    [int]$SmtpPort = 587,
    [Parameter(Mandatory = $true)][string]$SmtpUser,
    [string]$SmtpPassword,
    [Parameter(Mandatory = $true)][string]$AdminEmail,
    [string]$EmailFrom,
    [string]$DbHost = 'db',
    [string]$DbUser = 'eet',
    [string]$DbName = 'eet',
    [string]$TrustedProxyIps = '',
    [switch]$AdminInternalOnly,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$destino = Join-Path $repo 'deploy\.env.easypanel'

if ((Test-Path $destino) -and -not $Force) {
    throw "Ya existe $destino. Use -Force para reemplazarlo."
}

function New-Hex([int]$bytes) {
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $buffer = New-Object byte[] $bytes
    $rng.GetBytes($buffer)
    $rng.Dispose()
    return (($buffer | ForEach-Object { $_.ToString('x2') }) -join '')
}

function Quote-Env([string]$value) {
    return '"' + ($value -replace '\\', '\\\\' -replace '"', '\"') + '"'
}

$Dominio = (($Dominio -replace '^https?://', '') -replace '/$', '').Trim()
if (-not $Dominio) {
    throw "El dominio no puede quedar vacío."
}
if (-not $SmtpPassword) {
    $seguro = Read-Host -AsSecureString "Password SMTP para $SmtpUser"
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro)
    try {
        $SmtpPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}
if (-not $SmtpPassword) {
    throw "El password SMTP es obligatorio."
}

$secreto = New-Hex 48
$pepper = New-Hex 32
$dbPassword = New-Hex 24
if (-not $EmailFrom) {
    $EmailFrom = "Validador EET <no-reply@$Dominio>"
}
$adminInterno = if ($AdminInternalOnly) { 'true' } else { 'false' }

$lineas = @(
    "# Generado por scripts/prepare-easypanel-env.ps1 — NO versionar.",
    "POSTGRES_USER=$DbUser",
    "POSTGRES_DB=$DbName",
    "POSTGRES_PASSWORD=$dbPassword",
    "EET_DATABASE_URL=postgresql+psycopg://${DbUser}:$dbPassword@${DbHost}:5432/$DbName",
    "EET_ENVIRONMENT=prod",
    "EET_AUTH_ENABLED=true",
    "EET_COOKIE_SECURE=true",
    "EET_SESSION_COOKIE=__Host-eet_session",
    "EET_PUBLIC_BASE_URL=https://$Dominio",
    "EET_SECRET_KEY=$secreto",
    "EET_AUTH_PEPPER=$pepper",
    "EET_FERNET_KEYS=",
    "EET_EMAIL_BACKEND=smtp",
    "EET_SMTP_HOST=$SmtpHost",
    "EET_SMTP_PORT=$SmtpPort",
    "EET_SMTP_STARTTLS=true",
    "EET_SMTP_USER=$SmtpUser",
    "EET_SMTP_PASSWORD=$(Quote-Env $SmtpPassword)",
    "EET_EMAIL_FROM=$(Quote-Env $EmailFrom)",
    "EET_ADMIN_INTERNAL_ONLY=$adminInterno",
    "EET_INTERNAL_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32",
    "EET_TRUSTED_PROXY_IPS=$TrustedProxyIps",
    "EET_EXPOSE_DOCS=false",
    "EET_MAX_UPLOAD_MB=20",
    "EET_LDAP_ENABLED=false"
)

New-Item -ItemType Directory -Force -Path (Split-Path $destino) | Out-Null
[IO.File]::WriteAllText($destino, (($lineas -join "`n") + "`n"),
    [Text.UTF8Encoding]::new($false))
& icacls.exe $destino /inheritance:r /grant:r "$($env:USERNAME):(F)" 2>$null | Out-Null

Write-Host "Variables generadas: $destino"
Write-Host "Correo administrador para el bootstrap: $AdminEmail"
Write-Host ""
Write-Host "Siguientes pasos:"
Write-Host "  1. Copie el contenido en Easypanel → Environment (no lo commitee)."
Write-Host "     Si usa el Compose de Easypanel, habilite 'Create .env file'."
Write-Host "  2. En App/Dockerfile, use -DbHost con el nombre del servicio de base."
Write-Host "  3. Configure el dominio y HTTPS en Easypanel."
Write-Host "  4. Despliegue y cree el admin en la consola:"
Write-Host "     cd /app && .venv/bin/python scripts/bootstrap_admin.py"
Write-Host ""
Write-Host "Para copiar al portapapeles (contiene secretos):"
Write-Host "  Get-Content '$destino' -Raw | Set-Clipboard"
