#include "hardware/PicoBridge.hpp"

#include <algorithm>
#include <cerrno>
#include <utility>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <iostream>
#include <sstream>
#include <sys/select.h>
#include <termios.h>
#include <unistd.h>
#include <vector>

namespace rover::hardware {

using Clock = std::chrono::steady_clock;

std::string formatDriveLine(const control::WheelCommand& c) {
    char buffer[128];
    std::snprintf(buffer, sizeof(buffer), "C,%.3f,%.3f,%.1f,%.1f,%.1f,%.1f\n", c.dutyLeft,
                  c.dutyRight, c.steerDeg[0], c.steerDeg[1], c.steerDeg[2], c.steerDeg[3]);
    return buffer;
}

bool parseTelemetryLine(const std::string& line, PicoTelemetry& out) {
    if (line.size() < 2 || line[0] != 'S' || line[1] != ',') {
        return false;
    }
    std::vector<long> values;
    std::stringstream stream(line.substr(2));
    std::string field;
    while (std::getline(stream, field, ',')) {
        try {
            std::size_t used = 0;
            const long value = std::stol(field, &used);
            if (used != field.size()) {
                return false;
            }
            values.push_back(value);
        } catch (const std::exception&) {
            return false;
        }
    }
    if (values.size() != 8) {
        return false;
    }
    out.picoMs = values[0];
    out.encLeft = values[1];
    out.encRight = values[2];
    for (std::size_t i = 0; i < 4; ++i) {
        out.distCm[i] = static_cast<int>(values[3 + i]);
    }
    out.flags = static_cast<int>(values[7]);
    out.valid = true;
    return true;
}

// ---------------- SerialPico ----------------

SerialPico::SerialPico(std::string path) : path_(std::move(path)) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        openPort();
    }
    reader_ = std::thread([this] { readLoop(); });
}

SerialPico::~SerialPico() {
    running_ = false;
    if (reader_.joinable()) {
        reader_.join();
    }
    std::lock_guard<std::mutex> lock(mutex_);
    closePortLocked();
}

bool SerialPico::openPort() {
    fd_ = ::open(path_.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (fd_ < 0) {
        telemetry_.portOpen = false;
        return false;
    }
    termios tty{};
    if (tcgetattr(fd_, &tty) == 0) {
        cfmakeraw(&tty);
        cfsetispeed(&tty, B115200);
        cfsetospeed(&tty, B115200);
        tty.c_cflag |= CLOCAL | CREAD;
        tcsetattr(fd_, TCSANOW, &tty);
    }
    tcflush(fd_, TCIFLUSH);
    telemetry_.portOpen = true;
    std::cout << "[Pico] Opened " << path_ << '\n';
    return true;
}

void SerialPico::closePortLocked() {
    if (fd_ >= 0) {
        ::close(fd_);
        fd_ = -1;
    }
    telemetry_.portOpen = false;
}

void SerialPico::writeLine(const std::string& line) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (fd_ < 0) {
        return;
    }
    std::size_t sent = 0;
    while (sent < line.size()) {
        const ssize_t n = ::write(fd_, line.data() + sent, line.size() - sent);
        if (n < 0) {
            if (errno == EAGAIN || errno == EINTR) {
                continue;
            }
            std::cerr << "[Pico] Write failed: " << std::strerror(errno) << '\n';
            closePortLocked();
            return;
        }
        sent += static_cast<std::size_t>(n);
    }
}

void SerialPico::send(const control::WheelCommand& command) {
    writeLine(formatDriveLine(command));
}

void SerialPico::estop() { writeLine("E\n"); }
void SerialPico::clearEstop() { writeLine("R\n"); }

void SerialPico::handleLine(const std::string& line) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!line.empty() && line[0] == 'X') {
        ++telemetry_.rejects;
        telemetry_.lastReject = line.size() > 2 ? line.substr(2) : "";
        return;
    }
    if (parseTelemetryLine(line, telemetry_)) {
        lastRx_ = Clock::now();
    }
}

