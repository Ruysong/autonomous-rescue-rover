#pragma once

// Laptop <-> robot protocol. v1.2 adds path / pose_correction and the scan message;
// v1.1 adds motion / set_mode / estop / resume and a richer
// telemetry message. v1.0 drive / stop are still accepted. See shared/protocol/PROTOCOL.md.

#include <array>
#include <optional>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace rover::protocol {

inline constexpr char kVersion[] = "1.2";
inline constexpr char kV11[] = "1.1";
inline constexpr char kLegacyVersion[] = "1.0";

// v1.0: normalized throttle / steer in [-1, 1].
struct DriveCommand {
    double throttle;
    double steer;
};

struct StopCommand {};

// v1.1
struct MotionCommand {
    std::string source;     // "manual" | "autonomous"
    std::string steerMode;  // "normal" | "spin"
    double v = 0.0;         // m/s
    double kappa = 0.0;     // 1/m
    double wz = 0.0;        // rad/s (spin)
};

struct SetModeCommand {
    std::string mode;  // "manual" | "autonomous"
};

struct EstopCommand {};
struct ResumeCommand {};

// v1.2 (autonomy): map-frame path for Pure Pursuit; empty points = cancel.
struct PathCommand {
    int id = 0;
    double speed = 0.0;  // m/s, 0 = robot default
    std::vector<std::pair<double, double>> points;
};

// v1.2: map <- odom transform from the laptop SLAM.
struct PoseCorrectionCommand {
    double x = 0.0;
    double y = 0.0;
    double yaw = 0.0;
};

using Command = std::variant<DriveCommand, StopCommand, MotionCommand, SetModeCommand, EstopCommand,
                             ResumeCommand, PathCommand, PoseCorrectionCommand>;

struct CommandParseResult {
    std::variant<Command, std::string> result;
};

struct Telemetry {
    double timestamp;
    double motorSpeed;        // mean |duty| sign kept, [-1, 1]
    std::string mode;         // link / build mode ("pico", "mock")
    std::string safetyState;
    double lastCommandAge;

    // v1.1 extension (all optional on the wire for v1.0 readers)
    std::string controlMode = "manual";
    std::string steerMode = "normal";
    bool estop = false;
    double dutyLeft = 0.0;
    double dutyRight = 0.0;
    std::array<double, 4> steerDeg{0.0, 0.0, 0.0, 0.0};
    bool picoOk = false;
    int picoFlags = 0;
    long encLeft = 0;
    long encRight = 0;
    std::array<int, 4> ultrasonicCm{-1, -1, -1, -1};
    double maxCurvature = 0.0;
    double maxSpeed = 0.0;
    bool spinEnabled = false;

    // v1.2 extension
    std::array<double, 3> odomPose{0.0, 0.0, 0.0};  // x, y, yaw
    std::array<double, 3> mapPose{0.0, 0.0, 0.0};
    double odomDistance = 0.0;
    bool imuOk = false;
    bool lidarOk = false;
    std::string followState = "idle";
    int pathId = -1;
    double pathRemaining = 0.0;
};

// v1.2: one LiDAR revolution in the robot frame + the odom pose at that moment.
struct ScanMessage {
    double timestamp = 0.0;
    unsigned sequence = 0;
    std::array<double, 3> odomPose{0.0, 0.0, 0.0};
    std::vector<int> rangesMm;  // 360 bins, 1 deg, CCW from +x; 0 = no return
};

bool isValid(const CommandParseResult& result);
const Command* commandFrom(const CommandParseResult& result);
const std::string* errorFrom(const CommandParseResult& result);

CommandParseResult parseCommand(const std::string& payload);
std::string serializeTelemetry(const Telemetry& telemetry);
std::string serializeScan(const ScanMessage& scan);

}  // namespace rover::protocol
