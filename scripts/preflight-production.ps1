# Preflight de producción: valida el .env, certificados, compose y Settings.
# No imprime secretos.
[CmdletBinding()]
param(
    [string]$EnvFile = 'deploy/.env.production',
    [string]$ComposeFile = 'deploy/docker-compose.prod.yml',
    [switch]$SkipSettings
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repo $EnvFile
$composePath = Join-Path $repo $ComposeFile
$errores = New-Object System.Collections.Generic.List[string]

function Check([bool]$ok, [string]$label) {
    if ($ok) {
        Write-Host "OK   $label"
    } else {
        Write-Host "FAIL $label"
        $errores.Add($label) | Out-Null
    }
}

if (-not (Test-Path $envPath)) {
    throw "No existe $envPath. Ejecute scripts/prepare-production.ps1."
}
if (-not (Test-Path $composePath)) {
    throw "No existe $composePath."
}

$map = @{}
Get-Content $envPath | ForEach-Object {
    $linea = $_.Trim()
    if (-not $linea -or $linea.StartsWith('#')) { return }
    $pos = $linea.IndexOf('=')
    if ($pos -lt 1) { return }
    $clave = $linea.Substring(0, $pos).Trim()
    $valor = $linea.Substring($pos + 1).Trim()
    if ($valor.StartsWith('"') -and $valor.EndsWith('"') -and $valor.Length -ge 2) {
        $valor = $valor.Substring(1, $valor.Length - 2)
    }
    $map[$clave] = $valor
}

$get = { param($k) if ($map.ContainsKey($k)) { $map[$k] } else { '' } }

Check ((& $get 'EET_ENVIRONMENT') -eq 'prod') 'EET_ENVIRONMENT=prod'
Check (((& $get 'EET_AUTH_ENABLED') -match '^(?i:true)$')) 'EET_AUTH_ENABLED=true'
Check (((& $get 'EET_COOKIE_SECURE') -match '^(?i:true)$')) 'EET_COOKIE_SECURE=true'
Check ((& $get 'EET_SESSION_COOKIE') -eq '__Host-eet_session') 'EET_SESSION_COOKIE=__Host-eet_session'
Check (((& $get 'EET_PUBLIC_BASE_URL') -match '^https://')) 'EET_PUBLIC_BASE_URL=https://...'
Check (((& $get 'EET_PUBLIC_BASE_URL') -notmatch 'example\.gov\.py')) 'Dominio real (no example.gov.py)'
Check (((& $get 'EET_EMAIL_BACKEND') -eq 'smtp')) 'EET_EMAIL_BACKEND=smtp'
Check (((& $get 'EET_SMTP_HOST') -notmatch 'example\.gov\.py') -and ((& $get 'EET_SMTP_HOST') -ne '')) 'EET_SMTP_HOST real'
Check (((& $get 'EET_SMTP_USER') -ne '') -and ((& $get 'EET_SMTP_USER') -notmatch 'CAMBIAR')) 'EET_SMTP_USER configurado'
Check (((& $get 'EET_SMTP_PASSWORD') -ne '') -and ((& $get 'EET_SMTP_PASSWORD') -notmatch 'CAMBIAR')) 'EET_SMTP_PASSWORD configurado'
Check (((& $get 'EET_EMAIL_FROM') -notmatch 'example\.gov\.py') -and ((& $get 'EET_EMAIL_FROM') -ne '')) 'EET_EMAIL_FROM real'
Check (((& $get 'EET_ADMIN_INTERNAL_ONLY') -match '^(?i:true)$')) 'EET_ADMIN_INTERNAL_ONLY=true'
Check (((& $get 'EET_INTERNAL_CIDRS') -ne '')) 'EET_INTERNAL_CIDRS configurado'
Check (((& $get 'EET_TRUSTED_PROXY_IPS') -ne '')) 'EET_TRUSTED_PROXY_IPS configurado'
Check (((& $get 'EET_EXPOSE_DOCS') -match '^(?i:false)$')) 'EET_EXPOSE_DOCS=false'
Check (((& $get 'EET_SECRET_KEY').Length -ge 32)) 'EET_SECRET_KEY >= 32 caracteres'
Check (((& $get 'EET_AUTH_PEPPER').Length -ge 16)) 'EET_AUTH_PEPPER >= 16 caracteres'
Check (((& $get 'POSTGRES_USER') -ne '')) 'POSTGRES_USER configurado'
Check (((& $get 'POSTGRES_DB') -ne '')) 'POSTGRES_DB configurado'
Check (((& $get 'POSTGRES_PASSWORD').Length -ge 24)) 'POSTGRES_PASSWORD >= 24 caracteres'
Check (((& $get 'POSTGRES_PASSWORD') -match '^[A-Za-z0-9_-]+$')) 'POSTGRES_PASSWORD URL-safe (sin @ : / % # ?)'
Check (((& $get 'EET_DATABASE_URL')).Contains("://$((& $get 'POSTGRES_USER')):$((& $get 'POSTGRES_PASSWORD'))@")) 'EET_DATABASE_URL usa usuario y password de PostgreSQL'
Check (((& $get 'EET_DATABASE_URL')).Contains("/$((& $get 'POSTGRES_DB'))")) 'EET_DATABASE_URL usa POSTGRES_DB'
Check (-not ($map.Keys | Where-Object { $_ -like 'EET_DEMO_*' })) 'Sin variables EET_DEMO_*'
Check (-not (($map.Values | Where-Object { $_ -match 'CAMBIAR' }))) 'Sin valores CAMBIAR pendientes'

$certDir = Join-Path $repo 'deploy\certs'
Check (Test-Path (Join-Path $certDir 'fullchain.pem')) 'deploy/certs/fullchain.pem'
Check (Test-Path (Join-Path $certDir 'privkey.pem')) 'deploy/certs/privkey.pem'

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
    throw 'WSL no está disponible para validar Compose/Settings.'
}
$wslRepo = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $repo) | Out-String).Trim()
$wslEnv = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $envPath) | Out-String).Trim()
$wslCompose = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $composePath) | Out-String).Trim()

Write-Host ''
Write-Host 'Validando docker compose...'
& wsl.exe -d Ubuntu -u root --exec bash -lc `
    "cd '$wslRepo' && docker compose --env-file '$wslEnv' -f '$wslCompose' config --quiet"
if ($LASTEXITCODE -ne 0) {
    Check $false 'docker compose config'
} else {
    Check $true 'docker compose config'
}

if (-not $SkipSettings) {
    Write-Host 'Validando Settings() en la imagen...'
    & wsl.exe -d Ubuntu -u root --exec bash -lc `
        "cd '$wslRepo' && docker compose --env-file '$wslEnv' -f '$wslCompose' run --rm --no-deps --build app .venv/bin/python scripts/check_production_config.py"
    if ($LASTEXITCODE -ne 0) {
        Check $false 'Settings() de producción'
    } else {
        Check $true 'Settings() de producción'
    }
}

Write-Host ''
if ($errores.Count) {
    Write-Host "PREFLIGHT FALLÓ: $($errores.Count) problema(s)." -ForegroundColor Red
    exit 1
}
Write-Host 'PREFLIGHT OK' -ForegroundColor Green
