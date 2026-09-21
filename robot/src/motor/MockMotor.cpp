#include "motor/MockMotor.hpp"

#include <iostream>

void MockMotor::setSpeed(double speed) {
    std::cout << "[MockMotor] speed = " << speed << '\n';
}

void MockMotor::stop() {
    std::cout << "[MockMotor] stop\n";
}
