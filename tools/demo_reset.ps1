# Demo reset and warm-up. Safe to run before every rehearsal and right before the demo.
#
#   powershell -ExecutionPolicy Bypass -File tools\demo_reset.ps1
#   powershell -ExecutionPolicy Bypass -File tools\demo_reset.ps1 -MaxRequests 450
#   powershell -ExecutionPolicy Bypass -File tools\demo_reset.ps1 -ForgetMail   (after trashing the live mail)
#
# 1. stops the program (only this program's processes)
# 2. backs up the data folder and the demo folder to
#    %LOCALAPPDATA%\DecisionAgent_demo\backups\<timestamp> (nothing is deleted)
# 3. restores the demo folder from its pristine copy (only files that differ)
# 4. turns the demo profile on (tools\demo_profile.ps1 off restores the previous settings)
# 5. starts the program, runs the first check if needed (warm cache), and prints the requests
#    used, the stale lines against the answer key and the exact live edit to type
param(
    [string]$Variant = "demo_bookswap",
    [int]$MaxRequests = 450,
    [switch]$ForgetMail,
    [string]$InstallDir = ""
)
$ErrorActionPreference = "Continue"
function Step($message) { Write-Host "[demo-reset] $message" }

if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\DecisionAgent" }
$vpy = Join-Path $InstallDir "venv\Scripts\python.exe"
$pyw = Join-Path $InstallDir "venv\Scripts\pythonw.exe"
if (-not (Test-Path $vpy)) { throw "Installed program not found in $InstallDir. Run tools\update_windows.ps1 first." }
$script = Join-Path $PSScriptRoot "demo_reset.py"

$saved = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$env:PYTHONIOENCODING = "utf-8"
try {
    $running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='decision-agent.exe'" |
        Where-Object {
            $_.ProcessId -ne $PID -and $_.CommandLine -and
            $_.CommandLine -match 'decision_agent\.desktop\s+(run|serve)|decision_agent\.desktop\s*$'
        })
    foreach ($process in $running) {
        Step "stopping process $($process.ProcessId)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    foreach ($process in $running) { Wait-Process -Id $process.ProcessId -Timeout 15 -ErrorAction SilentlyContinue }

    & $vpy $script prepare --variant $Variant
    if ($LASTEXITCODE -ne 0) { Step "prepare failed; the program is left stopped"; exit 1 }

    & $vpy -m decision_agent.desktop demo-profile on

    Step "starting the program (about 1 minute)"
    Start-Process -FilePath $pyw -ArgumentList "-m decision_agent.desktop run" -WorkingDirectory $InstallDir
    $warmArgs = @("warm", "--variant", $Variant, "--max-requests", $MaxRequests)
    if ($ForgetMail) { $warmArgs += "--forget-mail" }
    & $vpy $script @warmArgs
    $code = $LASTEXITCODE
}
finally {
    [Console]::OutputEncoding = $saved
}
if ($code -eq 0) { Step "ready" } elseif ($code -eq 2) { Step "done, but the result differs from the answer key (see above)" } else { Step "not ready (see above)" }
exit $code
