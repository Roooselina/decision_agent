# Reinstall Decision Agent from the repo working tree and create clickable shortcuts.
#
# Run in PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\install_latest.ps1
#
# Options:
#   -Repo <path>   repo folder (default: the folder of this script if it holds pyproject.toml,
#                  otherwise %USERPROFILE%\Downloads\decision_agent)
#   -KeepVenv      reuse the existing .venv instead of building a new one
#   -WipeData      also delete the app data folder (%APPDATA%\DecisionAgent) after you confirm
#   -Test          run the test suite after installing
#   -NoLaunch      do not start the program at the end
#
# Not touched: files in watched folders, the Windows Credential Manager (API key, logins).

param(
    [string]$Repo = "",
    [switch]$KeepVenv,
    [switch]$WipeData,
    [switch]$Test,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Continue"

function Step($message) { Write-Host "[install] $message" }

function Check($what) {
    if ($LASTEXITCODE -ne 0) { throw "Failed: $what (exit code $LASTEXITCODE)" }
}

function Find-Python {
    foreach ($candidate in @("py -3.12", "py -3.11", "py -3.13", "py -3", "python")) {
        $parts = $candidate.Split(" ")
        $exe = $parts[0]
        $rest = @()
        if ($parts.Length -gt 1) { $rest = $parts[1..($parts.Length - 1)] }
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $out = & $exe @rest -c "import sys; print(int(sys.version_info >= (3, 11)))" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq "1") { return @{ Exe = $exe; Args = $rest } }
    }
    return $null
}

# 1. Locate the repo
if (-not $Repo) {
    if (Test-Path (Join-Path $PSScriptRoot "pyproject.toml")) { $Repo = $PSScriptRoot }
    else { $Repo = Join-Path $env:USERPROFILE "Downloads\decision_agent" }
}
if (-not (Test-Path (Join-Path $Repo "pyproject.toml"))) { throw "pyproject.toml not found in $Repo" }
$Repo = (Resolve-Path $Repo).Path
Step "repo: $Repo"

if (Get-Command git -ErrorAction SilentlyContinue) {
    $branch = (& git -C $Repo rev-parse --abbrev-ref HEAD 2>$null)
    $last = (& git -C $Repo log -1 --oneline 2>$null)
    $dirty = (& git -C $Repo status --porcelain 2>$null | Measure-Object).Count
    Step "branch: $branch, last commit: $last, uncommitted files: $dirty"
}

# 2. Stop running copies of the program
$venv = Join-Path $Repo ".venv"
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='decision-agent.exe'" |
    Where-Object {
        $_.ProcessId -ne $PID -and (
            ($_.ExecutablePath -and $_.ExecutablePath -like "$venv\*") -or
            ($_.CommandLine -and $_.CommandLine -match 'decision_agent\.desktop')
        )
    }
foreach ($process in $running) {
    Step "stopping process $($process.ProcessId)"
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 3. Remove the old autostart entry and old shortcuts
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$hadAutostart = $null -ne (Get-ItemProperty -Path $runKey -Name "DecisionAgent" -ErrorAction SilentlyContinue)
if ($hadAutostart) {
    Remove-ItemProperty -Path $runKey -Name "DecisionAgent" -ErrorAction SilentlyContinue
    Step "removed old autostart entry (it will be set again after the install)"
}

$shell = New-Object -ComObject WScript.Shell
$linkFolders = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs")
)
foreach ($folder in $linkFolders) {
    Get-ChildItem -Path $folder -Filter "*.lnk" -ErrorAction SilentlyContinue | ForEach-Object {
        $link = $shell.CreateShortcut($_.FullName)
        $text = "$($link.TargetPath) $($link.Arguments) $($link.WorkingDirectory)"
        if ($text -match 'decision_agent|DecisionAgent|decision-agent') {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
            Step "removed old shortcut: $($_.Name)"
        }
    }
}

# 4. Optional: wipe app data (settings, state, mail index, logs)
$appData = Join-Path $env:APPDATA "DecisionAgent"
if ($WipeData -and (Test-Path $appData)) {
    $answer = Read-Host "Delete $appData (settings, results, mail index)? Type yes to confirm"
    if ($answer -eq "yes") {
        Remove-Item -Path $appData -Recurse -Force
        Step "deleted $appData"
    } else {
        Step "kept $appData"
    }
}

# 5. Build the environment and install
if ((Test-Path $venv) -and -not $KeepVenv) {
    Step "removing old .venv"
    Remove-Item -Path $venv -Recurse -Force
}
if (-not (Test-Path $venv)) {
    $python = Find-Python
    if (-not $python) { throw "Python 3.11 or newer was not found. Install it from python.org and run again." }
    Step "creating .venv with $($python.Exe) $($python.Args -join ' ')"
    & $python.Exe @($python.Args) -m venv $venv
    Check "create .venv"
}
$vpy = Join-Path $venv "Scripts\python.exe"
$pyw = Join-Path $venv "Scripts\pythonw.exe"

Step "upgrading pip"
& $vpy -m pip install --upgrade pip
Check "upgrade pip"

Step "installing the program (this can take a few minutes)"
Push-Location $Repo
& $vpy -m pip install -e ".[desktop]"
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw "Failed: pip install (exit code $code)" }

& $vpy -c "import webview" 2>$null
if ($LASTEXITCODE -ne 0) {
    Step "installing pywebview"
    & $vpy -m pip install pywebview
    Check "install pywebview"
}

& $vpy -c "import decision_agent.desktop.cli"
Check "import check"
Step "version: $(& $vpy -m decision_agent.desktop --version)"

if ($Test) {
    Step "running tests"
    Push-Location $Repo
    & $vpy -m pip install -e ".[dev]"
    & $vpy -m pytest -q
    Pop-Location
}

# 6. Shortcuts (Korean name written as unicode escapes so the file stays plain ASCII)
$name = [regex]::Unescape('\uacb0\uc815\u0020\ubcc0\uacbd\u0020\uc810\uac80')
$icon = Join-Path $Repo "packaging\app.ico"
$targets = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "$name.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$name.lnk")
)
foreach ($path in $targets) {
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pyw
    $link.Arguments = "-m decision_agent.desktop run"
    $link.WorkingDirectory = $Repo
    $link.Description = $name
    if (Test-Path $icon) { $link.IconLocation = $icon }
    $link.Save()
    Step "shortcut: $path"
}

if ($hadAutostart) {
    & $vpy -m decision_agent.desktop autostart on | Out-Null
    Step "autostart set again"
}

# 7. Start
if (-not $NoLaunch) {
    Step "starting the program"
    Start-Process -FilePath $pyw -ArgumentList "-m decision_agent.desktop run" -WorkingDirectory $Repo
}
Step "done. Use the shortcut named with the product name on the desktop or in the Start menu."
