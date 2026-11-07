"""RoverAid Pico 2 firmware (MicroPython). Runs at boot as main.py.

Pin map = 전장_설계도 5-0 배정표 (11/6). Protocol over USB CDC, one line per message:

  Pi -> Pico
    C,<v_left>,<v_right>,<a_fl>,<a_fr>,<a_rl>,<a_rr>   v in [-1, 1] (duty, + = forward), a in deg (0 = straight)
    E                                                 emergency stop (outputs off until R)
    R                                                 clear emergency stop
  Pico -> Pi (TELEM_HZ)
    S,<ms>,<enc_l>,<enc_r>,<d_fl>,<d_fr>,<d_rl>,<d_rr>,<flags>   d in cm, -1 = no echo
    X,<reason>                                        rejected command

flags: 1 link timeout, 2 e-stop, 4 last command clamped, 8 no command since boot.
Safety (Pico protects, Pi decides): no output until the first valid C; link timeout -> speed 0 and
steering neutral; soft limits per servo; slew limits on duty and angle.
No hardware watchdog yet: it would keep resetting the chip when mpremote stops main.py.
"""
import sys
import time
import select
from machine import Pin, PWM, time_pulse_us

# ---- config (measure and adjust per channel) ----
MOTOR = {"L": (5, 4, 1), "R": (20, 21, 1)}  # (PWM gpio, DIR gpio, sign); DIR 0 = forward (11/3 teleop)
MOTOR_HZ = 20000
DUTY_MAX = 0.6           # hard cap on |duty|
DUTY_SLEW = 2.0          # duty units per second (0 -> 0.6 in 0.3 s)
SERVO = {                # gpio, center_us, min_deg, max_deg, sign
    "FL": (6, 1500, -30, 30, 1),
    "FR": (18, 1500, -30, 30, 1),
    "RL": (7, 1500, -30, 30, 1),
    "RR": (19, 1500, -30, 30, 1),
}
SERVO_ORDER = ("FL", "FR", "RL", "RR")
US_PER_DEG = 1000 / 90
ANGLE_SLEW = 180.0       # deg per second
LINK_TIMEOUT_MS = 300
ENC = {"L": (8, 9), "R": (16, 17)}  # A, B
SONAR = (("FL", 0, 1), ("FR", 2, 3), ("RL", 10, 11), ("RR", 12, 13))
SONAR_TIMEOUT_US = 25000
LOOP_MS = 10
TELEM_HZ = 20

# ---- outputs: start low, no pulses ----
motors = {}
for k, (p, d, s) in MOTOR.items():
    Pin(p, Pin.OUT, value=0)
    dirp = Pin(d, Pin.OUT, value=0)
    pwm = PWM(Pin(p), freq=MOTOR_HZ, duty_u16=0)
    motors[k] = (pwm, dirp, s)

servos = {}
for k, (p, c, lo, hi, s) in SERVO.items():
    Pin(p, Pin.OUT, value=0)
    servos[k] = None  # PWM created on first command, so servos stay limp until then


def servo_out(k, deg):
    p, c, lo, hi, s = SERVO[k]
    if servos[k] is None:
        servos[k] = PWM(Pin(p), freq=50, duty_u16=0)
    us = c + s * deg * US_PER_DEG
    servos[k].duty_u16(int(us * 65535 / 20000))


def motor_out(k, v):
    pwm, dirp, s = motors[k]
    v *= s
    dirp.value(1 if v < 0 else 0)
    pwm.duty_u16(int(min(abs(v), DUTY_MAX) * 65535))


def all_off():
    for k in motors:
        motor_out(k, 0)
    for k in SERVO_ORDER:
        if servos[k] is not None:
            servos[k].duty_u16(0)


# ---- encoders (x1 on A rising edge, direction from B) ----
enc = {"L": 0, "R": 0}


def make_cb(side, b):
    def cb(_):
        enc[side] += 1 if b.value() == 0 else -1
    return cb


