$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

if (-not (Test-Path .\dist\GRate.exe)) {
    Write-Host "GRate.exe missing - building exe first..."
    powershell -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1
}

$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup 6 not found. Installing via winget..."
    winget install --id JRSoftware.InnoSetup -e --accept-package-agreements --accept-source-agreements
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $iscc) {
    throw "ISCC.exe not found after install. Install Inno Setup 6 manually."
}

New-Item -ItemType Directory -Force -Path installer\output | Out-Null
& $iscc .\installer\grate.iss
Write-Host "Built: installer\output\GRate-Setup.exe"
