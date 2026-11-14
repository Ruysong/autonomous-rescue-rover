"""Planning (navigation, laptop): costmap, frontier candidates with ids, shortest paths.

One Dijkstra from the robot over the known free space gives both: which frontiers are reachable
(and how far) and the path to any of them. The search runs on the explored part of the grid
only, so it stays fast in pure Python.

The planner ignores the minimum turning radius (~0.77 m). The robot's Pure Pursuit smooths the
path and backs up when the next point is behind it.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import cv2
import numpy as np

from .slam import ScanSlam

SQRT2 = math.sqrt(2.0)


@dataclass
class PlannerConfig:
    robot_radius: float = 0.40       # m, inflation (half width 0.35 + margin)
    clearance: float = 0.70          # m, prefer staying this far from walls
    clearance_weight: float = 4.0
    occ_threshold: float = 0.65
    free_threshold: float = 0.40
    min_frontier_cells: int = 8
    start_free_radius: float = 0.35  # m, robot cell neighbourhood treated as passable
    waypoint_spacing: float = 0.15   # m between path points sent to the robot
    heading_weight: float = 0.6      # m of path cost per rad of turn needed at the start


@dataclass
class Frontier:
    id: int
    x: float
    y: float
    size: int                        # cells
    distance: float = math.inf       # path length (m) from the robot
    bearing: float = 0.0             # rad, relative to the robot heading


@dataclass
class Plan:
    frontiers: list[Frontier]
    dist: np.ndarray                 # path cost from the robot, cropped grid
    parent: np.ndarray
    crop: tuple                      # (iy0, iy1, ix0, ix1)
    passable: np.ndarray
    start: tuple                     # (iy, ix) in the crop


def _disk(radius_cells: int) -> np.ndarray:
    k = 2 * radius_cells + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def build_plan(slam: ScanSlam, robot_pose, config: PlannerConfig | None = None) -> Plan | None:
    config = config or PlannerConfig()
    bounds = slam.known_bounds(margin=int(config.clearance / slam.config.resolution) + 2)
    if bounds is None:
        return None
    iy0, iy1, ix0, ix1 = bounds
    res = slam.config.resolution
    p = slam.probability()[iy0:iy1, ix0:ix1]
    occ = p > config.occ_threshold
    free = p < config.free_threshold
    unknown = ~occ & ~free

    inflated = cv2.dilate(occ.astype(np.uint8), _disk(int(math.ceil(config.robot_radius / res)))) > 0
    passable = free & ~inflated
    sx, sy = slam.to_cell(robot_pose[0], robot_pose[1])
    start = (int(sy) - iy0, int(sx) - ix0)
    if not (0 <= start[0] < p.shape[0] and 0 <= start[1] < p.shape[1]):
        return None
    # The robot may sit inside the inflation near a wall: let it leave.
    r = int(math.ceil(config.start_free_radius / res))
    yy, xx = np.ogrid[:p.shape[0], :p.shape[1]]
    near_start = (yy - start[0]) ** 2 + (xx - start[1]) ** 2 <= r * r
    passable |= near_start & ~occ

    wall_dist = cv2.distanceTransform((~occ).astype(np.uint8), cv2.DIST_L2, 5) * res
    extra = config.clearance_weight * np.clip(config.clearance - wall_dist, 0.0, None)
    dist, parent = _dijkstra(passable, extra, start, res)

    # Frontiers: passable cells next to unknown space.
    unknown_near = cv2.dilate(unknown.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    frontier_mask = (passable & unknown_near & np.isfinite(dist)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(frontier_mask, connectivity=8)
    frontiers: list[Frontier] = []
    for label in range(1, count):
        size = int(stats[label, cv2.CC_STAT_AREA])
        if size < config.min_frontier_cells:
            continue
        cy, cx = np.nonzero(labels == label)
        # Target: the cell of the cluster closest to its centroid (centroid may be off-cluster).
        k = int(np.argmin((cy - cy.mean()) ** 2 + (cx - cx.mean()) ** 2))
        ty, tx = int(cy[k]), int(cx[k])
        wx, wy = slam.cell_center(tx + ix0, ty + iy0)
        bearing = math.atan2(math.sin(math.atan2(wy - robot_pose[1], wx - robot_pose[0]) - robot_pose[2]),
                             math.cos(math.atan2(wy - robot_pose[1], wx - robot_pose[0]) - robot_pose[2]))
        frontiers.append(Frontier(id=0, x=float(wx), y=float(wy), size=size,
                                  distance=float(dist[ty, tx]), bearing=bearing))
    frontiers.sort(key=lambda f: f.distance)
    for i, f in enumerate(frontiers):
        f.id = i + 1
    return Plan(frontiers, dist, parent, bounds, passable, start)


def _dijkstra(passable: np.ndarray, extra: np.ndarray, start, res: float):
    h, w = passable.shape
    dist = np.full((h, w), np.inf)
    parent = np.full((h, w), -1, np.int64)
    flat_pass = passable.ravel()
    flat_extra = extra.ravel()
    flat_dist = dist.ravel()
    flat_parent = parent.ravel()
    s = start[0] * w + start[1]
    flat_dist[s] = 0.0
    heap = [(0.0, s)]
    steps = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
             (-1, -1, SQRT2), (-1, 1, SQRT2), (1, -1, SQRT2), (1, 1, SQRT2)]
    while heap:
        d, i = heapq.heappop(heap)
        if d > flat_dist[i]:
            continue
        y, x = divmod(i, w)
        for dy, dx, c in steps:
            ny, nx = y + dy, x + dx
            if ny < 0 or ny >= h or nx < 0 or nx >= w:
                continue
            j = ny * w + nx
            if not flat_pass[j]:
                continue
            nd = d + c * res * (1.0 + flat_extra[j])
            if nd < flat_dist[j]:
                flat_dist[j] = nd
                flat_parent[j] = i
                heapq.heappush(heap, (nd, j))
    return dist, parent


def path_to(plan: Plan, slam: ScanSlam, goal_xy, config: PlannerConfig | None = None):
    """Map-frame waypoints from the robot to the goal cell (via the Dijkstra tree), or None."""
    config = config or PlannerConfig()
    iy0, _, ix0, _ = plan.crop
    gx, gy = slam.to_cell(goal_xy[0], goal_xy[1])
    gy, gx = int(gy) - iy0, int(gx) - ix0
    h, w = plan.dist.shape
    if not (0 <= gy < h and 0 <= gx < w) or not np.isfinite(plan.dist[gy, gx]):
        return None
    flat_parent = plan.parent.ravel()
    cells = []
    i = gy * w + gx
    s = plan.start[0] * w + plan.start[1]
    while i != -1:
        cells.append(divmod(i, w))
        if i == s:
            break
        i = int(flat_parent[i])
    cells.reverse()
    ys = np.array([c[0] for c in cells]) + iy0
    xs = np.array([c[1] for c in cells]) + ix0
    wx, wy = slam.cell_center(xs, ys)
    points = [(float(wx[0]), float(wy[0]))]
    for x, y in zip(wx[1:], wy[1:]):
        if math.hypot(x - points[-1][0], y - points[-1][1]) >= config.waypoint_spacing:
            points.append((float(x), float(y)))
    if (float(wx[-1]), float(wy[-1])) != points[-1]:
        points.append((float(wx[-1]), float(wy[-1])))
    return points


def nearest_frontier(frontiers: list[Frontier], config: PlannerConfig | None = None) -> int | None:
    """Default choice: shortest path, with a cost for frontiers that need a big turn first."""
    config = config or PlannerConfig()
    best, best_cost = None, math.inf
    for f in frontiers:
        cost = f.distance + config.heading_weight * abs(f.bearing)
        if cost < best_cost:
            best, best_cost = f.id, cost
    return best
