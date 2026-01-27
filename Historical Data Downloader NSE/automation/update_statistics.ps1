[CmdletBinding()]
param(
    [string]$PythonExe = "D:/Trading Strategies/.venv/Scripts/python.exe",
    [string]$DataRoot = "",
    [string]$StatsRoot = "",
    [switch]$Quiet
)

$projectRoot = (Split-Path -Parent $PSScriptRoot)
$scriptPath = Join-Path $projectRoot "tools/generate_statistics.py"

if (-not (Test-Path $PythonExe)) {
    throw "Python executable not found at '$PythonExe'. Update the -PythonExe parameter."
}

if (-not (Test-Path $scriptPath)) {
    throw "Statistics generator script not found at '$scriptPath'."
}

$argsList = @("`"$scriptPath`"")

if ($DataRoot) {
    $argsList += "--root"; $argsList += "`"$DataRoot`""
}

if ($StatsRoot) {
    $argsList += "--stats-root"; $argsList += "`"$StatsRoot`""
}

if ($Quiet) {
    $argsList += "--quiet"
}

Write-Host "Running statistics generator..." -ForegroundColor Cyan
& $PythonExe @argsList
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    throw "Statistics generation failed with exit code $exitCode."
}

Write-Host "Statistics refresh completed successfully." -ForegroundColor Green