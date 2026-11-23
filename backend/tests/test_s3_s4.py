"""Unit tests for Stage 3 (s3_grid.py) and Stage 4 (s4_svg.py)."""

from __future__ import annotations

import base64
import io
import math
import xml.etree.ElementTree as ET

from PIL import Image

from backend.app.schemas import (
    ColorDraft,
    FunctionalColor,
    GridConfig,
    StructuralLine,
    StyleDraft,
    SvgConfig,
    VectorRegion,
)
from backend.app.stages.s3_grid import (
    clip_polygon_to_box,
    polygon_area,
    project_style_to_grid,
    segment_intersects_box_distance,
)
from backend.app.stages.s4_svg import (
    compute_cross_stitch_beziers,
    generate_svg_and_png,
    sample_cubic_bezier_2d,
)


def _make_test_color_draft() -> ColorDraft:
    return ColorDraft(
        palette_id="pal_test",
        name="Test Palette",
        base_name="Warm White",
        base_hex="#F5F1E6",
        grid_filament_name="Light Gray Grid",
        grid_filament_hex="#C4C8CE",
        colors=[
            FunctionalColor(
                slot=1,
                name="Crimson Accent",
                hex="#D13B2E",
                oklch=(0.56, 0.20, 28.0),
                role="primary_code",
                bambu_extruder=2,
            ),
            FunctionalColor(
                slot=2,
                name="Deep Indigo",
                hex="#1E3A5F",
                oklch=(0.35, 0.09, 250.0),
                role="secondary_code",
                bambu_extruder=3,
            ),
            FunctionalColor(
                slot=3,
                name="Ochre Gold",
                hex="#D99B26",
                oklch=(0.72, 0.15, 78.0),
                role="accent_code",
                bambu_extruder=4,
            ),
        ],
    )


def test_clip_polygon_to_box_and_polygon_area_exact():
    # 1. Full unit square inside [0, 1] x [0, 1]
    unit_sq = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert math.isclose(polygon_area(unit_sq), 1.0, rel_tol=1e-9)
    clipped_full = clip_polygon_to_box(unit_sq, 0.0, 0.0, 1.0, 1.0)
    assert math.isclose(polygon_area(clipped_full), 1.0, rel_tol=1e-9)

    # 2. Half overlap: square [0.35, 1.35] x [0.0, 1.0] clipped to [0, 1] x [0, 1] -> area 0.65
    shifted_sq = [(0.35, 0.0), (1.35, 0.0), (1.35, 1.0), (0.35, 1.0)]
    clipped_half = clip_polygon_to_box(shifted_sq, 0.0, 0.0, 1.0, 1.0)
    assert math.isclose(polygon_area(clipped_half), 0.65, rel_tol=1e-9)

    # 3. Quarter corner overlap: [-0.5, 0.5] x [-0.5, 0.5] clipped to [0, 1] x [0, 1] -> area 0.25
    corner_sq = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
    clipped_corner = clip_polygon_to_box(corner_sq, 0.0, 0.0, 1.0, 1.0)
    assert math.isclose(polygon_area(clipped_corner), 0.25, rel_tol=1e-9)

    # 4. Right triangle inside box: (0,0)-(1,0)-(0,1) -> area 0.5
    tri = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]
    clipped_tri = clip_polygon_to_box(tri, 0.0, 0.0, 1.0, 1.0)
    assert math.isclose(polygon_area(clipped_tri), 0.5, rel_tol=1e-9)

    # 5. Disjoint polygon outside box -> empty list, area 0.0
    outside = [(2.0, 2.0), (3.0, 2.0), (3.0, 3.0), (2.0, 3.0)]
    clipped_out = clip_polygon_to_box(outside, 0.0, 0.0, 1.0, 1.0)
    assert clipped_out == []
    assert polygon_area(clipped_out) == 0.0


def test_segment_intersects_box_distance():
    # Segment passing horizontally through center (0.5, 0.5) of unit cell [0, 1] x [0, 1]
    hit_center, score_center = segment_intersects_box_distance(
        (0.0, 0.5), (1.0, 0.5), cx=0.5, cy=0.5, half_size=0.5
    )
    assert hit_center is True
    assert math.isclose(score_center, 1.0, rel_tol=1e-6)

    # Segment completely outside the box
    hit_miss, score_miss = segment_intersects_box_distance(
        (0.0, 1.8), (1.0, 1.8), cx=0.5, cy=0.5, half_size=0.5
    )
    assert hit_miss is False
    assert score_miss == 0.0

    # Segment barely grazing a corner has much lower score than center hit
    hit_graze, score_graze = segment_intersects_box_distance(
        (0.92, 1.0), (1.0, 0.92), cx=0.5, cy=0.5, half_size=0.5
    )
    assert hit_graze is True
    assert 0.0 < score_graze < 0.20


