"""Keyboard drive through the Pico firmware (run on the Pi). ssh -t rover@<pi> "python3 ~/pico/pico_teleop.py"

Keys (drive only while held, release = stop)
  w / s     forward / backward          - / =   duty -0.1 / +0.1 (default 0.3, max 0.6)
  a / d     steer left / right 5 deg    c       steer straight
  m         steering mode: front only / front+rear opposite (tighter turn)
  space     stop                        e / r   e-stop / clear
  q         quit (stop, then the Pico link timeout returns steering to neutral)
Shows the Pico telemetry line (encoders, ultrasonic cm, flags).
"""
import curses
import time

import serial

PORT, BAUD = "/dev/ttyACM0", 115200
SEND_HZ = 20
RELEASE_S = 0.6   # SSH key auto-repeat starts after ~0.5 s
STEER_MAX = 30


def main(scr):
    curses.curs_set(0)
    scr.timeout(1000 // SEND_HZ)
    s = serial.Serial(PORT, BAUD, timeout=0)
    duty, steer, mode, drive, last_drive = 0.3, 0, 0, 0, 0.0
    rx, telem = b"", "(no telemetry yet)"
    try:
        while True:
            k = scr.getch()
            now = time.time()
            if k in (ord("w"), ord("s")):
                drive, last_drive = (1 if k == ord("w") else -1), now
            elif k == ord(" "):
                drive = 0
            elif k == ord("a"):
                steer = max(-STEER_MAX, steer - 5)
            elif k == ord("d"):
                steer = min(STEER_MAX, steer + 5)
            elif k == ord("c"):
                steer = 0
            elif k == ord("m"):
                mode ^= 1
            elif k == ord("-"):
                duty = max(0.1, round(duty - 0.1, 1))
            elif k == ord("="):
                duty = min(0.6, round(duty + 0.1, 1))
            elif k == ord("e"):
                s.write(b"E\n")
            elif k == ord("r"):
                s.write(b"R\n")
            elif k == ord("q"):
                break
            if drive and now - last_drive > RELEASE_S:
                drive = 0
            v = drive * duty
            rear = -steer if mode else 0
            s.write(("C,%.2f,%.2f,%d,%d,%d,%d\n" % (v, v, steer, steer, rear, rear)).encode())

            rx += s.read(4096)
            while b"\n" in rx:
                line, rx = rx.split(b"\n", 1)
                line = line.decode(errors="replace").strip()
                if line.startswith("S,") or line.startswith("X,"):
                    telem = line
            scr.erase()
            scr.addstr(0, 0, "drive %+d  duty %.1f  steer %+d  mode %s" % (
                drive, duty, steer, "front+rear" if mode else "front"))
            scr.addstr(2, 0, "S,ms,encL,encR,FL,FR,RL,RR,flags")
            scr.addstr(3, 0, telem[:scr.getmaxyx()[1] - 1])
            scr.addstr(5, 0, "w/s drive  a/d steer  c center  m mode  -/= duty  space stop  e/r estop  q quit")
    finally:
        s.write(b"C,0,0,0,0,0,0\n")
        s.close()


curses.wrapper(main)
