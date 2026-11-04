# Levanta el stack local (WSL + Docker) y abre el login.
#
#   powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
#   powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1 -NoBrowser
[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL no está disponible. Instálelo con: wsl --install -d Ubuntu"
}

# Script bash en archivo: evita problemas de quoting con Start-Process.
$sh = Join-Path $env:TEMP 'eet-start-local.sh'
$contenido = @'
set -e
systemctl start docker 2>/dev/null || service docker start 2>/dev/null || true
cd /opt/sistema_modelo_mapas
docker compose up -d --wait --wait-timeout 180
docker compose ps
'@
[IO.File]::WriteAllText($sh, $contenido.Replace("`r`n", "`n"))
$wslSh = '/mnt/c' + ($sh.Substring(2) -replace '\\', '/')

$out = Join-Path $env:TEMP 'eet-start-local.out.log'
$err = Join-Path $env:TEMP 'eet-start-local.err.log'
Remove-Item $out, $err -ErrorAction SilentlyContinue

$codigo = 1
for ($intento = 1; $intento -le 2; $intento++) {
    $proceso = Start-Process -FilePath 'wsl.exe' `
        -ArgumentList @('-d', 'Ubuntu', '-u', 'root', 'bash', $wslSh) `
        -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err
    $codigo = $proceso.ExitCode
    if ($codigo -eq 0) {
        break
    }
    Start-Sleep -Seconds 3
}

$listo = $false
for ($intento = 0; $intento -lt 60; $intento++) {
    try {
        $respuesta = Invoke-WebRequest -Uri 'http://localhost:8000/salud' `
            -TimeoutSec 3 -UseBasicParsing
        if ($respuesta.StatusCode -eq 200) {
            $listo = $true
            break
        }
    } catch {
        # La app todavía está arrancando.
    }
    Start-Sleep -Seconds 1
}

if (-not $listo) {
    $detalle = @()
    if (Test-Path $out) { $detalle += Get-Content $out -Tail 30 }
    if (Test-Path $err) { $detalle += Get-Content $err -Tail 30 }
    throw ("La app no respondió en 60 s (wsl exit $codigo).`n" +
           ($detalle -join "`n"))
}

Write-Host "App lista: http://localhost:8000/auth/login"
if (-not $NoBrowser) {
    Start-Process 'http://localhost:8000/auth/login'
}
