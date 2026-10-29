# Install or update Decision Agent for the current Windows user from this repository.
#
#   powershell -ExecutionPolicy Bypass -File .\tools\update_windows.ps1
#
# What it does, in order:
#   1. installs the current code from the repository into a fresh environment beside the
#      old copy (the program keeps running; a failed install leaves the old copy working)
#   2. stops the running program (only this program's processes)
#   3. removes the old installed copy and puts the new one in its place
#   4. writes install_info.json (version, commit, time) for the program's About card
#   5. creates desktop and Start menu shortcuts (pythonw, no console window)
#   6. runs selftest and prints the version and commit, then starts the program
#
# Kept as is: %APPDATA%\DecisionAgent (settings, results, mail index, logs), the Windows
# Credential Manager entries (API key, logins), and every file in the watched folders.
# Needs no administrator rights: it writes only under %LOCALAPPDATA%, %APPDATA% and the
# current user's registry. Running it twice in a row is safe.
#
# Options:
#   -Repo <path>        repository to install from (default: the parent of this script's folder)
#   -InstallDir <path>  install folder (default: %LOCALAPPDATA%\Programs\DecisionAgent)
#   -NoLaunch           do not start the program at the end
#   -NoStop             leave a running copy alone (for trying the script into another folder)
#   -NoShortcuts        do not touch shortcuts or the autostart entry (same purpose)

param(
    [string]$Repo = "",
    [string]$InstallDir = "",
    [switch]$NoLaunch,
    [switch]$NoStop,
    [switch]$NoShortcuts
)

# Native programs (py, git, pip) write notes to stderr; their exit codes are checked instead.
$ErrorActionPreference = "Continue"

function Step($message) { Write-Host "[update] $message" }

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

function Write-Utf8NoBom($path, $text) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}

# 1. Locations
if (-not $Repo) { $Repo = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path (Join-Path $Repo "pyproject.toml"))) { throw "pyproject.toml not found in $Repo" }
$Repo = (Resolve-Path $Repo).Path
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\DecisionAgent" }
if ($InstallDir.Length -gt 100) {
    throw "The install folder path is too long for Windows (some files would pass 260 characters). Use a shorter -InstallDir."
}
$venv = Join-Path $InstallDir "venv"
$staging = Join-Path $InstallDir "venv.new"
$vpy = Join-Path $venv "Scripts\python.exe"
$pyw = Join-Path $venv "Scripts\pythonw.exe"
$dataDir = Join-Path $env:APPDATA "DecisionAgent"
Step "repository: $Repo"
Step "install folder: $InstallDir"
Step "data folder (kept): $dataDir"

$commit = ""
$dirty = $false
if (Get-Command git -ErrorAction SilentlyContinue) {
    $commit = "$(& git -C $Repo rev-parse --short HEAD 2>$null)".Trim()
    $dirty = ((& git -C $Repo status --porcelain --untracked-files=no 2>$null | Measure-Object).Count -gt 0)
}

$python = Find-Python
if (-not $python) { throw "Python 3.11 or newer was not found. Install it from python.org and run again." }

# 2. Build the new copy beside the old one while the program keeps running, so a failed
#    install (network, disk) leaves the old copy working. It is a copy, not linked to the repo.
$hadAutostart = $null -ne (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "DecisionAgent" -ErrorAction SilentlyContinue)
New-Item -ItemType Directory -Force -Path $InstallDir -ErrorAction Stop | Out-Null
if (Test-Path $staging) { Remove-Item -Path $staging -Recurse -Force -ErrorAction Stop }
Step "creating the environment with $($python.Exe) $($python.Args -join ' ')"
& $python.Exe @($python.Args) -m venv $staging
Check "create environment"
$spy = Join-Path $staging "Scripts\python.exe"
& $spy -m pip install --quiet --upgrade pip
Check "upgrade pip"
Step "installing the program (this can take a few minutes)"
& $spy -m pip install --quiet "${Repo}[desktop]"
Check "pip install"
& $spy -c "import decision_agent.desktop.cli"
Check "import check"

