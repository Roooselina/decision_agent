# Smoke test for the built exe. Uses a temporary app data folder and a copy of the demo
# documents, so it never touches the real settings, credentials, or user folders.
#   powershell -ExecutionPolicy Bypass -File packaging\smoke_test.ps1 -Exe dist\DecisionAgent\DecisionAgent.exe
param(
    [Parameter(Mandatory = $true)][string]$Exe,
    [int]$Port = 8799
)
$ErrorActionPreference = "Stop"
$Exe = (Resolve-Path $Exe).Path
$repo = Split-Path -Parent $PSScriptRoot
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("da-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $work | Out-Null
$project = Join-Path $work "project"
Copy-Item -Recurse (Join-Path $repo "data\demo") $project
$env:DECISION_AGENT_HOME = Join-Path $work "home"

function Hashes($folder) {
    Get-ChildItem -Recurse -File $folder | Sort-Object FullName | ForEach-Object { (Get-FileHash $_.FullName).Hash + " " + $_.Name }
}

try {
    Write-Host "smoke: --version"
    $p = Start-Process -FilePath $Exe -ArgumentList "--version" -Wait -PassThru -RedirectStandardOutput "$work\version.txt"
    if ($p.ExitCode -ne 0) { throw "--version exited with $($p.ExitCode)" }
    Write-Host ("  version " + (Get-Content "$work\version.txt" -Raw).Trim())

    Write-Host "smoke: scan"
    $before = Hashes $project
    $report = Join-Path $work "scan.json"
    $p = Start-Process -FilePath $Exe -ArgumentList @("scan", "--folder", "`"$project`"", "--report", "`"$report`"") -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "scan exited with $($p.ExitCode)" }
    $data = Get-Content $report -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($data.inconsistent -lt 1) { throw "scan found no inconsistencies in the demo documents" }
    $after = Hashes $project
    if (Compare-Object $before $after) { throw "the scanned folder changed" }
    Write-Host "  findings $($data.findings.Count), inconsistent $($data.inconsistent), folder unchanged"

    Write-Host "smoke: server and /api/ping"
    $server = Start-Process -FilePath $Exe -ArgumentList @("serve", "--port", "$Port") -PassThru
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $ping = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/ping" -TimeoutSec 2
            if ($ping.app -eq "decision-agent") { $ok = $true; break }
        } catch { }
    }
    try {
        $code = (Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/status" -ErrorAction Stop).StatusCode
    } catch { $code = $_.Exception.Response.StatusCode.value__ }
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    if (-not $ok) { throw "server did not answer /api/ping" }
    if ($code -ne 403) { throw "/api/status without the token returned $code instead of 403" }
    Write-Host "  ping ok (version $($ping.version)), API refuses requests without the token"

    Write-Host "smoke: bundle contents"
    $bundle = Split-Path -Parent $Exe
    $static = Get-ChildItem -Recurse -File $bundle -Include "app.js", "app.css", "index.html", "app.ico"
    if ($static.Count -lt 4) { throw "UI files are missing from the bundle" }
    $leaks = Get-ChildItem -Recurse -File $bundle -Include "settings.json", "*.sqlite", "ui_token", "meeting.md"
    if ($leaks) { throw "the bundle contains user data or demo files: $($leaks.Name -join ', ')" }
    Write-Host "smoke: all passed" -ForegroundColor Green
    exit 0
} catch {
    Write-Host "smoke: FAILED - $_" -ForegroundColor Red
    exit 1
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