for side, (a, b) in ENC.items():
    pb = Pin(b, Pin.IN, Pin.PULL_UP)
    Pin(a, Pin.IN, Pin.PULL_UP).irq(trigger=Pin.IRQ_RISING, handler=make_cb(side, pb), hard=True)

# ---- ultrasonic, one sensor per telemetry tick ----
sonar = [(n, Pin(t, Pin.OUT, value=0), Pin(e, Pin.IN)) for n, t, e in SONAR]
dist = [-1] * len(sonar)
sonar_i = 0


def ping(trig, echo):
    trig.value(1)
    time.sleep_us(10)
    trig.value(0)
    t = time_pulse_us(echo, 1, SONAR_TIMEOUT_US)
    return -1 if t < 0 else round(t / 58.0)


# ---- state ----
target_v = {"L": 0.0, "R": 0.0}
cur_v = {"L": 0.0, "R": 0.0}
target_a = {k: 0.0 for k in SERVO_ORDER}
cur_a = {k: 0.0 for k in SERVO_ORDER}
estop = False
started = False
clamped = False
last_cmd = time.ticks_ms()
buf = ""
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)


def reply(s):
    sys.stdout.write(s + "\n")


def handle(line):
    global estop, started, clamped, last_cmd
    f = line.split(",")
    if f[0] == "C" and len(f) == 7:
        try:
            v = [float(x) for x in f[1:3]]
            a = [float(x) for x in f[3:7]]
        except ValueError:
            reply("X,parse")
            return
        clamped = False
        for k, x in zip(("L", "R"), v):
            y = max(-1.0, min(1.0, x))
            clamped |= y != x
            target_v[k] = y
        for k, x in zip(SERVO_ORDER, a):
            lo, hi = SERVO[k][2], SERVO[k][3]
            y = max(lo, min(hi, x))
            clamped |= y != x
            target_a[k] = y
        started = True
        last_cmd = time.ticks_ms()
    elif f[0] == "E":
        estop = True
    elif f[0] == "R":
        estop = False
        last_cmd = time.ticks_ms()
    else:
        reply("X,unknown")


def step(cur, tgt, max_d):
    d = tgt - cur
    return tgt if abs(d) <= max_d else cur + (max_d if d > 0 else -max_d)


all_off()
next_loop = time.ticks_ms()
next_telem = next_loop
while True:
    # read whatever arrived, line by line (USB packets may split or join lines)
    while poll.poll(0):
        ch = sys.stdin.read(1)
        if ch == "\n":
            if buf:
                handle(buf.strip())
            buf = ""
        elif len(buf) < 120:
            buf += ch

    now = time.ticks_ms()
    if time.ticks_diff(now, next_loop) >= 0:
        next_loop = time.ticks_add(next_loop, LOOP_MS)
        timeout = started and time.ticks_diff(now, last_cmd) > LINK_TIMEOUT_MS
        if estop or not started:
            for k in cur_v:
                cur_v[k] = 0.0
            all_off()
        else:
            dt = LOOP_MS / 1000
            for k in cur_v:
                cur_v[k] = step(cur_v[k], 0.0 if timeout else target_v[k], DUTY_SLEW * dt)
                motor_out(k, cur_v[k])
            for k in SERVO_ORDER:
                cur_a[k] = step(cur_a[k], 0.0 if timeout else target_a[k], ANGLE_SLEW * dt)
                servo_out(k, cur_a[k])

    if time.ticks_diff(now, next_telem) >= 0:
        next_telem = time.ticks_add(next_telem, 1000 // TELEM_HZ)
        _, t, e = sonar[sonar_i]
        dist[sonar_i] = ping(t, e)
        sonar_i = (sonar_i + 1) % len(sonar)
        flags = ((1 if (started and time.ticks_diff(now, last_cmd) > LINK_TIMEOUT_MS) else 0)
                 | (2 if estop else 0) | (4 if clamped else 0) | (8 if not started else 0))
        reply("S,%d,%d,%d,%d,%d,%d,%d,%d" % (now, enc["L"], enc["R"], dist[0], dist[1], dist[2], dist[3], flags))
