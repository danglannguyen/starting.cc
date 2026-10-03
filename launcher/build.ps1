# Builds ats.exe (the double-click launcher) in the project folder.
# Uses the C# compiler that comes with Windows, so nothing extra is installed.
# Run from the project folder:  .\launcher\build.ps1

$ErrorActionPreference = "Stop"

$compiler = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
$projectFolder = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot "ats_launcher.cs"
$output = Join-Path $projectFolder "ats.exe"

if (-not (Test-Path $compiler)) {
    throw "C# compiler not found at $compiler"
}

& $compiler /nologo /target:exe /optimize+ "/out:$output" $source
if ($LASTEXITCODE -ne 0) {
    throw "Building ats.exe failed (see the messages above)."
}
Write-Host "Built $output"
