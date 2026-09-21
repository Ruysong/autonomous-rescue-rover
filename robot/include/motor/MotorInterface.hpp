#pragma once

class MotorInterface {
public:
    virtual ~MotorInterface() = default;

    virtual void setSpeed(double speed) = 0;
    virtual void stop() = 0;
};
