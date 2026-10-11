param([switch]$Mock, [int]$Port = 8000)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend/dist/index.html')) { throw 'Run scripts/setup.ps1 to build the frontend first.' }
if ($Mock) { $env:SUGGESTION_PROVIDER = 'mock' }
else { $env:SUGGESTION_PROVIDER = 'generic_llm' }
$env:PYTHONPATH = Join-Path $projectRoot 'backend'
& $pythonPath -m alembic -c backend/alembic.ini upgrade head
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
Write-Host "PedagoLoop: http://127.0.0.1:$Port"
$worker = $null
$ocrWorker = $null
try {
    $ocrWorker = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'app.ocr_worker') `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    Write-Host "OCR worker PID: $($ocrWorker.Id)"
    if (-not $Mock) {
        $worker = Start-Process -FilePath $pythonPath -ArgumentList @('-m', 'app.worker') `
            -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
        Write-Host "Generation worker PID: $($worker.Id)"
    }
    & $pythonPath -m uvicorn app.api:app --app-dir backend --host 127.0.0.1 --port $Port
} finally {
    if ($worker -and -not $worker.HasExited) { Stop-Process -Id $worker.Id -Force }
    if ($ocrWorker -and -not $ocrWorker.HasExited) { Stop-Process -Id $ocrWorker.Id -Force }
}
