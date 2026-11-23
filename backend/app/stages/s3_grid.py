"""Stage 3: Canvas Processing & Pixelated Grid Projection Engine (画布处理与像素化草稿).

Implements pure-Python exact computational geometry (Sutherland-Hodgman polygon clipping,
Shoelace polygon area, Liang-Barsky segment-box intersection & proximity scoring) and projects
normalized Stage 1 StyleDraft vector geometry onto the physical cross-stitch cell matrix
(default 60x60 cells @ 2.5mm = 150.0x150.0mm).

Supports three projection modes:
1. "strict_50" (Mode A): Cell fills to 100% with dominant color k* iff A_{r,c,k*} >= block_threshold.
2. "dominant_foreground" (Mode B): Cell fills to 100% with dominant color k* iff total foreground
   area sum_{k>=1} A_{r,c,k} >= block_threshold.
3. "hybrid_line_preserving" (Mode C): Applies Mode B for solid modular regions, then evaluates
   structural_lines and fills cells where a line passes through with effective coverage >= line_threshold.
"""

from __future__ import annotations

import math
import uuid
from typing import Callable

from ..schemas import (
    ColorDraft,
    GridConfig,
    GridDraft,
    Point2D,
    StyleDraft,
)

_EPS = 1e-12


def _clip_against_plane(
    vertices: list[tuple[float, float]],
    inside_fn: Callable[[tuple[float, float]], bool],
    intersect_fn: Callable[[tuple[float, float], tuple[float, float]], tuple[float, float]],
) -> list[tuple[float, float]]:
    """Sutherland-Hodgman single half-plane clip step."""
    if not vertices:
        return []
    out: list[tuple[float, float]] = []
    prev = vertices[-1]
    prev_inside = inside_fn(prev)
    for curr in vertices:
        curr_inside = inside_fn(curr)
        if curr_inside:
            if not prev_inside:
                out.append(intersect_fn(prev, curr))
            out.append(curr)
        elif prev_inside:
            out.append(intersect_fn(prev, curr))
        prev = curr
        prev_inside = curr_inside
    return out


