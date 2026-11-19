#include "hardware/Lidar.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>

#include <fcntl.h>
#include <sys/select.h>
#include <termios.h>
#include <unistd.h>

namespace rover::hardware {
namespace {

using namespace std::chrono_literals;
constexpr double kPi = 3.14159265358979323846;

int binOf(double angleRad) {
    double deg = angleRad * 180.0 / kPi;
    deg = std::fmod(deg, 360.0);
    if (deg < 0) deg += 360.0;
    return static_cast<int>(std::lround(deg)) % kScanBins;
}

void putRange(LaserScan& scan, int bin, double rangeMm) {
    const auto value = static_cast<std::uint16_t>(std::clamp(rangeMm, 1.0, 65535.0));
    auto& slot = scan.rangesMm[static_cast<std::size_t>(bin)];
    if (slot == 0 || value < slot) slot = value;
    ++scan.points;
}

// Reads exactly n bytes or times out.
bool readExact(int fd, std::uint8_t* out, std::size_t n, std::chrono::milliseconds timeout) {
    std::size_t got = 0;
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (got < n) {
        const auto left = deadline - std::chrono::steady_clock::now();
        if (left <= 0ms) return false;
        fd_set set;
        FD_ZERO(&set);
        FD_SET(fd, &set);
        const auto us = std::chrono::duration_cast<std::chrono::microseconds>(left).count();
        timeval tv{static_cast<time_t>(us / 1000000), static_cast<suseconds_t>(us % 1000000)};
        if (::select(fd + 1, &set, nullptr, nullptr, &tv) <= 0) return false;
        const ssize_t r = ::read(fd, out + got, n - got);
        if (r <= 0) return false;
        got += static_cast<std::size_t>(r);
    }
    return true;
}

void sendCmd(int fd, std::uint8_t cmd) {
    const std::uint8_t bytes[2] = {0xA5, cmd};
    (void)!::write(fd, bytes, 2);
}

}  // namespace

bool decodeNode(const std::uint8_t* p, ScanNode& out) {
    const bool start = (p[0] & 1) != 0;
    const bool inverse = ((p[0] >> 1) & 1) != 0;
    if (start == inverse || (p[1] & 1) == 0) return false;
    out.start = start;
    out.quality = p[0] >> 2;
    out.angleDeg = ((p[1] >> 1) | (p[2] << 7)) / 64.0;
    out.distMm = (p[3] | (p[4] << 8)) / 4.0;
    return true;
}

ScanAssembler::ScanAssembler(LidarMount mount) : mount_(mount) {}

void ScanAssembler::addPoint(double angleDeg, double distMm) {
    const double r = distMm / 1000.0;
    if (distMm <= 0.0 || r < mount_.minRangeM || r > mount_.maxRangeM) return;
    const double a = (mount_.yawOffsetDeg + mount_.direction * angleDeg) * kPi / 180.0;
    const double x = mount_.x + r * std::cos(a);
    const double y = mount_.y + r * std::sin(a);
    putRange(current_, binOf(std::atan2(y, x)), std::hypot(x, y) * 1000.0);
}

bool ScanAssembler::add(const ScanNode& node, std::chrono::steady_clock::time_point now,
                        LaserScan& completed) {
    bool done = false;
    if (node.start) {
        if (started_ && current_.points > 0) {
            current_.valid = true;
            current_.stamp = now;
            current_.sequence = ++sequence_;
            completed = current_;
            done = true;
        }
        current_ = LaserScan{};
        started_ = true;
    }
    if (started_) addPoint(node.angleDeg, node.distMm);
    return done;
}

// ---------------------------------------------------------------- RplidarC1

RplidarC1::RplidarC1(std::string path, LidarMount mount)
    : path_(std::move(path)), mount_(mount), thread_([this] { run(); }) {}

RplidarC1::~RplidarC1() {
    running_ = false;
    if (thread_.joinable()) thread_.join();
}

LaserScan RplidarC1::latest() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return latest_;
}

bool RplidarC1::startScan(int fd) {
    sendCmd(fd, 0x25);  // STOP
    std::this_thread::sleep_for(50ms);
    tcflush(fd, TCIFLUSH);
    sendCmd(fd, 0x20);  // SCAN
    std::uint8_t descriptor[7];
    // The motor needs to spin up before the descriptor arrives.
    if (!readExact(fd, descriptor, 7, 3000ms)) return false;
    return descriptor[0] == 0xA5 && descriptor[1] == 0x5A;
}