def test_projection_modes_a_b_c_comparison():
    """Prove on synthetic test cases that:
    - Mode A ('strict_50') drops a 35% Color 1 + 35% Color 2 boundary cell AND drops a thin structural line (<50% area).
    - Mode B ('dominant_foreground') fills the 35% + 35% boundary cell (70% >= 50%), while still dropping the thin line.
    - Mode C ('hybrid_line_preserving') both fills the boundary cell AND preserves the thin structural line.
    """
    palette = _make_test_color_draft()
    cols, rows = 10, 10

    # Target boundary cell is (row=5, col=5), spanning normalized [0.5, 0.6] x [0.5, 0.6].
    # Inside cell (5, 5):
    # - Region 1 (Color 1) occupies x in [0.50, 0.536] (36% of cell width) and y in [0.40, 0.70] (full height of row 5 & neighbors) -> 0.36 area in cell (5,5)
    # - Region 2 (Color 2) occupies x in [0.536, 0.570] (34% of cell width) and y in [0.40, 0.70] -> 0.34 area in cell (5,5)
    # Total foreground area in cell (5, 5) = 0.36 + 0.34 = 0.70 (>= 0.50),
    # while max single-color area is 0.36 (< 0.50).
    reg_color1 = VectorRegion(
        id="reg_c1",
        color_slot=1,
        exterior=[(0.500, 0.40), (0.536, 0.40), (0.536, 0.70), (0.500, 0.70)],
        semantic_role="primary_subject",
    )
    reg_color2 = VectorRegion(
        id="reg_c2",
        color_slot=2,
        exterior=[(0.536, 0.40), (0.570, 0.40), (0.570, 0.70), (0.536, 0.70)],
        semantic_role="modular_accent",
    )

    # Thin structural line (Color 3) passing horizontally through row=2 (y=0.25, cell centers of row 2)
    # across cols 1..8 (x from 0.15 to 0.85), with thin weight (<50% cell area).
    thin_line = StructuralLine(
        id="line_ leg",
        color_slot=3,
        points=[(0.15, 0.25), (0.85, 0.25)],
        weight=0.015,
        role="structural_contour",
    )

    draft = StyleDraft(
        draft_id="style_test_modes",
        name="Synthetic Mode Test",
        description="Boundary cell + thin structural line test",
        regions=[reg_color1, reg_color2],
        structural_lines=[thin_line],
    )

    # 1. Mode A: strict_50
    cfg_a = GridConfig(
        cols=cols,
        rows=rows,
        cell_size_mm=2.5,
        projection_mode="strict_50",
        block_threshold=0.50,
        line_threshold=0.20,
        despeckle=False,
    )
    grid_a = project_style_to_grid(draft, palette, cfg_a)
    # Mode A drops the 36% + 34% boundary cell at (5, 5) and drops the structural line at row 2
    assert grid_a.cells[5][5] == 0
    assert all(grid_a.cells[2][c] == 0 for c in range(1, 9))

    # 2. Mode B: dominant_foreground
    cfg_b = GridConfig(
        cols=cols,
        rows=rows,
        cell_size_mm=2.5,
        projection_mode="dominant_foreground",
        block_threshold=0.50,
        line_threshold=0.20,
        despeckle=False,
    )
    grid_b = project_style_to_grid(draft, palette, cfg_b)
    # Mode B fills the boundary cell (5, 5) with dominant color slot 1 (0.36 > 0.34, sum 0.70 >= 0.50)
    assert grid_b.cells[5][5] == 1
    # Mode B still does not activate the thin structural line in row 2
    assert all(grid_b.cells[2][c] == 0 for c in range(1, 9))

    # 3. Mode C: hybrid_line_preserving
    cfg_c = GridConfig(
        cols=cols,
        rows=rows,
        cell_size_mm=2.5,
        projection_mode="hybrid_line_preserving",
        block_threshold=0.50,
        line_threshold=0.20,
        despeckle=True,
    )
    grid_c = project_style_to_grid(draft, palette, cfg_c)
    # Mode C fills the boundary cell (5, 5) with dominant color 1 AND preserves the thin structural line in row 2 with color 3!
    assert grid_c.cells[5][5] == 1
    for c in range(1, 9):
        assert grid_c.cells[2][c] == 3


def test_despeckle_and_manual_overrides():
    palette = _make_test_color_draft()
    # Create an isolated 1-cell polygon inside cell (3, 3) on a 10x10 grid: [0.3, 0.4] x [0.3, 0.4]
    isolated_reg = VectorRegion(
        id="speckle",
        color_slot=1,
        exterior=[(0.31, 0.31), (0.39, 0.31), (0.39, 0.39), (0.31, 0.39)],
    )
    draft = StyleDraft(
        draft_id="style_speckle",
        name="Speckle Test",
        description="Isolated 1-cell speckle",
        regions=[isolated_reg],
        structural_lines=[],
    )

    # Without despeckle, cell (3, 3) has area 0.8 * 0.8 = 0.64 >= 0.50 -> filled
    grid_no_despeckle = project_style_to_grid(
        draft,
        palette,
        GridConfig(cols=10, rows=10, despeckle=False),
    )
    assert grid_no_despeckle.cells[3][3] == 1

    # With despeckle=True, isolated 1-cell region speckle is removed -> 0
    # And manual_overrides {"7,8": 2} forces cell (7, 8) to slot 2
    grid_despeckled = project_style_to_grid(
        draft,
        palette,
        GridConfig(
            cols=10,
            rows=10,
            despeckle=True,
            manual_overrides={"7,8": 2},
        ),
    )
    assert grid_despeckled.cells[3][3] == 0
    assert grid_despeckled.cells[7][8] == 2
    assert grid_despeckled.filled_cell_count == 1
    assert grid_despeckled.color_counts == {2: 1}


