# Live person-stop demo

The operator holds W/S to request forward/reverse motion. The laptop receives the Pi webcam stream, runs YOLO person detection, and blocks drive when it sees a person, loses the video, or loses Pi telemetry. Space stops immediately. Steering and autonomous navigation are outside this demo.

```text
Pi webcam --HTTP :8080--> Laptop GUI / YOLO --TCP :9998--> Pi C++ --GPIO--> left/right motor
                                   <--TCP :9997 telemetry--
```

The shared-device Tailscale connection used for development did not permit the Pi to open a direct TCP connection to the laptop. The laptop script uses SSH reverse forwarding for both TCP ports. The Pi connects to `127.0.0.1` through that tunnel. The camera stream is fetched separately from the Pi.

## Build on the Pi

The existing team `hw_test` files are left untouched. The motor adapter claims BCM PWM/DIR pins 5/6 (left) and 16/20 (right), matching the team's `teleop.py`. Stop any other motor program before starting this controller.

```bash
sudo apt install cmake g++ nlohmann-json3-dev liblgpio-dev
cmake -S robot -B build -DROVER_WITH_GPIO=ON
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

Without `-DROVER_WITH_GPIO=ON`, the build uses `MockMotor` only. GPIO remains disabled at runtime unless `--motor gpio --arm-gpio` is passed.

## Run

Start the Pi webcam stream if it is not already running:

```bash
cd ~/hw_test && python3 cam_stream.py
```

On Windows PowerShell, from the repository root, supply your own Pi address, Python environment, and locally stored YOLO model. The script starts the SSH tunnel and GUI, then closes its tunnel when the GUI exits. `-Real -PhysicalReady` is for a secured physical test with motor power cutoff available. Omit those two switches for display-only video detection.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_person_stop_demo.ps1 `
  -PiHost 'PI_ADDRESS' `
  -PythonPath 'C:\path\to\python.exe' `
  -ModelPath 'C:\path\to\yolo26n.pt' `
  -Real -PhysicalReady
```

After the GUI starts, run the Pi controller from the repository root:

```bash
./build/rover_controller --host 127.0.0.1 --motor gpio --arm-gpio --max-duty 15
```

The default GUI request is throttle `0.1`, equivalent to 10% PWM duty, with the Pi capped at 15%. The GUI and telemetry show **requested output**, not measured wheel speed. First test with the wheels raised: briefly press W, release it, then check Space, reverse, and person-triggered stop. If the right wheels rotate the opposite way, stop motor power and restart the Pi controller with `--invert-right`.

The Pi stops motor output on command disconnect or after 500 ms without a valid command. Person detection locks the GUI until the person leaves the frame and the operator presses R followed by **Start manual control**. A person detector and software timeout do not replace the physical power cutoff.

## Verification status

The GPIO-enabled C++ build and CTest passed on a Pi 4 without opening GPIO during the tests. The laptop manual safety tests passed. Physical direction, stopping distance, and camera latency require an on-site check; the repository does not claim those results.