void RplidarC1::run() {
    while (running_) {
        const int fd = ::open(path_.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
        if (fd < 0) {
            std::this_thread::sleep_for(1s);
            continue;
        }
        termios tty{};
        tcgetattr(fd, &tty);
        cfmakeraw(&tty);
        cfsetispeed(&tty, B460800);
        cfsetospeed(&tty, B460800);
        tty.c_cflag |= CLOCAL | CREAD;
        tcsetattr(fd, TCSANOW, &tty);
        // C1 needs DTR low to run the motor on some adapters; the CP210x default works.

        if (!startScan(fd)) {
            std::cerr << "[Lidar] No scan descriptor from " << path_ << "; retrying\n";
            ::close(fd);
            std::this_thread::sleep_for(1s);
            continue;
        }
        std::cout << "[Lidar] Scanning on " << path_ << '\n';
        ScanAssembler assembler(mount_);
        std::uint8_t node[5];
        int bad = 0;
        // The C1 sends the descriptor at once but nodes only after the motor is up to
        // speed (1-2 s). A short first timeout would send STOP and restart forever.
        bool firstNode = true;
        while (running_) {
            if (!readExact(fd, node, 5, firstNode ? 5000ms : 1000ms)) {
                std::cerr << "[Lidar] Read timeout; restarting scan\n";
                break;
            }
            firstNode = false;
            ScanNode decoded;
            if (!decodeNode(node, decoded)) {
                // Resync by shifting one byte.
                std::uint8_t skip;
                readExact(fd, &skip, 1, 100ms);
                if (++bad > 2000) break;
                continue;
            }
            LaserScan done;
            if (assembler.add(decoded, std::chrono::steady_clock::now(), done)) {
                std::lock_guard<std::mutex> lock(mutex_);
                latest_ = done;
            }
        }
        sendCmd(fd, 0x25);
        ::close(fd);
        std::this_thread::sleep_for(500ms);
    }
}

// ---------------------------------------------------------------- mock

std::vector<Segment> defaultMockWorld() {
    // Robot starts at (0, 0) facing +x. An L-shaped room (6 x 4 m plus a 2 m wide corridor
    // going +y from the far end) with a box in the middle.
    return {
        {-1.0, -1.5, 5.0, -1.5}, {5.0, -1.5, 5.0, 5.5},  {5.0, 5.5, 3.0, 5.5},
        {3.0, 5.5, 3.0, 2.5},    {3.0, 2.5, -1.0, 2.5},  {-1.0, 2.5, -1.0, -1.5},
        {1.6, 0.2, 2.2, 0.2},    {2.2, 0.2, 2.2, 0.8},   {2.2, 0.8, 1.6, 0.8},
        {1.6, 0.8, 1.6, 0.2},
    };
}

LaserScan castScan(const navigation::Pose2D& pose, const std::vector<Segment>& world,
                   double maxRangeM) {
    LaserScan scan;
    for (int i = 0; i < kScanBins; ++i) {
        const double a = pose.yaw + i * kPi / 180.0;
        const double dx = std::cos(a);
        const double dy = std::sin(a);
        double best = std::numeric_limits<double>::infinity();
        for (const auto& s : world) {
            const double ex = s.x2 - s.x1;
            const double ey = s.y2 - s.y1;
            const double den = dx * ey - dy * ex;
            if (std::fabs(den) < 1e-9) continue;
            const double qx = s.x1 - pose.x;
            const double qy = s.y1 - pose.y;
            const double t = (qx * ey - qy * ex) / den;   // along the ray
            const double u = (qx * dy - qy * dx) / den;   // along the segment
            if (t > 0 && u >= 0 && u <= 1 && t < best) best = t;
        }
        if (best <= maxRangeM) {
            scan.rangesMm[static_cast<std::size_t>(i)] =
                static_cast<std::uint16_t>(std::lround(best * 1000.0));
            ++scan.points;
        }
    }
    scan.valid = true;
    return scan;
}

MockLidar::MockLidar(std::function<navigation::Pose2D()> truePose, std::vector<Segment> world)
    : truePose_(std::move(truePose)), world_(std::move(world)), thread_([this] {
          unsigned seq = 0;
          while (running_) {
              auto scan = castScan(truePose_(), world_, 12.0);
              scan.stamp = std::chrono::steady_clock::now();
              scan.sequence = ++seq;
              {
                  std::lock_guard<std::mutex> lock(mutex_);
                  latest_ = scan;
              }
              std::this_thread::sleep_for(100ms);
          }
      }) {}

MockLidar::~MockLidar() {
    running_ = false;
    if (thread_.joinable()) thread_.join();
}

LaserScan MockLidar::latest() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return latest_;
}

}  // namespace rover::hardware
