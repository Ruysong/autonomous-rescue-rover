#include "communication/Protocol.hpp"
#include "control/CommandGate.hpp"
#include "control/Config.hpp"
#include "control/Kinematics.hpp"
#include "hardware/PicoBridge.hpp"

#include <chrono>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <variant>

namespace {

using namespace std::chrono_literals;
using namespace rover::control;

int failures = 0;

void check(bool condition, const std::string& message) {
    if (!condition) {
        ++failures;
        std::cerr << "FAIL: " << message << '\n';
    }
}

bool near(double a, double b, double tol = 1e-6) { return std::fabs(a - b) <= tol; }

void testStraight() {
    Geometry g;
    Motion m;
    m.v = 0.12;
    const auto out = solve(g, m);
    check(near(out.dutyLeft, 0.5) && near(out.dutyRight, 0.5), "straight: duty = v / max speed");
    for (double a : out.steerDeg) check(near(a, 0.0), "straight: wheels straight");
    check(!out.clamped && !out.rejected, "straight: not clamped");
}

void testLeftTurn() {
    Geometry g;
    Motion m;
    m.v = 0.1;
    m.kappa = 1.0;  // R = 1 m to the left
    const auto out = solve(g, m);
    check(out.dutyLeft < out.dutyRight, "left turn: inner (left) side slower");
    check(out.steerDeg[0] > 0 && out.steerDeg[1] > 0, "left turn: front wheels steer left");
    check(out.steerDeg[2] < 0 && out.steerDeg[3] < 0, "left turn: rear wheels steer right");
    check(out.steerDeg[0] > out.steerDeg[1], "left turn: inner front steers more (Ackermann)");
    // FL: atan(0.28 / (1 - 0.2825)) = 21.31 deg
    check(near(out.steerDeg[0], 21.31, 0.05), "left turn: FL angle");
    // Middle wheels: v (1 -+ yMid k)
    check(near(out.dutyLeft, 0.1 * (1 - 0.3125) / 0.24), "left turn: left duty");
}

void testCurvatureLimit() {
    Geometry g;
    const double kMax = maxCurvature(g);
    check(near(kMax, 1.3025, 0.01), "max curvature about 1.30 1/m at 30 deg");
    Motion m;
    m.v = 0.1;
    m.kappa = 5.0;
    const auto out = solve(g, m);
    check(out.clamped, "curvature beyond limit is clamped");
    for (double a : out.steerDeg) check(std::fabs(a) <= 30.0 + 1e-9, "angles within limit");
    check(near(std::fabs(out.steerDeg[0]), 30.0, 0.01), "inner front hits the limit");
}

void testDutyScaling() {
    Geometry g;
    Motion m;
    m.v = 1.0;  // far beyond 0.24 * 0.6
    m.kappa = 0.5;
    const auto out = solve(g, m);
    check(out.clamped, "duty clamp reported");
    check(std::fabs(out.dutyRight) <= 0.6 + 1e-9, "duty never above max_duty");
    const double ratio = out.dutyLeft / out.dutyRight;
    check(near(ratio, (1 - 0.3125 * 0.5) / (1 + 0.3125 * 0.5)), "duty clamp keeps turn shape");
}

void testSpin() {
    Geometry g;
    Motion m;
    m.mode = SteerMode::spin;
    m.wz = 0.5;
    check(solve(g, m).rejected, "spin rejected while disabled");
    g.spinEnabled = true;
    check(solve(g, m).rejected, "spin rejected when corners need more than the limit");
    g.maxSteerDeg = 50;
    const auto out = solve(g, m);
    check(!out.rejected, "spin allowed with 50 deg range");
    check(out.dutyLeft < 0 && out.dutyRight > 0, "spin CCW: left back, right forward");
    check(near(out.steerDeg[0], -44.73, 0.05) && near(out.steerDeg[1], 44.73, 0.05),
          "spin: front corners tangent");
}

void testSignTrim() {
    Geometry g;
    g.steerSign = {1, -1, 1, 1};
    g.steerTrimDeg = {2, 0, 0, 0};
    g.motorSign = {1, -1};
    Motion m;
    m.v = 0.12;
    const auto out = solve(g, m);
    check(near(out.steerDeg[0], 2.0), "trim applied");
    check(near(out.dutyRight, -0.5), "motor sign applied");
}

Clock::time_point t0 = Clock::now();

void testGateTimeoutAndAuthority() {
    CommandGate gate(Geometry{}, GateConfig{});
    auto t = t0;
    check(!gate.tick(t).has_value(), "gate idle before any command");
    gate.linkConnected(t);
    Motion m;
    m.v = 0.12;
    check(!gate.applyMotion(Source::autonomous, m, t), "autonomous ignored in manual mode");
    check(gate.applyMotion(Source::manual, m, t), "manual accepted in manual mode");
    auto out = gate.tick(t + 10ms);
    check(out && near(out->dutyLeft, 0.5), "manual drive reaches the wheels");
    out = gate.tick(t + 600ms);
    check(out && near(out->dutyLeft, 0.0), "timeout stops the wheels");
    check(gate.status(t + 600ms).safetyState == "timeout", "timeout state reported");
    out = gate.tick(t + 2000ms);
    check(!out.has_value(), "gate goes quiet after stopping");

    gate.setControlMode(ControlMode::autonomous, t + 2100ms);
    check(!gate.applyMotion(Source::manual, m, t + 2100ms), "manual ignored in autonomous mode");
    check(gate.applyMotion(Source::autonomous, m, t + 2100ms), "autonomous accepted");
}

void testGateEstop() {
    CommandGate gate(Geometry{}, GateConfig{});
    auto t = t0;
    gate.linkConnected(t);
    Motion m;
    m.v = 0.12;
    gate.applyMotion(Source::manual, m, t);
    gate.estop(t + 10ms);
    auto out = gate.tick(t + 20ms);
    check(out && near(out->dutyLeft, 0.0), "estop stops at once");
    check(!gate.applyMotion(Source::manual, m, t + 30ms), "estop latched: motion ignored");
    gate.resume(t + 40ms);
    check(gate.applyMotion(Source::manual, m, t + 50ms), "resume: motion accepted again");
    out = gate.tick(t + 60ms);
    check(out && near(out->dutyLeft, 0.5), "drives after resume");
}

void testGateDisconnectAndStop() {
    CommandGate gate(Geometry{}, GateConfig{});
    auto t = t0;
    gate.linkConnected(t);
    Motion m;
    m.v = 0.12;
    gate.applyMotion(Source::manual, m, t);
    gate.tick(t + 10ms);
    gate.linkDisconnected(t + 20ms);
    auto out = gate.tick(t + 30ms);
    check(out && near(out->dutyLeft, 0.0), "link loss stops");
    check(gate.status(t + 30ms).safetyState == "disconnected", "disconnected state");

    gate.linkConnected(t + 40ms);
    gate.applyMotion(Source::manual, m, t + 50ms);
    gate.stop(t + 60ms);
    out = gate.tick(t + 70ms);
    check(out && near(out->dutyLeft, 0.0), "stop is immediate");
    check(gate.applyMotion(Source::manual, m, t + 80ms), "stop is not latched");
}

void testGateModeSwitchOrder() {
    Geometry g;
    g.spinEnabled = true;
    g.maxSteerDeg = 50;
    GateConfig c;
    CommandGate gate(g, c);
    auto t = t0;
    gate.linkConnected(t);
    Motion drive;
    drive.v = 0.12;
    gate.applyMotion(Source::manual, drive, t);
    gate.tick(t + 10ms);

    Motion spin;
    spin.mode = SteerMode::spin;
    spin.wz = 0.4;
    gate.applyMotion(Source::manual, spin, t + 20ms);
    auto out = gate.tick(t + 30ms);
    check(out && near(out->dutyLeft, 0.0) && near(out->steerDeg[0], 0.0),
          "switch step 1: brake with old angles");
    // brake 350 ms, then steering: about 45 deg / 180 deg/s + 200 ms margin = 450 ms
    gate.applyMotion(Source::manual, spin, t + 300ms);
    out = gate.tick(t + 400ms);
    check(out && near(out->dutyLeft, 0.0) && std::fabs(out->steerDeg[0]) > 40.0,
          "switch step 2: wheels turn, still no drive");
    gate.applyMotion(Source::manual, spin, t + 700ms);
    out = gate.tick(t + 750ms);
    check(out && near(out->dutyLeft, 0.0), "switch step 2 continues within steer time");
    gate.applyMotion(Source::manual, spin, t + 1000ms);
    out = gate.tick(t + 1000ms);
    check(out && out->dutyLeft < 0.0 && out->dutyRight > 0.0, "switch step 3: drive in spin");
}

void testGateRejectsImpossibleSpin() {
    CommandGate gate(Geometry{}, GateConfig{});
    gate.linkConnected(t0);
    Motion spin;
    spin.mode = SteerMode::spin;
    spin.wz = 0.4;
    check(!gate.applyMotion(Source::manual, spin, t0), "spin refused while disabled");
    check(gate.status(t0).safetyState == "rejected", "rejected state reported");
    check(gate.status(t0).steerMode == SteerMode::normal, "steer mode unchanged");
}

void testProtocol() {
    using namespace rover::protocol;
    auto r = parseCommand(
        R"({"_version":"1.1","type":"motion","source":"manual","steer_mode":"normal","v":0.1,"kappa":0.5})");
    check(isValid(r), "motion parses");
    if (const auto* c = commandFrom(r)) {
        const auto* m = std::get_if<MotionCommand>(c);
        check(m && near(m->v, 0.1) && near(m->kappa, 0.5) && m->source == "manual",
              "motion fields");
    }
    check(!isValid(parseCommand(
              R"({"_version":"1.1","type":"motion","source":"bob","steer_mode":"normal","v":0.1})")),
          "bad source rejected");
    check(!isValid(parseCommand(
              R"({"_version":"1.1","type":"motion","source":"manual","steer_mode":"crab","v":0.1})")),
          "crab rejected");
    check(!isValid(parseCommand(
              R"({"_version":"1.1","type":"motion","source":"manual","steer_mode":"normal","v":9})")),
          "absurd speed rejected");
    check(!isValid(parseCommand(R"({"_version":"1.0","type":"estop"})")),
          "v1.1 types need v1.1");
    check(isValid(parseCommand(R"({"_version":"1.0","type":"drive","throttle":0.1,"steer":0})")),
          "legacy drive accepted");
    check(isValid(parseCommand(R"({"_version":"1.1","type":"set_mode","mode":"autonomous"})")),
          "set_mode parses");
    check(isValid(parseCommand(R"({"_version":"1.1","type":"estop"})")) &&
              isValid(parseCommand(R"({"_version":"1.1","type":"resume"})")),
          "estop / resume parse");
    Telemetry t{};
    const std::string json = serializeTelemetry(t);
    check(json.find("\"control_mode\"") != std::string::npos, "telemetry has v1.1 fields");
}

void testPicoLines() {
    using namespace rover::hardware;
    PicoTelemetry t;
    check(parseTelemetryLine("S,82479543,2,1,66,366,-1,50,8", t), "S line parses");
    check(t.encLeft == 2 && t.distCm[2] == -1 && t.flags == 8, "S line fields");
    PicoTelemetry u;
    check(!parseTelemetryLine("S,1,2,3", u) && !u.valid, "short S line rejected");
    check(!parseTelemetryLine("S,1,2,x,4,5,6,7,8", u), "non-numeric S line rejected");
    WheelCommand c;
    c.dutyLeft = 0.25;
    c.dutyRight = -0.5;
    c.steerDeg = {10, 9.5, -10, -9.5};
    check(formatDriveLine(c) == "C,0.250,-0.500,10.0,9.5,-10.0,-9.5\n", "C line format");
}

void testProtection() {
    Geometry g; CommandGate gate(g, {}); const auto t = Clock::now();
    gate.linkConnected(t); Motion m; m.v=0.12;
    gate.protection(false,0.05,t);
    gate.applyMotion(Source::manual,m,t); auto out=gate.tick(t);
    check(out && std::fabs(out->dutyLeft*g.maxWheelSpeed)<=0.050001,"protection caps manual speed");
    gate.protection(true,0.05,t+10ms);
    check(!gate.applyMotion(Source::manual,m,t+20ms),"hold rejects new manual motion");
    gate.protection(false,0.05,t+30ms);
    check(!gate.applyMotion(Source::manual,m,t+40ms),"clear observation does not release latch");
    gate.resume(t+40ms);
    check(gate.applyMotion(Source::manual,m,t+50ms),"explicit resume releases clear hold");
    gate.tick(t+600ms);
    check(!gate.applyMotion(Source::manual,m,t+601ms),"lost guard heartbeat latches hold");
    gate.protection(false,0.5,t+610ms);gate.resume(t+610ms);
    gate.linkDisconnected(t+620ms);
    check(!gate.applyMotion(Source::manual,m,t+630ms),"no motion while disconnected");
    gate.linkConnected(t+640ms);gate.setControlMode(ControlMode::autonomous,t+640ms);
    gate.localBlocked(true,t+640ms);
    check(!gate.applyMotion(Source::autonomous,m,t+650ms),"local obstacle blocks autonomous");
    check(!gate.applyMotion(Source::manual,m,t+650ms),"local obstacle blocks manual");
    gate.localBlocked(false,t+660ms);
    check(gate.applyMotion(Source::autonomous,m,t+660ms),"clear lidar allows motion");
    check(rover::protocol::isValid(rover::protocol::parseCommand(
        R"({"_version":"1.2","type":"protection","hold":false,"speed_limit":0.05})")),"protection parse");
    check(!rover::protocol::isValid(rover::protocol::parseCommand(
        R"({"_version":"1.2","type":"protection","hold":1,"speed_limit":0.05})")),"protection rejects numeric hold");
}

void testConfig() {
    const auto config = loadConfig(ROVER_DEFAULT_CONFIG);
    check(near(config.geometry.yMid, 0.3125), "config tread loaded");
    check(config.gate.commandTimeout == 500ms, "config timeout loaded");
}

}  // namespace

int main() {
    try {
        testStraight();
        testLeftTurn();
        testCurvatureLimit();
        testDutyScaling();
        testSpin();
        testSignTrim();
        testGateTimeoutAndAuthority();
        testGateEstop();
        testGateDisconnectAndStop();
        testGateModeSwitchOrder();
        testGateRejectsImpossibleSpin();
        testProtocol();
        testPicoLines();
        testConfig();
        testProtection();
    } catch (const std::exception& error) {
        std::cerr << "exception: " << error.what() << '\n';
        return 1;
    }
    if (failures) {
        std::cerr << failures << " check(s) failed\n";
        return 1;
    }
    std::cout << "robot_control_tests: all checks passed\n";
    return 0;
}