def test_compute_cross_stitch_beziers_center_bulge():
    cell_size = 2.5
    bulge_ratio = 0.09
    lower_bez, upper_bez = compute_cross_stitch_beziers(
        col=0, row=0, cell_size_mm=cell_size, bulge_ratio=bulge_ratio, corner_inset_mm=0.0
    )
    p0, p1, p2, p3 = lower_bez
    q0, q1, q2, q3 = upper_bez

    # Endpoints match cell corners
    assert p0 == (0.0, 0.0)
    assert p3 == (2.5, 2.5)
    assert q0 == (0.0, 2.5)
    assert q3 == (2.5, 0.0)

    # Control points P1, P2 for lower diagonal (\: x - y = 0) must have non-zero perpendicular distance
    expected_delta = bulge_ratio * cell_size
    dist_p1 = abs(p1[0] - p1[1]) / math.sqrt(2.0)
    dist_p2 = abs(p2[0] - p2[1]) / math.sqrt(2.0)
    assert math.isclose(dist_p1, expected_delta, rel_tol=1e-6)
    assert math.isclose(dist_p2, expected_delta, rel_tol=1e-6)
    assert dist_p1 > 0.20

    # Control points Q1, Q2 for upper diagonal (/: x + y - cell_size = 0) must have non-zero perpendicular distance
    dist_q1 = abs(q1[0] + q1[1] - cell_size) / math.sqrt(2.0)
    dist_q2 = abs(q2[0] + q2[1] - cell_size) / math.sqrt(2.0)
    assert math.isclose(dist_q1, expected_delta, rel_tol=1e-6)
    assert math.isclose(dist_q2, expected_delta, rel_tol=1e-6)

    # Sampled midpoint (t=0.5, index 6 of 12 steps) bulges away from cell center (1.25, 1.25) by 0.75 * delta
    sampled_lower = sample_cubic_bezier_2d(p0, p1, p2, p3, steps=12)
    assert len(sampled_lower) == 13
    mid_pt = sampled_lower[6]
    mid_bulge = math.hypot(mid_pt[0] - 1.25, mid_pt[1] - 1.25)
    assert math.isclose(mid_bulge, 0.75 * expected_delta, rel_tol=1e-6)


def test_generate_svg_and_png_valid_output():
    palette = _make_test_color_draft()
    # Default 60x60 @ 2.5mm = 150.0mm x 150.0mm physical canvas
    region = VectorRegion(
        id="center_block",
        color_slot=1,
        exterior=[(0.40, 0.40), (0.60, 0.40), (0.60, 0.60), (0.40, 0.60)],
    )
    line = StructuralLine(
        id="cross_axis",
        color_slot=2,
        points=[(0.20, 0.50), (0.80, 0.50)],
        weight=0.016,
    )
    draft = StyleDraft(
        draft_id="style_full_60",
        name="60x60 Canvas Test",
        description="Full 150mm canvas test",
        regions=[region],
        structural_lines=[line],
    )

    grid = project_style_to_grid(draft, palette, GridConfig())
    assert grid.canvas_width_mm == 150.0
    assert grid.canvas_height_mm == 150.0
    assert grid.filled_cell_count > 0

    svg_res = generate_svg_and_png(grid, palette, SvgConfig())
    assert svg_res.width_mm == 150.0
    assert svg_res.height_mm == 150.0
    assert svg_res.stitch_count == grid.filled_cell_count

    # Verify SVG header and attributes
    assert 'width="150.0mm"' in svg_res.svg_content
    assert 'height="150.0mm"' in svg_res.svg_content
    assert 'stroke-width="0.4"' in svg_res.svg_content
    assert " C " in svg_res.svg_content

    # Parse XML to verify well-formedness and structure
    root = ET.fromstring(svg_res.svg_content)
    assert root.attrib["width"] == "150.0mm"
    assert root.attrib["height"] == "150.0mm"

    paths = root.findall(".//{http://www.w3.org/2000/svg}path")
    # Every filled cell produces 2 diagonal cubic Bezier paths
    assert len(paths) == grid.filled_cell_count * 2
    for p_elem in paths:
        d_attr = p_elem.attrib.get("d", "")
        assert d_attr.startswith("M ") and " C " in d_attr
        assert p_elem.attrib.get("stroke-width") == "0.4"

    # Verify base64 PNG decodes to a valid 800x800 RGB image
    raw_png = base64.b64decode(svg_res.png_base64)
    assert raw_png.startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(io.BytesIO(raw_png)) as pil_img:
        assert pil_img.size == (800, 800)
        assert pil_img.mode == "RGB"
