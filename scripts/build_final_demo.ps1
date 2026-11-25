$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path -LiteralPath $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (!(Test-Path -LiteralPath $compiler)) { throw 'Windows .NET Framework C# compiler not found.' }
$output = Join-Path $repo 'RoverAid Demo.exe'
& $compiler /nologo /target:exe "/out:$output" (Join-Path $PSScriptRoot 'RoverAidDemo.cs')
if ($LASTEXITCODE) { throw 'Launcher compilation failed.' }
Write-Host "Built $output"
