"""Stage 4: Organic Curved Cross-Stitch Plotter SVG & PNG Draft Engine (SVG与PNG草稿生成).

Generates physical-dimension multi-layer Plotter SVG (`150.0mm x 150.0mm`, `0.4mm` stroke width)
and an 800x800 high-resolution PNG preview draft using cubic Bézier cross-stitch diagonals
that gently bulge at their center intersection (`两条曲线在交叉处中间微微隆起避免呆板`).
"""

from __future__ import annotations

import base64
import io
import math
from xml.sax.saxutils import escape as xml_escape

from PIL import Image, ImageDraw

from ..schemas import (
    ColorDraft,
    FunctionalColor,
    GridDraft,
    Point2D,
    SvgConfig,
    SvgExportResult,
)

_INV_SQRT2 = 1.0 / math.sqrt(2.0)


def compute_cross_stitch_beziers(
    col: int,
    row: int,
    cell_size_mm: float,
    bulge_ratio: float = 0.09,
    corner_inset_mm: float = 0.0,
) -> tuple[
    tuple[Point2D, Point2D, Point2D, Point2D],
    tuple[Point2D, Point2D, Point2D, Point2D],
]:
    """Compute cubic Bézier control points (lower_stroke, upper_stroke) for cell (row, col).

    - Lower stroke (`\\`): from top-left (x0+inset, y0+inset) to bottom-right (x1-inset, y1-inset).
      Control points P1 (1/3 along diagonal) and P2 (2/3 along diagonal) are displaced perpendicular
      to the `\\` diagonal by `delta = bulge_ratio * cell_size_mm`.
    - Upper stroke (`/`): from bottom-left (x0+inset, y1-inset) to top-right (x1-inset, y0+inset).
      Control points Q1 (1/3 along `/`) and Q2 (2/3 along `/`) are displaced perpendicular
      to the `/` diagonal by `delta = bulge_ratio * cell_size_mm`.
    """
    x0 = col * cell_size_mm
    y0 = row * cell_size_mm
    x1 = (col + 1) * cell_size_mm
    y1 = (row + 1) * cell_size_mm

    inset = max(0.0, min(corner_inset_mm, cell_size_mm * 0.45))
    xa = x0 + inset
    ya = y0 + inset
    xb = x1 - inset
    yb = y1 - inset

    span_x = xb - xa
    span_y = yb - ya
    delta = bulge_ratio * cell_size_mm

    # Lower stroke (\): (xa, ya) -> (xb, yb), unit normal n1 = (-1/sqrt(2), +1/sqrt(2))
    n1_x = -delta * _INV_SQRT2
    n1_y = delta * _INV_SQRT2
    p0: Point2D = (xa, ya)
    p1: Point2D = (xa + span_x / 3.0 + n1_x, ya + span_y / 3.0 + n1_y)
    p2: Point2D = (xa + 2.0 * span_x / 3.0 + n1_x, ya + 2.0 * span_y / 3.0 + n1_y)
    p3: Point2D = (xb, yb)

    # Upper stroke (/): (xa, yb) -> (xb, ya), unit normal n2 = (+1/sqrt(2), +1/sqrt(2))
    n2_x = delta * _INV_SQRT2
    n2_y = delta * _INV_SQRT2
    q0: Point2D = (xa, yb)
    q1: Point2D = (xa + span_x / 3.0 + n2_x, yb - span_y / 3.0 + n2_y)
    q2: Point2D = (xa + 2.0 * span_x / 3.0 + n2_x, yb - 2.0 * span_y / 3.0 + n2_y)
    q3: Point2D = (xb, ya)

    return ((p0, p1, p2, p3), (q0, q1, q2, q3))


