# Registra el watchdog que mantiene disponible el Validador EET local.
# Requiere una consola elevada (UAC) porque crea una tarea programada.
#
#   powershell -ExecutionPolicy Bypass -File scripts/register-autostart.ps1
#   Unregister-ScheduledTask -TaskName "EET Local Watchdog" -Confirm:$false
[CmdletBinding()]
param(
    [string]$TaskName = 'EET Local Watchdog',
    [int]$Minutes = 5
)

$ErrorActionPreference = 'Stop'

$keep = Join-Path $PSScriptRoot 'keep-local.ps1'
$keepWsl = Join-Path $PSScriptRoot 'keep-wsl-alive.ps1'
if (-not (Test-Path $keep) -or -not (Test-Path $keepWsl)) {
    throw "Faltan keep-local.ps1 o keep-wsl-alive.ps1 en $PSScriptRoot"
}

$accion = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$keep`""

$triggerLogon = New-ScheduledTaskTrigger -AtLogOn `
    -User "$env:USERDOMAIN\$env:USERNAME"
# Trigger temporal: garantiza la ejecución cada N minutos aunque no haya un
# nuevo inicio de sesión (el de logon solo se dispara al entrar).
$triggerTimer = New-ScheduledTaskTrigger -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$configuracion = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# Tarea 1: mantiene una sesión WSL viva (sleep infinity) para que la VM no se
# apague al terminar el proceso que lanzó Docker.
$accionKeep = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$keepWsl`""
$triggerKeep = New-ScheduledTaskTrigger -AtLogOn `
    -User "$env:USERDOMAIN\$env:USERNAME"
$configKeep = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650) `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Stop-ScheduledTask -TaskName 'EET WSL Keepalive' -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName 'EET WSL Keepalive' `
    -Action $accionKeep -Trigger $triggerKeep -Settings $configKeep `
    -Principal $principal `
    -Description 'Mantiene viva la VM WSL del Validador EET local.' `
    -Force | Out-Null
Start-ScheduledTask -TaskName 'EET WSL Keepalive'

# Tarea 2: cada N minutos comprueba /salud y levanta el stack si hace falta.
Register-ScheduledTask -TaskName $TaskName `
    -Action $accion -Trigger @($triggerLogon, $triggerTimer) `
    -Settings $configuracion `
    -Principal $principal `
    -Description 'Mantiene disponible el Validador EET local (WSL + Docker).' `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Host "Tarea 'EET WSL Keepalive' registrada (sesión WSL persistente)."
Write-Host "Tarea '$TaskName' registrada y ejecutada (cada $Minutes minutos)."
Write-Host "Para quitarlas: Unregister-ScheduledTask -TaskName 'EET Local Watchdog','EET WSL Keepalive' -Confirm:`$false"
Write-Host "Log: $env:LOCALAPPDATA\EET\keep-local.log"
