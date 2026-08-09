$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    Write-Host "Creating venv..."
    py -3.12 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

New-Item -ItemType Directory -Force -Path dist, build | Out-Null

.\.venv\Scripts\pyinstaller.exe `
    --noconfirm `
    --clean `
    --windowed `
    --name GRate `
    --onefile `
    --paths . `
    --hidden-import aubio `
    --hidden-import sounddevice `
    --hidden-import mido.backends.rtmidi `
    --collect-submodules grate `
    --exclude-module pyqtgraph.examples `
    --exclude-module PySide6.Qt3DAnimation `
    --exclude-module PySide6.Qt3DCore `
    --exclude-module PySide6.Qt3DExtras `
    --exclude-module PySide6.Qt3DInput `
    --exclude-module PySide6.Qt3DLogic `
    --exclude-module PySide6.Qt3DRender `
    --exclude-module PySide6.QtWebEngineCore `
    --exclude-module PySide6.QtWebEngineWidgets `
    --exclude-module PySide6.QtWebEngineQuick `
    grate\main.py

Write-Host "Built: dist\GRate.exe"
