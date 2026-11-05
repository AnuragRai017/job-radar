$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "backend\.venv\Scripts\python.exe"
$log = Join-Path $root "data\apply.log"

Set-Location (Join-Path $root "backend")
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $python)) {
    "Python venv missing at $python" | Out-File -Append -Encoding utf8 $log
    exit 1
}

& $python run_apply.py --live --limit 10 *>> $log
exit $LASTEXITCODE
