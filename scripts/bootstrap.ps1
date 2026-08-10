param(
    [switch]$SeedDevUser
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
$envExample = Join-Path $repoRoot ".env.example"
$composeFile = Join-Path $repoRoot "infra\compose\docker-compose.yml"
$backendRoot = Join-Path $repoRoot "backend"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    Copy-Item -LiteralPath $envExample -Destination $envFile
    Write-Host "Created local .env from .env.example."
}

$settings = @{}
foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
        continue
    }
    $parts = $trimmed.Split("=", 2)
    if ($parts.Count -ne 2 -or $parts[0] -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw "Invalid .env line: $line"
    }
    $settings[$parts[0]] = $parts[1]
    Set-Item -Path ("Env:" + $parts[0]) -Value $parts[1]
}

if (-not $settings.ContainsKey("DATABASE_URL")) {
    throw ".env must define DATABASE_URL."
}
if (-not $settings.ContainsKey("CASEFILE_TEST_DATABASE_URL")) {
    throw ".env must define CASEFILE_TEST_DATABASE_URL."
}
if (
    -not $settings.ContainsKey("CASEFILE_MASTER_KEY") -or
    [string]::IsNullOrWhiteSpace($settings["CASEFILE_MASTER_KEY"])
) {
    $generatedMasterKey = & $python -c (
        "from casefile.agent_runtime.credentials import generate_master_key; " +
        "print(generate_master_key())"
    )
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($generatedMasterKey)) {
        throw "Failed to generate CASEFILE_MASTER_KEY."
    }
    $settings["CASEFILE_MASTER_KEY"] = $generatedMasterKey.Trim()
    Set-Item -Path Env:CASEFILE_MASTER_KEY -Value $settings["CASEFILE_MASTER_KEY"]
    $masterKeyLine = "CASEFILE_MASTER_KEY=" + $settings["CASEFILE_MASTER_KEY"]
    $masterKeyReplaced = $false
    $updatedEnvLines = foreach ($line in Get-Content -LiteralPath $envFile -Encoding utf8) {
        if ($line -match '^CASEFILE_MASTER_KEY=') {
            if (-not $masterKeyReplaced) {
                $masterKeyLine
                $masterKeyReplaced = $true
            }
        } else {
            $line
        }
    }
    if (-not $masterKeyReplaced) {
        $updatedEnvLines += $masterKeyLine
    }
    Set-Content -LiteralPath $envFile -Encoding utf8 -Value $updatedEnvLines
    Write-Host "Generated CASEFILE_MASTER_KEY in the local .env file."
}

$null = Get-Command docker -ErrorAction Stop
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Engine is unavailable. Start Docker Desktop and run this script again."
}

Push-Location $repoRoot
try {
    & docker compose --env-file $envFile -f $composeFile up -d
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose failed to start the CaseFile PostgreSQL services."
    }

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
    $containerNames = @("casefile-postgres", "casefile-postgres-test")
    foreach ($containerName in $containerNames) {
        do {
            $health = (& docker inspect --format '{{.State.Health.Status}}' $containerName 2>$null)
            if ($health -eq "healthy") {
                break
            }
            if ([DateTimeOffset]::UtcNow -ge $deadline) {
                throw "Timed out waiting for $containerName to become healthy."
            }
            Start-Sleep -Seconds 1
        } while ($true)
    }

    & $python -c "import alembic, psycopg, sqlalchemy"
    if ($LASTEXITCODE -ne 0) {
        throw "Backend dependencies are missing. Install backend[dev] first."
    }

    & $python -m alembic -c backend/alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Alembic failed to migrate the development database."
    }

    @'
import os
from sqlalchemy import create_engine, text

expected_revision = "20260809224245"
expected_tables = {
    "agent_model_calls", "agent_step_runs",
    "users", "user_provider_settings", "projects", "casefiles", "drafts", "briefs",
    "brief_versions", "source_records", "brief_intakes", "brief_intake_questions",
    "brief_intake_candidates", "agent_threads", "agent_messages",
    "agent_patch_sets", "agent_patch_operations", "casefile_objects", "casefile_refs",
    "casefile_contract_refs", "draft_operations", "narrative_phases", "entities",
    "people", "locations", "events", "information_units", "evidence_items",
    "testimonies", "claims", "knowledge_states", "knowledge_state_entries",
    "hypotheses", "reasoning_paths", "reasoning_nodes", "reasoning_edges",
    "relationships", "resolution_specs", "resolution_slots", "casefile_constraints",
    "structure_locks", "draft_snapshots", "canon_versions", "audit_events",
    "task_runs", "task_attempts", "task_events",
}
engine = create_engine(os.environ["DATABASE_URL"])
with engine.connect() as connection:
    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    tables = set(connection.execute(text("""
        SELECT tablename
          FROM pg_tables
         WHERE schemaname = 'public'
           AND tablename <> 'alembic_version'
    """)).scalars())
if revision != expected_revision:
    raise SystemExit(f"Expected Alembic {expected_revision}, got {revision}")
if tables != expected_tables:
    missing = sorted(expected_tables - tables)
    unexpected = sorted(tables - expected_tables)
    raise SystemExit(f"Business table mismatch; missing={missing}, unexpected={unexpected}")
print(f"Database ready at revision {revision} with {len(tables)} business tables.")
'@ | & $python -
    if ($LASTEXITCODE -ne 0) {
        throw "Database verification failed."
    }

    if ($SeedDevUser) {
        @'
import os
from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"])
with engine.begin() as connection:
    user_id = connection.execute(text("""
        SELECT id FROM users WHERE status = 'active' ORDER BY id LIMIT 1
    """)).scalar_one_or_none()
    if user_id is None:
        user_id = connection.execute(text("""
            INSERT INTO users (display_name, status)
            VALUES ('Local Developer', 'active')
            RETURNING id
        """)).scalar_one()
print(f"Use request header: X-CaseFile-User-Id: {user_id}")
'@ | & $python -
        if ($LASTEXITCODE -ne 0) {
            throw "Development user seeding failed."
        }
    }

    Write-Host "CaseFile database bootstrap completed."
} finally {
    Pop-Location
}
