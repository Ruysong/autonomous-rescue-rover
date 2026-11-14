#pragma once

#include "motor/MotorInterface.hpp"

#include <array>

// Pi 4 -> Cytron MDD10A, matching the team's existing hw_test/teleop.py pins.
// This adapter owns only the two motor channels; it does not claim servo pins.
class GpioMotor final : public MotorInterface {
public:
    explicit GpioMotor(double maxDutyPercent, bool invertRight = false);
    ~GpioMotor() override;

    GpioMotor(const GpioMotor&) = delete;
    GpioMotor& operator=(const GpioMotor&) = delete;

    void setSpeed(double speed) override;
    void stop() override;

private:
    void stopOutputs() noexcept;
    void release() noexcept;
    void requireSuccess(int status, const char* operation);

    static constexpr std::array<int, 2> kPwmPins{5, 16};
    static constexpr std::array<int, 2> kDirectionPins{6, 20};
    static constexpr int kPwmFrequencyHz = 1000;

    int handle_ = -1;
    std::array<bool, 4> claimed_{};
    double maxDutyPercent_ = 0.0;
    bool invertRight_ = false;
    bool faulted_ = false;
    int currentDirection_ = 0;
    double currentDutyPercent_ = 0.0;
};
