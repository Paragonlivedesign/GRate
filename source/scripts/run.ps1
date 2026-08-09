# Launch GRate without a console window (dev / packaged).
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $root 'dist\GRate.exe'
if (Test-Path $exe) {
    Start-Process $exe
    exit 0
}
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) {
    throw "Neither dist\GRate.exe nor .venv\Scripts\pythonw.exe found."
}
Start-Process $pythonw -ArgumentList '-m', 'grate.main' -WorkingDirectory $root
