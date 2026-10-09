#include "communication/FramedSocket.hpp"
#include "communication/Protocol.hpp"
#include "motor/MotorInterface.hpp"
#include "state/RobotState.hpp"

#include <arpa/inet.h>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <thread>
#include <unistd.h>

namespace {

void require(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

class RecordingMotor final : public MotorInterface {
public:
    void setSpeed(double value) override { speed = value; }
    void stop() override { speed = 0.0; ++stopCount; }

    double speed = 0.0;
    int stopCount = 0;
};

void testFraming() {
    int sockets[2]{};
    require(socketpair(AF_UNIX, SOCK_STREAM, 0, sockets) == 0, "socketpair failed");

    const std::string payload = R"({"_version":"1.0","type":"stop"})";
    const std::uint32_t length = htonl(static_cast<std::uint32_t>(payload.size()));
    std::string frame(sizeof(length), '\0');
    std::memcpy(frame.data(), &length, sizeof(length));
    frame += payload;
    for (char byte : frame) {
        require(send(sockets[0], &byte, 1, 0) == 1, "fragmented send failed");
    }
    const auto received = rover::communication::recvFrame(sockets[1]);
    require(received.status == rover::communication::FrameStatus::received,
            "fragmented frame was not received");
    require(received.payload == payload, "fragmented frame payload mismatch");

    std::string error;
    require(rover::communication::sendFrame(sockets[1], payload, error) ==
                rover::communication::FrameStatus::received,
            "sendFrame failed");
    require(rover::communication::recvFrame(sockets[0]).payload == payload,
            "sendFrame round trip failed");

    ::close(sockets[0]);
    ::close(sockets[1]);
}

void testProtocolAndSafety() {
    const auto drive = rover::protocol::parseCommand(
        R"({"_version":"1.0","type":"drive","throttle":0.5,"steer":0.0})");
    require(rover::protocol::isValid(drive), "valid drive rejected");
    require(!rover::protocol::isValid(rover::protocol::parseCommand(
                R"({"_version":"1.0","type":"drive","throttle":1.5,"steer":0.0})")),
            "out-of-range drive accepted");
    require(!rover::protocol::isValid(rover::protocol::parseCommand(
                R"({"_version":"1.0","type":"unknown"})")),
            "unknown command accepted");
    require(!rover::protocol::isValid(rover::protocol::parseCommand("not JSON")),
            "invalid JSON accepted");

    RecordingMotor motor;
    rover::state::RobotState state(motor);
    state.commandConnected();
    state.applyCommand(*rover::protocol::commandFrom(drive));
    require(std::abs(motor.speed - 0.5) < 0.0001, "drive did not reach motor");
    require(state.snapshot().safetyState == "normal", "valid command did not set normal state");

    std::this_thread::sleep_for(std::chrono::milliseconds(3100));
    state.checkCommandTimeout();
    require(motor.stopCount == 1, "timeout did not stop motor");
    require(state.snapshot().safetyState == "timeout", "timeout state missing");

    state.applyCommand(*rover::protocol::commandFrom(drive));
    state.commandDisconnected();
    require(motor.stopCount == 2, "disconnect did not stop motor");
    require(state.snapshot().safetyState == "disconnected", "disconnect state missing");
}

}  // namespace

int main() {
    try {
        testFraming();
        testProtocolAndSafety();
        std::cout << "robot runtime tests passed\n";
    } catch (const std::exception& error) {
        std::cerr << "robot runtime tests failed: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