def sample_cubic_bezier_2d(
    p0: Point2D,
    p1: Point2D,
    p2: Point2D,
    p3: Point2D,
    steps: int = 12,
) -> list[Point2D]:
    """Sample a 2D cubic Bézier curve into `steps + 1` polyline vertices."""
    n = max(1, int(steps))
    points: list[Point2D] = []
    for i in range(n + 1):
        t = i / n
        u = 1.0 - t
        u2 = u * u
        t2 = t * t
        b0 = u2 * u
        b1 = 3.0 * u2 * t
        b2 = 3.0 * u * t2
        b3 = t2 * t
        x = b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0]
        y = b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1]
        points.append((x, y))
    return points


def _order_cells_nearest_neighbor(cells_list: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Order (row, col) cells via greedy Nearest-Neighbor TSP to minimize pen-up travel distance."""
    n = len(cells_list)
    if n <= 2:
        return list(cells_list)

    unvisited = set(cells_list)
    curr = cells_list[0]
    unvisited.remove(curr)
    ordered = [curr]

    while unvisited:
        cr, cc = curr
        best_next = min(
            unvisited,
            key=lambda rc: ((rc[0] - cr) * (rc[0] - cr) + (rc[1] - cc) * (rc[1] - cc), rc[0], rc[1]),
        )
        unvisited.remove(best_next)
        ordered.append(best_next)
        curr = best_next

    return ordered


def _hex_to_rgb(hex_str: str, fallback: tuple[int, int, int] = (245, 241, 230)) -> tuple[int, int, int]:
    """Parse a #RRGGBB hex string into an (R, G, B) integer tuple."""
    s = (hex_str or "").strip().lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback


def _darken_rgb(rgb: tuple[int, int, int], factor: float = 0.62) -> tuple[int, int, int]:
    """Darken an RGB color for subtle center-crossing thread shadow rendering."""
    return (
        max(0, min(255, int(round(rgb[0] * factor)))),
        max(0, min(255, int(round(rgb[1] * factor)))),
        max(0, min(255, int(round(rgb[2] * factor)))),
    )


def _blend_rgb(
    c1: tuple[int, int, int], c2: tuple[int, int, int], alpha: float
) -> tuple[int, int, int]:
    """Linearly blend two RGB colors: (1 - alpha) * c1 + alpha * c2."""
    return (
        max(0, min(255, int(round(c1[0] * (1.0 - alpha) + c2[0] * alpha)))),
        max(0, min(255, int(round(c1[1] * (1.0 - alpha) + c2[1] * alpha)))),
        max(0, min(255, int(round(c1[2] * (1.0 - alpha) + c2[2] * alpha)))),
    )


def _format_mm(val: float) -> str:
    """Format millimeter dimension preserving `.1f` for whole floats like `150.0`."""
    fval = float(val)
    if fval.is_integer():
        return f"{fval:.1f}"
    return f"{round(fval, 4):g}"


def _sanitize_layer_id(name: str, slot: int) -> str:
    """Create a clean XML id suffix from a FunctionalColor name."""
    chars = [ch if (ch.isascii() and ch.isalnum()) else "_" for ch in name]
    slug = "".join(chars).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or f"Color{slot}"


def generate_svg_and_png(
    grid_draft: GridDraft,
    color_draft: ColorDraft,
    config: SvgConfig | None = None,
) -> SvgExportResult:
    """Generate physical-unit multi-layer Plotter SVG and 800x800 PNG draft from a GridDraft."""
    cfg = config.model_copy(deep=True) if config is not None else SvgConfig()

    cols = grid_draft.config.cols
    rows = grid_draft.config.rows
    cell_size_mm = grid_draft.config.cell_size_mm
    width_mm = round(cols * cell_size_mm, 4)
    height_mm = round(rows * cell_size_mm, 4)

    w_str = _format_mm(width_mm)
    h_str = _format_mm(height_mm)
    stroke_w_str = f"{round(float(cfg.stroke_width_mm), 4):g}"
    grid_stroke_w_str = f"{round(float(cfg.stroke_width_mm) * 0.6, 4):g}"

    # Build lookup map from color slot -> FunctionalColor
    color_map: dict[int, FunctionalColor] = {fc.slot: fc for fc in color_draft.colors}

    # Group filled cells by color slot
    cells_by_slot: dict[int, list[tuple[int, int]]] = {}
    stitch_count = 0
    for r in range(min(rows, len(grid_draft.cells))):
        row_vals = grid_draft.cells[r]
        for c in range(min(cols, len(row_vals))):
            slot = row_vals[c]
            if slot > 0:
                stitch_count += 1
                cells_by_slot.setdefault(slot, []).append((r, c))

    # 1. Construct physical-unit Plotter SVG
    svg_lines: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
        f'width="{w_str}mm" height="{h_str}mm" viewBox="0 0 {w_str} {h_str}">',
        f'  <rect id="background-base" x="0" y="0" width="{w_str}" height="{h_str}" '
        f'fill="{xml_escape(color_draft.base_hex)}"/>',
    ]

    if cfg.include_grid_layer:
        grid_hex = xml_escape(color_draft.grid_filament_hex)
        svg_lines.append(
            '  <g id="layer-0-grid" inkscape:groupmode="layer" inkscape:label="0_Grid_LightGray">'
        )
        for c in range(cols + 1):
            x_pos = f"{c * cell_size_mm:.3f}"
            svg_lines.append(
                f'    <line x1="{x_pos}" y1="0.000" x2="{x_pos}" y2="{height_mm:.3f}" '
                f'stroke="{grid_hex}" stroke-width="{grid_stroke_w_str}" stroke-opacity="0.55"/>'
            )
        for r in range(rows + 1):
            y_pos = f"{r * cell_size_mm:.3f}"
            svg_lines.append(
                f'    <line x1="0.000" y1="{y_pos}" x2="{width_mm:.3f}" y2="{y_pos}" '
                f'stroke="{grid_hex}" stroke-width="{grid_stroke_w_str}" stroke-opacity="0.55"/>'
            )
        svg_lines.append("  </g>")

    # Store ordered per-slot cells so PNG rendering uses identical ordering
    ordered_by_slot: dict[int, list[tuple[int, int]]] = {}
    for slot in sorted(cells_by_slot.keys()):
        raw_cells = cells_by_slot[slot]
        ordered_cells = (
            _order_cells_nearest_neighbor(raw_cells)
            if cfg.optimize_plotter_path
            else list(raw_cells)
        )
        ordered_by_slot[slot] = ordered_cells

        fc = color_map.get(slot)
        color_name = fc.name if fc is not None else f"Color_{slot}"
        color_hex = xml_escape(fc.hex if fc is not None else "#222222")
        layer_slug = _sanitize_layer_id(color_name, slot)
        layer_label = xml_escape(f"{slot}_{color_name}")

        svg_lines.append(
            f'  <g id="layer-{slot}-{layer_slug}" inkscape:groupmode="layer" '
            f'inkscape:label="{layer_label}">'
        )
        for r, c in ordered_cells:
            lower_bez, upper_bez = compute_cross_stitch_beziers(
                col=c,
                row=r,
                cell_size_mm=cell_size_mm,
                bulge_ratio=cfg.bulge_ratio,
                corner_inset_mm=cfg.corner_inset_mm,
            )
            p0, p1, p2, p3 = lower_bez
            q0, q1, q2, q3 = upper_bez
            svg_lines.append(
                f'    <path d="M {p0[0]:.3f} {p0[1]:.3f} '
                f'C {p1[0]:.3f} {p1[1]:.3f}, {p2[0]:.3f} {p2[1]:.3f}, {p3[0]:.3f} {p3[1]:.3f}" '
                f'stroke="{color_hex}" stroke-width="{stroke_w_str}" '
                f'stroke-linecap="round" fill="none"/>'
            )
            svg_lines.append(
                f'    <path d="M {q0[0]:.3f} {q0[1]:.3f} '
                f'C {q1[0]:.3f} {q1[1]:.3f}, {q2[0]:.3f} {q2[1]:.3f}, {q3[0]:.3f} {q3[1]:.3f}" '
                f'stroke="{color_hex}" stroke-width="{stroke_w_str}" '
                f'stroke-linecap="round" fill="none"/>'
            )
        svg_lines.append("  </g>")

    svg_lines.append("</svg>")
    svg_content = "\n".join(svg_lines)

    # 2. Render 800x800 high-resolution PNG draft via PIL
    png_w = 800
    png_h = 800
    base_rgb = _hex_to_rgb(color_draft.base_hex, (245, 241, 230))
    grid_rgb = _hex_to_rgb(color_draft.grid_filament_hex, (196, 200, 206))
    subtle_grid_rgb = _blend_rgb(base_rgb, grid_rgb, 0.55)

    img = Image.new("RGB", (png_w, png_h), base_rgb)
    draw = ImageDraw.Draw(img)

    scale_x = png_w / max(1e-6, width_mm)
    scale_y = png_h / max(1e-6, height_mm)
    avg_scale = 0.5 * (scale_x + scale_y)

    if cfg.include_grid_layer:
        for c in range(cols + 1):
            px = min(png_w - 1, max(0, int(round(c * cell_size_mm * scale_x))))
            draw.line([(px, 0), (px, png_h - 1)], fill=subtle_grid_rgb, width=1)
        for r in range(rows + 1):
            py = min(png_h - 1, max(0, int(round(r * cell_size_mm * scale_y))))
            draw.line([(0, py), (png_w - 1, py)], fill=subtle_grid_rgb, width=1)

    stroke_px = max(2, int(round(cfg.stroke_width_mm * avg_scale)))

    for slot in sorted(ordered_by_slot.keys()):
        fc = color_map.get(slot)
        thread_rgb = _hex_to_rgb(fc.hex if fc is not None else "#222222", (34, 34, 34))
        shadow_rgb = _blend_rgb(base_rgb, _darken_rgb(thread_rgb, 0.55), 0.75)

        for r, c in ordered_by_slot[slot]:
            lower_bez, upper_bez = compute_cross_stitch_beziers(
                col=c,
                row=r,
                cell_size_mm=cell_size_mm,
                bulge_ratio=cfg.bulge_ratio,
                corner_inset_mm=cfg.corner_inset_mm,
            )
            lower_pts = sample_cubic_bezier_2d(*lower_bez, steps=12)
            upper_pts = sample_cubic_bezier_2d(*upper_bez, steps=12)

            lower_px = [(pt[0] * scale_x, pt[1] * scale_y) for pt in lower_pts]
            upper_px = [(pt[0] * scale_x, pt[1] * scale_y) for pt in upper_pts]

            # Lower thread (\)
            draw.line(lower_px, fill=thread_rgb, width=stroke_px, joint="curve")

            # Subtle center crossing shadow under the upper diagonal (/)
            mid_slice = upper_px[3:10]
            if len(mid_slice) >= 2:
                shadow_px = [(pt[0] + 1.0, pt[1] + 1.0) for pt in mid_slice]
                draw.line(shadow_px, fill=shadow_rgb, width=stroke_px, joint="curve")

            # Upper thread (/) arching over the lower thread
            draw.line(upper_px, fill=thread_rgb, width=stroke_px, joint="curve")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_base64 = base64.b64encode(buf.getvalue()).decode("ascii")

    return SvgExportResult(
        svg_content=svg_content,
        svg_filename=f"{grid_draft.grid_id}_plotter.svg",
        png_filename=f"{grid_draft.grid_id}_draft.png",
        png_base64=png_base64,
        width_mm=width_mm,
        height_mm=height_mm,
        stitch_count=stitch_count,
    )
