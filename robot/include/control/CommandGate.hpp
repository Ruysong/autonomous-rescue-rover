#pragma once

// Command Gate: the single place that decides what reaches the wheels.
//
// - Authority: control mode is manual (laptop gamepad) or autonomous (robot path tracker).
//   Motion from the other source is ignored.
// - Timeout: no fresh motion from the active source -> stop.
// - Stop: one-shot stop (next motion drives again). E-stop: latched until resume.
// - Steer-mode change (normal <-> spin): brake -> steer -> drive. Drive is held at zero while the
//   wheels slow down and the servos turn.
// - Link loss to the laptop: stop. (State estimation keeps running elsewhere.)
//
// All methods take `now` so the gate is deterministic in tests. Thread-safe.

#include "control/Kinematics.hpp"

#include <chrono>
#include <mutex>
#include <optional>
#include <string>

namespace rover::control {

using Clock = std::chrono::steady_clock;

enum class ControlMode { manual, autonomous };
enum class Source { manual, autonomous };

const char* toString(ControlMode mode);
bool controlModeFrom(const std::string& text, ControlMode& mode);
bool sourceFrom(const std::string& text, Source& source);

struct GateConfig {
    std::chrono::milliseconds commandTimeout{500};
    std::chrono::milliseconds brakeTime{350};     // Pico duty slew 2.0/s: 0.6 -> 0 in 0.3 s
    double steerRateDegPerSec = 180.0;            // Pico angle slew
    std::chrono::milliseconds steerMargin{200};
    std::chrono::milliseconds idleAfterStop{1000};  // stop sending C after this long without motion
};

struct GateStatus {
    ControlMode controlMode = ControlMode::manual;
    SteerMode steerMode = SteerMode::normal;
    std::string safetyState = "disconnected";  // normal, stopped, timeout, estop, disconnected,
                                               // switching, rejected
    bool estop = false;
    bool linkConnected = false;
    double lastCommandAge = 0.0;
    WheelCommand output;
    unsigned ignoredCommands = 0;
};

class CommandGate {
public:
    CommandGate(Geometry geometry, GateConfig config);

    void linkConnected(Clock::time_point now);
    void linkDisconnected(Clock::time_point now);

    // Returns false when the motion was ignored (wrong source, e-stop).
    bool applyMotion(Source source, const Motion& motion, Clock::time_point now);
    void setControlMode(ControlMode mode, Clock::time_point now);
    void stop(Clock::time_point now);
    void estop(Clock::time_point now);
    void resume(Clock::time_point now);
    void protection(bool hold, double speedLimit, Clock::time_point now);
    void localBlocked(bool blocked, Clock::time_point now);

    // Called by the output loop (20 Hz). nullopt = send nothing (Pico link timeout takes over).
    std::optional<WheelCommand> tick(Clock::time_point now);

    GateStatus status(Clock::time_point now) const;
    const Geometry& geometry() const { return geometry_; }

private:
    enum class Phase { idle, active, braking, steering };

    void haltLocked(Clock::time_point now, const char* reason);
    static Source sourceOf(ControlMode mode);

    Geometry geometry_;
    GateConfig config_;
    mutable std::mutex mutex_;

    ControlMode controlMode_ = ControlMode::manual;
    SteerMode steerMode_ = SteerMode::normal;
    Phase phase_ = Phase::idle;
    std::string safetyState_ = "disconnected";
    bool estop_ = false;
    bool linkConnected_ = false;
    bool hasMotion_ = false;
    bool guardEnabled_ = false, guardHold_ = false, guardLatched_ = false, localBlocked_ = false;
    double speedLimit_ = 0.5;
    Clock::time_point lastGuard_{};
    Motion motion_;
    std::optional<SteerMode> pendingMode_;
    Clock::time_point lastMotion_{};
    Clock::time_point phaseEnd_{};
    Clock::time_point stoppedAt_{};
    WheelCommand output_;
    unsigned ignored_ = 0;
};

}  // namespace rover::control
