param(
    [switch]$Real,
    [switch]$PhysicalReady,
    [switch]$Check,
    [string]$PiHost = $env:ROVER_PI_HOST,
    [string]$PythonPath = $env:ROVER_DEMO_PYTHON,
    [string]$ModelPath = $env:ROVER_DEMO_MODEL
)

$ErrorActionPreference = 'Stop'

if ($Real -and -not $PhysicalReady) {
    throw 'Real mode requires -PhysicalReady after the rover is secured and motor power can be cut.'
}
if ($PhysicalReady -and -not $Real) {
    throw '-PhysicalReady is only used with -Real.'
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($PiHost)) {
    throw 'Set -PiHost or ROVER_PI_HOST to the Pi address.'
}
if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $repoRoot '.venv\Scripts\python.exe'
}
if ([string]::IsNullOrWhiteSpace($ModelPath)) {
    $ModelPath = Join-Path $repoRoot 'yolo26n.pt'
}
$cameraUrl = "http://${PiHost}:8080/stream"

foreach ($path in @($PythonPath, $ModelPath)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing demo dependency: $path"
    }
}

if ($Check) {
    Write-Host 'Demo Python and model found.'
    exit 0
}

$arguments = @('-m', 'laptop.demo.live_person_stop', '--source', $cameraUrl, '--model', $ModelPath)
$tunnel = $null

try {
    if ($Real) {
        $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
        $tunnel = Start-Process -FilePath $ssh -WindowStyle Hidden -PassThru -ArgumentList @(
            '-N', '-o', 'BatchMode=yes', '-o', 'ExitOnForwardFailure=yes',
            '-o', 'ConnectTimeout=6', '-o', 'ServerAliveInterval=10',
            '-o', 'ServerAliveCountMax=3',
            '-R', '9998:127.0.0.1:9998', '-R', '9997:127.0.0.1:9997',
            "rover@$PiHost"
        )
        Start-Sleep -Seconds 2
        $tunnel.Refresh()
        if ($tunnel.HasExited) {
            throw 'SSH tunnel did not start. Check that Pi and Tailscale are on.'
        }
        $arguments += @('--pi-real', '--confirm-physical-stop-ready', '--bind', '127.0.0.1')
        Write-Host "SSH tunnel is running (PID $($tunnel.Id)). Start the Pi runtime with --host 127.0.0.1."
    } else {
        Write-Host 'Display-only mode. No motor command will be sent.'
    }

    Push-Location -LiteralPath $repoRoot
    try {
        & $PythonPath @arguments
    } finally {
        Pop-Location
    }
} finally {
    if ($null -ne $tunnel) {
        $tunnel.Refresh()
        if (-not $tunnel.HasExited) {
            Stop-Process -Id $tunnel.Id
        }
    }
}
