# Despliegue en Easypanel — Validador EET

Este repositorio **no debe desplegarse como Compose dentro de Easypanel**. El
compose de producción (`deploy/docker-compose.prod.yml`) administra nginx,
TLS y puertos 80/443, y Easypanel ya hace esa función con su proxy. En
Easypanel se usa el modo **App (Dockerfile)** y una base PostGIS aparte.

## 0. Dos modos posibles

| Modo | Cuándo usarlo | Archivo |
|---|---|---|
| **App + Dockerfile** (recomendado) | Una app y una base PostGIS administradas por separado | `Dockerfile` |
| **Compose** | Si prefiere app + base en un solo servicio de Easypanel | `docker-compose.easypanel.yml` |

El `docker-compose.yml` de la raíz es **solo para desarrollo local** y no debe
usarse en Easypanel: publica `5432` y `8000`, fija `container_name` y corre la
app como root para el bind mount local. Easypanel avisa precisamente por esos
campos.

## 1. Error `unknown instruction: services:`

Ese error aparece cuando en Easypanel el campo **Dockerfile** apunta a
`docker-compose.yml`:

```text
docker buildx build -f .../docker-compose.yml ...
dockerfile parse error on line 1: unknown instruction: services:
```

Corrección en **Easypanel → proyecto → servicio → Settings / Build**:

| Campo | Valor |
|---|---|
| Build method | **Dockerfile** |
| Dockerfile | `Dockerfile` |
| Build context | `/` (raíz del repositorio) |
| Puerto | `8000` |

No use `docker-compose.yml` ni `deploy/docker-compose.prod.yml` en el campo
Dockerfile. Guarde y vuelva a desplegar.

## 1.1 Avisos `container_name` y `ports`

Si Easypanel muestra:

```text
container_name is used in app...
ports is used in app...
container_name is used in db...
ports is used in db...
```

está leyendo el `docker-compose.yml` de desarrollo. Easypanel administra los
nombres de contenedor y el proxy/TLS, así que esos campos no deben estar en un
Compose de Easypanel. Dos soluciones:

1. **Recomendada:** use el modo App/Dockerfile y no un servicio Compose.
2. Si usa Compose, apunte **Docker Compose File** a
   `docker-compose.easypanel.yml` (no a `docker-compose.yml`). Ese archivo:

   - no tiene `container_name` ni `ports`;
   - no levanta nginx (Easypanel hace TLS/proxy);
   - usa volúmenes nombrados para PostgreSQL y uploads;
   - escucha internamente en `app:8000` y `db:5432`.

   En Easypanel configure el dominio hacia el servicio interno `app`, puerto
   `8000`.

## 1.2 Variables de entorno y `.env.example`

Easypanel **no lee** `deploy/.env.production` del repositorio: los secretos se
cargan en **Environment**. Para Compose, Easypanel puede crear un `.env` en el
build path a partir de `.env.example` (habilite **Create .env file**).

Archivos del repositorio:

| Archivo | Uso |
|---|---|
| `.env.example` | Plantilla para el editor de Environment de Easypanel/Compose |
| `deploy/.env.production.example` | Plantilla para el host con `docker-compose.prod.yml` |
| `deploy/.env.production` | Generado localmente, gitignored, **no va a Easypanel** |
| `deploy/.env.easypanel` | Generado por el script, gitignored, para copiar/pegar |

Genere valores reales sin escribirlos a mano:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/prepare-easypanel-env.ps1 `
  -Dominio "eet.aep.gov.py" `
  -SmtpHost "smtp.aep.gov.py" `
  -SmtpUser "validador" `
  -AdminEmail "admin@aep.gov.py"
```

Con `-DbHost "db"` para el Compose; con el nombre del servicio de base si usa
App/Dockerfile. Luego copie el contenido en Easypanel → Environment (o use
`Get-Content deploy/.env.easypanel -Raw | Set-Clipboard`). No lo commitee.

> **Password de PostgreSQL:** use solo caracteres URL-safe. Lo más simple es
> `openssl rand -hex 24`. Si el password contiene `@`, `%`, `:`, `/`, `#` o
> `?`, debe percent-encodearlo dentro de `EET_DATABASE_URL`
> (`@` → `%40`, `%` → `%25`). Un `@` sin codificar parte mal la URL y la app no
> conecta.

## 2. Base de datos PostGIS

Cree en el mismo proyecto un servicio de base de datos con la imagen
`postgis/postgis:16-3.4` (o el template PostGIS de Easypanel). Use:

```text
POSTGRES_DB=eet
POSTGRES_USER=eet
POSTGRES_PASSWORD=<password-URL-safe>
```

