# Mantiene viva la VM WSL con una sesión `sleep infinity`.
# Si WSL se apaga (o el proceso termina), lo vuelve a iniciar.
$ErrorActionPreference = 'SilentlyContinue'

while ($true) {
    $proceso = Start-Process -FilePath 'wsl.exe' `
        -ArgumentList @('-d', 'Ubuntu', '-u', 'root', '--exec', 'sleep', 'infinity') `
        -PassThru -WindowStyle Hidden
    if ($proceso) {
        $proceso.WaitForExit()
    }
    Start-Sleep -Seconds 5
}
