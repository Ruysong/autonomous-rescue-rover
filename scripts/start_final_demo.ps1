[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$LaptopOnly,
    [switch]$Live,
    [switch]$NoFoxgloveOpen,
    [ValidateRange(0, 600)] [int]$Seconds = 0,
    [string]$PythonPath = $env:ROVER_PYTHON,
    [string]$AIAssets = $env:ROVER_AI_ASSETS,
    [string]$FoxglovePath = $env:ROVER_FOXGLOVE,
    [string]$PersonSource = '',
    [string]$Rover = $(if ($env:ROVER_SSH) { $env:ROVER_SSH } else { 'rover@rover' }),
    [string]$RemoteDir = $(if ($env:ROVER_DIR) { $env:ROVER_DIR } else { '~/rover_demo' })
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repo

if (([int]$Check.IsPresent + [int]$LaptopOnly.IsPresent + [int]$Live.IsPresent) -gt 1) {
    throw 'Choose one of -Check, -LaptopOnly, and -Live.'
}
if (!$Check -and !$LaptopOnly -and !$Live) {
    $Live = (Read-Host 'RoverAid | Type LIVE with a clear course and a spotter at the rover. Enter checks this laptop') -ceq 'LIVE'
    $Check = !$Live
}
if ($Seconds -and !$LaptopOnly) { throw '-Seconds is only available with -LaptopOnly.' }

if (!$AIAssets) {
    $AIAssets = $repo
    $existing = Join-Path (Split-Path -Parent $repo) 'roveraid-exploration'
    if (!(Test-Path -LiteralPath (Join-Path $repo 'models\yolo26n-pose.pt')) -and (Test-Path -LiteralPath $existing)) {
        $AIAssets = $existing
    }
}
$AIAssets = (Resolve-Path -LiteralPath $AIAssets).Path
if (!$PythonPath) {
    $PythonPath = Join-Path $repo '.venv\Scripts\python.exe'
    if (!(Test-Path -LiteralPath $PythonPath)) {
        $PythonPath = Join-Path $AIAssets '.venv\Scripts\python.exe'
    }
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).Path
$model = Join-Path $AIAssets 'models\yolo26n-pose.pt'
foreach ($relative in @('models\yolo26n-pose.pt', 'models\Qwen3VL-2B-Instruct-Q4_K_M.gguf',
                        'models\mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf', 'runtime\llama\llama-server.exe')) {
    if (!(Test-Path -LiteralPath (Join-Path $AIAssets $relative) -PathType Leaf)) {
        throw "Missing demo asset $relative. Set ROVER_AI_ASSETS or pass -AIAssets."
    }
}
& $PythonPath -c 'import cv2, foxglove, lap, pygame, torch, ultralytics'
if ($LASTEXITCODE) { throw 'Install laptop/requirements.txt in the selected Python environment.' }
Write-Host 'Python demo dependencies OK'

if (!$FoxglovePath) {
    foreach ($relative in @('Programs\desktop\Foxglove.exe', 'Programs\Foxglove\Foxglove.exe',
                            'Programs\foxglove-studio\Foxglove Studio.exe')) {
        $candidate = Join-Path $env:LOCALAPPDATA $relative
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $FoxglovePath = $candidate
            break
        }
    }
}
if ($FoxglovePath) { $FoxglovePath = (Resolve-Path -LiteralPath $FoxglovePath).Path }
$foxgloveInstalled = $FoxglovePath -or (Test-Path Registry::HKEY_CLASSES_ROOT\foxglove)
Write-Host "Python: $PythonPath"
Write-Host "AI assets: $AIAssets"
if ($FoxglovePath) {
    Write-Host "Foxglove: $FoxglovePath"
} elseif (!$foxgloveInstalled) {
    Write-Host 'Open Foxglove manually at ws://127.0.0.1:8765. Import docs/foxglove/roveraid_layout.json once.'
}
if ($Check) { exit 0 }

