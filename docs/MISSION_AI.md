# Mission AI integration

Development base: `yw/autonomy` at `6b90a15`. One runtime: `laptop.navigation.run_explore`.
SLAM, frontier generation and robot path tracking are retained. No separate AI GUI is needed.

## Setup

Python 3.11+ on the laptop:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r laptop/requirements.txt
```

Use opencv-python with window support, not opencv-python-headless. Install llama.cpp and
Qwen3-VL-2B-Instruct Q4_K_M GGUF separately; large models, videos and runtime binaries are
not committed. Download from the official projects:
- https://github.com/ggml-org/llama.cpp/releases
- https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF

Any healthy OpenAI-compatible local server can be used with `--ai-endpoint` (default
127.0.0.1:8091). Configure its model alias as `local-qwen3-vl-2b` and enable JSON-schema outputs.
No cloud key or personal login is used. No fine-tuning is required.

Optional Windows auto-start: `--ai-assets ASSET_ROOT` reuses the existing asset layout:

```text
ASSET_ROOT/
  models/Qwen3VL-2B-Instruct-Q4_K_M.gguf
  models/mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf
  runtime/llama/llama-server.exe
  runtime/llama-vulkan/llama-server.exe  (optional NVIDIA Vulkan backend)
```

Only a server started by this program is terminated on exit. An existing server is reused.
YOLO weights (default `yolo26n.pt`) are separate; pass `--model PATH` to reuse a local weight file.

## Run

Laptop first (no automatic driving until Resume / Space or gamepad Start):

```powershell
python -m laptop.navigation.run_explore --llm --ai-assets ASSET_ROOT --person-source http://PI:8080/stream
```

Or `scripts/start_mission.ps1 -AIAssets ASSET_ROOT -Source http://PI:8080/stream`.
`--gamepad` is optional. `--mission "Find people and report observations"` changes the task.
Without `--llm` the original rule-based selector remains available as a baseline.
Without `--person-source` camera protection is explicitly disabled; local LiDAR still applies.

Rebuild the Pi runtime from this SAME branch, then:

```bash
cmake -S robot -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
./build/rover_controller --host LAPTOP_IP --pico mock
```

Mock mode has no physical motor output. Real Pico operation requires on-site hardware
calibration and the existing TELEOP.md procedure. The integrated laptop refuses new motion
when telemetry does not advertise `protection_supported`; do not mix an old Pi binary with it.
Never run a second process against the Pico serial port.

## Decision flow

```text
scan + odometry -> ScanSlam -> Planner frontiers
                                |
recent image observations + mission + failure/choice history
                                |
                   AISelector (one worker, no backlog)
                                |
                    Qwen candidate ID + reason category
                                |
       TTL / mission generation / pose / current-location validation
                                |
          current map path -> Pi Pure Pursuit -> CommandGate -> Pico
```

The selector returns immediately while a request is running. No nearest-goal fallback is
silently applied in LLM mode: errors, null/invalid IDs and expired results hold pending selection.
Snapshot IDs are mapped to coordinates and matched to a currently reachable frontier within
0.25 m; current renumbered IDs are never confused with old IDs. Results expire after 20 s or
if the rover moved more than 0.3 m / 0.35 rad. A selected goal is kept during normal following;
map replans do not call the LLM again unless the goal disappears, is reached or is blocked.

Candidate `weighted_cost` includes obstacle-clearance penalties; it is not measured metres.
Boundary cells are not room area. Image-side observations are uncalibrated visual hints, not
world positions or person ranges. No identity or injury diagnosis is performed.
Reason categories are schema-constrained. A person-based category is available only when
recent person observations exist. A selected valid ID is not proof of semantic optimality.

## New protection policy

Person detection alone produces observations, not a stop. Thresholds are in
`laptop/config/mission.json` and are INITIAL TEST VALUES, not calibrated stopping distances.
- Small / side detections: observe and record.
- Significant overlap with a central lower image ROI: caution, cap commanded speed to 0.05 m/s.
- Large sustained ROI occupancy over distinct frame times: latched protection hold.
- Video older than 1 s / unavailable when enabled: latched hold.
- Resume requires fresh clear video for 1 s and an explicit click. A model response cannot resume.
- Forward image zones are not used as a rear camera. Reverse protection relies on local LiDAR.

The laptop repeats a `protection` heartbeat every 150 ms. The Pi enforces its speed cap for
manual AND autonomous commands, and latches a hold after 500 ms without protection messages
once supervision is enabled. A hold rejects new motion until fresh clear protection + Resume.
Command-link disconnect rejects motion and clears the path. Common LiDAR guards now apply in
both modes, including stale scans. GUI PAUSE cancels exploration; RESUME explicitly re-arms;
E-STOP latches emergency stop. Gamepad E-stop remains available.

LiDAR scans at about 73 cm height miss low objects. Image zones are heuristic, can miss or
false-trigger, and do not replace physical supervision. There is no measured stop-distance or
real-rover safety validation in this change. Existing Pico limits remain unchanged.

## GUI / records

One bounded 1180x760 English dashboard: map/frontier IDs/path, annotated video/protection ROI,
AI state/reason, robot/follower state; PAUSE / RESUME / E-STOP buttons.
Keyboard Space = pause/resume, E = E-stop, M = manual, S = save map, Q = exit.
Headless mode has no clickable resume; use the GUI for camera-protection demonstrations.
JSONL logs include observations, AI requests/selections/rejections/errors and protection holds
alongside the existing scans/telemetry/commands. Model output is not ground truth.

## Verification

```powershell
python -m pytest tests -q
python scripts/check_mission_ai.py --assets ASSET_ROOT
python scripts/check_explorer_ai.py --assets ASSET_ROOT
```

The two model checks use SYNTHETIC inputs and never open robot ports. The latter uses the
actual SLAM, Planner, AISelector and Explorer to produce a validated path.

```bash
ctest --test-dir build --output-on-failure
```

Physical close-person, stopping-distance and full live-camera driving tests remain on-site work.
