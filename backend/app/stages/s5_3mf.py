"""Stage 5: BambuLab X2D & MakerWorld Multi-Color .3mf Project Engine.

Generates:
1. Lightweight 3D preview payload (`Preview3DPayload`) for the interactive Three.js viewport,
   modeling the 0.40 mm Light Gray base grid and 3D-arched cross-stitch threads where the
   upper diagonal thread (`/`) arches cleanly over the lower diagonal thread (`\\`) at the
   center intersection (`dz(t) = stitch_arch_z_mm * sin(pi * t)^2`).
2. Watertight, closed, outward-oriented 2-manifold triangulated meshes via `sweep_ribbon_mesh`.
3. Complete OPC `.3mf` ZIP archive for Bambu Studio / MakerWorld configured for Bambu Lab X2D
   with 0.4 mm nozzle, Arachne thin-wall perimeter generator, multi-color extruder assignment
   (Extruder 1 = Light Gray Grid `#C4C8CE`, Extruders 2..K = functional thread colors),
   centered at (128.0, 128.0, 0.0) on the 256 x 256 mm build plate.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageDraw

from ..schemas import (
    Bambu3mfConfig,
    Bambu3mfExportResult,
    ColorDraft,
    FunctionalColor,
    GridDraft,
    Point2D,
    Point3D,
    Preview3DPayload,
    Preview3DThread,
)

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "bambu_x2d_3mf"

DEFAULT_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="config" ContentType="text/xml"/>
</Types>
"""

DEFAULT_RELS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
  <Relationship Target="/Metadata/plate_1.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
