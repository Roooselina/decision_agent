# Turn the demo profile on or off, or show it.
#
#   powershell -ExecutionPolicy Bypass -File tools\demo_profile.ps1 on
#   powershell -ExecutionPolicy Bypass -File tools\demo_profile.ps1 off
#   powershell -ExecutionPolicy Bypass -File tools\demo_profile.ps1 status
#
# "on" and "off" change settings.json, which is only safe while the program is stopped (the
# running program keeps its own copy and would write it back). So this script stops the
# program, switches the profile with the installed copy, and starts the program again.
# The preset values live in %APPDATA%\DecisionAgent\demo_profile.json (editable).
# "off" restores exactly the values that were there before "on".
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("on", "off", "status")]
    [string]$Action,
    [string]$InstallDir = "",
    [switch]$NoLaunch
)

$ErrorActionPreference = "Continue"
function Step($message) { Write-Host "[demo-profile] $message" }

if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\DecisionAgent" }
$vpy = Join-Path $InstallDir "venv\Scripts\python.exe"
$pyw = Join-Path $InstallDir "venv\Scripts\pythonw.exe"
if (-not (Test-Path $vpy)) {
    $repo = Split-Path -Parent $PSScriptRoot
    $vpy = Join-Path $repo ".venv\Scripts\python.exe"
    $pyw = Join-Path $repo ".venv\Scripts\pythonw.exe"
    Step "installed copy not found; using $vpy"
}

function Invoke-App([string[]]$arguments) {
    # The program prints UTF-8; read it as UTF-8 so Korean text survives.
    $saved = [Console]::OutputEncoding
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $out = (& $vpy -m decision_agent.desktop @arguments | Out-String)
    $code = $LASTEXITCODE
    [Console]::OutputEncoding = $saved
    Write-Host $out.TrimEnd()
    return $code
}

if ($Action -eq "status") {
    exit (Invoke-App @("demo-profile", "status"))
}

# Stop only this program's processes (same rule as update_windows.ps1).
$running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='decision-agent.exe'" |
    Where-Object {
        $_.ProcessId -ne $PID -and $_.CommandLine -and
        $_.CommandLine -match 'decision_agent\.desktop\s+(run|serve)|decision_agent\.desktop\s*$'
    })
foreach ($process in $running) {
    Step "stopping process $($process.ProcessId)"
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
foreach ($process in $running) {
    Wait-Process -Id $process.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
}

$code = Invoke-App @("demo-profile", $Action)

if ($running.Count -gt 0 -and -not $NoLaunch) {
    Start-Process -FilePath $pyw -ArgumentList "-m decision_agent.desktop run" -WorkingDirectory $InstallDir
    Step "started the program again"
}
elseif ($running.Count -eq 0) { Step "the program was not running; start it from the desktop shortcut" }
exit $code