def clip_polygon_to_box(
    poly: list[tuple[float, float]],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> list[tuple[float, float]]:
    """Clip a 2D polygon against the axis-aligned bounding box [x0, x1] x [y0, y1]
    using the Sutherland-Hodgman algorithm.
    """
    if len(poly) < 3:
        return []

    x_min, x_max = (x0, x1) if x0 <= x1 else (x1, x0)
    y_min, y_max = (y0, y1) if y0 <= y1 else (y1, y0)

    def _ix(
        p_a: tuple[float, float], p_b: tuple[float, float], x_edge: float
    ) -> tuple[float, float]:
        dx = p_b[0] - p_a[0]
        if abs(dx) < _EPS:
            return (x_edge, p_a[1])
        t = (x_edge - p_a[0]) / dx
        return (x_edge, p_a[1] + t * (p_b[1] - p_a[1]))

    def _iy(
        p_a: tuple[float, float], p_b: tuple[float, float], y_edge: float
    ) -> tuple[float, float]:
        dy = p_b[1] - p_a[1]
        if abs(dy) < _EPS:
            return (p_a[0], y_edge)
        t = (y_edge - p_a[1]) / dy
        return (p_a[0] + t * (p_b[0] - p_a[0]), y_edge)

    pts = list(poly)
    # 1. Left plane: x >= x_min
    pts = _clip_against_plane(
        pts,
        lambda p: p[0] >= x_min - _EPS,
        lambda a, b: _ix(a, b, x_min),
    )
    if len(pts) < 3:
        return []
    # 2. Right plane: x <= x_max
    pts = _clip_against_plane(
        pts,
        lambda p: p[0] <= x_max + _EPS,
        lambda a, b: _ix(a, b, x_max),
    )
    if len(pts) < 3:
        return []
    # 3. Bottom/Top plane: y >= y_min
    pts = _clip_against_plane(
        pts,
        lambda p: p[1] >= y_min - _EPS,
        lambda a, b: _iy(a, b, y_min),
    )
    if len(pts) < 3:
        return []
    # 4. Top/Bottom plane: y <= y_max
    pts = _clip_against_plane(
        pts,
        lambda p: p[1] <= y_max + _EPS,
        lambda a, b: _iy(a, b, y_max),
    )
    if len(pts) < 3:
        return []
    return pts


def polygon_area(poly: list[tuple[float, float]]) -> float:
    """Compute the unsigned area of a 2D polygon using the Shoelace formula."""
    n = len(poly)
    if n < 3:
        return 0.0
    area2 = 0.0
    j = n - 1
    for i in range(n):
        area2 += (poly[j][0] * poly[i][1]) - (poly[i][0] * poly[j][1])
        j = i
    return abs(area2) * 0.5


def segment_intersects_box_distance(
    p0: tuple[float, float],
    p1: tuple[float, float],
    cx: float,
    cy: float,
    half_size: float,
) -> tuple[bool, float]:
    """Check whether line segment p0->p1 intersects the cell box centered at (cx, cy)
    with radius half_size ([cx - half_size, cx + half_size] x [cy - half_size, cy + half_size]),
    and return (intersects, normalized_coverage_score).

    Uses Liang-Barsky parametric clipping to obtain the exact clipped segment inside the cell,
    combining normalized chord length and center proximity into a coverage score in [0.0, 1.0].
    """
    if half_size <= 0.0:
        return (False, 0.0)

    x_min = cx - half_size
    x_max = cx + half_size
    y_min = cy - half_size
    y_max = cy + half_size

    dx = p1[0] - p0[0]
    dy = p1[1] - p0[1]

    # Degenerate zero-length segment
    if abs(dx) < _EPS and abs(dy) < _EPS:
        if x_min - _EPS <= p0[0] <= x_max + _EPS and y_min - _EPS <= p0[1] <= y_max + _EPS:
            dist = math.hypot(p0[0] - cx, p0[1] - cy)
            max_r = math.sqrt(2.0) * half_size
            score = max(0.0, min(1.0, 1.0 - dist / max_r))
            return (True, score)
        return (False, 0.0)

    t0 = 0.0
    t1 = 1.0
    checks = (
        (-dx, p0[0] - x_min),
        (dx, x_max - p0[0]),
        (-dy, p0[1] - y_min),
        (dy, y_max - p0[1]),
    )
    for p_val, q_val in checks:
        if abs(p_val) < _EPS:
            if q_val < -_EPS:
                return (False, 0.0)
        else:
            t = q_val / p_val
            if p_val < 0.0:
                if t > t1 + _EPS:
                    return (False, 0.0)
                if t > t0:
                    t0 = t
            else:
                if t < t0 - _EPS:
                    return (False, 0.0)
                if t < t1:
                    t1 = t

    if t0 > t1 + _EPS:
        return (False, 0.0)

    t0 = max(0.0, min(1.0, t0))
    t1 = max(t0, min(1.0, t1))

    # Clipped endpoints inside the box
    q0x = p0[0] + t0 * dx
    q0y = p0[1] + t0 * dy
    q1x = p0[0] + t1 * dx
    q1y = p0[1] + t1 * dy

    box_size = 2.0 * half_size
    clipped_len = math.hypot(q1x - q0x, q1y - q0y)
    chord_ratio = min(1.0, clipped_len / box_size)

    # Closest point on the clipped segment q0->q1 to the cell center (cx, cy)
    cdx = q1x - q0x
    cdy = q1y - q0y
    c_len2 = cdx * cdx + cdy * cdy
    if c_len2 < _EPS:
        closest_x, closest_y = q0x, q0y
    else:
        u = ((cx - q0x) * cdx + (cy - q0y) * cdy) / c_len2
        u = max(0.0, min(1.0, u))
        closest_x = q0x + u * cdx
        closest_y = q0y + u * cdy

    dist_center = math.hypot(closest_x - cx, closest_y - cy)
    max_radius = math.sqrt(2.0) * half_size
    proximity_score = max(0.0, min(1.0, 1.0 - (dist_center / max_radius)))

    # Combine chord coverage and center proximity so corner grazes remain small (<0.20)
    # while center-crossing lines and short interior polyline segments score high.
    score = max(0.0, min(1.0, 0.5 * chord_ratio + 0.5 * proximity_score))
    return (True, score)


def _clamp_color_slot(slot: int, max_slot: int) -> int:
    """Clamp a 1-based color slot to [1, max_slot], preserving 0 for negative space."""
    if slot <= 0:
        return 0
    if max_slot <= 0:
        return 1
    return max(1, min(int(slot), max_slot))


def _transform_point(
    pt: Point2D,
    cols: int,
    rows: int,
    scale: float,
    offset_x: float,
    offset_y: float,
) -> tuple[float, float]:
    """Transform a normalized [0.0, 1.0] point into grid coordinates [0, cols] x [0, rows]."""
    sx = 0.5 + (pt[0] - 0.5) * scale
    sy = 0.5 + (pt[1] - 0.5) * scale
    return (sx * cols + offset_x, sy * rows + offset_y)


def _select_dominant_color(slot_areas: dict[int, float]) -> tuple[int, float]:
    """Return (dominant_color_slot, max_slot_area) among positive color slots."""
    best_slot = 0
    best_area = 0.0
    for slot, area in slot_areas.items():
        if slot >= 1 and (area > best_area + _EPS or (abs(area - best_area) <= _EPS and slot > best_slot)):
            best_area = area
            best_slot = slot
    return (best_slot, best_area)


def project_style_to_grid(
    style_draft: StyleDraft,
    color_draft: ColorDraft,
    config: GridConfig | None = None,
) -> GridDraft:
    """Project a Stage 1 StyleDraft and Stage 2 ColorDraft onto the Stage 3 cross-stitch grid."""
    cfg = config.model_copy(deep=True) if config is not None else GridConfig()
    cols = cfg.cols
    rows = cfg.rows
    max_slot = max((c.slot for c in color_draft.colors), default=max(1, len(color_draft.colors)))

    # Per-cell map of {color_slot: exact_clipped_area}
    cell_color_areas: list[list[dict[int, float]]] = [
        [{} for _ in range(cols)] for _ in range(rows)
    ]

    # 1. Exact polygon-cell clipping with AABB culling for all VectorRegions
    for region in style_draft.regions:
        if len(region.exterior) < 3:
            continue
        ext_grid = [
            _transform_point(p, cols, rows, cfg.scale, cfg.offset_x, cfg.offset_y)
            for p in region.exterior
        ]
        holes_grid = [
            [
                _transform_point(p, cols, rows, cfg.scale, cfg.offset_x, cfg.offset_y)
                for p in hole
            ]
            for hole in region.holes
            if len(hole) >= 3
        ]

        xs = [p[0] for p in ext_grid]
        ys = [p[1] for p in ext_grid]
        c_min = max(0, int(math.floor(min(xs))))
        c_max = min(cols - 1, int(math.floor(max(xs))))
        r_min = max(0, int(math.floor(min(ys))))
        r_max = min(rows - 1, int(math.floor(max(ys))))

        if c_min > c_max or r_min > r_max:
            continue

        is_negative = region.color_slot <= 0 or region.semantic_role == "negative_space"
        clamped_slot = 0 if is_negative else _clamp_color_slot(region.color_slot, max_slot)

        for r in range(r_min, r_max + 1):
            y0 = float(r)
            y1 = float(r + 1)
            for c in range(c_min, c_max + 1):
                x0 = float(c)
                x1 = float(c + 1)
                clipped_ext = clip_polygon_to_box(ext_grid, x0, y0, x1, y1)
                if not clipped_ext:
                    continue
                area = polygon_area(clipped_ext)
                if area <= _EPS:
                    continue

                for h_grid in holes_grid:
                    clipped_hole = clip_polygon_to_box(h_grid, x0, y0, x1, y1)
                    if clipped_hole:
                        area -= polygon_area(clipped_hole)

                area = max(0.0, min(1.0, area))
                if area <= _EPS:
                    continue

                slot_map = cell_color_areas[r][c]
                if is_negative:
                    old_total = sum(slot_map.values())
                    if old_total > _EPS:
                        factor = max(0.0, old_total - area) / old_total
                        for k in list(slot_map.keys()):
                            slot_map[k] *= factor
                            if slot_map[k] <= _EPS:
                                del slot_map[k]
                else:
                    slot_map[clamped_slot] = min(1.0, slot_map.get(clamped_slot, 0.0) + area)

    # 2. Evaluate cell classification under the selected projection_mode
    cells: list[list[int]] = [[0 for _ in range(cols)] for _ in range(rows)]
    cell_coverages: list[list[float]] = [[0.0 for _ in range(cols)] for _ in range(rows)]
    structural_cells: set[tuple[int, int]] = set()

    mode = cfg.projection_mode
    block_thresh = cfg.block_threshold

    for r in range(rows):
        for c in range(cols):
            slot_map = cell_color_areas[r][c]
            if not slot_map:
                continue
            total_fg_area = min(1.0, sum(slot_map.values()))
            dom_slot, dom_area = _select_dominant_color(slot_map)

            if mode == "strict_50":
                cell_coverages[r][c] = round(dom_area, 4)
                if dom_slot >= 1 and dom_area + _EPS >= block_thresh:
                    cells[r][c] = dom_slot
            else:
                # Both "dominant_foreground" (Mode B) and "hybrid_line_preserving" (Mode C)
                # fill solid modular regions when total foreground area >= block_threshold.
                cell_coverages[r][c] = round(total_fg_area, 4)
                if dom_slot >= 1 and total_fg_area + _EPS >= block_thresh:
                    cells[r][c] = dom_slot

    # 3. Mode C ("hybrid_line_preserving"): Evaluate structural_lines with line_threshold
    if mode == "hybrid_line_preserving":
        line_thresh = cfg.line_threshold
        avg_grid_dim = 0.5 * (cols + rows)

        for sline in style_draft.structural_lines:
            if len(sline.points) < 2:
                continue
            line_slot = _clamp_color_slot(sline.color_slot, max_slot)
            if line_slot <= 0:
                continue

            pts_grid = [
                _transform_point(p, cols, rows, cfg.scale, cfg.offset_x, cfg.offset_y)
                for p in sline.points
            ]
            # Normalize ribbon weight contribution in grid-cell units
            ribbon_width_cells = max(0.0, sline.weight * avg_grid_dim)

            # Accumulate best/combined coverage per cell for this polyline
            line_cell_scores: dict[tuple[int, int], float] = {}

            for idx in range(len(pts_grid) - 1):
                p0 = pts_grid[idx]
                p1 = pts_grid[idx + 1]
                c_min = max(0, int(math.floor(min(p0[0], p1[0]) - 0.5)))
                c_max = min(cols - 1, int(math.ceil(max(p0[0], p1[0]) + 0.5)))
                r_min = max(0, int(math.floor(min(p0[1], p1[1]) - 0.5)))
                r_max = min(rows - 1, int(math.ceil(max(p0[1], p1[1]) + 0.5)))

                for r in range(r_min, r_max + 1):
                    cy = r + 0.5
                    for c in range(c_min, c_max + 1):
                        cx = c + 0.5
                        hit, seg_score = segment_intersects_box_distance(p0, p1, cx, cy, 0.5)
                        if not hit:
                            continue
                        # Combine geometric distance/chord score with physical ribbon area
                        ribbon_bonus = min(0.25, ribbon_width_cells * 0.15) if seg_score >= 0.15 else 0.0
                        effective_cov = min(1.0, seg_score + ribbon_bonus)
                        prev_cov = line_cell_scores.get((r, c), 0.0)
                        if effective_cov > prev_cov:
                            line_cell_scores[(r, c)] = effective_cov

            for (r, c), cov in line_cell_scores.items():
                if cov > cell_coverages[r][c]:
                    cell_coverages[r][c] = round(min(1.0, cov), 4)
                if cov + _EPS >= line_thresh:
                    cells[r][c] = line_slot
                    structural_cells.add((r, c))

    # 4. Optional despeckle: remove isolated 1-cell noise speckles (0 active 8-connected neighbors)
    # that were not placed by a structural line.
    if cfg.despeckle:
        snapshot = [row[:] for row in cells]
        for r in range(rows):
            for c in range(cols):
                if snapshot[r][c] == 0 or (r, c) in structural_cells:
                    continue
                has_neighbor = False
                for dr in (-1, 0, 1):
                    nr = r + dr
                    if nr < 0 or nr >= rows:
                        continue
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0:
                            continue
                        nc = c + dc
                        if 0 <= nc < cols and snapshot[nr][nc] > 0:
                            has_neighbor = True
                            break
                    if has_neighbor:
                        break
                if not has_neighbor:
                    cells[r][c] = 0

    # 5. Apply manual_overrides ("row,col" -> color_slot) last so user brush edits always win
    for key, raw_slot in cfg.manual_overrides.items():
        try:
            parts = key.split(",")
            if len(parts) != 2:
                continue
            r = int(parts[0].strip())
            c = int(parts[1].strip())
        except (ValueError, TypeError):
            continue
        if 0 <= r < rows and 0 <= c < cols:
            if raw_slot <= 0:
                cells[r][c] = 0
                cell_coverages[r][c] = 0.0
            else:
                cells[r][c] = _clamp_color_slot(raw_slot, max_slot)
                cell_coverages[r][c] = max(cell_coverages[r][c], 1.0)

    # 6. Compute aggregate statistics
    color_counts: dict[int, int] = {}
    filled_cell_count = 0
    for r in range(rows):
        for c in range(cols):
            slot = cells[r][c]
            if slot > 0:
                filled_cell_count += 1
                color_counts[slot] = color_counts.get(slot, 0) + 1

    total_cells = max(1, rows * cols)
    fill_percentage = round((filled_cell_count / total_cells) * 100.0, 2)
    canvas_width_mm = round(cols * cfg.cell_size_mm, 4)
    canvas_height_mm = round(rows * cfg.cell_size_mm, 4)

    return GridDraft(
        grid_id=f"grid_{uuid.uuid4().hex[:8]}",
        style_draft_id=style_draft.draft_id,
        palette_id=color_draft.palette_id,
        config=cfg,
        canvas_width_mm=canvas_width_mm,
        canvas_height_mm=canvas_height_mm,
        cells=cells,
        cell_coverages=cell_coverages,
        filled_cell_count=filled_cell_count,
        fill_percentage=fill_percentage,
        color_counts=color_counts,
    )
