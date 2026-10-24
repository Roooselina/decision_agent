# Build the Windows program and its installer. Run from the repository root in PowerShell:
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
# Requires Python 3.11 or newer on PATH. The installer step needs Inno Setup 6 (iscc.exe);
# without it the script still builds and tests the exe and the zip.
# Results: dist\DecisionAgent\DecisionAgent.exe, dist\DecisionAgent-windows.zip,
#          dist\DecisionAgent-Setup.exe
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }

Step "Python environment"
if (-not (Test-Path ".build-venv")) { python -m venv .build-venv }
. .\.build-venv\Scripts\Activate.ps1
python -m pip install --upgrade pip | Out-Null
python -m pip install -e ".[desktop,dev,build]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

if (-not $SkipTests) {
    Step "Lint and tests"
    ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff failed" }
    python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
}

Step "PyInstaller"
pyinstaller packaging\decision_agent.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "build failed" }
$exe = (Resolve-Path "dist\DecisionAgent\DecisionAgent.exe").Path

& "$PSScriptRoot\smoke_test.ps1" -Exe $exe
if ($LASTEXITCODE -ne 0) { throw "smoke test failed" }

Step "Zip"
Compress-Archive -Path "dist\DecisionAgent" -DestinationPath "dist\DecisionAgent-windows.zip" -Force

if (-not $SkipInstaller) {
    Step "Installer"
    $iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $path = if ($iscc) { $iscc.Source } else { $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1 }
    if (-not $path) {
        Write-Warning "Inno Setup 6 (ISCC.exe) not found. Install it from https://jrsoftware.org/isinfo.php and run again."
    } else {
        $version = python -c "import decision_agent; print(decision_agent.__version__)"
        & $path "/DAppVersion=$version" "packaging\installer.iss"
        if ($LASTEXITCODE -ne 0) { throw "installer build failed" }
        Write-Host "Installer: dist\DecisionAgent-Setup.exe"
    }
}
Write-Host "Done: dist\DecisionAgent-windows.zip" -ForegroundColor Green
