"""Frontier exploration loop (navigation, laptop).

    scan (robot) -> ScanSlam -> pose_correction (robot)
    telemetry    -> follow state / control mode
    tick (2 Hz)  -> frontiers (ids) -> selector picks one id -> path (robot Pure Pursuit)

The selector is the hook for the LLM goal choice (mission block): it gets the frontier list and
returns an id. An id that is not in the list is rejected and the nearest frontier is used.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from laptop.communication.messages import make_path, make_pose_correction

from .planner import Frontier, Plan, PlannerConfig, build_plan, nearest_frontier, path_to
from .slam import ScanSlam

Selector = Callable[[list[Frontier], dict[str, Any]], int | None]


@dataclass
class ExplorerConfig:
    speed: float = 0.10                 # m/s sent with each path (robot caps it)
    replan_period: float = 4.0          # s, replan while following (map grows)
    correction_period: float = 0.2      # s between pose_correction messages
    min_goal_distance: float = 0.6      # m, closer frontiers are ignored
    blacklist_radius: float = 0.5       # m
    max_attempts: int = 3               # blocked / failed tries before a frontier is dropped
    telemetry_timeout: float = 1.0


@dataclass
class ExplorerStatus:
    active: bool = False
    done: bool = False
    note: str = ""
    goal: Frontier | None = None
    path: list[tuple] = field(default_factory=list)
    frontiers: list[Frontier] = field(default_factory=list)
    ai_goal: dict[str, Any] | None = None


class Explorer:
    def __init__(self, slam: ScanSlam, send: Callable[[dict], bool],
                 config: ExplorerConfig | None = None, planner: PlannerConfig | None = None,
                 selector: Selector | None = None) -> None:
        self.slam = slam
        self.send = send
        self.config = config or ExplorerConfig()
        self.planner = planner or PlannerConfig()
        self.selector = selector
        self.status = ExplorerStatus()
        self.lock = threading.Lock()
        self.telemetry: dict[str, Any] = {}
        self._telemetry_at = 0.0
        self._last_correction = 0.0
        self._last_plan = 0.0
        self._path_id = 0
        self.goal_history = []
        self._last_goal_result = None
        self._attempts: list[tuple[float, float, int]] = []   # x, y, count
        self._last_follow_seen: tuple[int, str] | None = None
        self.last_result = None

    # ---- inputs (called from the communication threads)
    def on_scan(self, message: dict) -> None:
        with self.lock:
            self.last_result = self.slam.process(message["odom_pose"], message["ranges_mm"])
            now = time.monotonic()
            correction = self.slam.correction
        if now - self._last_correction >= self.config.correction_period:
            self._last_correction = now
            self.send(make_pose_correction(*correction))

    def on_telemetry(self, message: dict) -> None:
        with self.lock:
            self.telemetry = message
            self._telemetry_at = time.monotonic()

    # ---- control
    def start(self) -> None:
        self.status.active, self.status.done, self.status.note = True, False, "started"
        self._last_plan = 0.0

    def pause(self, note: str = "paused") -> None:
        self.status.active = False
        if hasattr(self.selector, "invalidate"): self.selector.invalidate()
        self.status.note = note
        self.status.goal = None
        self.status.path = []
        self.status.ai_goal = None
        self._path_id += 1
        self.send(make_path([], self._path_id))

    # ---- frontier bookkeeping
    def _attempt_count(self, x: float, y: float) -> int:
        for ax, ay, n in self._attempts:
            if math.hypot(ax - x, ay - y) < self.config.blacklist_radius:
                return n
        return 0

    def _note_attempt(self, x: float, y: float) -> None:
        for i, (ax, ay, n) in enumerate(self._attempts):
            if math.hypot(ax - x, ay - y) < self.config.blacklist_radius:
                self._attempts[i] = (ax, ay, n + 1)
                return
        self._attempts.append((x, y, 1))

    def _robot_ready(self) -> tuple[bool, str]:
        t = self.telemetry
        if time.monotonic() - self._telemetry_at > self.config.telemetry_timeout:
            return False, "no telemetry"
        if t.get("estop"):
            return False, "robot e-stop"
        if t.get("control_mode") != "autonomous":
            return False, "robot in manual mode (Start on the pad / --autostart)"
        return True, ""

    def tick(self) -> None:
        """Call at ~2 Hz from the main loop."""
        if not self.status.active:
            return
        with self.lock:
            ready, why = self._robot_ready()
            follow = self.telemetry.get("follow", {})
        if not ready:
            if self.status.path: self.pause(why)
            else: self.status.note = why
            return
        state = follow.get("state", "idle")
        path_id = follow.get("path_id", -1)
        now = time.monotonic()

        current = path_id == self._path_id and self.status.goal is not None
        if current and state in ('reached','blocked','no_scan') and self._last_goal_result != (path_id,state):
            self._last_goal_result=(path_id,state)
            self.goal_history.append({'x':self.status.goal.x,'y':self.status.goal.y,'state':state})
            self.goal_history=self.goal_history[-20:]
        if current and state in ("blocked", "no_scan"):
            if (path_id, state) != self._last_follow_seen:
                self._note_attempt(self.status.goal.x, self.status.goal.y)
            self._last_follow_seen = (path_id, state)
        need = (not current or state in ("reached", "idle", "blocked")
                or now - self._last_plan >= self.config.replan_period)
        if not need:
            return
        if current and state == "blocked" and now - self._last_plan < 1.5:
            return  # give the LiDAR guard a moment (person walking past)
        self._plan(now)

    def _plan(self, now: float) -> None:
        with self.lock:
            pose = self.slam.pose
            plan: Plan | None = build_plan(self.slam, pose, self.planner)
            if plan is None:
                self.status.note = "no map yet"
                return
            candidates = [f for f in plan.frontiers
                          if f.distance >= self.config.min_goal_distance
                          and self._attempt_count(f.x, f.y) < self.config.max_attempts]
            self.status.frontiers = candidates
            if not candidates:
                self.status.done = True
                self.status.active = False
                self.status.goal = None
                self.status.ai_goal = None
                self.status.note = "exploration done (no reachable frontier)"
                self._path_id += 1
                self.send(make_path([], self._path_id))
                return
            choice = None
            ai_goal = None
            old = self.status.goal
            follow_state = self.telemetry.get('follow',{}).get('state')
            if old is not None and follow_state not in ('reached','blocked','idle','no_scan'):
                same = min(candidates,key=lambda f:math.hypot(f.x-old.x,f.y-old.y))
                if math.hypot(same.x-old.x,same.y-old.y) < .4:
                    choice = same.id
                    ai_goal = self.status.ai_goal
            if choice is None and self.selector is not None:
                try:
                    choose = getattr(self.selector,'choose',self.selector)
                    choice = choose(candidates, {'pose':pose,
                        'attempts':{f.id:self._attempt_count(f.x,f.y) for f in candidates},'goal_results':list(self.goal_history)})
                except Exception as error:
                    self.status.note = f"selector error: {error}"
                if choice not in {f.id for f in candidates}: choice = None
                selection = getattr(self.selector, 'last_selection', None)
                if selection is not None and selection['candidate_id'] == choice:
                    ai_goal = selection
                if choice is None and getattr(self.selector,'strict',False):
                    self.status.note = self.selector.status
                    self.status.ai_goal = None
                    if self.status.path:
                        self.status.path=[]; self.status.goal=None
                        self._path_id+=1; self.send(make_path([],self._path_id))
                    return
            if choice is None: choice = nearest_frontier(candidates,self.planner)
            goal = next(f for f in candidates if f.id == choice)
            points = path_to(plan, self.slam, (goal.x, goal.y), self.planner)
        self._last_plan = now
        if not points or len(points) < 2:
            self._note_attempt(goal.x, goal.y)
            self.status.note = f"no path to frontier {goal.id}"
            return
        self._path_id += 1
        self.status.goal = goal
        self.status.path = points
        self.status.note = (f"frontier {goal.id}/{len(candidates)} "
                            f"cost {goal.distance:.1f}, {len(points)} pts")
        sent = self.send(make_path(points, self._path_id, self.config.speed))
        self.status.ai_goal = ({**ai_goal, 'candidate_id': goal.id, 'x': goal.x, 'y': goal.y}
                               if ai_goal and sent else None)
