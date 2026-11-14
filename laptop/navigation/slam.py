"""2D LiDAR SLAM (navigation / mapping, laptop): occupancy grid + correlative scan-to-map matching.

Input per scan: the robot's odom pose at the scan and 360 ranges in the robot frame (1 deg bins,
CCW from +x, mm, 0 = no return). Output: the map pose of the robot and the map <- odom
correction that the robot uses to follow map-frame paths.

No loop closure: drift that the scan-to-map match cannot see accumulates. That is fine for a
room or two; see docs/AUTONOMY.md for when to switch to slam_toolbox.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

ANGLES = np.deg2rad(np.arange(360, dtype=np.float64))


def wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


def compose(a, b):
    """a * b for (x, y, yaw) tuples."""
    c, s = math.cos(a[2]), math.sin(a[2])
    return (a[0] + c * b[0] - s * b[1], a[1] + s * b[0] + c * b[1], wrap(a[2] + b[2]))


def inverse(a):
    c, s = math.cos(a[2]), math.sin(a[2])
    return (-c * a[0] - s * a[1], s * a[0] - c * a[1], wrap(-a[2]))


@dataclass
class SlamConfig:
    resolution: float = 0.05        # m per cell
    size_m: float = 40.0            # square map, robot starts in the middle
    max_range: float = 8.0          # m, longer returns are not used (C1 is noisy far out)
    min_range: float = 0.15
    l_occ: float = 0.85
    l_free: float = -0.7            # one pass makes a cell free (p 0.33)
    l_clamp: float = 4.0
    sigma: float = 0.05             # m, likelihood field width
    min_score: float = 0.5          # mean field value to accept a match
    insert_dist: float = 0.10       # m moved since the last map update
    insert_angle: float = math.radians(5.0)
    coarse_xy: float = 0.15         # +- search window (m) around the odom prior
    coarse_xy_step: float = 0.05
    coarse_th: float = math.radians(7.5)
    coarse_th_step: float = math.radians(2.5)
    fine_xy: float = 0.03
    fine_xy_step: float = 0.0075
    fine_th: float = math.radians(1.25)
    fine_th_step: float = math.radians(0.125)
    match_points: int = 180         # scan points used for matching (subsampled)


@dataclass
class SlamResult:
    pose: tuple                     # map frame
    correction: tuple               # map <- odom
    matched: bool
    score: float
    inserted: bool


@dataclass
class ScanSlam:
    config: SlamConfig = field(default_factory=SlamConfig)
    use_matching: bool = True       # False = odometry-only mapping (for comparison)

    def __post_init__(self) -> None:
        c = self.config
        self.cells = int(round(c.size_m / c.resolution))
        self.origin = -c.size_m / 2.0                 # world coordinate of cell 0 edge
        self.logodds = np.zeros((self.cells, self.cells), np.float32)   # [iy, ix]
        self.field = np.zeros_like(self.logodds)
        self.correction = (0.0, 0.0, 0.0)
        self.pose = (0.0, 0.0, 0.0)
        self.trajectory: list[tuple] = []
        self.scans_inserted = 0
        self.matches = 0
        self.rejected_matches = 0
        self._last_insert: tuple | None = None
        self._misses_in_row = 0

    # ---- grid helpers
    def to_cell(self, x, y):
        r = self.config.resolution
        return (np.floor((np.asarray(x) - self.origin) / r).astype(np.int64),
                np.floor((np.asarray(y) - self.origin) / r).astype(np.int64))

    def cell_center(self, ix, iy):
        r = self.config.resolution
        return (self.origin + (np.asarray(ix) + 0.5) * r, self.origin + (np.asarray(iy) + 0.5) * r)

    def probability(self) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.logodds))

    def known_bounds(self, margin: int = 10):
        """(iy0, iy1, ix0, ix1) of the explored area, or None."""
        known = np.abs(self.logodds) > 0.05
        ys, xs = np.nonzero(known)
        if len(xs) == 0:
            return None
        return (max(0, ys.min() - margin), min(self.cells, ys.max() + margin + 1),
                max(0, xs.min() - margin), min(self.cells, xs.max() + margin + 1))

    # ---- scan handling
    def points(self, ranges_mm) -> np.ndarray:
        r = np.asarray(ranges_mm, np.float64) / 1000.0
        ok = (r >= self.config.min_range) & (r <= self.config.max_range)
        return np.stack([r[ok] * np.cos(ANGLES[ok]), r[ok] * np.sin(ANGLES[ok])], axis=1)

    def _score(self, pts: np.ndarray, pose) -> np.ndarray:
        """Scores of a batch of candidate poses (K, 3) for points (N, 2). Mean field value."""
        poses = np.atleast_2d(pose)
        c, s = np.cos(poses[:, 2])[:, None], np.sin(poses[:, 2])[:, None]
        wx = poses[:, 0:1] + c * pts[None, :, 0] - s * pts[None, :, 1]
        wy = poses[:, 1:2] + s * pts[None, :, 0] + c * pts[None, :, 1]
        # Bilinear interpolation between cell centres: sub-cell (and sub-degree) resolution.
        r = self.config.resolution
        fx = (wx - self.origin) / r - 0.5
        fy = (wy - self.origin) / r - 0.5
        x0 = np.floor(fx).astype(np.int64)
        y0 = np.floor(fy).astype(np.int64)
        ax, ay = fx - x0, fy - y0
        n = self.cells
        inside = (x0 >= 0) & (x0 < n - 1) & (y0 >= 0) & (y0 < n - 1)
        x0 = np.clip(x0, 0, n - 2)
        y0 = np.clip(y0, 0, n - 2)
        f = self.field
        v = ((1 - ax) * (1 - ay) * f[y0, x0] + ax * (1 - ay) * f[y0, x0 + 1]
             + (1 - ax) * ay * f[y0 + 1, x0] + ax * ay * f[y0 + 1, x0 + 1])
        return np.where(inside, v, 0.0).mean(axis=1)

    def _search(self, pts, prior, xy, xy_step, th, th_step, penalty_weight):
        offs = np.arange(-xy, xy + 1e-9, xy_step)
        ths = np.arange(-th, th + 1e-9, th_step)
        gx, gy, gt = np.meshgrid(offs, offs, ths, indexing="ij")
        cand = np.stack([prior[0] + gx.ravel(), prior[1] + gy.ravel(), prior[2] + gt.ravel()], 1)
        # Mild preference for the prior so flat scores (corridors) do not wander.
        penalty = penalty_weight * (np.hypot(gx.ravel(), gy.ravel()) / max(xy, 1e-9) + np.abs(gt.ravel()) / max(th, 1e-9))
        scores = self._score(pts, cand) - penalty
        best = int(np.argmax(scores))
        return tuple(cand[best]), float(scores[best] + penalty[best])

    def match(self, pts, prior):
        c = self.config
        coarse, _ = self._search(pts, prior, c.coarse_xy, c.coarse_xy_step, c.coarse_th,
                                 c.coarse_th_step, 0.01)
        return self._search(pts, coarse, c.fine_xy, c.fine_xy_step, c.fine_th, c.fine_th_step, 0.0)

    def insert(self, pose, ranges_mm) -> None:
        c = self.config
        r = np.asarray(ranges_mm, np.float64) / 1000.0
        hit = (r >= c.min_range) & (r <= c.max_range)
        # Free space: along every hit ray up to just before the hit; no-return rays are skipped.
        step = c.resolution * 0.7
        t = np.arange(0.0, c.max_range, step)
        a = ANGLES[hit] + pose[2]
        rr = r[hit]
        mask = t[None, :] < (rr[:, None] - c.resolution)
        fx = pose[0] + (t[None, :] * np.cos(a)[:, None])[mask]
        fy = pose[1] + (t[None, :] * np.sin(a)[:, None])[mask]
        ix, iy = self.to_cell(fx, fy)
        free = np.zeros_like(self.logodds, bool)
        ok = (ix >= 0) & (ix < self.cells) & (iy >= 0) & (iy < self.cells)
        free[iy[ok], ix[ok]] = True
        hx = pose[0] + rr * np.cos(a)
        hy = pose[1] + rr * np.sin(a)
        ix, iy = self.to_cell(hx, hy)
        occ = np.zeros_like(free)
        ok = (ix >= 0) & (ix < self.cells) & (iy >= 0) & (iy < self.cells)
        occ[iy[ok], ix[ok]] = True
        free &= ~occ
        self.logodds[free] += c.l_free
        self.logodds[occ] += c.l_occ
        np.clip(self.logodds, -c.l_clamp, c.l_clamp, out=self.logodds)
        self._update_field()
        self.scans_inserted += 1
        self._last_insert = pose

    def _update_field(self) -> None:
        occupied = (self.logodds > 0.5).astype(np.uint8)
        if not occupied.any():
            self.field[:] = 0.0
            return
        dist = cv2.distanceTransform((1 - occupied).astype(np.uint8), cv2.DIST_L2, 5)
        d = dist * self.config.resolution
        self.field = np.exp(-(d * d) / (2.0 * self.config.sigma ** 2)).astype(np.float32)

    def process(self, odom_pose, ranges_mm) -> SlamResult:
        odom_pose = tuple(float(v) for v in odom_pose)
        prior = compose(self.correction, odom_pose)
        pts = self.points(ranges_mm)
        matched, score, pose = False, 0.0, prior
        if self.use_matching and self.scans_inserted > 0 and len(pts) >= 30:
            step = max(1, len(pts) // self.config.match_points)
            cand, score = self.match(pts[::step], prior)
            if score >= self.config.min_score:
                pose, matched = cand, True
                self.matches += 1
                self._misses_in_row = 0
            else:
                self.rejected_matches += 1
                self._misses_in_row += 1
        self.pose = (pose[0], pose[1], wrap(pose[2]))
        self.correction = compose(self.pose, inverse(odom_pose))
        self.trajectory.append(self.pose)

        inserted = False
        last = self._last_insert
        if len(pts) >= 30 and (
            last is None
            or math.hypot(self.pose[0] - last[0], self.pose[1] - last[1]) > self.config.insert_dist
            or abs(wrap(self.pose[2] - last[2])) > self.config.insert_angle
        ):
            # Only trust a scan for the map when it matched (or it is the very first one).
            # After many misses in a row (new area, little overlap) fall back to odometry.
            if matched or last is None or not self.use_matching or self._misses_in_row >= 10:
                self.insert(self.pose, ranges_mm)
                inserted = True
        return SlamResult(self.pose, self.correction, matched, score, inserted)
