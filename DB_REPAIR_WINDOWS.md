# Reparacion de conexion PostgreSQL local

## Diagnostico confirmado

- El backend usa `postgresql+asyncpg://postgres:postgres@localhost:5432/emergency_db`.
- PostgreSQL 16 esta escuchando en `5432`.
- PostgreSQL 18 esta escuchando en `5433`.
- La red local no esta bloqueada: el puerto `5432` responde en localhost.
- Los logs del servidor muestran la causa raiz:
  - `password authentication failed for user "postgres"`
  - El intento coincide con `pg_hba.conf` usando `scram-sha-256` para `127.0.0.1/32` y `::1/128`.

Conclusión: la aplicacion llega al servidor, pero la clave real del usuario `postgres` no coincide con la configurada en `backend/.env`.

## Efectos secundarios detectados

- Ademas de la autenticacion fallida, los logs muestran errores de esquema:
  - falta la columna `solicitudes.fecha_incidente`
- Eso indica que, una vez recuperada la conexion, todavia hay que aplicar migraciones.

## Script de reparacion

Se agrego el script:

- `backend/tools/repair_postgres_local.ps1`

El script:

- respalda `pg_hba.conf`
- cambia temporalmente localhost a `trust`
- reinicia el servicio PostgreSQL 16
- restablece la clave de `postgres` a `postgres`
- restaura `scram-sha-256`
- reinicia el servicio otra vez
- prueba la conexion real con `asyncpg`
- ejecuta `alembic upgrade head`

## Como ejecutarlo

Abre PowerShell **como Administrador** y corre:

```powershell
cd "c:\Users\ALEXANDER\OneDrive\Escritorio\si2_1erexa\backend"
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\tools\repair_postgres_local.ps1
```

## Verificacion posterior

Si el script termina bien:

1. Reinicia el backend:

```powershell
cd "c:\Users\ALEXANDER\OneDrive\Escritorio\si2_1erexa\backend"
py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

2. Prueba la API:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health
```

3. Prueba login desde la web.

## Prevencion

- Mantener sincronizados `backend/.env`, pgAdmin y la password real del rol `postgres`.
- Ejecutar migraciones despues de cambios de esquema:
  - `py -m alembic upgrade head`
- Evitar asumir que la clave del rol `postgres` sigue siendo la de instalacion.