</Relationships>
"""


def _extract_grid_dimensions(grid_draft: GridDraft) -> tuple[int, int, float]:
    """Return (cols, rows, cell_size_mm) from GridDraft or its nested GridConfig."""
    cfg = getattr(grid_draft, "config", None)
    cols = getattr(grid_draft, "cols", None) or (
        cfg.cols if cfg is not None else (len(grid_draft.cells[0]) if grid_draft.cells else 60)
    )
    rows = getattr(grid_draft, "rows", None) or (
        cfg.rows if cfg is not None else (len(grid_draft.cells) if grid_draft.cells else 60)
    )
    cell_size_mm = getattr(grid_draft, "cell_size_mm", None) or (
        cfg.cell_size_mm if cfg is not None else 2.5
    )
    return int(cols), int(rows), float(cell_size_mm)


def _deterministic_uuid(seed: str) -> str:
    """Create a deterministic UUIDv4-formatted string from a seed string."""
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-4{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}"


def cubic_bezier_2d(
    p0: Point2D, p1: Point2D, p2: Point2D, p3: Point2D, t: float
) -> Point2D:
    """Evaluate a 2D cubic Bézier curve at parameter t in [0.0, 1.0]."""
    u = 1.0 - t
    b0 = u * u * u
    b1 = 3.0 * u * u * t
    b2 = 3.0 * u * t * t
    b3 = t * t * t
    x = b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0]
    y = b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1]
    return (x, y)


def build_bulged_bezier_control_points(
    start: Point2D, end: Point2D, bulge_offset_mm: float
) -> tuple[Point2D, Point2D, Point2D, Point2D]:
    """Compute cubic Bézier control points (P0, P1, P2, P3) with perpendicular center bulge.

    The control points P1 and P2 are placed at 1/3 and 2/3 along the chord and offset
    perpendicularly by (4/3) * bulge_offset_mm so that at t = 0.5 the curve midpoint
    deflects by exactly `bulge_offset_mm` from the straight diagonal chord.
    """
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length = math.hypot(dx, dy)
    if length <= 1e-9:
        return (start, start, end, end)

    nx = -dy / length
    ny = dx / length
    ctrl_offset = (4.0 / 3.0) * bulge_offset_mm

    p1 = (
        start[0] + dx / 3.0 + ctrl_offset * nx,
        start[1] + dy / 3.0 + ctrl_offset * ny,
    )
    p2 = (
        start[0] + (2.0 * dx) / 3.0 + ctrl_offset * nx,
        start[1] + (2.0 * dy) / 3.0 + ctrl_offset * ny,
    )
    return (start, p1, p2, end)


def compute_cell_thread_polylines(
    row: int,
    col: int,
    rows: int,
    cell_size_mm: float,
    config: Bambu3mfConfig,
) -> tuple[list[Point3D], list[Point3D]]:
    """Compute 3D centerline polylines (lower_points, upper_points) for a single stitch cell.

    In Z-up 3D printer coordinates:
      x0 = col * p, x1 = (col + 1) * p
      y0 = (rows - 1 - row) * p, y1 = (rows - row) * p
    - Lower Diagonal (`\\`): from (x0, y1) to (x1, y0), spanning Z in [0.40, 0.60 mm]
      along the interior span and anchored into the grid corners at Z_bottom = grid_height_mm - 0.04.
    - Upper Diagonal (`/`): from (x0, y0) to (x1, y1), with smooth bell-shaped 3D Z-arch
      dz(t) = stitch_arch_z_mm * sin(pi * t)^2 so it bridges cleanly over the lower thread
      at the center intersection (Z in [0.60, 0.80 mm] at t = 0.5) while anchoring into the
      grid corners at t = 0 and t = 1.
    """
    p = cell_size_mm
    x0 = col * p
    x1 = (col + 1) * p
    y0 = (rows - 1 - row) * p
    y1 = y0 + p

    delta_xy = config.bulge_ratio * p
    n_seg = max(2, int(config.curve_segments))

    z_span_center = config.grid_height_mm + 0.5 * config.stitch_thickness_mm
    anchor_dip = min(0.04, 0.5 * config.grid_height_mm)

    lower_cp = build_bulged_bezier_control_points((x0, y1), (x1, y0), delta_xy)
    upper_cp = build_bulged_bezier_control_points((x0, y0), (x1, y1), delta_xy)

    lower_points: list[Point3D] = []
    upper_points: list[Point3D] = []

    for i in range(n_seg + 1):
        t = i / n_seg
        dip = anchor_dip if (i == 0 or i == n_seg) else 0.0

        lx, ly = cubic_bezier_2d(*lower_cp, t)
        lz = z_span_center - dip
        lower_points.append((round(lx, 5), round(ly, 5), round(lz, 5)))

        ux, uy = cubic_bezier_2d(*upper_cp, t)
        dz = config.stitch_arch_z_mm * (math.sin(math.pi * t) ** 2)
        uz = z_span_center - dip + dz
        upper_points.append((round(ux, 5), round(uy, 5), round(uz, 5)))

    return lower_points, upper_points


def sweep_ribbon_mesh(
    polyline: list[Point3D],
    width: float = 0.4,
    height: float = 0.2,
) -> tuple[list[Point3D], list[tuple[int, int, int]]]:
    """Sweep a rectangular cross-section (width x height) along a 3D polyline.

    Produces a closed, watertight, outward-oriented 2-manifold triangle mesh:
    - 4 rectangular cross-section vertices per station (deduplicated per tube)
    - 8 side triangles per segment
    - 2 start-cap triangles + 2 end-cap triangles
    - Every directed edge (u, v) has an exact opposite directed edge (v, u).
    """
    pts: list[Point3D] = []
    for pt in polyline:
        if not pts or math.dist(pts[-1], pt) > 1e-6:
            pts.append((float(pt[0]), float(pt[1]), float(pt[2])))

    if len(pts) < 2:
        raise ValueError("sweep_ribbon_mesh requires at least 2 distinct 3D points")

    hw = float(width) * 0.5
    hh = float(height) * 0.5
    m = len(pts)

    vertices: list[Point3D] = []
    for i, (px, py, pz) in enumerate(pts):
        if i == 0:
            tx = pts[1][0] - px
            ty = pts[1][1] - py
            tz = pts[1][2] - pz
        elif i == m - 1:
            tx = px - pts[m - 2][0]
            ty = py - pts[m - 2][1]
            tz = pz - pts[m - 2][2]
        else:
            tx = pts[i + 1][0] - pts[i - 1][0]
            ty = pts[i + 1][1] - pts[i - 1][1]
            tz = pts[i + 1][2] - pts[i - 1][2]

        len_xy = math.hypot(tx, ty)
        if len_xy > 1e-8:
            # Right-handed frame where T_xy = (tx, ty, 0)/len_xy, N = (-ty, tx, 0)/len_xy, B = (0, 0, 1)
            # N x B = +T_xy
            nx, ny, nz = -ty / len_xy, tx / len_xy, 0.0
            bx, by, bz = 0.0, 0.0, 1.0
        else:
            # Purely vertical fallback: T = (0, 0, sign(tz)), N = (1, 0, 0), B = (0, sign(tz), 0)
            sign_z = 1.0 if tz >= 0.0 else -1.0
            nx, ny, nz = 1.0, 0.0, 0.0
            bx, by, bz = 0.0, sign_z, 0.0

        # 4 counter-clockwise cross-section vertices in the (N, B) plane:
        # 0: (-hw, -hh), 1: (+hw, -hh), 2: (+hw, +hh), 3: (-hw, +hh)
        v0 = (
            round(px - hw * nx - hh * bx, 5),
            round(py - hw * ny - hh * by, 5),
            round(pz - hw * nz - hh * bz, 5),
        )
        v1 = (
            round(px + hw * nx - hh * bx, 5),
            round(py + hw * ny - hh * by, 5),
            round(pz + hw * nz - hh * bz, 5),
        )
        v2 = (
            round(px + hw * nx + hh * bx, 5),
            round(py + hw * ny + hh * by, 5),
            round(pz + hw * nz + hh * bz, 5),
        )
        v3 = (
            round(px - hw * nx + hh * bx, 5),
            round(py - hw * ny + hh * by, 5),
            round(pz - hw * nz + hh * bz, 5),
        )
        vertices.extend((v0, v1, v2, v3))

    triangles: list[tuple[int, int, int]] = []

    # Start cap at i = 0 (outward normal points in -T direction)
    triangles.append((0, 2, 1))
    triangles.append((0, 3, 2))

    # Side walls connecting station i to station i + 1
    for i in range(m - 1):
        base_i = 4 * i
        next_i = 4 * (i + 1)
        for k in range(4):
            k_next = (k + 1) & 3
            b_k = base_i + k
            b_kn = base_i + k_next
            n_k = next_i + k
            n_kn = next_i + k_next
            triangles.append((b_k, b_kn, n_kn))
            triangles.append((b_k, n_kn, n_k))

    # End cap at i = m - 1 (outward normal points in +T direction)
    end_base = 4 * (m - 1)
    triangles.append((end_base + 0, end_base + 1, end_base + 2))
    triangles.append((end_base + 0, end_base + 2, end_base + 3))

    return vertices, triangles


def _append_component_mesh(
    target_vertices: list[Point3D],
    target_triangles: list[tuple[int, int, int]],
    comp_vertices: list[Point3D],
    comp_triangles: list[tuple[int, int, int]],
) -> None:
    """Append a closed component mesh into an object mesh buffer with index offset."""
    v_offset = len(target_vertices)
    target_vertices.extend(comp_vertices)
    for v1, v2, v3 in comp_triangles:
        target_triangles.append((v1 + v_offset, v2 + v_offset, v3 + v_offset))


def build_grid_base_mesh(
    cols: int,
    rows: int,
    cell_size_mm: float,
    config: Bambu3mfConfig,
) -> tuple[list[Point3D], list[tuple[int, int, int]]]:
    """Generate the watertight 3D mesh for Part 1: Grid_Base_LightGray (Extruder 1).

    Includes:
    - All (rows + 1) horizontal grid bars (y = r * p for r in 0..rows, x in [0, W]).
    - All (cols + 1) vertical grid bars (x = c * p for c in 0..cols, y in [0, H]).
    - Outer 4 border bars reinforced with `config.frame_width_mm` when `config.include_outer_frame` is True.
    """
    p = cell_size_mm
    width_mm = cols * p
    height_mm = rows * p
    z_mid = config.grid_height_mm * 0.5
    h = config.grid_height_mm

    vertices: list[Point3D] = []
    triangles: list[tuple[int, int, int]] = []

    # Horizontal bars (rows + 1)
    for r in range(rows + 1):
        y = r * p
        is_border = r == 0 or r == rows
        bar_w = (
            config.frame_width_mm
            if (config.include_outer_frame and is_border)
            else config.line_width_mm
        )
        comp_v, comp_t = sweep_ribbon_mesh(
            [(0.0, y, z_mid), (width_mm, y, z_mid)],
            width=bar_w,
            height=h,
        )
        _append_component_mesh(vertices, triangles, comp_v, comp_t)

    # Vertical bars (cols + 1)
    for c in range(cols + 1):
        x = c * p
        is_border = c == 0 or c == cols
        bar_w = (
            config.frame_width_mm
            if (config.include_outer_frame and is_border)
            else config.line_width_mm
        )
        comp_v, comp_t = sweep_ribbon_mesh(
            [(x, 0.0, z_mid), (x, height_mm, z_mid)],
            width=bar_w,
            height=h,
        )
        _append_component_mesh(vertices, triangles, comp_v, comp_t)

    return vertices, triangles


def _build_color_lookup(color_draft: ColorDraft) -> dict[int, FunctionalColor]:
    """Map 1-based color_slot -> FunctionalColor."""
    return {fc.slot: fc for fc in color_draft.colors}


def _resolve_functional_color(
    slot: int, color_lookup: dict[int, FunctionalColor]
) -> FunctionalColor:
    """Look up FunctionalColor by slot or synthesize a valid fallback."""
    if slot in color_lookup:
        return color_lookup[slot]
    fallback_palette = [
        "#D1493F",
        "#2A5C8A",
        "#E09F3E",
        "#3B7A57",
        "#6D4C7D",
        "#333333",
    ]
    hex_val = fallback_palette[(slot - 1) % len(fallback_palette)]
    return FunctionalColor(
        slot=slot,
        name=f"Color Slot {slot}",
        hex=hex_val,
        oklch=(0.55, 0.14, float((slot * 67) % 360)),
        role="primary_code",
        bambu_extruder=slot + 1,
    )


def build_preview_3d_payload(
    grid_draft: GridDraft,
    color_draft: ColorDraft,
    config: Bambu3mfConfig | None = None,
) -> Preview3DPayload:
    """Build the lightweight 3D preview payload for the Three.js BambuLab X2D viewport."""
    cfg = config or Bambu3mfConfig()
    cols, rows, p = _extract_grid_dimensions(grid_draft)
    width_mm = round(cols * p, 4)
    height_mm = round(rows * p, 4)
    z_grid = round(cfg.grid_height_mm * 0.5, 4)

    grid_lines: list[tuple[Point3D, Point3D]] = []
    for r in range(rows + 1):
        y = round(r * p, 4)
        grid_lines.append(((0.0, y, z_grid), (width_mm, y, z_grid)))
    for c in range(cols + 1):
        x = round(c * p, 4)
        grid_lines.append(((x, 0.0, z_grid), (x, height_mm, z_grid)))

    color_lookup = _build_color_lookup(color_draft)
    threads: list[Preview3DThread] = []

    for row_idx in range(min(rows, len(grid_draft.cells))):
        row_cells = grid_draft.cells[row_idx]
        for col_idx in range(min(cols, len(row_cells))):
            slot = int(row_cells[col_idx])
            if slot <= 0:
                continue
            fc = _resolve_functional_color(slot, color_lookup)
            lower_pts, upper_pts = compute_cell_thread_polylines(
                row=row_idx,
                col=col_idx,
                rows=rows,
                cell_size_mm=p,
                config=cfg,
            )
            threads.append(
                Preview3DThread(
                    row=row_idx,
                    col=col_idx,
                    color_slot=slot,
                    hex=fc.hex,
                    extruder=fc.bambu_extruder,
                    lower_points=lower_pts,
                    upper_points=upper_pts,
                )
            )

    return Preview3DPayload(
        plate_size_mm=(256.0, 256.0),
        canvas_size_mm=(width_mm, height_mm),
        grid_lines=grid_lines,
        grid_hex=color_draft.grid_filament_hex,
        grid_height_mm=cfg.grid_height_mm,
        line_width_mm=cfg.line_width_mm,
        stitch_thickness_mm=cfg.stitch_thickness_mm,
        threads=threads,
    )


def render_plate_thumbnail_png(
    grid_draft: GridDraft,
    color_draft: ColorDraft,
    size_px: int = 512,
) -> bytes:
    """Auto-render a 512x512 PNG build-plate thumbnail using Pillow ImageDraw."""
    cols, rows, _ = _extract_grid_dimensions(grid_draft)
    img = Image.new("RGBA", (size_px, size_px), color_draft.base_hex)
    draw = ImageDraw.Draw(img)

    margin = int(size_px * 0.08)
    usable_w = size_px - 2 * margin
    usable_h = size_px - 2 * margin
    cell_w = usable_w / max(1, cols)
    cell_h = usable_h / max(1, rows)

    # Draw base grid lines
    for r in range(rows + 1):
        y = margin + r * cell_h
        width = 2 if (r == 0 or r == rows) else 1
        draw.line(
            [(margin, y), (margin + usable_w, y)],
            fill=color_draft.grid_filament_hex,
            width=width,
        )
    for c in range(cols + 1):
        x = margin + c * cell_w
        width = 2 if (c == 0 or c == cols) else 1
        draw.line(
            [(x, margin), (x, margin + usable_h)],
            fill=color_draft.grid_filament_hex,
            width=width,
        )

    color_lookup = _build_color_lookup(color_draft)
    stroke_w = max(2, int(round(min(cell_w, cell_h) * 0.24)))

    for r in range(min(rows, len(grid_draft.cells))):
        row_cells = grid_draft.cells[r]
        for c in range(min(cols, len(row_cells))):
            slot = int(row_cells[c])
            if slot <= 0:
                continue
            fc = _resolve_functional_color(slot, color_lookup)
            x0 = margin + c * cell_w
            y0 = margin + r * cell_h
            x1 = x0 + cell_w
            y1 = y0 + cell_h
            draw.line([(x0, y0), (x1, y1)], fill=fc.hex, width=stroke_w)
            draw.line([(x0, y1), (x1, y0)], fill=fc.hex, width=stroke_w)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _sanitize_part_name(raw_name: str) -> str:
    """Create a clean ASCII/Unicode identifier suitable for 3MF part metadata."""
    cleaned = re.sub(r"[^\w\-]+", "_", raw_name.strip())
    return cleaned.strip("_") or "Color"


def _build_3dmodel_xml(
    part_specs: list[dict[str, Any]],
    assembly_id: int,
    build_offset_x: float,
    build_offset_y: float,
) -> bytes:
    """Serialize 3D/3dmodel.model XML following the 3MF Core + Production specification."""
    ns_core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    ns_bambu = "http://schemas.bambulab.com/package/2021"
    ns_prod = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"

    ET.register_namespace("", ns_core)
    ET.register_namespace("BambuStudio", ns_bambu)
    ET.register_namespace("p", ns_prod)

    model_el = ET.Element(
        f"{{{ns_core}}}model",
        {
            "unit": "millimeter",
            "xml:lang": "en-US",
            f"{{{ns_core}}}requiredextensions": "p",
        },
    )
    # Note: ElementTree places namespace prefixes on attributes with `{uri}`; for
    # standard 3MF attribute `requiredextensions="p"` we set it directly:
    if f"{{{ns_core}}}requiredextensions" in model_el.attrib:
        del model_el.attrib[f"{{{ns_core}}}requiredextensions"]
    model_el.set("requiredextensions", "p")

    meta_app = ET.SubElement(
        model_el, f"{{{ns_core}}}metadata", {"name": "Application"}
    )
    meta_app.text = "BambuStudio-01.10.00.89"

    meta_ver = ET.SubElement(
        model_el, f"{{{ns_core}}}metadata", {"name": "BambuStudio:3mfVersion"}
    )
    meta_ver.text = "1"

    resources_el = ET.SubElement(model_el, f"{{{ns_core}}}resources")

    for part in part_specs:
        obj_id = str(part["part_id"])
        obj_uuid = _deterministic_uuid(f"part_{obj_id}_{part['name']}")
        obj_el = ET.SubElement(
            resources_el,
            f"{{{ns_core}}}object",
            {
                "id": obj_id,
                "name": part["name"],
                "type": "model",
                f"{{{ns_prod}}}UUID": obj_uuid,
            },
        )
        mesh_el = ET.SubElement(obj_el, f"{{{ns_core}}}mesh")
        verts_el = ET.SubElement(mesh_el, f"{{{ns_core}}}vertices")
        for vx, vy, vz in part["vertices"]:
            ET.SubElement(
                verts_el,
                f"{{{ns_core}}}vertex",
                {
                    "x": f"{vx:.5f}",
                    "y": f"{vy:.5f}",
                    "z": f"{vz:.5f}",
                },
            )
        tris_el = ET.SubElement(mesh_el, f"{{{ns_core}}}triangles")
        for v1, v2, v3 in part["triangles"]:
            ET.SubElement(
                tris_el,
                f"{{{ns_core}}}triangle",
                {
                    "v1": str(v1),
                    "v2": str(v2),
                    "v3": str(v3),
                },
            )

    # Top-level parent assembly object combining all mesh parts
    assembly_uuid = _deterministic_uuid(f"assembly_{assembly_id}")
    assembly_el = ET.SubElement(
        resources_el,
        f"{{{ns_core}}}object",
        {
            "id": str(assembly_id),
            "name": "3D_Print_Lace_Assembly",
            "type": "model",
            f"{{{ns_prod}}}UUID": assembly_uuid,
        },
    )
    components_el = ET.SubElement(assembly_el, f"{{{ns_core}}}components")
    for part in part_specs:
        comp_uuid = _deterministic_uuid(f"comp_{part['part_id']}")
        ET.SubElement(
            components_el,
            f"{{{ns_core}}}component",
            {
                "objectid": str(part["part_id"]),
                f"{{{ns_prod}}}UUID": comp_uuid,
                "transform": "1 0 0 0 1 0 0 0 1 0 0 0",
            },
        )

    build_uuid = _deterministic_uuid("build_plate_1")
    build_el = ET.SubElement(
        model_el,
        f"{{{ns_core}}}build",
        {f"{{{ns_prod}}}UUID": build_uuid},
    )
    item_uuid = _deterministic_uuid(f"build_item_{assembly_id}")
    ET.SubElement(
        build_el,
        f"{{{ns_core}}}item",
        {
            "objectid": str(assembly_id),
            f"{{{ns_prod}}}UUID": item_uuid,
            "transform": f"1 0 0 0 1 0 0 0 1 {build_offset_x:.4f} {build_offset_y:.4f} 0",
            "printable": "1",
        },
    )

    ET.indent(model_el, space="  ")
    xml_body = ET.tostring(model_el, encoding="unicode")
    # Ensure xmlns:BambuStudio is present on root element even if only referenced in metadata value
    if "xmlns:BambuStudio=" not in xml_body:
        xml_body = xml_body.replace(
            f'xmlns="{ns_core}"',
            f'xmlns="{ns_core}" xmlns:BambuStudio="{ns_bambu}"',
            1,
        )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n").encode("utf-8")


def _build_model_settings_xml(
    part_specs: list[dict[str, Any]],
    assembly_id: int,
) -> bytes:
    """Serialize Metadata/model_settings.config XML mapping parts to Bambu extruders."""
    config_el = ET.Element("config")
    obj_el = ET.SubElement(config_el, "object", {"id": str(assembly_id)})
    ET.SubElement(
        obj_el, "metadata", {"key": "name", "value": "3D_Print_Lace_Assembly"}
    )
    ET.SubElement(obj_el, "metadata", {"key": "extruder", "value": "1"})

    for part in part_specs:
        part_el = ET.SubElement(
            obj_el,
            "part",
            {"id": str(part["part_id"]), "subtype": "normal_part"},
        )
        ET.SubElement(
            part_el, "metadata", {"key": "name", "value": str(part["name"])}
        )
        ET.SubElement(
            part_el,
            "metadata",
            {"key": "extruder", "value": str(part["extruder"])},
        )
        ET.SubElement(
            part_el,
            "mesh_stat",
            {
                "face_count": str(len(part["triangles"])),
                "edges_fixed": "0",
                "degenerate_facets": "0",
                "facets_removed": "0",
                "facets_reversed": "0",
                "backwards_edges": "0",
            },
        )

    plate_el = ET.SubElement(config_el, "plate")
    ET.SubElement(plate_el, "metadata", {"key": "plater_id", "value": "1"})
    ET.SubElement(
        plate_el, "metadata", {"key": "plater_name", "value": "3D_Print_Lace"}
    )
    ET.SubElement(plate_el, "metadata", {"key": "printable", "value": "true"})
    ET.SubElement(
        plate_el,
        "metadata",
        {"key": "thumbnail_file", "value": "Metadata/plate_1.png"},
    )
    instance_el = ET.SubElement(plate_el, "model_instance")
    ET.SubElement(
        instance_el,
        "metadata",
        {"key": "object_id", "value": str(assembly_id)},
    )
    ET.SubElement(instance_el, "metadata", {"key": "instance_id", "value": "0"})
    ET.SubElement(instance_el, "metadata", {"key": "identify_id", "value": "1"})

    ET.indent(config_el, space="  ")
    xml_body = ET.tostring(config_el, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n").encode("utf-8")


def _build_project_settings_json(
    color_draft: ColorDraft,
    part_specs: list[dict[str, Any]],
    config: Bambu3mfConfig,
) -> bytes:
    """Build Metadata/project_settings.config JSON tailored for 0.4mm lace on Bambu Lab X2D."""
    template_path = TEMPLATE_DIR / "project_settings.json"
    if template_path.exists():
        settings: dict[str, Any] = json.loads(template_path.read_text(encoding="utf-8"))
    else:
        settings = {}

    # Determine full extruder palette array (1-indexed extruder -> 0-indexed list)
    max_extruder = 1
    for fc in color_draft.colors:
        max_extruder = max(max_extruder, fc.bambu_extruder)
    for part in part_specs:
        max_extruder = max(max_extruder, int(part["extruder"]))

    filament_colours = [color_draft.grid_filament_hex] + [
        "#333333" for _ in range(max_extruder - 1)
    ]
    for fc in sorted(color_draft.colors, key=lambda c: c.bambu_extruder):
        idx = fc.bambu_extruder - 1
        if 0 <= idx < len(filament_colours):
            filament_colours[idx] = fc.hex
    for part in part_specs:
        idx = int(part["extruder"]) - 1
        if 0 <= idx < len(filament_colours):
            filament_colours[idx] = part["hex"]

    line_w_str = f"{config.line_width_mm:g}"
    nozzle_str = f"{config.nozzle_diameter_mm:g}"

    settings.update(
        {
            "printer_model": config.printer_model,
            "nozzle_diameter": [nozzle_str],
            "wall_generator": "arachne",
            "detect_thin_wall": "1",
            "min_bead_width": "85%",
            "min_feature_size": "20%",
            "layer_height": "0.2",
            "initial_layer_print_height": "0.2",
            "line_width": line_w_str,
            "outer_wall_line_width": line_w_str,
            "inner_wall_line_width": line_w_str,
            "initial_layer_line_width": line_w_str,
            "filament_colour": filament_colours,
            "filament_type": ["PLA" for _ in filament_colours],
            "filament_settings_id": [
                "Bambu PLA Basic @BBL X2D" for _ in filament_colours
            ],
            "different_settings_to_system": [
                "wall_generator;detect_thin_wall;line_width;outer_wall_line_width;inner_wall_line_width",
                *["" for _ in filament_colours],
            ],
        }
    )
    return json.dumps(settings, indent=2, ensure_ascii=False).encode("utf-8")


def generate_bambu_3mf(
    grid_draft: GridDraft,
    color_draft: ColorDraft,
    config: Bambu3mfConfig | None = None,
    thumbnail_png_bytes: bytes | None = None,
) -> tuple[bytes, Bambu3mfExportResult]:
    """Generate a Bambu Studio & MakerWorld multi-color `.3mf` project file and export summary."""
    cfg = config or Bambu3mfConfig()
    cols, rows, p = _extract_grid_dimensions(grid_draft)
    width_mm = cols * p
    height_mm = rows * p

    preview_3d = build_preview_3d_payload(grid_draft, color_draft, cfg)

    # 1. Part 1: Light Gray Base Grid (Extruder 1)
    grid_verts, grid_tris = build_grid_base_mesh(cols, rows, p, cfg)
    part_specs: list[dict[str, Any]] = [
        {
            "part_id": 1,
            "name": "Grid_Base_LightGray",
            "color_slot": 0,
            "extruder": 1,
            "hex": color_draft.grid_filament_hex,
            "role": "grid_base",
            "stitch_count": 0,
            "vertices": grid_verts,
            "triangles": grid_tris,
        }
    ]

    # 2. Parts 2..M: Group colored cross-stitch threads by color_slot
    threads_by_slot: dict[int, list[Preview3DThread]] = {}
    for thread in preview_3d.threads:
        threads_by_slot.setdefault(thread.color_slot, []).append(thread)

    color_lookup = _build_color_lookup(color_draft)
    next_part_id = 2

    for slot in sorted(threads_by_slot.keys()):
        slot_threads = threads_by_slot[slot]
        fc = _resolve_functional_color(slot, color_lookup)
        part_verts: list[Point3D] = []
        part_tris: list[tuple[int, int, int]] = []

        for thread in slot_threads:
            low_v, low_t = sweep_ribbon_mesh(
                thread.lower_points,
                width=cfg.line_width_mm,
                height=cfg.stitch_thickness_mm,
            )
            _append_component_mesh(part_verts, part_tris, low_v, low_t)

            up_v, up_t = sweep_ribbon_mesh(
                thread.upper_points,
                width=cfg.line_width_mm,
                height=cfg.stitch_thickness_mm,
            )
            _append_component_mesh(part_verts, part_tris, up_v, up_t)

        safe_color_name = _sanitize_part_name(fc.name)
        part_specs.append(
            {
                "part_id": next_part_id,
                "name": f"Stitch_Color_{slot}_{safe_color_name}",
                "color_slot": slot,
                "extruder": fc.bambu_extruder,
                "hex": fc.hex,
                "role": fc.role,
                "stitch_count": len(slot_threads),
                "vertices": part_verts,
                "triangles": part_tris,
            }
        )
        next_part_id += 1

    assembly_id = next_part_id

    # Center the [0, W] x [0, H] model at (128.0, 128.0, 0.0) on the 256 x 256 mm build plate
    plate_center_x, plate_center_y = 128.0, 128.0
    build_offset_x = plate_center_x - (width_mm * 0.5)
    build_offset_y = plate_center_y - (height_mm * 0.5)

    # Read OPC templates
    content_types_path = TEMPLATE_DIR / "content_types.xml"
    rels_path = TEMPLATE_DIR / "rels.xml"
    content_types_bytes = (
        content_types_path.read_bytes()
        if content_types_path.exists()
        else DEFAULT_CONTENT_TYPES_XML.encode("utf-8")
    )
    rels_bytes = (
        rels_path.read_bytes()
        if rels_path.exists()
        else DEFAULT_RELS_XML.encode("utf-8")
    )

    model_xml_bytes = _build_3dmodel_xml(
        part_specs=part_specs,
        assembly_id=assembly_id,
        build_offset_x=build_offset_x,
        build_offset_y=build_offset_y,
    )
    model_settings_bytes = _build_model_settings_xml(
        part_specs=part_specs,
        assembly_id=assembly_id,
    )
    project_settings_bytes = _build_project_settings_json(
        color_draft=color_draft,
        part_specs=part_specs,
        config=cfg,
    )
    plate_png_bytes = (
        thumbnail_png_bytes
        if (thumbnail_png_bytes and len(thumbnail_png_bytes) > 0)
        else render_plate_thumbnail_png(grid_draft, color_draft, size_px=512)
    )

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as zf:
        zf.writestr("[Content_Types].xml", content_types_bytes)
        zf.writestr("_rels/.rels", rels_bytes)
        zf.writestr("3D/3dmodel.model", model_xml_bytes)
        zf.writestr("Metadata/model_settings.config", model_settings_bytes)
        zf.writestr("Metadata/project_settings.config", project_settings_bytes)
        zf.writestr("Metadata/plate_1.png", plate_png_bytes)

    archive_bytes = zip_buffer.getvalue()
    total_triangles = sum(len(part["triangles"]) for part in part_specs)

    extruder_map: list[dict[str, Any]] = [
        {
            "part_id": part["part_id"],
            "name": part["name"],
            "color_slot": part["color_slot"],
            "extruder": part["extruder"],
            "hex": part["hex"],
            "role": part["role"],
            "stitch_count": part["stitch_count"],
            "triangle_count": len(part["triangles"]),
        }
        for part in part_specs
    ]

    filename = f"lace_x2d_{grid_draft.grid_id}_{cols}x{rows}.3mf"
    export_result = Bambu3mfExportResult(
        filename=filename,
        file_size_bytes=len(archive_bytes),
        part_count=len(part_specs),
        total_triangles=total_triangles,
        extruder_map=extruder_map,
        preview_3d=preview_3d,
    )
    return archive_bytes, export_result
