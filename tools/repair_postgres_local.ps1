param(
    [int]$PostgresVersion = 16,
    [string]$DbUser = "postgres",
    [string]$DbPassword = "postgres",
    [string]$DbName = "emergency_db",
    [int]$Port = 5432
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Ensure-Admin {
    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentUser)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Ejecuta este script en PowerShell como Administrador."
    }
}

function Set-LocalTrustMode {
    param(
        [string]$HbaPath,
        [string]$Method
    )

    $content = Get-Content $HbaPath -Raw
    $content = $content -replace '(?m)^host\s+all\s+all\s+127\.0\.0\.1/32\s+\S+\s*$', "host    all             all             127.0.0.1/32            $Method"
    $content = $content -replace '(?m)^host\s+all\s+all\s+::1/128\s+\S+\s*$', "host    all             all             ::1/128                 $Method"
    Set-Content -Path $HbaPath -Value $content -Encoding ASCII
}

Ensure-Admin

$serviceName = "postgresql-x64-$PostgresVersion"
$baseDir = "C:\Program Files\PostgreSQL\$PostgresVersion"
$dataDir = Join-Path $baseDir "data"
$hbaPath = Join-Path $dataDir "pg_hba.conf"
$psqlPath = Join-Path $baseDir "bin\psql.exe"
$backupPath = Join-Path $env:TEMP "pg_hba.conf.$PostgresVersion.si2.bak"
$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path $hbaPath)) {
    throw "No se encontro $hbaPath"
}

if (-not (Test-Path $psqlPath)) {
    throw "No se encontro $psqlPath"
}

Write-Step "Respaldando pg_hba.conf"
Copy-Item $hbaPath $backupPath -Force

try {
    Write-Step "Habilitando trust temporal para localhost"
    Set-LocalTrustMode -HbaPath $hbaPath -Method "trust"

    Write-Step "Reiniciando servicio $serviceName"
    Restart-Service $serviceName -Force

    Write-Step "Restableciendo password de $DbUser"
    & $psqlPath -h 127.0.0.1 -p $Port -U $DbUser -d postgres -c "ALTER USER $DbUser WITH PASSWORD '$DbPassword';"

    Write-Step "Restaurando scram-sha-256 en localhost"
    Set-LocalTrustMode -HbaPath $hbaPath -Method "scram-sha-256"

    Write-Step "Reiniciando servicio $serviceName"
    Restart-Service $serviceName -Force

    Write-Step "Probando conexion con asyncpg"
    Push-Location $repoRoot
    try {
        $env:DB_SMOKE_DSN = "postgresql://${DbUser}:${DbPassword}@127.0.0.1:${Port}/${DbName}"
        py tools/db_smoke.py
    }
    finally {
        Remove-Item Env:DB_SMOKE_DSN -ErrorAction SilentlyContinue
        Pop-Location
    }

    Write-Step "Aplicando migraciones Alembic"
    Push-Location $repoRoot
    try {
        py -m alembic upgrade head
    }
    finally {
        Pop-Location
    }

    Write-Step "Proceso completado"
    Write-Host "Si db_smoke muestra QUERY OK, reinicia el backend y prueba login."
}
catch {
    Write-Warning $_
    if (Test-Path $backupPath) {
        Write-Step "Restaurando respaldo de pg_hba.conf"
        Copy-Item $backupPath $hbaPath -Force
        try {
            Restart-Service $serviceName -Force
        }
        catch {
            Write-Warning "No se pudo reiniciar el servicio automaticamente. Reinicialo manualmente."
        }
    }
    throw
}
