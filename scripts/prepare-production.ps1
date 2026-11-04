# Genera deploy/.env.production con secretos aleatorios y datos de producción.
# No imprime secretos. El archivo queda gitignored y con ACL restringida.
#
#   powershell -ExecutionPolicy Bypass -File scripts/prepare-production.ps1 `
#       -Dominio "eet.aep.gov.py" -SmtpHost "smtp.aep.gov.py" `
#       -SmtpUser "validador" -AdminEmail "admin@aep.gov.py"
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Dominio,
    [Parameter(Mandatory = $true)][string]$SmtpHost,
    [int]$SmtpPort = 587,
    [Parameter(Mandatory = $true)][string]$SmtpUser,
    [string]$SmtpPassword,
    [Parameter(Mandatory = $true)][string]$AdminEmail,
    [string]$EmailFrom,
    [string]$VpnCidr,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$destino = Join-Path $repo 'deploy\.env.production'
$certs = Join-Path $repo 'deploy\certs'

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

$Dominio = (($Dominio -replace '^https?://', '') -replace '/$', '').Trim()
if (-not $Dominio) {
    throw "El dominio no puede quedar vacío."
}
$secreto = New-Hex 48
$pepper = New-Hex 32
$dbPassword = New-Hex 24
if (-not $EmailFrom) {
    $EmailFrom = "Validador EET <no-reply@$Dominio>"
}

$cidrs = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32"
if ($VpnCidr) {
    $cidrs = "$cidrs,$($VpnCidr.Trim())"
}

$lineas = @(
    "# Generado por scripts/prepare-production.ps1 — NO versionar.",
    "EET_ENVIRONMENT=prod",
    "EET_AUTH_ENABLED=true",
    "EET_COOKIE_SECURE=true",
    "EET_SESSION_COOKIE=__Host-eet_session",
    "EET_PUBLIC_BASE_URL=https://$Dominio",
    "EET_SECRET_KEY=$secreto",
    "EET_AUTH_PEPPER=$pepper",
    "EET_FERNET_KEYS=",
    "POSTGRES_USER=eet",
    "POSTGRES_DB=eet",
    "POSTGRES_PASSWORD=$dbPassword",
    "EET_DATABASE_URL=postgresql+psycopg://eet:$dbPassword@db:5432/eet",
    "EET_EMAIL_BACKEND=smtp",
    "EET_SMTP_HOST=$SmtpHost",
    "EET_SMTP_PORT=$SmtpPort",
    "EET_SMTP_STARTTLS=true",
    "EET_SMTP_USER=$SmtpUser",
    "EET_SMTP_PASSWORD=$(Quote-Env $SmtpPassword)",
    "EET_EMAIL_FROM=$(Quote-Env $EmailFrom)",
    "EET_ADMIN_INTERNAL_ONLY=true",
    "EET_INTERNAL_CIDRS=$cidrs",
    "EET_TRUSTED_PROXY_IPS=172.30.0.10",
    "EET_EXPOSE_DOCS=false",
    "EET_MAX_UPLOAD_MB=20",
    "EET_LDAP_ENABLED=false"
)

$uploads = Join-Path $repo 'uploads'
New-Item -ItemType Directory -Force -Path (Split-Path $destino) | Out-Null
New-Item -ItemType Directory -Force -Path $certs | Out-Null
New-Item -ItemType Directory -Force -Path $uploads | Out-Null
[IO.File]::WriteAllText($destino, (($lineas -join "`n") + "`n"),
    [Text.UTF8Encoding]::new($false))

# Restringir el archivo al usuario actual (NTFS). Si falla, no bloquea.
& icacls.exe $destino /inheritance:r /grant:r "$($env:USERNAME):(F)" 2>$null | Out-Null

Write-Host "Configuración creada: $destino"
Write-Host "Correo administrador para el bootstrap: $AdminEmail"
Write-Host ""
Write-Host "Siguientes pasos:"
Write-Host "  0. En el host Linux, dé al directorio de uploads el UID del contenedor:"
Write-Host "     sudo install -d -o 10001 -g 10001 -m 0750 $uploads"
Write-Host "  1. Copie fullchain.pem y privkey.pem en deploy/certs/."
Write-Host "  2. Ejecute: powershell -ExecutionPolicy Bypass -File scripts/preflight-production.ps1"
Write-Host "  3. Levante: docker compose --env-file deploy/.env.production -f deploy/docker-compose.prod.yml up -d --build"
Write-Host "  4. Cree el admin: scripts/bootstrap-admin.ps1 (o el comando del README)."
