#include "motor/GpioMotor.hpp"

#include <lgpio.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <stdexcept>
#include <string>
#include <thread>

GpioMotor::GpioMotor(double maxDutyPercent, bool invertRight)
    : maxDutyPercent_(maxDutyPercent), invertRight_(invertRight) {
    if (!std::isfinite(maxDutyPercent) || maxDutyPercent <= 0.0 || maxDutyPercent > 20.0) {
        throw std::invalid_argument("GPIO motor duty cap must be in (0, 20] percent");
    }
    handle_ = lgGpiochipOpen(0);
    if (handle_ < 0) {
        throw std::runtime_error("Could not open gpiochip0: " + std::to_string(handle_));
    }
    const std::array<int, 4> pins{kPwmPins[0], kDirectionPins[0],
                                  kPwmPins[1], kDirectionPins[1]};
    try {
        for (std::size_t i = 0; i < pins.size(); ++i) {
            requireSuccess(lgGpioClaimOutput(handle_, 0, pins[i], 0), "claim motor GPIO");
            claimed_[i] = true;
        }
    } catch (...) {
        release();
        throw;
    }
}

GpioMotor::~GpioMotor() {
    release();
}

void GpioMotor::requireSuccess(int status, const char* operation) {
    if (status < 0) {
        throw std::runtime_error(std::string(operation) + " failed: " + std::to_string(status));
    }
}

void GpioMotor::setSpeed(double speed) {
    if (faulted_) {
        throw std::runtime_error("Motor GPIO fault is latched; restart after inspection");
    }
    if (!std::isfinite(speed) || speed < -1.0 || speed > 1.0) {
        throw std::invalid_argument("Motor speed request must be in [-1, 1]");
    }
    if (speed == 0.0) {
        stop();
        return;
    }
    const int direction = speed > 0.0 ? 1 : -1;
    const double duty = std::min(std::abs(speed) * 100.0, maxDutyPercent_);
    if (currentDirection_ == direction && currentDutyPercent_ == duty) {
        return;
    }
    try {
        if (currentDirection_ != 0 && currentDirection_ != direction) {
            stopOutputs();
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        // Never reverse DIR while PWM is active.
        if (currentDirection_ == 0) {
            const int reverse = direction < 0 ? 1 : 0;
            requireSuccess(lgGpioWrite(handle_, kDirectionPins[0], reverse), "left DIR");
            requireSuccess(lgGpioWrite(handle_, kDirectionPins[1], reverse ^ invertRight_),
                           "right DIR");
        }
        requireSuccess(lgTxPwm(handle_, kPwmPins[0], kPwmFrequencyHz,
                               static_cast<float>(duty), 0, 0), "left PWM");
        requireSuccess(lgTxPwm(handle_, kPwmPins[1], kPwmFrequencyHz,
                               static_cast<float>(duty), 0, 0), "right PWM");
        currentDirection_ = direction;
        currentDutyPercent_ = duty;
    } catch (...) {
        faulted_ = true;
        stopOutputs();
        throw;
    }
}

void GpioMotor::stopOutputs() noexcept {
    if (handle_ < 0) {
        return;
    }
    for (std::size_t side = 0; side < 2; ++side) {
        if (claimed_[side * 2]) {
            lgTxPwm(handle_, kPwmPins[side], kPwmFrequencyHz, 0, 0, 0);
            lgGpioWrite(handle_, kPwmPins[side], 0);
        }
        if (claimed_[side * 2 + 1]) {
            lgGpioWrite(handle_, kDirectionPins[side], 0);
        }
    }
    currentDirection_ = 0;
    currentDutyPercent_ = 0.0;
}

void GpioMotor::stop() {
    stopOutputs();
}

void GpioMotor::release() noexcept {
    if (handle_ < 0) {
        return;
    }
    stopOutputs();
    const std::array<int, 4> pins{kPwmPins[0], kDirectionPins[0],
                                  kPwmPins[1], kDirectionPins[1]};
    for (std::size_t i = 0; i < pins.size(); ++i) {
        if (claimed_[i]) {
            lgGpioFree(handle_, pins[i]);
            claimed_[i] = false;
        }
    }
    lgGpiochipClose(handle_);
    handle_ = -1;
}
