#include "state/RobotState.hpp"

#include <chrono>
#include <exception>
#include <iostream>
#include <type_traits>
#include <utility>
#include <variant>

namespace rover::state {
RobotState::RobotState(MotorInterface& motor, std::string mode,
                       std::chrono::milliseconds commandTimeout)
    : motor_(motor), mode_(std::move(mode)), commandTimeout_(commandTimeout) {}

void RobotState::commandConnected() {
    std::lock_guard<std::mutex> lock(mutex_);
    commandConnected_ = true;
    hasValidCommand_ = false;
    safetyState_ = hardwareFault_ ? "hardware_fault" : "disconnected";
}

void RobotState::applyCommand(const protocol::Command& command) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (hardwareFault_) {
        motor_.stop();
        return;
    }
    try {
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
    } catch (const std::exception& error) {
        motor_.stop();
        motorSpeed_ = 0.0;
        hardwareFault_ = true;
        safetyState_ = "hardware_fault";
        std::cerr << "[Safety] Motor error; stopped and latched: " << error.what() << '\n';
        return;
    }

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
    safetyState_ = hardwareFault_ ? "hardware_fault" : "disconnected";
    std::cout << "[Safety] Command connection lost; stopped\n";
}

void RobotState::checkCommandTimeout() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!commandConnected_ || !hasValidCommand_ || hardwareFault_ || safetyState_ == "timeout") {
        return;
    }

    if (std::chrono::steady_clock::now() - lastValidCommand_ >= commandTimeout_) {
        motor_.stop();
        motorSpeed_ = 0.0;
        safetyState_ = "timeout";
        std::cout << "[Safety] No valid command for " << commandTimeout_.count()
                  << " ms; stopped\n";
    }
}

protocol::Telemetry RobotState::snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto now = std::chrono::system_clock::now();
    const double timestamp = std::chrono::duration<double>(now.time_since_epoch()).count();
    const double commandAge = hasValidCommand_
        ? std::chrono::duration<double>(std::chrono::steady_clock::now() - lastValidCommand_).count()
        : 0.0;

    return {timestamp, motorSpeed_, mode_, safetyState_, commandAge};
}

}  // namespace rover::state
