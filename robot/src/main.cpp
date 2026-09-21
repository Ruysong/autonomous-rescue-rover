#include "communication/TcpServer.hpp"
#include "motor/MockMotor.hpp"

#include <exception>
#include <iostream>
#include <sstream>
#include <string>

namespace {

constexpr double kDefaultSpeed = 0.5;

void handleCommand(const std::string& command, MotorInterface& motor) {
    std::istringstream input(command);
    std::string verb;
    input >> verb;

    if (verb == "FORWARD") {
        motor.setSpeed(kDefaultSpeed);
    } else if (verb == "BACKWARD") {
        motor.setSpeed(-kDefaultSpeed);
    } else if (verb == "STOP") {
        motor.stop();
    } else if (verb == "SET_SPEED") {
        double speed = 0.0;
        std::string unexpected;

        if (!(input >> speed) || input >> unexpected || speed < -1.0 || speed > 1.0) {
            std::cerr << "[Command] Invalid SET_SPEED command: " << command << '\n';
            return;
        }

        motor.setSpeed(speed);
    } else {
        std::cerr << "[Command] Unknown command: " << command << '\n';
    }
}

}  // namespace

int main() {
    std::cout << "Autonomous Rescue Rover controller started.\n";

    MockMotor motor;
    TcpServer server(9998);

    try {
        server.run(
            [&motor](const std::string& command) { handleCommand(command, motor); },
            [&motor]() { motor.stop(); });
    } catch (const std::exception& error) {
        std::cerr << "Controller stopped: " << error.what() << '\n';
        return 1;
    }

    return 0;
}
