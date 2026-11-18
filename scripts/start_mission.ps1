param(
    [string]$AIAssets = $env:ROVER_AI_ASSETS,
    [string]$Endpoint = 'http://127.0.0.1:8091',
    [string]$Source = '',
    [string]$Model = 'yolo26n.pt',
    [switch]$Gamepad
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$missionPython = if (Test-Path -LiteralPath '.venv/Scripts/python.exe') { '.venv/Scripts/python.exe' } else { 'python' }
$missionArgs = @('-m','laptop.navigation.run_explore','--llm','--ai-endpoint',$Endpoint)
if ($AIAssets) { $missionArgs += @('--ai-assets',$AIAssets) }
if ($Source) { $missionArgs += @('--person-source',$Source,'--model',$Model) }
if ($Gamepad) { $missionArgs += '--gamepad' }
& $missionPython @missionArgs
exit $LASTEXITCODE
