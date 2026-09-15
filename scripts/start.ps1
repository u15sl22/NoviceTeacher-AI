param([switch]$Mock, [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Run scripts/setup.ps1 to build the frontend first.' }
if ($Mock) { $env:SUGGESTION_PROVIDER = 'mock' }
else { $env:SUGGESTION_PROVIDER = 'generic_llm' }
& $pythonPath -m alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
Write-Host "PedagoLoop: http://127.0.0.1:$Port"
& $pythonPath -m uvicorn app.api:app --app-dir backend --host 127.0.0.1 --port $Port
