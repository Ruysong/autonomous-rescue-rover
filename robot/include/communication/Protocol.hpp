#pragma once

// Laptop <-> robot protocol. v1.1 adds motion / set_mode / estop / resume and a richer
// telemetry message. v1.0 drive / stop are still accepted. See shared/protocol/PROTOCOL.md.

#include <array>
#include <optional>
#include <string>
#include <variant>

namespace rover::protocol {

inline constexpr char kVersion[] = "1.1";
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

using Command = std::variant<DriveCommand, StopCommand, MotionCommand, SetModeCommand, EstopCommand,
                             ResumeCommand>;

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
};

bool isValid(const CommandParseResult& result);
const Command* commandFrom(const CommandParseResult& result);
const std::string* errorFrom(const CommandParseResult& result);

CommandParseResult parseCommand(const std::string& payload);
std::string serializeTelemetry(const Telemetry& telemetry);

}  // namespace rover::protocol
