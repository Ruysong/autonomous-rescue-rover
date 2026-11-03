#include "communication/FramedSocket.hpp"
#include "communication/Protocol.hpp"
#include "communication/TcpClient.hpp"
#include "motor/MockMotor.hpp"
#include "state/RobotState.hpp"
#ifdef ROVER_WITH_GPIO
#include "motor/GpioMotor.hpp"
#endif

#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <cstdint>
#include <exception>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

using namespace std::chrono_literals;
volatile std::sig_atomic_t stopRequested = 0;

void requestStop(int) {
    stopRequested = 1;
}

struct Settings {
    std::string laptopHost;
    std::uint16_t commandPort = 9998;
    std::uint16_t telemetryPort = 9997;
    std::string motorMode = "mock";
    bool armGpio = false;
    bool invertRight = false;
    double maxDutyPercent = 15.0;
};

void printUsage() {
    std::cout << "Usage: rover_controller --host LAPTOP_IP "
                 "[--command-port 9998] [--telemetry-port 9997] "
                 "[--motor gpio --arm-gpio --max-duty 15 [--invert-right]]\n"
                 "Default motor: mock. GPIO mode requires a GPIO-enabled build.\n";
}

std::uint16_t parsePort(const std::string& text) {
    std::size_t used = 0;
    const unsigned long value = std::stoul(text, &used);
    if (used != text.size() || value == 0 || value > 65535) {
        throw std::invalid_argument("Port must be between 1 and 65535");
    }
    return static_cast<std::uint16_t>(value);
}

double parseDuty(const std::string& text) {
    std::size_t used = 0;
    const double value = std::stod(text, &used);
    if (used != text.size() || !std::isfinite(value) || value <= 0.0 || value > 20.0) {
        throw std::invalid_argument("--max-duty must be in (0, 20] percent");
    }
    return value;
}

bool parseArgs(int argc, char* argv[], Settings& settings) {
    for (int i = 1; i < argc; ++i) {
        const std::string option = argv[i];
        if (option == "--help") {
            printUsage();
            return false;
        }
        if (option == "--arm-gpio") {
            settings.armGpio = true;
            continue;
        }
        if (option == "--invert-right") {
            settings.invertRight = true;
            continue;
        }
        if (i + 1 >= argc) {
            throw std::invalid_argument("Missing value for " + option);
        }

        const std::string value = argv[++i];
        if (option == "--host") {
            settings.laptopHost = value;
        } else if (option == "--command-port") {
            settings.commandPort = parsePort(value);
        } else if (option == "--telemetry-port") {
            settings.telemetryPort = parsePort(value);
        } else if (option == "--motor") {
            settings.motorMode = value;
        } else if (option == "--max-duty") {
            settings.maxDutyPercent = parseDuty(value);
        } else {
            throw std::invalid_argument("Unknown option: " + option);
        }
    }

    if (settings.laptopHost.empty()) {
        throw std::invalid_argument("--host must be the laptop address");
    }
    if (settings.motorMode != "mock" && settings.motorMode != "gpio") {
        throw std::invalid_argument("--motor must be mock or gpio");
    }
    if (settings.motorMode == "gpio" && !settings.armGpio) {
        throw std::invalid_argument("GPIO mode requires --arm-gpio");
    }
    if (settings.motorMode == "mock" && (settings.armGpio || settings.invertRight)) {
        throw std::invalid_argument("GPIO options require --motor gpio");
    }
#ifndef ROVER_WITH_GPIO
    if (settings.motorMode == "gpio") {
        throw std::invalid_argument("GPIO mode needs a ROVER_WITH_GPIO=ON build");
    }
#endif
    return true;
}

[[noreturn]] void runCommands(const Settings& settings, rover::state::RobotState& state) {
    for (;;) {
        rover::communication::TcpClient client;
        std::string error;
        if (!client.connectTo(settings.laptopHost, settings.commandPort, error)) {
            std::cerr << "[Command] Connect failed: " << error << '\n';
            std::this_thread::sleep_for(1s);
            continue;
        }

        std::cout << "[Command] Connected to laptop:" << settings.commandPort << '\n';
        state.commandConnected();

        for (;;) {
            const auto frame = rover::communication::recvFrame(client.fd());
            if (frame.status != rover::communication::FrameStatus::received) {
                if (frame.status != rover::communication::FrameStatus::disconnected) {
                    std::cerr << "[Command] Receive failed: " << frame.error << '\n';
                }
                break;
            }

            const auto parsed = rover::protocol::parseCommand(frame.payload);
            if (const auto* command = rover::protocol::commandFrom(parsed)) {
                state.applyCommand(*command);
            } else {
                std::cerr << "[Command] Rejected: " << *rover::protocol::errorFrom(parsed) << '\n';
            }
        }

        state.commandDisconnected();
        client.close();
        std::this_thread::sleep_for(1s);
    }
}

[[noreturn]] void runTelemetry(const Settings& settings, const rover::state::RobotState& state) {
    for (;;) {
        rover::communication::TcpClient client;
        std::string error;
        if (!client.connectTo(settings.laptopHost, settings.telemetryPort, error)) {
            std::cerr << "[Telemetry] Connect failed: " << error << '\n';
            std::this_thread::sleep_for(1s);
            continue;
        }

        std::cout << "[Telemetry] Connected to laptop:" << settings.telemetryPort << '\n';
        for (;;) {
            const std::string payload = rover::protocol::serializeTelemetry(state.snapshot());
            const auto status = rover::communication::sendFrame(client.fd(), payload, error);
            if (status != rover::communication::FrameStatus::received) {
                std::cerr << "[Telemetry] Send failed: " << error << '\n';
                break;
            }
            std::this_thread::sleep_for(200ms);
        }

        client.close();
        std::this_thread::sleep_for(1s);
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    Settings settings;
    try {
        if (!parseArgs(argc, argv, settings)) {
            return 0;
        }
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        printUsage();
        return 1;
    }

    std::unique_ptr<MotorInterface> motor;
    try {
        if (settings.motorMode == "gpio") {
#ifdef ROVER_WITH_GPIO
            motor = std::make_unique<GpioMotor>(settings.maxDutyPercent, settings.invertRight);
#endif
        } else {
            motor = std::make_unique<MockMotor>();
        }
    } catch (const std::exception& error) {
        std::cerr << "Motor initialization failed: " << error.what() << '\n';
        return 1;
    }

    std::cout.setf(std::ios::unitbuf);
    std::cout << "Autonomous Rescue Rover controller started ("
              << settings.motorMode << " mode).\n";
    const auto timeout = settings.motorMode == "gpio" ? 500ms : 3s;
    rover::state::RobotState state(*motor, settings.motorMode,
                                  std::chrono::duration_cast<std::chrono::milliseconds>(timeout));
    if (settings.motorMode == "gpio") {
        std::signal(SIGINT, requestStop);
        std::signal(SIGTERM, requestStop);
    }
    std::thread telemetry([&] { runTelemetry(settings, state); });
    std::thread watchdog([&] {
        for (;;) {
            if (stopRequested) {
                state.commandDisconnected();
                std::_Exit(0);
            }
            state.checkCommandTimeout();
            std::this_thread::sleep_for(100ms);
        }
    });
    runCommands(settings, state);
}
