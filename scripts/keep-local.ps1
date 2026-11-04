# Watchdog local: si la app no responde, arranca WSL + Docker + compose.
# Lo ejecuta la tarea programada "EET Local Watchdog" cada pocos minutos.
$ErrorActionPreference = 'SilentlyContinue'

$logDir = Join-Path $env:LOCALAPPDATA 'EET'
$log = Join-Path $logDir 'keep-local.log'

try {
    $respuesta = Invoke-WebRequest -Uri 'http://localhost:8000/salud' `
        -TimeoutSec 5 -UseBasicParsing
    if ($respuesta.StatusCode -eq 200) {
        exit 0
    }
} catch {
    # La app no responde: se intenta levantar el stack.
}

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
"[$(Get-Date -Format o)] /salud no responde; levantando el stack" |
    Add-Content -Path $log

$out = Join-Path $logDir 'start-local.out.log'
$err = Join-Path $logDir 'start-local.err.log'
$argumentos = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', (Join-Path $PSScriptRoot 'start-local.ps1'),
    '-NoBrowser'
)

try {
    $proceso = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList $argumentos `
        -Wait -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $out -RedirectStandardError $err
    if ($proceso.ExitCode -eq 0) {
        "[$(Get-Date -Format o)] stack levantado" | Add-Content -Path $log
        exit 0
    }
    "[$(Get-Date -Format o)] start-local exit $($proceso.ExitCode)" |
        Add-Content -Path $log
    if (Test-Path $err) {
        Get-Content $err -Tail 20 | Add-Content -Path $log
    }
    exit $proceso.ExitCode
} catch {
    "[$(Get-Date -Format o)] ERROR: $($_.Exception.Message)" |
        Add-Content -Path $log
    exit 1
}
