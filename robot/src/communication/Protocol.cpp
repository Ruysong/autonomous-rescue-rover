#include "communication/Protocol.hpp"

#include <cmath>
#include <nlohmann/json.hpp>
#include <string>
#include <utility>

namespace rover::protocol {
namespace {

using Json = nlohmann::json;

CommandParseResult invalid(std::string error) {
    return {{std::move(error)}};
}

bool isFiniteAndInRange(double value) {
    return std::isfinite(value) && value >= -1.0 && value <= 1.0;
}

}  // namespace

bool isValid(const CommandParseResult& result) {
    return std::holds_alternative<Command>(result.result);
}

const Command* commandFrom(const CommandParseResult& result) {
    return std::get_if<Command>(&result.result);
}

const std::string* errorFrom(const CommandParseResult& result) {
    return std::get_if<std::string>(&result.result);
}

CommandParseResult parseCommand(const std::string& payload) {
    try {
        const Json message = Json::parse(payload);

        if (!message.is_object()) {
            return invalid("Message must be a JSON object");
        }

        if (!message.contains("_version") || !message["_version"].is_string()) {
            return invalid("Missing protocol version");
        }

        if (message["_version"].get<std::string>() != kVersion) {
            return invalid("Unsupported protocol version");
        }

        if (!message.contains("type") || !message["type"].is_string()) {
            return invalid("Missing message type");
        }

        const std::string type = message["type"].get<std::string>();
        if (type == "stop") {
            return {{Command{StopCommand{}}}};
        }

        if (type != "drive") {
            return invalid("Unsupported command type");
        }

        if (!message.contains("throttle") || !message["throttle"].is_number() ||
            !message.contains("steer") || !message["steer"].is_number()) {
            return invalid("Drive command requires numeric throttle and steer");
        }

        const double throttle = message["throttle"].get<double>();
        const double steer = message["steer"].get<double>();

        if (!isFiniteAndInRange(throttle) || !isFiniteAndInRange(steer)) {
            return invalid("Drive values must be finite numbers in [-1.0, 1.0]");
        }

        return {{Command{DriveCommand{throttle, steer}}}};
    } catch (const Json::exception& error) {
        return invalid(std::string("Invalid JSON: ") + error.what());
    }
}

std::string serializeTelemetry(const Telemetry& telemetry) {
    const Json message = {
        {"_version", kVersion},
        {"type", "telemetry"},
        {"timestamp", telemetry.timestamp},
        {"motor_speed", telemetry.motorSpeed},
        {"mode", telemetry.mode},
        {"safety_state", telemetry.safetyState},
        {"last_command_age", telemetry.lastCommandAge},
    };

    return message.dump();
}

}  // namespace rover::protocol
