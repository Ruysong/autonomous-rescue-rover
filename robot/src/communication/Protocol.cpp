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

CommandParseResult valid(Command command) {
    return {{std::move(command)}};
}

bool isFiniteAndInRange(double value) {
    return std::isfinite(value) && value >= -1.0 && value <= 1.0;
}

// Reads an optional finite number with a bound on |value|.
bool readNumber(const Json& message, const char* key, double bound, double& out,
                bool required) {
    if (!message.contains(key)) {
        return !required;
    }
    if (!message[key].is_number()) {
        return false;
    }
    const double value = message[key].get<double>();
    if (!std::isfinite(value) || std::fabs(value) > bound) {
        return false;
    }
    out = value;
    return true;
}

bool readString(const Json& message, const char* key, std::string& out) {
    if (!message.contains(key) || !message[key].is_string()) {
        return false;
    }
    out = message[key].get<std::string>();
    return true;
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

        const std::string version = message["_version"].get<std::string>();
        if (version != kVersion && version != kLegacyVersion) {
            return invalid("Unsupported protocol version");
        }

        if (!message.contains("type") || !message["type"].is_string()) {
            return invalid("Missing message type");
        }

        const std::string type = message["type"].get<std::string>();
        if (type == "stop") {
            return valid(StopCommand{});
        }

        if (type == "drive") {
            if (!message.contains("throttle") || !message["throttle"].is_number() ||
                !message.contains("steer") || !message["steer"].is_number()) {
                return invalid("Drive command requires numeric throttle and steer");
            }

            const double throttle = message["throttle"].get<double>();
            const double steer = message["steer"].get<double>();

            if (!isFiniteAndInRange(throttle) || !isFiniteAndInRange(steer)) {
                return invalid("Drive values must be finite numbers in [-1.0, 1.0]");
            }

            return valid(DriveCommand{throttle, steer});
        }

        if (version == kLegacyVersion) {
            return invalid("Unsupported command type");
        }

        if (type == "motion") {
            MotionCommand motion;
            if (!readString(message, "source", motion.source) ||
                (motion.source != "manual" && motion.source != "autonomous")) {
                return invalid("motion.source must be manual or autonomous");
            }
            if (!readString(message, "steer_mode", motion.steerMode) ||
                (motion.steerMode != "normal" && motion.steerMode != "spin")) {
                return invalid("motion.steer_mode must be normal or spin");
            }
            if (!readNumber(message, "v", 2.0, motion.v, true) ||
                !readNumber(message, "kappa", 20.0, motion.kappa, false) ||
                !readNumber(message, "wz", 5.0, motion.wz, false)) {
                return invalid("motion needs finite v (|v|<=2), kappa (|k|<=20), wz (|w|<=5)");
            }
            return valid(motion);
        }

        if (type == "set_mode") {
            SetModeCommand command;
            if (!readString(message, "mode", command.mode) ||
                (command.mode != "manual" && command.mode != "autonomous")) {
                return invalid("set_mode.mode must be manual or autonomous");
            }
            return valid(command);
        }

        if (type == "estop") {
            return valid(EstopCommand{});
        }
        if (type == "resume") {
            return valid(ResumeCommand{});
        }

        return invalid("Unsupported command type");
    } catch (const Json::exception& error) {
        return invalid(std::string("Invalid JSON: ") + error.what());
    }
}

std::string serializeTelemetry(const Telemetry& t) {
    const Json message = {
        {"_version", kVersion},
        {"type", "telemetry"},
        {"timestamp", t.timestamp},
        {"motor_speed", t.motorSpeed},
        {"mode", t.mode},
        {"safety_state", t.safetyState},
        {"last_command_age", t.lastCommandAge},
        {"control_mode", t.controlMode},
        {"steer_mode", t.steerMode},
        {"estop", t.estop},
        {"duty", {t.dutyLeft, t.dutyRight}},
        {"steer_deg", t.steerDeg},
        {"pico", {{"ok", t.picoOk}, {"flags", t.picoFlags}}},
        {"encoders", {t.encLeft, t.encRight}},
        {"ultrasonic_cm", t.ultrasonicCm},
        {"limits",
         {{"max_curvature", t.maxCurvature}, {"max_speed", t.maxSpeed},
          {"spin_enabled", t.spinEnabled}}},
    };

    return message.dump();
}

}  // namespace rover::protocol
