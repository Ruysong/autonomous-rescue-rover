#include "control/CommandGate.hpp"

#include <algorithm>
#include <cmath>
#include <utility>

namespace rover::control {

const char* toString(ControlMode mode) {
    return mode == ControlMode::autonomous ? "autonomous" : "manual";
}

bool controlModeFrom(const std::string& text, ControlMode& mode) {
    if (text == "manual") {
        mode = ControlMode::manual;
        return true;
    }
    if (text == "autonomous") {
        mode = ControlMode::autonomous;
        return true;
    }
    return false;
}

bool sourceFrom(const std::string& text, Source& source) {
    if (text == "manual") {
        source = Source::manual;
        return true;
    }
    if (text == "autonomous") {
        source = Source::autonomous;
        return true;
    }
    return false;
}

CommandGate::CommandGate(Geometry geometry, GateConfig config)
    : geometry_(std::move(geometry)), config_(config) {}

Source CommandGate::sourceOf(ControlMode mode) {
    return mode == ControlMode::autonomous ? Source::autonomous : Source::manual;
}

void CommandGate::haltLocked(Clock::time_point now, const char* reason) {
    if (phase_ != Phase::idle) {
        stoppedAt_ = now;
    }
    output_ = holdAngles(output_);
    hasMotion_ = false;
    pendingMode_.reset();
    phase_ = phase_ == Phase::idle ? Phase::idle : Phase::active;
    safetyState_ = reason;
}

void CommandGate::linkConnected(Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    linkConnected_ = true;
    if (!estop_) {
        haltLocked(now, "stopped");
    }
}

void CommandGate::linkDisconnected(Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    linkConnected_ = false;
    haltLocked(now, estop_ ? "estop" : "disconnected");
}

bool CommandGate::applyMotion(Source source, const Motion& motion, Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!linkConnected_ || estop_ || localBlocked_ || guardLatched_ || source != sourceOf(controlMode_)) {
        ++ignored_;
        return false;
    }
    if (solve(geometry_, motion).rejected) {
        // Impossible motion (e.g. spin while disabled): stop instead of half-switching modes.
        ++ignored_;
        haltLocked(now, "rejected");
        return false;
    }

    motion_ = motion;
    motion_.v = std::clamp(motion_.v, -speedLimit_, speedLimit_);
    motion_.wz = std::clamp(motion_.wz, -speedLimit_/geometry_.yMid, speedLimit_/geometry_.yMid);
    hasMotion_ = true;
    lastMotion_ = now;

    if (motion.mode != steerMode_ && pendingMode_ != motion.mode) {
        // Brake first, then turn the wheels, then drive.
        pendingMode_ = motion.mode;
        phase_ = Phase::braking;
        phaseEnd_ = now + config_.brakeTime;
        safetyState_ = "switching";
    } else if (phase_ == Phase::idle) {
        phase_ = Phase::active;
    }
    return true;
}

void CommandGate::setControlMode(ControlMode mode, Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (mode == controlMode_) {
        return;
    }
    controlMode_ = mode;
    // Authority changes always start from a stop.
    haltLocked(now, estop_ ? "estop" : "stopped");
}

void CommandGate::stop(Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    haltLocked(now, estop_ ? "estop" : "stopped");
}

void CommandGate::estop(Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    estop_ = true;
    haltLocked(now, "estop");
}

void CommandGate::resume(Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (guardEnabled_ && (guardHold_ || now-lastGuard_ >= std::chrono::milliseconds(500))) return;
    if (localBlocked_ || !linkConnected_) return;
    guardLatched_ = false;
    estop_ = false;
    haltLocked(now, linkConnected_ ? "stopped" : "disconnected");
}

std::optional<WheelCommand> CommandGate::tick(Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (guardEnabled_ && now-lastGuard_ >= std::chrono::milliseconds(500)) guardLatched_ = true;
    if (!linkConnected_ || localBlocked_ || guardLatched_) {
        haltLocked(now, estop_ ? "estop" : !linkConnected_ ? "disconnected" : localBlocked_ ? "blocked" : "protection_hold");
    }
    if (hasMotion_ && now - lastMotion_ >= config_.commandTimeout) {
        haltLocked(now, "timeout");
    }

    switch (phase_) {
    case Phase::idle:
        return std::nullopt;

    case Phase::braking:
        output_ = holdAngles(output_);
        if (now >= phaseEnd_) {
            // Turn the wheels to the new mode's angles with zero drive.
            steerMode_ = *pendingMode_;
            Motion still = motion_;
            still.v = 0.0;
            still.wz = 0.0;
            const WheelCommand target = solve(geometry_, still);
            double maxDelta = 0.0;
            for (std::size_t i = 0; i < 4; ++i) {
                maxDelta = std::max(maxDelta, std::fabs(target.steerDeg[i] - output_.steerDeg[i]));
            }
            output_ = holdAngles(target);
            const auto steerMs = std::chrono::milliseconds(
                static_cast<long>(1000.0 * maxDelta / config_.steerRateDegPerSec));
            phase_ = Phase::steering;
            phaseEnd_ = now + steerMs + config_.steerMargin;
        }
        return output_;

    case Phase::steering:
        if (now < phaseEnd_) {
            return output_;
        }
        pendingMode_.reset();
        phase_ = Phase::active;
        safetyState_ = "normal";
        break;

    case Phase::active:
        break;
    }

    if (!hasMotion_) {
        // Stopped: keep the wheels where they are for a moment, then go quiet.
        output_ = holdAngles(output_);
        if (now - stoppedAt_ >= config_.idleAfterStop) {
            phase_ = Phase::idle;
            return std::nullopt;
        }
        return output_;
    }

    Motion limited = motion_;
    limited.v = std::clamp(limited.v, -speedLimit_, speedLimit_);
    limited.wz = std::clamp(limited.wz, -speedLimit_/geometry_.yMid, speedLimit_/geometry_.yMid);
    const WheelCommand command = solve(geometry_, limited);
    if (command.rejected) {
        output_ = holdAngles(output_);
        output_.rejected = true;
        safetyState_ = "rejected";
        return output_;
    }
    output_ = command;
    safetyState_ = "normal";
    return output_;
}

void CommandGate::protection(bool hold, double limit, Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!std::isfinite(limit) || limit < 0 || limit > 0.5) return;
    guardEnabled_ = true; guardHold_ = hold; speedLimit_ = limit; lastGuard_ = now;
    if (hold) { guardLatched_ = true; haltLocked(now,"protection_hold"); }
}

void CommandGate::localBlocked(bool blocked, Clock::time_point now) {
    std::lock_guard<std::mutex> lock(mutex_);
    localBlocked_ = blocked;
    if (blocked) haltLocked(now,"blocked");
}

GateStatus CommandGate::status(Clock::time_point now) const {
    std::lock_guard<std::mutex> lock(mutex_);
    GateStatus s;
    s.controlMode = controlMode_;
    s.steerMode = steerMode_;
    s.safetyState = safetyState_;
    s.estop = estop_;
    s.linkConnected = linkConnected_;
    s.lastCommandAge = hasMotion_
        ? std::chrono::duration<double>(now - lastMotion_).count()
        : 0.0;
    s.output = output_;
    s.ignoredCommands = ignored_;
    return s;
}

}  // namespace rover::control
