#include "motor/MockMotor.hpp"

#include <iostream>

int main() {
    std::cout << "Autonomous Rescue Rover controller started.\n";

    MockMotor motor;
    motor.setSpeed(0.5);
    motor.stop();

    return 0;
}
