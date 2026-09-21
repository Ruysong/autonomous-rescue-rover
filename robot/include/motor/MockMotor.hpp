#pragma once

#include "motor/MotorInterface.hpp"

class MockMotor final : public MotorInterface {
public:
    void setSpeed(double speed) override;
    void stop() override;
};
