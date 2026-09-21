#pragma once

#include <string>
#include <variant>

namespace rover::protocol {

inline constexpr char kVersion[] = "1.0";

struct DriveCommand {
    double throttle;
    double steer;
};

struct StopCommand {};

using Command = std::variant<DriveCommand, StopCommand>;

struct CommandParseResult {
    std::variant<Command, std::string> result;
};

struct Telemetry {
    double timestamp;
    double motorSpeed;
    std::string mode;
    std::string safetyState;
    double lastCommandAge;
};

bool isValid(const CommandParseResult& result);
const Command* commandFrom(const CommandParseResult& result);
const std::string* errorFrom(const CommandParseResult& result);

CommandParseResult parseCommand(const std::string& payload);
std::string serializeTelemetry(const Telemetry& telemetry);

}  // namespace rover::protocol