# 3. Stop the running program: processes started from the install folder, or running the
#    decision_agent.desktop module. Other Python programs are left alone.
$running = @()
if (-not $NoStop) { $running = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='decision-agent.exe'" |
    Where-Object {
        $_.ProcessId -ne $PID -and (
            ($_.ExecutablePath -and $_.ExecutablePath -like "$InstallDir\*") -or
            ($_.CommandLine -and $_.CommandLine -match 'decision_agent\.desktop\s+(run|serve)|decision_agent\.desktop\s*$')
        )
    }) }
foreach ($process in $running) {
    Step "stopping process $($process.ProcessId)"
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
foreach ($process in $running) {
    Wait-Process -Id $process.ProcessId -Timeout 15 -ErrorAction SilentlyContinue
}
if ($NoStop) { Step "left any running copy alone (-NoStop)" }
elseif ($running.Count -eq 0) { Step "the program was not running" }

# 4. Replace the old copy with the new one (only the install folder's environment)
if (Test-Path $venv) {
    Step "removing the old installed copy"
    Remove-Item -Path $venv -Recurse -Force -ErrorAction Stop
}
Move-Item -Path $staging -Destination $venv -ErrorAction Stop
& $vpy -c "import decision_agent.desktop.cli"
Check "import check after the move"
$version = "$(& $vpy -m decision_agent.desktop --version)".Trim()

$info = [ordered]@{
    version = $version
    commit = $commit
    dirty = $dirty
    installed_at = (Get-Date -Format "yyyy-MM-ddTHH:mm:ss")
    install_dir = $InstallDir
    source = $Repo
}
Write-Utf8NoBom (Join-Path $InstallDir "install_info.json") ($info | ConvertTo-Json)

$icon = Join-Path $Repo "packaging\app.ico"
$installedIcon = Join-Path $InstallDir "app.ico"
if (Test-Path $icon) { Copy-Item -Path $icon -Destination $installedIcon -Force }

# 5. Shortcuts. Old ones pointing at any copy of this program are replaced.
#    The Korean name is written as unicode escapes so this file stays plain ASCII.
$name = [regex]::Unescape('\uacb0\uc815\u0020\ubcc0\uacbd\u0020\uc810\uac80')
$shell = New-Object -ComObject WScript.Shell
$linkFolders = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs")
)
if ($NoShortcuts) { $linkFolders = @(); Step "shortcuts and autostart left as they are (-NoShortcuts)" }
foreach ($folder in $linkFolders) {
    Get-ChildItem -Path $folder -Filter "*.lnk" -ErrorAction SilentlyContinue | ForEach-Object {
        $link = $shell.CreateShortcut($_.FullName)
        if ("$($link.TargetPath) $($link.Arguments)" -match 'decision_agent|DecisionAgent|decision-agent') {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
        }
    }
    $path = Join-Path $folder "$name.lnk"
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $pyw
    $link.Arguments = "-m decision_agent.desktop run"
    $link.WorkingDirectory = $InstallDir
    $link.Description = $name
    if (Test-Path $installedIcon) { $link.IconLocation = $installedIcon }
    $link.Save()
    Step "shortcut: $path"
}

# Autostart, when it was on, now starts the new copy.
if ($hadAutostart -and -not $NoShortcuts) {
    & $vpy -m decision_agent.desktop autostart on | Out-Null
    Step "autostart points to the new copy"
}

# 6. Check, report, start
Step "running selftest"
# selftest prints UTF-8; read it as UTF-8 so Korean text survives.
$consoleEncoding = [Console]::OutputEncoding
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$selftest = (& $vpy -m decision_agent.desktop selftest --no-model | Out-String)
$selftestOk = ($LASTEXITCODE -eq 0)
[Console]::OutputEncoding = $consoleEncoding
Write-Utf8NoBom (Join-Path $InstallDir "selftest.json") $selftest
if ($selftestOk) { Step "selftest passed" }
else { Step "selftest found a problem; see $(Join-Path $InstallDir 'selftest.json')" }

$commitText = $commit
if (-not $commitText) { $commitText = "unknown" }
if ($dirty) { $commitText = "$commitText (with uncommitted changes)" }
Step "version: $version"
Step "commit: $commitText"

if (-not $NoLaunch) {
    Start-Process -FilePath $pyw -ArgumentList "-m decision_agent.desktop run" -WorkingDirectory $InstallDir
    Step "started"
}
Step "done"
