"""Stage 1: Minimalist Geometric & Symbolic Style Draft Engine (风格草稿).

Extracts the core subject silhouette, posture axis, and narrative focal relationship
from an input image and compresses it into 3 distinct minimalist geometric StyleDraft
candidates with generous negative space (whitespace_ratio >= 0.55).
"""

from __future__ import annotations

import base64
from collections import deque
import io
import json
import math
from typing import Any
import urllib.error
import urllib.request

from PIL import Image

from backend.app.schemas import (
    ColorDraft,
    Point2D,
    StructuralLine,
    StyleDraft,
    VectorRegion,
)

# Default preview colors when no ColorDraft is supplied to render_style_draft_preview_svg
DEFAULT_BASE_HEX = "#F5F1E6"
DEFAULT_GRID_HEX = "#C4C8CE"
DEFAULT_SLOT_HEX: dict[int, str] = {
    0: DEFAULT_BASE_HEX,
    1: "#1D5B9B",  # Cobalt Blue
    2: "#008F7A",  # Teal Cyan
    3: "#E85D2A",  # Warm Orange
    4: "#25282A",  # Deep Structural Ink
}


def _clamp01(v: float) -> float:
    """Clamp a coordinate to [0.0, 1.0] rounded to 4 decimal places."""
    return round(max(0.0, min(1.0, float(v))), 4)


def _clamp_point(pt: Point2D, margin: float = 0.0) -> Point2D:
    """Clamp a 2D point into [margin, 1.0 - margin]."""
    lo = max(0.0, min(0.45, margin))
    hi = 1.0 - lo
    return (
        round(max(lo, min(hi, float(pt[0]))), 4),
        round(max(lo, min(hi, float(pt[1]))), 4),
    )


def _signed_polygon_area(pts: list[Point2D]) -> float:
    """Compute signed area of a 2D polygon using the Shoelace formula."""
    n = len(pts)
    if n < 3:
        return 0.0
    area2 = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
    return 0.5 * area2


def _polygon_area(pts: list[Point2D]) -> float:
    """Compute unsigned area of a 2D polygon."""
    return abs(_signed_polygon_area(pts))


def _ensure_ccw(pts: list[Point2D]) -> list[Point2D]:
    """Ensure polygon vertices are ordered counter-clockwise (positive shoelace area)."""
    if len(pts) < 3:
        return list(pts)
    if _signed_polygon_area(pts) < 0.0:
        return list(reversed(pts))
    return list(pts)


def _point_line_distance(pt: Point2D, start: Point2D, end: Point2D) -> float:
    """Perpendicular distance from pt to segment (start, end)."""
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    mag2 = dx * dx + dy * dy
    if mag2 < 1e-12:
        return math.hypot(pt[0] - start[0], pt[1] - start[1])
    t = max(0.0, min(1.0, ((pt[0] - start[0]) * dx + (pt[1] - start[1]) * dy) / mag2))
    proj_x = start[0] + t * dx
    proj_y = start[1] + t * dy
    return math.hypot(pt[0] - proj_x, pt[1] - proj_y)


def rdp_simplify(points: list[Point2D], epsilon: float) -> list[Point2D]:
    """Simplify a polyline using the Ramer-Douglas-Peucker algorithm."""
    if len(points) <= 2:
        return list(points)

    max_dist = -1.0
    index = 0
    start = points[0]
    end = points[-1]

    for i in range(1, len(points) - 1):
        d = _point_line_distance(points[i], start, end)
        if d > max_dist:
            max_dist = d
            index = i

    if max_dist > epsilon:
        left = rdp_simplify(points[: index + 1], epsilon)
        right = rdp_simplify(points[index:], epsilon)
        return left[:-1] + right
    return [start, end]