void SerialPico::readLoop() {
    std::string pending;
    char buffer[256];
    while (running_) {
        int fd;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (fd_ < 0 && !openPort()) {
                fd = -1;
            } else {
                fd = fd_;
            }
        }
        if (fd < 0) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            continue;
        }

        fd_set set;
        FD_ZERO(&set);
        FD_SET(fd, &set);
        timeval timeout{0, 200000};
        const int ready = ::select(fd + 1, &set, nullptr, nullptr, &timeout);
        if (ready <= 0) {
            continue;
        }
        const ssize_t n = ::read(fd, buffer, sizeof(buffer));
        if (n <= 0) {
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) {
                continue;
            }
            std::cerr << "[Pico] Port lost; reopening\n";
            std::lock_guard<std::mutex> lock(mutex_);
            closePortLocked();
            continue;
        }
        pending.append(buffer, static_cast<std::size_t>(n));
        std::size_t newline;
        while ((newline = pending.find('\n')) != std::string::npos) {
            std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            handleLine(line);
        }
        if (pending.size() > 4096) {
            pending.clear();
        }
    }
}

PicoTelemetry SerialPico::telemetry() const {
    std::lock_guard<std::mutex> lock(mutex_);
    PicoTelemetry out = telemetry_;
    out.age = out.valid ? std::chrono::duration<double>(Clock::now() - lastRx_).count() : 1e9;
    return out;
}

// ---------------- MockPico ----------------

MockPico::MockPico(double countsPerSecondAtFullDuty)
    : countsPerSecond_(countsPerSecondAtFullDuty),
      start_(Clock::now()),
      lastSend_(Clock::now()),
      lastAdvance_(Clock::now()) {}

void MockPico::advanceLocked(Clock::time_point now) {
    const double dt = std::chrono::duration<double>(now - lastAdvance_).count();
    lastAdvance_ = now;
    // Same safety as the firmware: 300 ms link timeout, outputs off during e-stop.
    const bool live = commanded_ && !estop_ && now - lastSend_ < std::chrono::milliseconds(300);
    if (live) {
        encLeft_ += last_.dutyLeft * countsPerSecond_ * dt;
        encRight_ += last_.dutyRight * countsPerSecond_ * dt;
    }
}

void MockPico::send(const control::WheelCommand& command) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto now = Clock::now();
    advanceLocked(now);
    last_ = command;
    last_.dutyLeft = std::clamp(last_.dutyLeft, -0.6, 0.6);
    last_.dutyRight = std::clamp(last_.dutyRight, -0.6, 0.6);
    commanded_ = true;
    lastSend_ = now;
}

void MockPico::estop() {
    std::lock_guard<std::mutex> lock(mutex_);
    advanceLocked(Clock::now());
    estop_ = true;
}

void MockPico::clearEstop() {
    std::lock_guard<std::mutex> lock(mutex_);
    advanceLocked(Clock::now());
    estop_ = false;
}

PicoTelemetry MockPico::telemetry() const {
    auto* self = const_cast<MockPico*>(this);
    std::lock_guard<std::mutex> lock(mutex_);
    const auto now = Clock::now();
    self->advanceLocked(now);
    PicoTelemetry out;
    out.valid = true;
    out.portOpen = true;
    out.picoMs = static_cast<long>(std::chrono::duration<double, std::milli>(now - start_).count());
    out.encLeft = static_cast<long>(std::lround(encLeft_));
    out.encRight = static_cast<long>(std::lround(encRight_));
    out.distCm = {120, 15, 15, 120};
    out.flags = (commanded_ ? 0 : 8) | (estop_ ? 2 : 0) |
                (commanded_ && now - lastSend_ >= std::chrono::milliseconds(300) ? 1 : 0);
    out.age = 0.0;
    return out;
}

control::WheelCommand MockPico::lastCommand() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return last_;
}

std::unique_ptr<PicoLink> makePicoLink(const std::string& spec) {
    if (spec == "mock") {
        return std::make_unique<MockPico>();
    }
    return std::make_unique<SerialPico>(spec);
}

}  // namespace rover::hardware
