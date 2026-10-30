# =============================================================================
# scripts/e2e-auth-reset.ps1
# Deja el entorno listo para una corrida limpia de scripts/e2e-auth.mjs:
#   - borra las cuentas de prueba `e2e-*` (salvo `e2e-admin`, que se recrea con
#     la CLI) y todos sus artefactos de autenticación;
#   - devuelve `e2e-admin` a su estado inicial (sin TOTP, rol admin, activa);
#   - vacía la bandeja de correo local para que el .eml más reciente sea el de
#     la corrida.
# No toca datos de negocio (cruces, fichas, entregas, puntos_malla).
#
# Uso (PowerShell, desde la raíz del repositorio):
#   ./scripts/e2e-auth-reset.ps1
#   ./scripts/e2e-auth-reset.ps1 -MailDir "C:\...\mail-e2e"
# =============================================================================
param(
  [string]$Contenedor = "eet-mapa-aep",
  [string]$MailDir = "C:\Users\pgonzalez\AppData\Local\Temp\opencode\mail-e2e",
  [string]$AdminEmail = "e2e-admin@ejemplo.org",
  [string]$AdminUsername = "e2e-admin"
)

$ErrorActionPreference = "Stop"

$sql = @"
delete from mfa_email_codes where usuario_id in (select id from usuarios where username like 'e2e-%');
delete from recovery_codes  where usuario_id in (select id from usuarios where username like 'e2e-%');
delete from mfa_totp        where usuario_id in (select id from usuarios where username like 'e2e-%');
delete from auth_tokens     where usuario_id in (select id from usuarios where username like 'e2e-%');
delete from sesiones        where usuario_id in (select id from usuarios where username like 'e2e-%');
delete from login_attempts  where identificador like 'e2e-%';
delete from usuarios where username like 'e2e-%' and username <> '$AdminUsername';
update usuarios
   set mfa_enrolled_at = null, failed_attempts = 0, locked_until = null,
       is_active = true, rol = 'admin', mfa_policy = 'totp',
       must_change_password = false
 where username = '$AdminUsername';
select id, username, rol, is_active, mfa_policy, mfa_enrolled_at
  from usuarios order by id;
"@

docker exec $Contenedor psql -U eet -d eet -c $sql | Out-Host
if ($LASTEXITCODE -ne 0) { throw "psql falló (¿está arriba el contenedor $Contenedor?)" }

# La cuenta administradora de prueba se crea por CLI si no existe.
$existe = docker exec $Contenedor psql -U eet -d eet -t -A -c "select count(*) from usuarios where username = '$AdminUsername';"
if (($existe | Out-String).Trim() -eq "0") {
  if (-not $env:EET_ADMIN_PASSWORD) {
    throw "Defina EET_ADMIN_PASSWORD antes de ejecutar el reset del E2E."
  }
  uv run python -m app.cli crear-admin --email $AdminEmail --username $AdminUsername --nombre "Admin E2E" 2>&1 | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el administrador de prueba" }
}

if (Test-Path $MailDir) {
  Get-ChildItem $MailDir -File | Remove-Item -Force
}
New-Item -ItemType Directory -Force -Path $MailDir | Out-Null
Write-Host "Entorno E2E listo (bandeja de correo vacía en $MailDir)."