def _simplify_closed_polygon(points: list[Point2D], epsilon: float, min_vertices: int = 4) -> list[Point2D]:
    """Simplify a closed polygon loop while preserving at least min_vertices."""
    if len(points) <= min_vertices:
        return _ensure_ccw(points)

    # Split closed loop at farthest vertex pair to avoid start/end seam bias
    i0 = 0
    i1 = len(points) // 2
    max_d2 = -1.0
    step = max(1, len(points) // 16)
    for i in range(0, len(points), step):
        for j in range(i + 1, len(points), step):
            d2 = (points[i][0] - points[j][0]) ** 2 + (points[i][1] - points[j][1]) ** 2
            if d2 > max_d2:
                max_d2 = d2
                i0, i1 = i, j

    arc1 = points[i0 : i1 + 1]
    arc2 = points[i1:] + points[: i0 + 1]
    simp1 = rdp_simplify(arc1, epsilon)
    simp2 = rdp_simplify(arc2, epsilon)
    merged = simp1[:-1] + simp2[:-1]

    # Deduplicate consecutive near-identical vertices
    cleaned: list[Point2D] = []
    for pt in merged:
        if not cleaned or math.hypot(pt[0] - cleaned[-1][0], pt[1] - cleaned[-1][1]) > 1e-3:
            cleaned.append(pt)
    if len(cleaned) > 1 and math.hypot(cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]) <= 1e-3:
        cleaned.pop()

    if len(cleaned) < min_vertices:
        # Subsample evenly from original contour
        k = max(min_vertices, 6)
        cleaned = [points[(idx * len(points)) // k] for idx in range(k)]

    return _ensure_ccw(cleaned)


def _trace_contour(mask: list[list[int]], width: int, height: int) -> list[Point2D]:
    """Trace outer boundary of a binary foreground mask using Moore-Neighbor tracing."""
    start: tuple[int, int] | None = None
    for y in range(height):
        for x in range(width):
            if mask[y][x]:
                start = (x, y)
                break
        if start is not None:
            break

    if start is None:
        return [(0.35, 0.35), (0.65, 0.35), (0.65, 0.65), (0.35, 0.65)]

    # Clockwise 8-neighborhood offsets: W, NW, N, NE, E, SE, S, SW
    neighbors = [
        (-1, 0),
        (-1, -1),
        (0, -1),
        (1, -1),
        (1, 0),
        (1, 1),
        (0, 1),
        (-1, 1),
    ]

    contour_px: list[tuple[int, int]] = [start]
    curr = start
    backtrack_dir = 0  # Entered from West
    max_steps = width * height * 2

    for _ in range(max_steps):
        found_next = False
        for step in range(1, 9):
            idx = (backtrack_dir + step) % 8
            dx, dy = neighbors[idx]
            nx, ny = curr[0] + dx, curr[1] + dy
            if 0 <= nx < width and 0 <= ny < height and mask[ny][nx]:
                curr = (nx, ny)
                # New backtrack direction points to the neighbor before the one we stepped from
                backtrack_dir = (idx + 4 + 1) % 8
                found_next = True
                break
        if not found_next or curr == start:
            break
        contour_px.append(curr)

    if len(contour_px) < 4:
        sx, sy = start
        return [
            ((sx) / width, (sy) / height),
            ((sx + 1) / width, (sy) / height),
            ((sx + 1) / width, (sy + 1) / height),
            ((sx) / width, (sy + 1) / height),
        ]

    pts = [((x + 0.5) / width, (y + 0.5) / height) for x, y in contour_px]
    return _ensure_ccw(pts)


def _regular_oriented_polygon(
    center: Point2D,
    rx: float,
    ry: float,
    angle_rad: float,
    sides: int = 8,
    phase: float = 0.0,
) -> list[Point2D]:
    """Generate a counter-clockwise oriented geometric polygon with given semi-axes."""
    cx, cy = center
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    pts: list[Point2D] = []
    for i in range(sides):
        theta = phase + (2.0 * math.pi * i) / sides
        lx = rx * math.cos(theta)
        ly = ry * math.sin(theta)
        gx = cx + lx * cos_a - ly * sin_a
        gy = cy + lx * sin_a + ly * cos_a
        pts.append((_clamp01(gx), _clamp01(gy)))
    return _ensure_ccw(pts)


class _SubjectGeometry:
    """Extracted geometric features of the input image subject."""

    def __init__(
        self,
        primary_contour: list[Point2D],
        secondary_contour: list[Point2D],
        accent_contour: list[Point2D],
        hole_contour: list[Point2D],
        centroid: Point2D,
        angle_rad: float,
        major_radius: float,
        minor_radius: float,
        ridge_points: list[Point2D],
        rib_segments: list[tuple[Point2D, Point2D]],
    ) -> None:
        self.primary_contour = primary_contour
        self.secondary_contour = secondary_contour
        self.accent_contour = accent_contour
        self.hole_contour = hole_contour
        self.centroid = centroid
        self.angle_rad = angle_rad
        self.major_radius = major_radius
        self.minor_radius = minor_radius
        self.ridge_points = ridge_points
        self.rib_segments = rib_segments


def _analyze_subject_geometry(image_bytes: bytes) -> _SubjectGeometry:
    """Segment the foreground subject and compute contours, posture axis, and medial ridge."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    grid_w, grid_h = 64, 64
    small_rgba = img.resize((grid_w, grid_h), Image.Resampling.BILINEAR)
    pixels = list(
        small_rgba.get_flattened_data()
        if hasattr(small_rgba, "get_flattened_data")
        else small_rgba.getdata()
    )

    # Composite over white for color comparison
    rgb_grid: list[tuple[float, float, float]] = []
    alpha_grid: list[float] = []
    for r, g, b, a in pixels:
        af = a / 255.0
        alpha_grid.append(af)
        rgb_grid.append((r * af + 255.0 * (1.0 - af), g * af + 255.0 * (1.0 - af), b * af + 255.0 * (1.0 - af)))

    # Estimate border background color and alpha
    border_indices: list[int] = []
    for y in range(grid_h):
        for x in range(grid_w):
            if x < 3 or x >= grid_w - 3 or y < 3 or y >= grid_h - 3:
                border_indices.append(y * grid_w + x)

    bg_r = sum(rgb_grid[i][0] for i in border_indices) / len(border_indices)
    bg_g = sum(rgb_grid[i][1] for i in border_indices) / len(border_indices)
    bg_b = sum(rgb_grid[i][2] for i in border_indices) / len(border_indices)
    bg_alpha = sum(alpha_grid[i] for i in border_indices) / len(border_indices)

    # Compute foreground salience score per pixel
    salience: list[float] = [0.0] * (grid_w * grid_h)
    for y in range(grid_h):
        for x in range(grid_w):
            idx = y * grid_w + x
            r, g, b = rgb_grid[idx]
            color_dist = math.sqrt((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2) / 441.67

            # Local gradient magnitude
            grad = 0.0
            if 0 < x < grid_w - 1 and 0 < y < grid_h - 1:
                rx1, gx1, bx1 = rgb_grid[y * grid_w + (x + 1)]
                rx0, gx0, bx0 = rgb_grid[y * grid_w + (x - 1)]
                ry1, gy1, by1 = rgb_grid[(y + 1) * grid_w + x]
                ry0, gy0, by0 = rgb_grid[(y - 1) * grid_w + x]
                gx_val = abs(rx1 - rx0) + abs(gx1 - gx0) + abs(bx1 - bx0)
                gy_val = abs(ry1 - ry0) + abs(gy1 - gy0) + abs(by1 - by0)
                grad = (gx_val + gy_val) / (6.0 * 255.0)

            alpha_diff = max(0.0, alpha_grid[idx] - bg_alpha) if bg_alpha < 0.5 else 0.0
            nx = (x + 0.5) / grid_w - 0.5
            ny = (y + 0.5) / grid_h - 0.5
            center_weight = max(0.25, 1.0 - 1.15 * (nx * nx + ny * ny))
            salience[idx] = (color_dist + 0.65 * grad + 1.4 * alpha_diff) * center_weight

    sorted_sal = sorted(salience)
    # Pick threshold at ~72nd percentile so foreground occupies ~28% of grid
    thresh = max(0.04, sorted_sal[int(0.72 * len(sorted_sal))])

    raw_mask = [[0] * grid_w for _ in range(grid_h)]
    fg_count = 0
    for y in range(1, grid_h - 1):
        for x in range(1, grid_w - 1):
            if salience[y * grid_w + x] >= thresh:
                raw_mask[y][x] = 1
                fg_count += 1

    # Fallback synthetic constructivist silhouette if image is completely flat/blank
    if fg_count < 24:
        for y in range(grid_h):
            for x in range(grid_w):
                nx = (x + 0.5) / grid_w - 0.5
                ny = (y + 0.5) / grid_h - 0.5
                if abs(nx) * 1.3 + abs(ny) * 0.95 <= 0.26:
                    raw_mask[y][x] = 1

    # 1 pass of 3x3 majority smoothing
    mask = [[0] * grid_w for _ in range(grid_h)]
    for y in range(1, grid_h - 1):
        for x in range(1, grid_w - 1):
            neighbors_on = sum(
                raw_mask[y + dy][x + dx]
                for dy in (-1, 0, 1)
                for dx in (-1, 0, 1)
            )
            mask[y][x] = 1 if neighbors_on >= 5 else 0

    # Connected Component Labeling (4-connected BFS) to isolate primary subject
    visited = [[False] * grid_w for _ in range(grid_h)]
    components: list[list[tuple[int, int]]] = []
    for y in range(grid_h):
        for x in range(grid_w):
            if mask[y][x] and not visited[y][x]:
                comp: list[tuple[int, int]] = []
                q: deque[tuple[int, int]] = deque([(x, y)])
                visited[y][x] = True
                while q:
                    cx, cy = q.popleft()
                    comp.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < grid_w and 0 <= ny < grid_h and mask[ny][nx] and not visited[ny][nx]:
                            visited[ny][nx] = True
                            q.append((nx, ny))
                components.append(comp)

    components.sort(key=len, reverse=True)
    primary_pixels = components[0] if components else [(x, y) for y in range(22, 42) for x in range(22, 42)]

    primary_mask = [[0] * grid_w for _ in range(grid_h)]
    for x, y in primary_pixels:
        primary_mask[y][x] = 1

    # Compute 2D moments & principal posture axis
    n_px = float(len(primary_pixels))
    mean_x = sum((x + 0.5) / grid_w for x, _ in primary_pixels) / n_px
    mean_y = sum((y + 0.5) / grid_h for _, y in primary_pixels) / n_px

    mu20 = sum(((x + 0.5) / grid_w - mean_x) ** 2 for x, _ in primary_pixels) / n_px
    mu02 = sum(((y + 0.5) / grid_h - mean_y) ** 2 for _, y in primary_pixels) / n_px
    mu11 = sum(
        ((x + 0.5) / grid_w - mean_x) * ((y + 0.5) / grid_h - mean_y)
        for x, y in primary_pixels
    ) / n_px

    angle_rad = 0.5 * math.atan2(2.0 * mu11, mu20 - mu02)
    trace = mu20 + mu02
    det = max(0.0, (mu20 - mu02) ** 2 + 4.0 * (mu11 ** 2))
    sqrt_det = math.sqrt(det)
    lambda1 = max(1e-4, 0.5 * (trace + sqrt_det))
    lambda2 = max(1e-4, 0.5 * (trace - sqrt_det))
    major_radius = min(0.32, max(0.11, 1.85 * math.sqrt(lambda1)))
    minor_radius = min(0.26, max(0.08, 1.85 * math.sqrt(lambda2)))

    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)

    # Split primary component along principal posture axis and luminance into secondary & accent masks
    secondary_mask = [[0] * grid_w for _ in range(grid_h)]
    accent_mask = [[0] * grid_w for _ in range(grid_h)]
    proj_vals: list[tuple[float, float, int, int]] = []
    for x, y in primary_pixels:
        px = (x + 0.5) / grid_w - mean_x
        py = (y + 0.5) / grid_h - mean_y
        u = px * cos_a + py * sin_a
        v = -px * sin_a + py * cos_a
        r, g, b = rgb_grid[y * grid_w + x]
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
        proj_vals.append((u, v, x, y))
        if u >= 0.05 * major_radius:
            secondary_mask[y][x] = 1
        if u <= -0.15 * major_radius and abs(v) <= 0.85 * minor_radius:
            accent_mask[y][x] = 1

    # Extract medial ridge polyline & perpendicular structural ribs along major axis
    u_min = min(item[0] for item in proj_vals)
    u_max = max(item[0] for item in proj_vals)
    span = max(0.08, u_max - u_min)
    num_bins = 5
    ridge_points: list[Point2D] = []
    rib_segments: list[tuple[Point2D, Point2D]] = []

    for b_idx in range(num_bins):
        u_lo = u_min + (b_idx / num_bins) * span
        u_hi = u_min + ((b_idx + 1) / num_bins) * span
        bin_pts = [item for item in proj_vals if u_lo <= item[0] <= u_hi]
        if bin_pts:
            u_mid = sum(item[0] for item in bin_pts) / len(bin_pts)
            v_mid = sum(item[1] for item in bin_pts) / len(bin_pts)
            v_min_b = min(item[1] for item in bin_pts)
            v_max_b = max(item[1] for item in bin_pts)
        else:
            u_mid = 0.5 * (u_lo + u_hi)
            v_mid = 0.0
            v_min_b = -0.4 * minor_radius
            v_max_b = 0.4 * minor_radius

        rx = mean_x + u_mid * cos_a - v_mid * sin_a
        ry = mean_y + u_mid * sin_a + v_mid * cos_a
        ridge_points.append((_clamp01(rx), _clamp01(ry)))

        if b_idx in (1, 2, 3) and (v_max_b - v_min_b) > 0.04:
            p1x = mean_x + u_mid * cos_a - (0.75 * v_min_b) * sin_a
            p1y = mean_y + u_mid * sin_a + (0.75 * v_min_b) * cos_a
            p2x = mean_x + u_mid * cos_a - (0.75 * v_max_b) * sin_a
            p2y = mean_y + u_mid * sin_a + (0.75 * v_max_b) * cos_a
            rib_segments.append(((_clamp01(p1x), _clamp01(p1y)), (_clamp01(p2x), _clamp01(p2y))))

    raw_primary_contour = _trace_contour(primary_mask, grid_w, grid_h)
    primary_contour = _simplify_closed_polygon(raw_primary_contour, epsilon=0.022, min_vertices=6)

    if any(any(row) for row in secondary_mask):
        raw_sec = _trace_contour(secondary_mask, grid_w, grid_h)
        secondary_contour = _simplify_closed_polygon(raw_sec, epsilon=0.025, min_vertices=4)
    else:
        secondary_contour = _regular_oriented_polygon(
            (mean_x + 0.25 * major_radius * cos_a, mean_y + 0.25 * major_radius * sin_a),
            0.48 * major_radius,
            0.48 * minor_radius,
            angle_rad,
            sides=6,
        )

    if any(any(row) for row in accent_mask):
        raw_acc = _trace_contour(accent_mask, grid_w, grid_h)
        accent_contour = _simplify_closed_polygon(raw_acc, epsilon=0.025, min_vertices=4)
    else:
        accent_contour = _regular_oriented_polygon(
            (mean_x - 0.35 * major_radius * cos_a, mean_y - 0.35 * major_radius * sin_a),
            0.36 * major_radius,
            0.36 * minor_radius,
            angle_rad,
            sides=4,
            phase=math.pi / 4.0,
        )

    hole_contour = _regular_oriented_polygon(
        (mean_x + 0.06 * major_radius * cos_a, mean_y + 0.06 * major_radius * sin_a),
        0.28 * major_radius,
        0.28 * minor_radius,
        angle_rad,
        sides=4,
        phase=math.pi / 4.0,
    )

    return _SubjectGeometry(
        primary_contour=primary_contour,
        secondary_contour=secondary_contour,
        accent_contour=accent_contour,
        hole_contour=hole_contour,
        centroid=(mean_x, mean_y),
        angle_rad=angle_rad,
        major_radius=major_radius,
        minor_radius=minor_radius,
        ridge_points=ridge_points,
        rib_segments=rib_segments,
    )


def _transform_points(
    pts: list[Point2D],
    src_center: Point2D,
    dst_center: Point2D,
    scale: float,
    margin: float = 0.04,
) -> list[Point2D]:
    """Translate and uniformly scale a list of points around src_center -> dst_center."""
    sx, sy = src_center
    dx, dy = dst_center
    out: list[Point2D] = []
    for px, py in pts:
        nx = dx + (px - sx) * scale
        ny = dy + (py - sy) * scale
        out.append(_clamp_point((nx, ny), margin=margin))
    return out


def _calibrate_draft_whitespace(
    regions: list[VectorRegion],
    structural_lines: list[StructuralLine],
    src_center: Point2D,
    focal_center: Point2D,
    whitespace_target: float,
) -> tuple[list[VectorRegion], list[StructuralLine], float]:
    """Scale regions and lines around focal_center so whitespace_ratio >= 0.55 and matches target."""
    target_ws = max(0.56, min(0.88, float(whitespace_target)))
    target_fg_area = 1.0 - target_ws

    # Measure raw foreground area before scaling
    raw_fg_area = 0.0
    for reg in regions:
        if reg.color_slot == 0:
            continue
        ext_area = _polygon_area(reg.exterior)
        hole_area = sum(_polygon_area(h) for h in reg.holes)
        raw_fg_area += max(0.0, ext_area - hole_area)

    raw_fg_area = max(1e-4, raw_fg_area)
    # Also ensure max radius fits comfortably inside canvas margins [0.06, 0.94]
    max_extent = 0.05
    for reg in regions:
        for px, py in reg.exterior:
            max_extent = max(max_extent, abs(px - src_center[0]), abs(py - src_center[1]))
    for line in structural_lines:
        for px, py in line.points:
            max_extent = max(max_extent, abs(px - src_center[0]), abs(py - src_center[1]))

    avail_half = min(
        focal_center[0] - 0.06,
        0.94 - focal_center[0],
        focal_center[1] - 0.06,
        0.94 - focal_center[1],
    )
    max_safe_scale = max(0.25, avail_half / max_extent)
    desired_scale = math.sqrt(target_fg_area / raw_fg_area)
    scale = min(max_safe_scale, desired_scale)

    scaled_regions: list[VectorRegion] = []
    net_fg_area = 0.0
    cutout_area = 0.0

    for reg in regions:
        new_ext = _ensure_ccw(_transform_points(reg.exterior, src_center, focal_center, scale))
        new_holes = [
            _ensure_ccw(_transform_points(h, src_center, focal_center, scale))
            for h in reg.holes
        ]
        scaled_regions.append(
            VectorRegion(
                id=reg.id,
                color_slot=reg.color_slot,
                exterior=new_ext,
                holes=new_holes,
                semantic_role=reg.semantic_role,
            )
        )
        if reg.color_slot == 0:
            cutout_area += _polygon_area(new_ext)
        else:
            net_fg_area += max(
                0.0,
                _polygon_area(new_ext) - sum(_polygon_area(h) for h in new_holes),
            )

    scaled_lines: list[StructuralLine] = []
    for line in structural_lines:
        new_pts = _transform_points(line.points, src_center, focal_center, scale)
        scaled_lines.append(
            StructuralLine(
                id=line.id,
                color_slot=line.color_slot,
                points=new_pts,
                weight=line.weight,
                role=line.role,
            )
        )

    effective_fg = max(0.04, min(0.44, net_fg_area - 0.5 * cutout_area))
    whitespace_ratio = round(max(0.55, min(0.95, 1.0 - effective_fg)), 4)
    return scaled_regions, scaled_lines, whitespace_ratio


def render_style_draft_preview_svg(
    draft: StyleDraft,
    color_draft: ColorDraft | None = None,
) -> str:
    """Render a StyleDraft (with optional ColorDraft palette) as a clean 600x600 SVG string."""
    size = 600.0
    base_hex = color_draft.base_hex if color_draft else DEFAULT_BASE_HEX
    grid_hex = color_draft.grid_filament_hex if color_draft else DEFAULT_GRID_HEX

    slot_colors: dict[int, str] = {0: base_hex}
    if color_draft and color_draft.colors:
        for fc in color_draft.colors:
            slot_colors[fc.slot] = fc.hex
    else:
        slot_colors.update(DEFAULT_SLOT_HEX)
        slot_colors[0] = base_hex

    def _resolve_color(slot: int) -> str:
        if slot == 0:
            return base_hex
        if slot in slot_colors:
            return slot_colors[slot]
        # Wrap modulo available positive slots
        pos_slots = sorted(k for k in slot_colors if k > 0)
        if not pos_slots:
            return "#1D5B9B"
        return slot_colors[pos_slots[(slot - 1) % len(pos_slots)]]

    svg_parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(size)} {int(size)}" width="100%" height="100%">',
        f'  <rect width="{int(size)}" height="{int(size)}" fill="{base_hex}" />',
        f'  <rect x="24" y="24" width="{int(size - 48)}" height="{int(size - 48)}" fill="none" stroke="{grid_hex}" stroke-width="1" stroke-dasharray="4 6" opacity="0.65" />',
    ]

    # Render VectorRegions (positive regions first, negative-space cutouts on top)
    ordered_regions = [r for r in draft.regions if r.color_slot > 0] + [
        r for r in draft.regions if r.color_slot == 0
    ]
    for reg in ordered_regions:
        if len(reg.exterior) < 3:
            continue
        fill_hex = _resolve_color(reg.color_slot)
        path_cmds: list[str] = []
        for idx, (x, y) in enumerate(reg.exterior):
            cmd = "M" if idx == 0 else "L"
            path_cmds.append(f"{cmd} {x * size:.2f} {y * size:.2f}")
        path_cmds.append("Z")

        for hole in reg.holes:
            if len(hole) < 3:
                continue
            for idx, (hx, hy) in enumerate(hole):
                cmd = "M" if idx == 0 else "L"
                path_cmds.append(f"{cmd} {hx * size:.2f} {hy * size:.2f}")
            path_cmds.append("Z")

        d_attr = " ".join(path_cmds)
        svg_parts.append(
            f'  <path id="{reg.id}" d="{d_attr}" fill="{fill_hex}" fill-rule="evenodd" />'
        )

    # Render StructuralLines
    for line in draft.structural_lines:
        if len(line.points) < 2:
            continue
        stroke_hex = _resolve_color(line.color_slot)
        pts_attr = " ".join(f"{x * size:.2f},{y * size:.2f}" for x, y in line.points)
        stroke_px = max(2.0, line.weight * size)
        svg_parts.append(
            f'  <polyline id="{line.id}" points="{pts_attr}" fill="none" '
            f'stroke="{stroke_hex}" stroke-width="{stroke_px:.2f}" '
            f'stroke-linecap="round" stroke-linejoin="round" />'
        )

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def _find_connected_components(
    mask: list[list[int]], width: int, height: int
) -> list[list[tuple[int, int]]]:
    """Return 4-connected foreground pixel components for a binary mask."""
    visited = [[False] * width for _ in range(height)]
    components: list[list[tuple[int, int]]] = []
    for y in range(height):
        for x in range(width):
            if mask[y][x] == 1 and not visited[y][x]:
                comp: list[tuple[int, int]] = []
                queue = deque([(x, y)])
                visited[y][x] = True
                while queue:
                    cx, cy = queue.popleft()
                    comp.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < width and 0 <= ny < height and mask[ny][nx] == 1 and not visited[ny][nx]:
                            visited[ny][nx] = True
                            queue.append((nx, ny))
                components.append(comp)
    return components


def _vectorize_flat_graphic_image(
    gen_image_bytes: bytes,
    draft_id: str,
    name: str,
    description: str,
    focal_center: Point2D,
    color_draft: ColorDraft | None = None,
) -> StyleDraft:
    """Convert a crisp flat-color Gemini-generated Style Draft image into VectorRegions + StructuralLines."""
    img = Image.open(io.BytesIO(gen_image_bytes)).convert("RGB")
    grid_n = 96
    small = img.resize((grid_n, grid_n), Image.Resampling.BILINEAR)
    pixels = small.load()

    # Estimate uniform background color from the 4 corners
    corners = [
        pixels[0, 0],
        pixels[grid_n - 1, 0],
        pixels[0, grid_n - 1],
        pixels[grid_n - 1, grid_n - 1],
    ]
    bg_r = sum(c[0] for c in corners) / 4.0
    bg_g = sum(c[1] for c in corners) / 4.0
    bg_b = sum(c[2] for c in corners) / 4.0

    # Collect foreground pixels
    fg_coords: list[tuple[int, int, tuple[int, int, int]]] = []
    for y in range(grid_n):
        for x in range(grid_n):
            r, g, b = pixels[x, y]
            dist = math.sqrt((r - bg_r) ** 2 + (g - bg_g) ** 2 + (b - bg_b) ** 2)
            if dist > 26.0:
                fg_coords.append((x, y, (r, g, b)))

    fg_ratio = len(fg_coords) / float(grid_n * grid_n)
    whitespace_ratio = round(max(0.55, min(0.95, 1.0 - fg_ratio)), 4)

    # Use provided functional palette centroids if available, otherwise cluster up to 4 flat hues
    if color_draft and color_draft.colors:
        centroids = [
            (
                float(int(fc.hex[1:3], 16)),
                float(int(fc.hex[3:5], 16)),
                float(int(fc.hex[5:7], 16)),
            )
            for fc in color_draft.colors
        ]
    else:
        centroids = []
        for _, _, rgb in fg_coords[:: max(1, len(fg_coords) // 400)]:
            if not centroids:
                centroids.append((float(rgb[0]), float(rgb[1]), float(rgb[2])))
                continue
            min_d = min(
                math.sqrt((rgb[0] - c[0]) ** 2 + (rgb[1] - c[1]) ** 2 + (rgb[2] - c[2]) ** 2)
                for c in centroids
            )
            if min_d > 55.0 and len(centroids) < 4:
                centroids.append((float(rgb[0]), float(rgb[1]), float(rgb[2])))
        if not centroids:
            centroids = [(0.0, 143.0, 122.0)]

    # Build per-slot binary masks and trace clean polygonal contours
    slot_masks: dict[int, list[list[int]]] = {
        slot: [[0] * grid_n for _ in range(grid_n)]
        for slot in range(1, len(centroids) + 1)
    }
    for x, y, rgb in fg_coords:
        best_slot = 1
        best_d = 1e9
        for idx, c in enumerate(centroids, start=1):
            d = (rgb[0] - c[0]) ** 2 + (rgb[1] - c[1]) ** 2 + (rgb[2] - c[2]) ** 2
            if d < best_d:
                best_d = d
                best_slot = idx
        slot_masks[best_slot][y][x] = 1

    regions: list[VectorRegion] = []
    structural_lines: list[StructuralLine] = []
    reg_counter = 0

    for slot, mask in slot_masks.items():
        comps = _find_connected_components(mask, grid_n, grid_n)
        for comp in comps:
            if len(comp) < 4:
                continue
            comp_mask = [[0] * grid_n for _ in range(grid_n)]
            for cx_i, cy_i in comp:
                comp_mask[cy_i][cx_i] = 1
            norm_contour = _trace_contour(comp_mask, grid_n, grid_n)
            if len(norm_contour) < 3:
                continue
            simplified = _simplify_closed_polygon(norm_contour, epsilon=0.009, min_vertices=3)
            if len(simplified) >= 3:
                reg_counter += 1
                regions.append(
                    VectorRegion(
                        id=f"{draft_id}_r{reg_counter}",
                        color_slot=slot,
                        exterior=_ensure_ccw(simplified),
                        holes=[],
                        semantic_role="primary_subject" if slot <= 2 else "modular_accent",
                    )
                )
            elif len(simplified) == 2:
                structural_lines.append(
                    StructuralLine(
                        id=f"{draft_id}_l{len(structural_lines) + 1}",
                        color_slot=slot,
                        points=simplified,
                        weight=0.016,
                        role="structural_contour",
                    )
                )

    # Ensure at least one structural spine line exists for Stage 3 Mode C testing
    if not structural_lines and fg_coords:
        xs = [p[0] / float(grid_n) for p in fg_coords]
        ys = [p[1] / float(grid_n) for p in fg_coords]
        structural_lines.append(
            StructuralLine(
                id=f"{draft_id}_axis",
                color_slot=min(len(centroids), 3),
                points=[(min(xs), (min(ys) + max(ys)) * 0.5), (max(xs), (min(ys) + max(ys)) * 0.5)],
                weight=0.015,
                role="posture_axis",
            )
        )

    b64_png = base64.b64encode(gen_image_bytes).decode("ascii")
    draft = StyleDraft(
        draft_id=draft_id,
        name=name,
        description=description,
        engine="ai",
        whitespace_ratio=whitespace_ratio,
        focal_center=focal_center,
        regions=regions,
        structural_lines=structural_lines,
        preview_svg=(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 600" width="100%" height="100%">'
            f'<image href="data:image/png;base64,{b64_png}" x="0" y="0" width="600" height="600" preserveAspectRatio="xMidYMid meet"/>'
            "</svg>"
        ),
    )
    return draft


def _try_gemini_image_style_drafts(
    image_bytes: bytes,
    whitespace_target: float,
    focal_center: Point2D,
    api_key: str,
    model: str,
    style_cue_images: list[bytes] | None = None,
) -> list[StyleDraft] | None:
    """Call Gemini native image generation (responseModalities: IMAGE) with visual style cues."""
    import os

    resolved_model = model or os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    if "image" not in resolved_model and "imagen" not in resolved_model:
        resolved_model = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{resolved_model}:generateContent"
        f"?key={api_key}"
    )

    ws_pct = int(round(max(0.55, min(0.85, whitespace_target)) * 100))
    variants = [
        (
            "style_gemini_architectural",
            "建筑几何体块 / Gemini Architectural Massing",
            "Gemini image-to-image modernist architectural massing with crisp flat planes and generous negative space.",
            (
                f"Transform the main subject of the input photo into a standalone editorial modernist geometric illustration "
                f"matching the clean flat-plane aesthetic of the reference style cues. Strip away all background clutter, sky, "
                f"and surrounding noise. Float the simplified subject centered at ({focal_center[0]:.2f}, {focal_center[1]:.2f}) "
                f"with at least {ws_pct}% solid warm-white (#F5F1E6) breathing whitespace. Use ONLY 3-4 flat, matte architectural "
                f"color blocks with sharp polygonal facets, zero gradients, zero textures, zero text."
            ),
        ),
        (
            "style_gemini_swiss_negative",
            "瑞士平面负空间 / Gemini Swiss Modernist & Negative Space",
            "Gemini Swiss graphic abstraction emphasizing high-contrast silhouettes, clean negative space cutouts, and structural axis marks.",
            (
                f"Transform the main subject of the input photo into a Swiss modernist minimalist graphic poster icon "
                f"inspired by the reference style cues. Remove the background completely and isolate the core silhouette and "
                f"narrative relationship on a solid warm-white (#F5F1E6) canvas with >= {ws_pct}% negative space. Use bold flat "
                f"color blocks (max 3-4 functional colors: teal, cobalt, vermilion orange, deep ink), crisp negative-space cutouts, "
                f"and minimal geometric structural lines. Zero gradients, zero shadows, zero text."
            ),
        ),
        (
            "style_gemini_gouache_icon",
            "极简平涂标识 / Gemini Flat Geometric Symbol",
            "Gemini ultra-restrained minimalist geometric symbol optimized for 60x60 cross-stitch grid translation.",
            (
                f"Distill the input photo into an ultra-minimalist geometric visual symbol with maximum recognizability using "
                f"the fewest possible flat modular shapes. Place the symbol as the sole focal point on a solid warm-white (#F5F1E6) "
                f"canvas with >= {ws_pct}% whitespace. Strictly flat solid colors (3-4 refined non-neon hues), clean geometric edges, "
                f"no gradients, no fine noise, no text—designed to translate cleanly onto a 60x60 cross-stitch grid."
            ),
        ),
    ]

    b64_src = base64.b64encode(image_bytes).decode("ascii")
    cue_parts: list[dict[str, Any]] = []
    if style_cue_images:
        for cue_bytes in style_cue_images[:3]:
            cue_parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(cue_bytes).decode("ascii"),
                    }
                }
            )

    drafts: list[StyleDraft] = []
    try:
        for draft_id, name, desc, prompt_text in variants:
            parts: list[dict[str, Any]] = [
                {"text": prompt_text},
                {"inline_data": {"mime_type": "image/png", "data": b64_src}},
                *cue_parts,
            ]
            payload = {
                "contents": [{"parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"]},
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=30.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            gen_b64: str | None = None
            for cand in body.get("candidates", []):
                for part in cand.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline and inline.get("data"):
                        gen_b64 = inline["data"]
                        break
                if gen_b64:
                    break

            if not gen_b64:
                continue

            gen_bytes = base64.b64decode(gen_b64)
            draft = _vectorize_flat_graphic_image(
                gen_image_bytes=gen_bytes,
                draft_id=draft_id,
                name=name,
                description=desc,
                focal_center=focal_center,
            )
            if draft.regions:
                drafts.append(draft)

        return drafts if drafts else None
    except (urllib.error.URLError, KeyError, ValueError, TypeError, OSError):
        return None


def generate_style_drafts(
    image_bytes: bytes,
    engine: str = "algorithmic",
    whitespace_target: float = 0.68,
    focal_offset: tuple[float, float] = (0.0, 0.0),
    api_key: str | None = None,
    model: str = "gemini-2.5-flash-image",
    style_cue_images: list[bytes] | None = None,
) -> list[StyleDraft]:
    """Generate 3 distinct minimalist geometric StyleDraft candidates from an input image."""
    import os

    focal_center: Point2D = (
        round(max(0.22, min(0.78, 0.5 + float(focal_offset[0]))), 4),
        round(max(0.22, min(0.78, 0.5 + float(focal_offset[1]))), 4),
    )

    resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
    if engine == "ai" and resolved_key:
        ai_drafts = _try_gemini_image_style_drafts(
            image_bytes=image_bytes,
            whitespace_target=whitespace_target,
            focal_center=focal_center,
            api_key=resolved_key,
            model=model,
            style_cue_images=style_cue_images,
        )
        if ai_drafts is not None:
            return ai_drafts

    geom = _analyze_subject_geometry(image_bytes)
    cx, cy = geom.centroid
    cos_a = math.cos(geom.angle_rad)
    sin_a = math.sin(geom.angle_rad)
    maj = geom.major_radius
    min_r = geom.minor_radius

    # -------------------------------------------------------------------------
    # Draft 1: 几何解构标识 / Modular Geometric Icon
    # -------------------------------------------------------------------------
    d1_raw_regions = [
        VectorRegion(
            id="d1_primary_body",
            color_slot=1,
            exterior=geom.primary_contour,
            holes=[geom.hole_contour],
            semantic_role="primary_subject",
        ),
        VectorRegion(
            id="d1_secondary_facet",
            color_slot=2,
            exterior=geom.secondary_contour,
            holes=[],
            semantic_role="primary_subject",
        ),
        VectorRegion(
            id="d1_modular_accent",
            color_slot=3,
            exterior=geom.accent_contour,
            holes=[],
            semantic_role="modular_accent",
        ),
    ]
    d1_raw_lines = [
        StructuralLine(
            id="d1_posture_spine",
            color_slot=3,
            points=geom.ridge_points,
            weight=0.016,
            role="posture_axis",
        ),
        StructuralLine(
            id="d1_facet_divider",
            color_slot=2,
            points=[
                (_clamp01(cx - 0.85 * min_r * sin_a), _clamp01(cy + 0.85 * min_r * cos_a)),
                (_clamp01(cx), _clamp01(cy)),
                (_clamp01(cx + 0.85 * min_r * sin_a), _clamp01(cy - 0.85 * min_r * cos_a)),
            ],
            weight=0.014,
            role="internal_structure",
        ),
    ]
    d1_regions, d1_lines, d1_ws = _calibrate_draft_whitespace(
        d1_raw_regions, d1_raw_lines, geom.centroid, focal_center, whitespace_target
    )
    draft_1 = StyleDraft(
        draft_id="style_modular_icon",
        name="几何解构标识 / Modular Geometric Icon",
        description=(
            "Segments core subject silhouette into simplified polygonal facets and "
            "modular color-coded blocks with negative-space breathing room."
        ),
        engine="algorithmic",
        whitespace_ratio=d1_ws,
        focal_center=focal_center,
        regions=d1_regions,
        structural_lines=d1_lines,
    )
    draft_1.preview_svg = render_style_draft_preview_svg(draft_1)

    # -------------------------------------------------------------------------
    # Draft 2: 结构线条与负空间 / Structural Lines & Negative Space
    # -------------------------------------------------------------------------
    coarse_silhouette = _simplify_closed_polygon(geom.primary_contour, epsilon=0.032, min_vertices=5)
    cutout_diamond = _regular_oriented_polygon(
        (cx - 0.14 * maj * cos_a, cy - 0.14 * maj * sin_a),
        0.34 * maj,
        0.26 * min_r,
        geom.angle_rad,
        sides=4,
    )
    d2_raw_regions = [
        VectorRegion(
            id="d2_silhouette_shell",
            color_slot=1,
            exterior=coarse_silhouette,
            holes=[geom.hole_contour],
            semantic_role="primary_subject",
        ),
        VectorRegion(
            id="d2_negative_cutout",
            color_slot=0,
            exterior=cutout_diamond,
            holes=[],
            semantic_role="negative_space",
        ),
        VectorRegion(
            id="d2_focal_node",
            color_slot=3,
            exterior=_regular_oriented_polygon(
                (cx + 0.38 * maj * cos_a, cy + 0.38 * maj * sin_a),
                0.22 * min_r,
                0.22 * min_r,
                geom.angle_rad,
                sides=6,
            ),
            holes=[],
            semantic_role="modular_accent",
        ),
    ]
    d2_raw_lines: list[StructuralLine] = [
        StructuralLine(
            id="d2_medial_ridge",
            color_slot=2,
            points=geom.ridge_points,
            weight=0.018,
            role="posture_axis",
        ),
        StructuralLine(
            id="d2_contour_chord",
            color_slot=3,
            points=coarse_silhouette[: min(4, len(coarse_silhouette))],
            weight=0.014,
            role="structural_contour",
        ),
    ]
    for r_idx, (p1, p2) in enumerate(geom.rib_segments[:2]):
        d2_raw_lines.append(
            StructuralLine(
                id=f"d2_internal_rib_{r_idx + 1}",
                color_slot=2 if r_idx % 2 == 0 else 3,
                points=[p1, p2],
                weight=0.014,
                role="internal_structure",
            )
        )

    d2_regions, d2_lines, d2_ws = _calibrate_draft_whitespace(
        d2_raw_regions,
        d2_raw_lines,
        geom.centroid,
        focal_center,
        min(0.88, whitespace_target + 0.03),
    )
    draft_2 = StyleDraft(
        draft_id="style_structural_negative",
        name="结构线条与负空间 / Structural Lines & Negative Space",
        description=(
            "Crisp outer polygon silhouette carved by internal negative-space cutouts "
            "and reinforced by medial posture ridge and structural ribs."
        ),
        engine="algorithmic",
        whitespace_ratio=d2_ws,
        focal_center=focal_center,
        regions=d2_regions,
        structural_lines=d2_lines,
    )
    draft_2.preview_svg = render_style_draft_preview_svg(draft_2)

    # -------------------------------------------------------------------------
    # Draft 3: 极简符号构成 / Minimalist Constructivist Symbol
    # -------------------------------------------------------------------------
    oct_core = _regular_oriented_polygon(
        (cx, cy),
        0.78 * maj,
        0.72 * min_r,
        geom.angle_rad,
        sides=8,
        phase=math.pi / 8.0,
    )
    chord_wing = _regular_oriented_polygon(
        (cx + 0.34 * maj * cos_a - 0.18 * min_r * sin_a, cy + 0.34 * maj * sin_a + 0.18 * min_r * cos_a),
        0.44 * maj,
        0.38 * min_r,
        geom.angle_rad + 0.35,
        sides=4,
    )
    counter_badge = _regular_oriented_polygon(
        (cx - 0.48 * maj * cos_a + 0.28 * min_r * sin_a, cy - 0.48 * maj * sin_a - 0.28 * min_r * cos_a),
        0.25 * min_r,
        0.25 * min_r,
        geom.angle_rad,
        sides=6,
    )
    inner_slot_cutout = _regular_oriented_polygon(
        (cx - 0.08 * maj * cos_a, cy - 0.08 * maj * sin_a),
        0.32 * maj,
        0.18 * min_r,
        geom.angle_rad,
        sides=4,
    )

    d3_raw_regions = [
        VectorRegion(
            id="d3_constructivist_core",
            color_slot=1,
            exterior=oct_core,
            holes=[inner_slot_cutout],
            semantic_role="primary_subject",
        ),
        VectorRegion(
            id="d3_oriented_chord",
            color_slot=2,
            exterior=chord_wing,
            holes=[],
            semantic_role="primary_subject",
        ),
        VectorRegion(
            id="d3_counter_accent",
            color_slot=3,
            exterior=counter_badge,
            holes=[],
            semantic_role="modular_accent",
        ),
    ]
    axis_p1 = (_clamp01(cx - 0.92 * maj * cos_a), _clamp01(cy - 0.92 * maj * sin_a))
    axis_p2 = (_clamp01(cx + 0.92 * maj * cos_a), _clamp01(cy + 0.92 * maj * sin_a))
    cross_p1 = (_clamp01(cx - 0.78 * min_r * sin_a), _clamp01(cy + 0.78 * min_r * cos_a))
    cross_p2 = (_clamp01(cx + 0.78 * min_r * sin_a), _clamp01(cy - 0.78 * min_r * cos_a))

    d3_raw_lines = [
        StructuralLine(
            id="d3_principal_axis",
            color_slot=3,
            points=[axis_p1, (cx, cy), axis_p2],
            weight=0.017,
            role="posture_axis",
        ),
        StructuralLine(
            id="d3_orthogonal_chord",
            color_slot=2,
            points=[cross_p1, axis_p2],
            weight=0.015,
            role="structural_contour",
        ),
        StructuralLine(
            id="d3_balance_bridge",
            color_slot=1,
            points=[cross_p2, (cx, cy), axis_p1],
            weight=0.014,
            role="internal_structure",
        ),
    ]

    d3_regions, d3_lines, d3_ws = _calibrate_draft_whitespace(
        d3_raw_regions,
        d3_raw_lines,
        geom.centroid,
        focal_center,
        min(0.90, whitespace_target + 0.05),
    )
    draft_3 = StyleDraft(
        draft_id="style_constructivist_symbol",
        name="极简符号构成 / Minimalist Constructivist Symbol",
        description=(
            "High-restraint constructivist symbol aligning octagonal and chord primitives "
            "along the subject's principal posture axis with counter-balanced accents."
        ),
        engine="algorithmic",
        whitespace_ratio=d3_ws,
        focal_center=focal_center,
        regions=d3_regions,
        structural_lines=d3_lines,
    )
    draft_3.preview_svg = render_style_draft_preview_svg(draft_3)

    return [draft_1, draft_2, draft_3]
