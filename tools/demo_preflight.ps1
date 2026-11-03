# Demo preflight: OK, WARN or FAIL per check; the last line says whether the demo can start.
#
#   powershell -ExecutionPolicy Bypass -File tools\demo_preflight.ps1            (1 model request)
#   powershell -ExecutionPolicy Bypass -File tools\demo_preflight.ps1 -NoModel   (no request)
param(
    [string]$Folder = "",
    [switch]$NoModel,
    [string]$InstallDir = ""
)
$ErrorActionPreference = "Continue"
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\DecisionAgent" }
$vpy = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { $vpy = Join-Path (Split-Path -Parent $PSScriptRoot) ".venv\Scripts\python.exe" }
$script = Join-Path $PSScriptRoot "demo_preflight.py"
$arguments = @($script)
if ($Folder) { $arguments += @("--folder", $Folder) }
if ($NoModel) { $arguments += "--no-model" }
# The script prints UTF-8; show it as UTF-8 so Korean text survives.
$saved = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONIOENCODING = "utf-8"
& $vpy @arguments
$code = $LASTEXITCODE
[Console]::OutputEncoding = $saved
exit $code
