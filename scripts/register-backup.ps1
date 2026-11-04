# Programa el backup diario en Windows/WSL (alternativa a systemd).
# Requiere un destino externo (NAS/disco) y una clave de cifrado en WSL.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destino,
    [string]$KeyFile = '/etc/eet/backup.key',
    [string]$Hora = '02:30',
    [int]$Dias = 30,
    [switch]$Now
)

$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'backup-local.sh'
if (-not (Test-Path $script)) {
    throw "No se encontró $script"
}
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "WSL no está disponible."
}
if (-not (Test-Path $Destino)) {
    New-Item -ItemType Directory -Force -Path $Destino | Out-Null
}

$scriptWsl = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $script) |
    Out-String).Trim()
$destinoWsl = ((& wsl.exe -d Ubuntu -u root --exec wslpath -a $Destino) |
    Out-String).Trim()
$keyCheck = & wsl.exe -d Ubuntu -u root --exec test -f $KeyFile
if ($LASTEXITCODE -ne 0) {
    throw "No existe la clave $KeyFile dentro de WSL. Genérela con: wsl -d Ubuntu -u root --exec bash -c 'install -d -m 700 /etc/eet && openssl rand -hex 32 > /etc/eet/backup.key && chmod 600 /etc/eet/backup.key'"
}

$accion = New-ScheduledTaskAction -Execute 'wsl.exe' `
    -Argument "-d Ubuntu -u root --exec bash `"$scriptWsl`" --dest `"$destinoWsl`" --key-file `"$KeyFile`" --retention-days $Dias --verify"
$trigger = New-ScheduledTaskTrigger -Daily -At $Hora
$config = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName 'EET Backup' `
    -Action $accion -Trigger $trigger -Settings $config `
    -Principal $principal `
    -Description 'Backup diario cifrado del Validador EET local.' `
    -Force | Out-Null

if ($Now) {
    Start-ScheduledTask -TaskName 'EET Backup'
}
Write-Host "Tarea 'EET Backup' registrada para las $Hora (cada día)."
Write-Host "Destino: $Destino"
