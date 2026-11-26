"""Operator dashboard for the laptop exploration program."""
from functools import lru_cache
import os
from pathlib import Path
import textwrap

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


SIZE = (1180, 860)
BACKGROUND = "#101720"
PANEL = "#18232E"
EDGE = "#344554"
INSET = "#0C141C"
WHITE = "#F3F7FA"
MUTED = "#B1C2D1"
TEAL = "#8FDED0"
AMBER = "#FFCC80"
RED = "#FFB6BE"

# Draw and hit-test the same rectangles.
BUTTONS = (
    ("manual", "Manual", (562, 26, 682, 72)),
    ("resume", "Start auto", (694, 26, 826, 72)),
    ("pause", "Pause", (838, 26, 965, 72)),
    ("estop", "E-STOP", (977, 26, 1156, 72)),
)
PICO_FLAGS = ((1, "link timeout"), (2, "E-stop"), (4, "clamped"), (8, "no command yet"))

CAMERA_BOUNDS = (24, 200, 632, 600)
MAP_BOUNDS = (648, 200, 1156, 600)


@lru_cache(maxsize=16)
def _font(size, bold=False):
    name = "seguisb.ttf" if bold else "segoeui.ttf"
    windows = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / name
    for face in (str(windows), "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(face, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


def _text(draw, xy, text, size=16, color=WHITE, *, bold=False, anchor="lt"):
    draw.text(xy, str(text), fill=color, font=_font(size, bold), anchor=anchor)


def _summary(draw, xy, text, width, *, size=16, lines=1, color=MUTED):
    font = _font(size)
    chunks = textwrap.wrap(str(text), width=max(10, width // (size // 2)),
                           replace_whitespace=True) or [""]
    for index, chunk in enumerate(chunks[:lines]):
        overflow = index == lines - 1 and len(chunks) > lines
        if overflow or font.getlength(chunk) > width:
            while chunk and font.getlength(chunk.rstrip() + "...") > width:
                chunk = chunk[:-1]
            chunk = chunk.rstrip() + "..."
        _text(draw, (xy[0], xy[1] + index * (size + 7)), chunk, size, color)


def _card(draw, box):
    draw.rounded_rectangle(box, radius=14, fill=PANEL, outline=EDGE, width=1)


def _indicator(draw, box, name, value, color):
    draw.rounded_rectangle(box, radius=9, fill=INSET)
    draw.ellipse((box[0] + 10, box[1] + 14, box[0] + 17, box[1] + 21), fill=color)
    _text(draw, (box[0] + 24, box[1] + 11), name, 13, MUTED)
    _text(draw, (box[0] + 12, box[1] + 32), value, 16, color, bold=True)


def _media(canvas, draw, box, title, caption, image, empty):
    _card(draw, box)
    x0, y0, x1, y1 = box
    _text(draw, (x0 + 16, y0 + 16), title, 19, bold=True)
    _text(draw, (x1 - 16, y0 + 18), caption, 14, MUTED, anchor="rt")
    left, top, right, bottom = x0 + 12, y0 + 51, x1 - 12, y1 - 12
    draw.rectangle((left, top, right, bottom), fill=INSET)
    if image is None:
        _text(draw, ((left + right) // 2, (top + bottom) // 2), empty, 17, MUTED, anchor="mm")
        return
    height, width = image.shape[:2]
    scale = min((right - left + 1) / width, (bottom - top + 1) / height)
    view = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))),
                      interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas.paste(Image.fromarray(cv2.cvtColor(view, cv2.COLOR_BGR2RGB)),
                 ((left + right + 1 - view.shape[1]) // 2, (top + bottom + 1 - view.shape[0]) // 2))


class Dashboard:
    def __init__(self):
        self.action = None

    def mouse(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            for action, _, (left, top, right, bottom) in BUTTONS:
                if left <= x <= right and top <= y <= bottom:
                    self.action = action
                    break

    def pop_action(self):
        value, self.action = self.action, None
        return value

    def draw(self, map_image, frame, explorer, selector, guard, llm, vision, mission=None):
        mission = mission or {}
        telemetry = explorer.telemetry
        mode = telemetry.get("control_mode")
        status = telemetry.get("safety_state", "")
        follow = telemetry.get("follow", {}).get("state", "")
        sensor_mode = mission.get("sensor_mode", "WAIT_CAMERA" if vision else "VISION_OFF")
        sensor_reason = mission.get("sensor_reason", guard.reason if vision else "Person detector is off")
        hold = mission.get("hold", False)
        blocked = status == "blocked" or follow in ("blocked", "no_scan")
        estop = telemetry.get("estop", False)
        link_ok = mission.get("rover_link_ok", bool(telemetry))
        lidar_ok = mission.get("lidar_fresh", telemetry.get("sensors", {}).get("lidar", False))
        limit = mission.get("speed_limit_mps")
        alert = mission.get("person_alert")
        decision = mission.get("qwen_decision")

        canvas = Image.new("RGB", SIZE, BACKGROUND)
        draw = ImageDraw.Draw(canvas)
        _text(draw, (24, 20), "RoverAid", 32, bold=True)
        subtitle = "Offline rehearsal" if mission.get("rehearsal") else "Operator console"
        _text(draw, (25, 59), subtitle, 15, MUTED)
        for action, label, box in BUTTONS:
            fill, edge, ink = PANEL, EDGE, WHITE
            if action == "estop":
                fill, edge = "#722B3B", "#EA778D"
            elif action == "resume":
                fill, edge, ink = "#91DFD0", "#91DFD0", "#102A29"
            elif action == "manual" and mode == "manual" and link_ok:
                edge = TEAL
            draw.rounded_rectangle(box, radius=9, fill=fill, outline=edge)
            center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
            _text(draw, center, label, 18, ink, bold=True, anchor="mm")

        _card(draw, (24, 94, 1156, 184))
        heading, color = sensor_mode.replace("_", " ").title().replace("Lidar", "LiDAR"), TEAL
        if estop:
            heading, color = "Emergency stop", RED
        elif hold:
            heading, color = "Protection hold", RED
        elif blocked or not link_ok or not lidar_ok:
            heading, color = ("Path blocked" if blocked else "Waiting for rover" if not link_ok
                              else "Waiting for LiDAR"), AMBER
        elif sensor_mode != "VISION_SEARCH":
            color = AMBER
        cause = sensor_reason
        if estop:
            cause = "Check the course before selecting a driving mode."
        elif hold:
            cause = guard.reason if guard.latched else "The rover reports a latched protection stop."
        elif blocked:
            cause = "The rover stopped path following. Check LiDAR and path clearance."
        elif not link_ok:
            cause = "Waiting for current rover telemetry."
        elif not lidar_ok:
            cause = "Waiting for a fresh LiDAR scan."
        _text(draw, (43, 111), heading, 24, color, bold=True)
        _summary(draw, (43, 148), cause, 650)
        _indicator(draw, (730, 108, 852, 170), "ROVER LINK",
                   "Connected" if link_ok else "Waiting", TEAL if link_ok else AMBER)
        _indicator(draw, (863, 108, 985, 170), "LIDAR",
                   "Fresh" if lidar_ok else "Waiting", TEAL if lidar_ok else AMBER)
        _indicator(draw, (996, 108, 1140, 170), "SPEED LIMIT",
                   f"{limit:.2f} m/s" if limit is not None else "-", WHITE)

        if frame is not None and vision and sensor_mode == "VISION_SEARCH":
            frame = frame.copy()
            h, w = frame.shape[:2]
            a, b, c, d = guard.config.roi
            zone_color = (113, 112, 237) if guard.level == "hold" else (177, 179, 94)
            cv2.rectangle(frame, (int(a * w), int(b * h)), (int(c * w), min(h - 1, int(d * h))), zone_color, 1)
        if not vision:
            camera_label = "Off"
        elif sensor_mode == "LIDAR_ONLY":
            camera_label = "Person search paused"
        else:
            camera_label = "YOLO Pose" if frame is not None else "Waiting"
        _media(canvas, draw, CAMERA_BOUNDS, "Front camera", camera_label, frame,
               "Waiting for camera frames" if vision else "Camera disabled")
        map_caption = f"Qwen target F{decision['candidate_id']}" if decision else "Frontiers and route"
        _media(canvas, draw, MAP_BOUNDS, "LiDAR map", map_caption, map_image,
               "Waiting for a map")

        _card(draw, (24, 616, 440, 820))
        _text(draw, (40, 633), "Qwen decision" if llm else "Mission", 19, bold=True)
        if decision:
            chosen = decision["model_candidate_id"]
            _text(draw, (40, 664), f"Selected F{chosen}", 28, TEAL, bold=True)
            _summary(draw, (40, 705), decision["reason"], 384, size=17, lines=2, color=WHITE)
            current = decision["candidate_id"]
            verdict = f"{decision['candidates_reviewed']} candidates  ·  {decision['seconds']:.1f} s"
            if current != chosen:
                verdict += f"  ·  Map target F{current}"
            _summary(draw, (40, 769), verdict, 384, size=15)
        else:
            ai_status = selector.status if selector else ("Waiting for Qwen" if llm else "Rule-based exploration")
            _summary(draw, (40, 667), ai_status, 384, size=18, lines=2, color=WHITE)
            _text(draw, (40, 744), "NAVIGATION", 12, MUTED, bold=True)
            _summary(draw, (40, 765), explorer.status.note or "Awaiting operator", 384, lines=2)

        _card(draw, (456, 616, 798, 820))
        _text(draw, (472, 633), "Rover telemetry", 19, bold=True)
        if telemetry:
            duty = telemetry.get("duty") or [0, 0]
            pico = telemetry.get("pico") or {}
            flags = pico.get("flags", 0) or 0
            pico_text = ", ".join(label for bit, label in PICO_FLAGS if flags & bit) or "OK"
            encoders = telemetry.get("encoders") or []
            counts = f"{encoders[0]} / {encoders[1]}" if len(encoders) >= 2 else "-"
            values = (
                ("Drive mode", (mode or "-").title()),
                ("Path follower", (follow or "-").replace("_", " ").title()),
                ("Motor duty L / R", f"{duty[0]:+.2f} / {duty[1]:+.2f}"),
                ("Pico", pico_text if pico.get("ok") else "Waiting"),
                ("Encoder L / R", counts),
                ("Command age", f"{telemetry.get('last_command_age', 0):.2f} s"),
            )
            for i, (label, value) in enumerate(values):
                y = 664 + i * 23
                _text(draw, (472, y), label, 14, MUTED)
                _summary(draw, (606, y), value, 176, size=14, color=WHITE)
        else:
            _text(draw, (472, 668), "Waiting for telemetry", 16, MUTED)

        _card(draw, (814, 616, 1156, 820))
        _text(draw, (830, 633), "Last person alert", 19, bold=True)
        if alert:
            draw.rounded_rectangle((830, 665, 1140, 734), radius=8, fill="#20443D")
            _text(draw, (842, 674), "PERSON FOUND", 17, TEAL, bold=True)
            posture = alert["posture"].replace("_", " ").title()
            _text(draw, (842, 704), f"{posture} · {alert['image_side'].title()}", 15, WHITE)
            _text(draw, (830, 750), f"Track {alert['track_id']}  ·  Confidence {alert['confidence']:.0%}", 15, MUTED)
        else:
            _text(draw, (830, 670), "Waiting for a confirmed detection" if vision else "Person detector disabled",
                  16, MUTED)
        if mode == "manual":
            _text(draw, (830, 784), "Hold LB + left stick to drive", 15, WHITE)

        _text(draw, (24, 833), "M  Manual     Space  Start / pause     E or Back  Emergency stop     Q  Quit",
              14, MUTED)
        return cv2.cvtColor(np.asarray(canvas), cv2.COLOR_RGB2BGR)