function Test-LocalPort([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try { return $client.ConnectAsync('127.0.0.1', $Port).Wait(100) -and $client.Connected }
    catch { return $false }
    finally { $client.Dispose() }
}

function Resolve-RoverTarget([string]$Target) {
    if ($Target -ne 'rover@rover') { return $Target }
    try {
        if ([System.Net.Dns]::GetHostAddresses('rover').Length) { return $Target }
    } catch [System.Net.Sockets.SocketException] {}
    $tailscale = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($tailscale) {
        $statusJson = & $tailscale.Source status --json 2>$null
        if (!$LASTEXITCODE -and $statusJson) {
            $status = $statusJson | ConvertFrom-Json
            $roverPeers = @($status.Peer.PSObject.Properties.Value | Where-Object { $_.HostName -ieq 'rover' })
            if ($status.BackendState -eq 'Running' -and $roverPeers.Count -eq 1) {
                $ip = $roverPeers[0].TailscaleIPs | Where-Object {
                    $_ -match '^100\.\d{1,3}\.\d{1,3}\.\d{1,3}$'
                } | Select-Object -First 1
                if ($ip) { return "rover@$ip" }
            }
        }
    }
    throw 'Cannot resolve rover. Connect Tailscale or pass -Rover rover@<IP>.'
}

$ports = @(9998, 9997, 8765)
if ($Live -and !$PersonSource) { $ports += 8088 }
foreach ($port in $ports) {
    if (Test-LocalPort $port) { throw "Local port $port is occupied. Close the other demo process." }
}

$sshProc = $cameraProc = $openJob = $null
try {
    if ($Live) {
        $Rover = Resolve-RoverTarget $Rover
        if ($Rover -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]*$' -or
            $RemoteDir -notmatch '^(~/|/)?[A-Za-z0-9_][A-Za-z0-9_./-]*$') { throw 'Invalid rover host or directory.' }
        $cameraStart = ''
        $sharedSshArgs = '-o BatchMode=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=8 ' +
            '-o ServerAliveInterval=3 -o ServerAliveCountMax=2'
        $null = New-Item -ItemType Directory -Force -Path 'logs'
        $sshLog = Join-Path $repo ('logs\pi_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
        if (!$PersonSource) {
            $PersonSource = 'http://127.0.0.1:8088/stream'
            # Video uses a separate SSH transport so an image burst does not queue
            # in front of command/telemetry traffic on the same TCP connection.
            $cameraSsh = @{ ArgumentList="-NT $sharedSshArgs -L 127.0.0.1:8088:127.0.0.1:8080 $Rover";
                PassThru=$true; WindowStyle='Hidden'; RedirectStandardOutput="$sshLog.camera.txt";
                RedirectStandardError="$sshLog.camera.err.txt" }
            $cameraProc = Start-Process ssh.exe @cameraSsh
            # Reuse the team camera. Start it only when there is no listener.
            $cameraStart = 'if ! ss -ltn sport = :8080 | grep -q LISTEN; then test -f ~/hw_test/cam_stream.py || exit 3; cd ~/hw_test && nohup python3 cam_stream.py /dev/video0 640x480 8080 12 > cam_stream.log 2>&1 < /dev/null & fi; '
        }
        $remote = "cd $RemoteDir && test -x ./build/rover_controller || exit 2; " + $cameraStart +
            "cd $RemoteDir && exec flock -n .demo.lock ./build/rover_controller --host 127.0.0.1 --max-duty 0.20"
        $sshArgs = "-tt $sharedSshArgs -R 9998:127.0.0.1:9998 " +
            "-R 9997:127.0.0.1:9997 $Rover " + '"' + $remote + '"'
        $sshStart = @{ ArgumentList=$sshArgs; PassThru=$true; WindowStyle='Hidden';
            RedirectStandardOutput="$sshLog.txt"; RedirectStandardError="$sshLog.err.txt" }
        $sshProc = Start-Process ssh.exe @sshStart
        Write-Host "Pi: $Rover"
        Write-Host "Pi output: $sshLog.txt"
    } elseif (!$PersonSource) {
        $PersonSource = '0'
    }

    if ($foxgloveInstalled -and !$NoFoxgloveOpen) {
        # Dispatch after the WebSocket listener has started.
        $openJob = Start-Job -ArgumentList @($FoxglovePath) -ScriptBlock {
            param([string]$Executable)
            $uri = 'foxglove://open?ds=foxglove-websocket&ds.url=ws://127.0.0.1:8765/'
            for ($i = 0; $i -lt 50; $i++) {
                $c = New-Object System.Net.Sockets.TcpClient
                try {
                    if ($c.ConnectAsync('127.0.0.1', 8765).Wait(100) -and $c.Connected) {
                        if ($Executable) { Start-Process -FilePath $Executable -ArgumentList $uri }
                        else { Start-Process $uri }
                        return
                    }
                } catch {} finally { $c.Dispose() }
                Start-Sleep -Milliseconds 200
            }
        }
    }
    $arguments = @('-m','laptop.navigation.run_explore','--bind','127.0.0.1','--foxglove',
                   '--person-source',$PersonSource,'--model',$model,'--confidence','.35',
                   '--llm','--ai-assets',$AIAssets,'--speed','.08')
    if ($Live) { $arguments += '--gamepad' } else { $arguments += '--mock-display' }
    if ($Seconds) { $arguments += @('--duration', "$Seconds") }
    & $PythonPath @arguments
    if ($LASTEXITCODE) { throw "Laptop runner exited with code $LASTEXITCODE." }
} finally {
    if ($openJob) { Remove-Job -Job $openJob -Force }
    if ($sshProc -and !$sshProc.HasExited) {
        # Python has sent Stop and closed the control channels.
        Stop-Process -Id $sshProc.Id -ErrorAction SilentlyContinue
    }
    if ($cameraProc -and !$cameraProc.HasExited) {
        Stop-Process -Id $cameraProc.Id -ErrorAction SilentlyContinue
    }
}