Monte un volumen persistente en `/var/lib/postgresql/data` y **no** publique
el puerto. En el servicio de la app, `EET_DATABASE_URL` debe apuntar al
nombre interno del servicio de base:

```text
postgresql+psycopg://eet:<password-URL-safe>@<servicio-db>:5432/eet
```

El password debe ser URL-safe (por ejemplo `openssl rand -hex 24`), porque se
inserta en la URL de conexión. El usuario, la base y el password de
`POSTGRES_*` deben ser **exactamente** los mismos que aparecen en
`EET_DATABASE_URL`; si el contenedor crea `eet`, no ponga `aepeet` en la URL.

En modo App/Dockerfile el host no es `db`: use el nombre interno del servicio
PostGIS que cree Easypanel (por ejemplo `eet-db`). En el Compose de Easypanel
el host es `db` porque es el nombre del servicio del archivo
`docker-compose.easypanel.yml`.

## 3. Variables de entorno de la app

Configure en **Environment** (nunca en el repositorio). Mínimo para producción:

```text
EET_ENVIRONMENT=prod
EET_AUTH_ENABLED=true
EET_COOKIE_SECURE=true
EET_SESSION_COOKIE=__Host-eet_session
EET_PUBLIC_BASE_URL=https://<dominio>
EET_SECRET_KEY=<48+ hex>
EET_AUTH_PEPPER=<32+ hex>
EET_FERNET_KEYS=
EET_DATABASE_URL=postgresql+psycopg://eet:<password>@<servicio-db>:5432/eet
EET_EMAIL_BACKEND=smtp
EET_SMTP_HOST=<smtp>
EET_SMTP_PORT=587
EET_SMTP_STARTTLS=true
EET_SMTP_USER=<usuario>
EET_SMTP_PASSWORD=<password>
EET_EMAIL_FROM=Validador EET <no-reply@<dominio>>
EET_EXPOSE_DOCS=false
EET_MAX_UPLOAD_MB=20
EET_LDAP_ENABLED=false
```

Sobre el proxy de Easypanel:

- Easypanel termina TLS y reenvía a la app. Deje
  `EET_TRUSTED_PROXY_IPS` con la IP/CIDR del proxy de Easypanel si la conoce;
  si no, déjelo vacío.
- Con `EET_ADMIN_INTERNAL_ONLY=true` y el proxy en una red privada, todas las
  peticiones parecen internas. Si el panel administrativo se usará desde
  Internet, use `EET_ADMIN_INTERNAL_ONLY=false` y mantenga TOTP obligatorio;
  si se usará solo por VPN, configure el CIDR de la VPN en
  `EET_INTERNAL_CIDRS`.

## 4. Volumen de uploads

Monte un volumen persistente en:

```text
/app/uploads
```

La imagen corre como `10001:10001` y crea `/app/uploads` con ese dueño; un
volumen nuevo de Docker hereda esos permisos. Si usa un **bind mount** del
host, ejecute antes:

```bash
sudo install -d -o 10001 -g 10001 -m 0750 <ruta-host>/uploads
```

Sin este volumen, los archivos de entrega se pierden al redesplegar.

## 5. Dominio y TLS

En **Domains**, agregue el dominio y active HTTPS. Easypanel emite y renueva
el certificado. No publique los puertos 80/443 desde la app: el proxy de
Easypanel ya los usa.

## 6. Primer administrador

Cuando el servicio esté saludable, abra la **Console/Terminal** del servicio y
ejecute:

```bash
cd /app
.venv/bin/python scripts/bootstrap_admin.py
```

Solicita usuario, nombre, correo y contraseña. El primer login exigirá cambiar
la contraseña y enrolar TOTP. En producción no se ejecuta la semilla demo.

## 7. Verificación

- `https://<dominio>/salud` → `{"estado":"ok"}`
- `https://<dominio>/ready` → `{"estado":"listo","base":"ok"}`
- El healthcheck de la imagen usa `/ready`.
- Login del administrador y enrolamiento TOTP.
- Subida de una entrega y confirmación.
- Revisar logs; cada petición lleva `request_id` para correlacionar.

## 8. Actualizaciones y rollback

- Cada push a `master` puede redesplegar la app.
- Antes de actualizar, ejecute el backup (`scripts/backup-local.sh` desde el
  host o el comando equivalente en la consola) y verifique el restore.
- Si una migración falla, no borre el volumen de PostgreSQL: restaure el
  backup en una base nueva y actualice `EET_DATABASE_URL`.
- Easypanel conserva el volumen de la base y el de uploads entre redespliegues.
