#pragma once

#include "communication/Protocol.hpp"
#include "motor/MotorInterface.hpp"

#include <chrono>
#include <mutex>
#include <string>

namespace rover::state {

class RobotState {
public:
    explicit RobotState(MotorInterface& motor, std::string mode = "mock",
                        std::chrono::milliseconds commandTimeout = std::chrono::seconds(3));

    void commandConnected();
    void applyCommand(const protocol::Command& command);
    void commandDisconnected();
    void checkCommandTimeout();
    protocol::Telemetry snapshot() const;

private:
    MotorInterface& motor_;
    std::string mode_;
    std::chrono::milliseconds commandTimeout_;
    mutable std::mutex mutex_;
    double motorSpeed_ = 0.0;
    std::string safetyState_ = "disconnected";
    bool commandConnected_ = false;
    bool hasValidCommand_ = false;
    bool hardwareFault_ = false;
    std::chrono::steady_clock::time_point lastValidCommand_{};
};

}  // namespace rover::state
