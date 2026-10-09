#include "state/RobotState.hpp"

#include <chrono>
#include <iostream>
#include <type_traits>
#include <variant>

namespace rover::state {
namespace {

constexpr auto kCommandTimeout = std::chrono::seconds(3);

}  // namespace

RobotState::RobotState(MotorInterface& motor) : motor_(motor) {}

void RobotState::commandConnected() {
    std::lock_guard<std::mutex> lock(mutex_);
    commandConnected_ = true;
    hasValidCommand_ = false;
    safetyState_ = "disconnected";
}

void RobotState::applyCommand(const protocol::Command& command) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::visit([this](const auto& value) {
        using T = std::decay_t<decltype(value)>;
        if constexpr (std::is_same_v<T, protocol::DriveCommand>) {
            motor_.setSpeed(value.throttle);
            motorSpeed_ = value.throttle;
        } else {
            motor_.stop();
            motorSpeed_ = 0.0;
        }
    }, command);

    lastValidCommand_ = std::chrono::steady_clock::now();
    hasValidCommand_ = true;
    safetyState_ = "normal";
}

void RobotState::commandDisconnected() {
    std::lock_guard<std::mutex> lock(mutex_);
    motor_.stop();
    motorSpeed_ = 0.0;
    commandConnected_ = false;
    hasValidCommand_ = false;
    safetyState_ = "disconnected";
    std::cout << "[Safety] Command connection lost; stopped\n";
}

void RobotState::checkCommandTimeout() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!commandConnected_ || !hasValidCommand_ || safetyState_ == "timeout") {
        return;
    }

    if (std::chrono::steady_clock::now() - lastValidCommand_ >= kCommandTimeout) {
        motor_.stop();
        motorSpeed_ = 0.0;
        safetyState_ = "timeout";
        std::cout << "[Safety] No valid command for 3 seconds; stopped\n";
    }
}

protocol::Telemetry RobotState::snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto now = std::chrono::system_clock::now();
    const double timestamp = std::chrono::duration<double>(now.time_since_epoch()).count();
    const double commandAge = hasValidCommand_
        ? std::chrono::duration<double>(std::chrono::steady_clock::now() - lastValidCommand_).count()
        : 0.0;

    return {timestamp, motorSpeed_, "mock", safetyState_, commandAge};
}

}  // namespace rover::state
