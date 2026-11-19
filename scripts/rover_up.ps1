<#
Start both sides with one command: the laptop runner (gamepad or exploration) and
rover_controller on the Pi over SSH. An SSH reverse tunnel carries TCP 9998 / 9997,
so the laptop firewall does not need to be opened. Requires an SSH key on the rover.

Ctrl+C (or closing the SSH side) stops rover_controller; the laptop runner window is
closed when the script ends.

Examples (from the repository root):
  scripts\rover_up.bat                                   # map window + gamepad, real Pico.
                                                         # Manual until pad Start / Space (M returns)
  scripts\rover_up.bat -SteerOnly                        # dry run: steering only, wheel duty 0
  scripts\rover_up.bat -MaxDuty 0.2                      # cap wheel duty
  scripts\rover_up.bat -Pico mock                        # Pi without Pico (no motor output)
  scripts\rover_up.bat -Pico mock -NoGamepad -Autostart  # exploration in the mock world
  scripts\rover_up.bat -PersonSource http://rover:8080/stream   # stop when a person is seen
  scripts\rover_up.bat -PersonSource http://rover:8080/stream -Foxglove   # + Foxglove live view
  scripts\rover_up.bat -Mode teleop                      # fallback: gamepad only, no map window
  scripts\rover_up.bat -Print                            # only print the two commands
#>
param(
    [ValidateSet('explore', 'teleop')] [string]$Mode = 'explore',     # teleop = run_gamepad only (no map window)
    [ValidateSet('real', 'mock')] [string]$Pico = 'real',
    [ValidateRange(-1.0, 0.6)] [double]$MaxDuty = -1.0,   # < 0: use robot/config/rover.json
    [switch]$SteerOnly,                                  # same as -MaxDuty 0
    [ValidateSet('', 'off', 'mock')] [string]$Lidar = '',
    [switch]$ImuOff,
    [switch]$Autostart,                                  # explore: start without the Start button
    [switch]$NoGamepad,                                  # explore: no gamepad
    [string]$PersonSource = '',                          # explore: camera stream for person stop
    [switch]$Foxglove,                                   # explore: also publish to the Foxglove app (ws://127.0.0.1:8765)
    [string[]]$LaptopArgs = @(),                         # extra arguments for the laptop runner
    [string]$Rover = $(if ($env:ROVER_SSH) { $env:ROVER_SSH } else { 'rover@rover' }),
    [string]$RemoteDir = $(if ($env:ROVER_DIR) { $env:ROVER_DIR } else { '~/rover_demo' }),   # Pi build of this branch
    [switch]$Print
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

# The remote command is passed to ssh as one string. Keep it free of quotes and shell
# metacharacters (PowerShell mangles embedded quotes for native programs).
if ($Rover -notmatch '^[A-Za-z0-9_.@-]+$') { throw "Unexpected characters in -Rover: $Rover" }
if ($RemoteDir -notmatch '^[~A-Za-z0-9_./-]+$') { throw "Unexpected characters in -RemoteDir: $RemoteDir" }

$python = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }

# Laptop side. Bind to localhost only: the rover reaches it through the tunnel.
if ($Mode -eq 'teleop') {
    $laptop = @('-m', 'laptop.teleop.run_gamepad', '--bind', '127.0.0.1')
} else {
    $laptop = @('-m', 'laptop.navigation.run_explore', '--bind', '127.0.0.1')
    if (-not $NoGamepad) { $laptop += '--gamepad' }
    if ($Autostart) { $laptop += '--autostart' }
    if ($PersonSource) { $laptop += @('--person-source', $PersonSource) }
    if ($Foxglove) { $laptop += '--foxglove' }
}
$laptop += $LaptopArgs

# Rover side.
$inv = [System.Globalization.CultureInfo]::InvariantCulture
$remote = "cd $RemoteDir && ./build/rover_controller --host 127.0.0.1"
if ($Pico -eq 'mock') { $remote += ' --pico mock' }
if ($SteerOnly) { $MaxDuty = 0.0 }
if ($MaxDuty -ge 0) { $remote += ' --max-duty ' + $MaxDuty.ToString('0.###', $inv) }
if ($Lidar) { $remote += " --lidar $Lidar" }
if ($ImuOff) { $remote += ' --imu off' }

$sshArgs = @('-t', '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=8',
    '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3',
    '-R', '9998:127.0.0.1:9998', '-R', '9997:127.0.0.1:9997', $Rover, $remote)

Write-Host "laptop: $python $($laptop -join ' ')"
Write-Host "rover : ssh $Rover `"$remote`""
if ($Print) { exit 0 }

if ($Pico -eq 'real' -and -not $SteerOnly -and $MaxDuty -ne 0) {
    Write-Host 'Real Pico with wheel duty: battery only with someone next to the rover.' -ForegroundColor Yellow
}

$laptopProc = $null
try {
    # Laptop first so it is listening when the rover connects. Separate window keeps
    # its status line readable.
    $laptopProc = Start-Process -FilePath $python -ArgumentList $laptop -PassThru
    Start-Sleep -Seconds 2
    $laptopProc.Refresh()
    if ($laptopProc.HasExited) { throw "Laptop runner exited (code $($laptopProc.ExitCode)). Run it alone to see the error." }

    & ssh @sshArgs
    $code = $LASTEXITCODE
    Write-Host "rover_controller ended (ssh exit $code)."
} finally {
    if ($laptopProc -and -not $laptopProc.HasExited) {
        Stop-Process -Id $laptopProc.Id -ErrorAction SilentlyContinue
        Write-Host 'Laptop runner closed.'
    }
}
