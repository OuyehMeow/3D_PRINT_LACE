"""Unit tests for Stage 5: BambuLab X2D & MakerWorld Multi-Color .3mf Engine (s5_3mf.py)."""

from __future__ import annotations

import io
import json
import math
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter

from PIL import Image
import pytest

from backend.app.schemas import (
    Bambu3mfConfig,
    ColorDraft,
    FunctionalColor,
    GridConfig,
    GridDraft,
    Point3D,
)
from backend.app.stages.s5_3mf import (
    build_grid_base_mesh,
    build_preview_3d_payload,
    compute_cell_thread_polylines,
    generate_bambu_3mf,
    sweep_ribbon_mesh,
)


def _make_sample_color_draft() -> ColorDraft:
    return ColorDraft(
        palette_id="pal_test_x2d",
        name="Crimson & Indigo Lace",
        base_name="Warm White / 暖白",
        base_hex="#F5F1E6",
        grid_filament_name="Light Gray Grid / 浅灰网格",
        grid_filament_hex="#C4C8CE",
        colors=[
            FunctionalColor(
                slot=1,
                name="Crimson Core",
                hex="#C83E37",
                oklch=(0.54, 0.18, 28.0),
                role="primary_code",
                bambu_extruder=2,
            ),
            FunctionalColor(
                slot=2,
                name="Indigo Structure",
                hex="#234B78",
                oklch=(0.41, 0.11, 252.0),
                role="structural_code",
                bambu_extruder=3,
            ),
            FunctionalColor(
                slot=3,
                name="Ochre Accent",
                hex="#D99B26",
                oklch=(0.71, 0.15, 78.0),
                role="accent_code",
                bambu_extruder=4,
            ),
        ],
    )


def _make_sample_grid_draft(cols: int = 12, rows: int = 12, cell_size_mm: float = 2.5) -> GridDraft:
    cells = [[0 for _ in range(cols)] for _ in range(rows)]
    # Populate a small cross-stitch pattern using slots 1, 2, 3
    cells[2][3] = 1
    cells[2][4] = 1
    cells[5][5] = 2
    cells[5][6] = 2
    cells[8][7] = 3

    color_counts = {1: 2, 2: 2, 3: 1}
    filled = sum(color_counts.values())
    return GridDraft(
        grid_id="grid_test01",
        style_draft_id="style_test01",
        palette_id="pal_test_x2d",
        config=GridConfig(cols=cols, rows=rows, cell_size_mm=cell_size_mm),
        canvas_width_mm=cols * cell_size_mm,
        canvas_height_mm=rows * cell_size_mm,
        cells=cells,
        filled_cell_count=filled,
        fill_percentage=round((filled / (cols * rows)) * 100.0, 2),
        color_counts=color_counts,
    )


def _triangle_area(p1: Point3D, p2: Point3D, p3: Point3D) -> float:
    ux, uy, uz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
    vx, vy, vz = p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def _signed_mesh_volume(
    vertices: list[Point3D], triangles: list[tuple[int, int, int]]
) -> float:
    total = 0.0
    for v1, v2, v3 in triangles:
        x1, y1, z1 = vertices[v1]
        x2, y2, z2 = vertices[v2]
        x3, y3, z3 = vertices[v3]
        total += (
            x1 * (y2 * z3 - z2 * y3)
            - y1 * (x2 * z3 - z2 * x3)
            + z1 * (x2 * y3 - y2 * x3)
        )
    return total / 6.0


def _assert_watertight_manifold(
    vertices: list[Point3D],
    triangles: list[tuple[int, int, int]],
    expected_components: int | None = None,
) -> None:
    assert len(vertices) >= 8
    assert len(triangles) >= 12

    directed_edges: Counter[tuple[int, int]] = Counter()
    undirected_edges: set[tuple[int, int]] = set()

    for v1, v2, v3 in triangles:
        assert 0 <= v1 < len(vertices)
        assert 0 <= v2 < len(vertices)
        assert 0 <= v3 < len(vertices)
        assert len({v1, v2, v3}) == 3, f"Degenerate triangle indices: {(v1, v2, v3)}"

        area = _triangle_area(vertices[v1], vertices[v2], vertices[v3])
        assert area > 1e-7, f"Degenerate zero-area triangle: {area}"

        for u, v in ((v1, v2), (v2, v3), (v3, v1)):
            directed_edges[(u, v)] += 1
            undirected_edges.add((min(u, v), max(u, v)))

    # Watertight closed 2-manifold rule: every directed edge (u, v) appears once
    # and its opposite directed edge (v, u) also appears once.
    for (u, v), count in directed_edges.items():
        assert count == 1, f"Non-manifold duplicate directed edge {(u, v)} (count={count})"
        opp_count = directed_edges.get((v, u), 0)
        assert opp_count == 1, f"Unpaired boundary edge {(u, v)} missing opposite {(v, u)}"

    # Outward-oriented counter-clockwise winding implies strictly positive enclosed volume
    vol = _signed_mesh_volume(vertices, triangles)
    assert vol > 1e-5, f"Expected positive enclosed volume for outward CCW normals, got {vol}"

    if expected_components is not None:
        euler_chi = len(vertices) - len(undirected_edges) + len(triangles)
        assert euler_chi == 2 * expected_components


def test_build_preview_3d_payload_arch_and_bulge() -> None:
    """Verify 3D preview payload grid lines, 2D organic center bulge, and 3D Z-arch bump."""
    color_draft = _make_sample_color_draft()
    grid_draft = _make_sample_grid_draft(cols=12, rows=12, cell_size_mm=2.5)
    cfg = Bambu3mfConfig(
        line_width_mm=0.4,
        grid_height_mm=0.40,
        stitch_thickness_mm=0.20,
        stitch_arch_z_mm=0.20,
        bulge_ratio=0.09,
        curve_segments=8,
    )

    payload = build_preview_3d_payload(grid_draft, color_draft, cfg)

    assert payload.plate_size_mm == (256.0, 256.0)
    assert payload.canvas_size_mm == (30.0, 30.0)
    # (rows + 1) horizontal + (cols + 1) vertical = 13 + 13 = 26 lines
    assert len(payload.grid_lines) == 26
    assert payload.grid_hex == "#C4C8CE"
    assert len(payload.threads) == 5

    for thread in payload.threads:
        assert len(thread.lower_points) == cfg.curve_segments + 1
        assert len(thread.upper_points) == cfg.curve_segments + 1
        mid_idx = cfg.curve_segments // 2

        lower_mid_z = thread.lower_points[mid_idx][2]
        upper_mid_z = thread.upper_points[mid_idx][2]

        # Upper thread midpoint Z must be strictly greater than lower thread midpoint Z
        # by config.stitch_arch_z_mm (0.20 mm)
        assert upper_mid_z > lower_mid_z
        assert math.isclose(
            upper_mid_z - lower_mid_z, cfg.stitch_arch_z_mm, rel_tol=1e-4, abs_tol=1e-4
        )
        assert math.isclose(lower_mid_z, 0.50, abs_tol=1e-4)
        assert math.isclose(upper_mid_z, 0.70, abs_tol=1e-4)

        # Endpoints anchor into the grid corners at z_center = 0.40 + 0.10 - 0.04 = 0.46 mm
        assert math.isclose(thread.lower_points[0][2], 0.46, abs_tol=1e-4)
        assert math.isclose(thread.lower_points[-1][2], 0.46, abs_tol=1e-4)
        assert math.isclose(thread.upper_points[0][2], 0.46, abs_tol=1e-4)
        assert math.isclose(thread.upper_points[-1][2], 0.46, abs_tol=1e-4)

        # Verify 2D XY center-bulge deflection at t = 0.5 equals bulge_ratio * cell_size_mm
        p = grid_draft.config.cell_size_mm
        x0 = thread.col * p
        y0 = (grid_draft.config.rows - 1 - thread.row) * p
        chord_mid_x = x0 + 0.5 * p
        chord_mid_y = y0 + 0.5 * p
        expected_bulge = cfg.bulge_ratio * p

        low_bulge = math.hypot(
            thread.lower_points[mid_idx][0] - chord_mid_x,
            thread.lower_points[mid_idx][1] - chord_mid_y,
        )
        up_bulge = math.hypot(
            thread.upper_points[mid_idx][0] - chord_mid_x,
            thread.upper_points[mid_idx][1] - chord_mid_y,
        )
        assert math.isclose(low_bulge, expected_bulge, rel_tol=1e-3, abs_tol=1e-3)
        assert math.isclose(up_bulge, expected_bulge, rel_tol=1e-3, abs_tol=1e-3)


def test_sweep_ribbon_mesh_and_grid_base_watertight() -> None:
    """Verify sweep_ribbon_mesh and build_grid_base_mesh produce closed watertight manifolds."""
    cfg = Bambu3mfConfig(curve_segments=8)
    low_pts, up_pts = compute_cell_thread_polylines(
        row=0, col=0, rows=10, cell_size_mm=2.5, config=cfg
    )

    low_v, low_t = sweep_ribbon_mesh(low_pts, width=0.4, height=0.2)
    assert len(low_v) == 4 * (cfg.curve_segments + 1)
    assert len(low_t) == 8 * cfg.curve_segments + 4
    _assert_watertight_manifold(low_v, low_t, expected_components=1)

    up_v, up_t = sweep_ribbon_mesh(up_pts, width=0.4, height=0.2)
    assert len(up_v) == 4 * (cfg.curve_segments + 1)
    assert len(up_t) == 8 * cfg.curve_segments + 4
    _assert_watertight_manifold(up_v, up_t, expected_components=1)

    grid_v, grid_t = build_grid_base_mesh(cols=10, rows=10, cell_size_mm=2.5, config=cfg)
    # 11 horizontal bars + 11 vertical bars = 22 closed prism components
    _assert_watertight_manifold(grid_v, grid_t, expected_components=22)


def test_generate_bambu_3mf_opc_package_and_watertight_meshes() -> None:
    """Verify .3mf ZIP structure, XML/JSON metadata, extruder mapping, and watertight meshes."""
    color_draft = _make_sample_color_draft()
    grid_draft = _make_sample_grid_draft(cols=12, rows=12, cell_size_mm=2.5)
    cfg = Bambu3mfConfig()

    archive_bytes, result = generate_bambu_3mf(grid_draft, color_draft, cfg)

    assert len(archive_bytes) == result.file_size_bytes
    assert result.filename.endswith(".3mf")
    # 1 base grid part + 3 active stitch color parts = 4 parts
    assert result.part_count == 4
    assert len(result.extruder_map) == 4
    assert result.extruder_map[0]["extruder"] == 1
    assert result.extruder_map[0]["hex"] == "#C4C8CE"
    assert [m["extruder"] for m in result.extruder_map] == [1, 2, 3, 4]

    with zipfile.ZipFile(io.BytesIO(archive_bytes), mode="r") as zf:
        names = set(zf.namelist())
        expected_files = {
            "[Content_Types].xml",
            "_rels/.rels",
            "3D/3dmodel.model",
            "Metadata/model_settings.config",
            "Metadata/project_settings.config",
            "Metadata/plate_1.png",
        }
        assert expected_files.issubset(names)

        # 1. Validate Metadata/plate_1.png
        png_bytes = zf.read("Metadata/plate_1.png")
        with Image.open(io.BytesIO(png_bytes)) as img:
            assert img.format == "PNG"
            assert img.size == (512, 512)

        # 2. Validate Metadata/project_settings.config
        proj_cfg = json.loads(
            zf.read("Metadata/project_settings.config").decode("utf-8")
        )
        assert proj_cfg["printer_model"] == "Bambu Lab X2D"
        assert proj_cfg["nozzle_diameter"] == ["0.4"]
        assert proj_cfg["wall_generator"] == "arachne"
        assert proj_cfg["detect_thin_wall"] == "1"
        assert proj_cfg["min_bead_width"] == "85%"
        assert proj_cfg["min_feature_size"] == "20%"
        assert proj_cfg["layer_height"] == "0.2"
        assert proj_cfg["initial_layer_print_height"] == "0.2"
        assert proj_cfg["line_width"] == "0.4"
        assert proj_cfg["outer_wall_line_width"] == "0.4"
        assert proj_cfg["inner_wall_line_width"] == "0.4"
        assert proj_cfg["initial_layer_line_width"] == "0.4"
        assert proj_cfg["filament_colour"] == [
            "#C4C8CE",
            "#C83E37",
            "#234B78",
            "#D99B26",
        ]
        assert proj_cfg["filament_type"] == ["PLA", "PLA", "PLA", "PLA"]
        assert any(
            "wall_generator;detect_thin_wall" in entry
            for entry in proj_cfg["different_settings_to_system"]
        )

        # 3. Validate 3D/3dmodel.model meshes & build plate centering transform
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        model_root = ET.fromstring(zf.read("3D/3dmodel.model"))
        assert model_root.attrib.get("unit") == "millimeter"

        metadata_map = {
            el.attrib["name"]: (el.text or "")
            for el in model_root.findall("m:metadata", ns)
        }
        assert metadata_map.get("Application") == "BambuStudio-01.10.00.89"
        assert metadata_map.get("BambuStudio:3mfVersion") == "1"

        mesh_objects = []
        assembly_objects = []
        for obj_el in model_root.findall("m:resources/m:object", ns):
            if obj_el.find("m:mesh", ns) is not None:
                mesh_objects.append(obj_el)
            elif obj_el.find("m:components", ns) is not None:
                assembly_objects.append(obj_el)

        assert len(mesh_objects) == 4
        assert len(assembly_objects) == 1

        assembly_el = assembly_objects[0]
        assembly_id = assembly_el.attrib["id"]
        comp_ids = [
            c.attrib["objectid"]
            for c in assembly_el.findall("m:components/m:component", ns)
        ]
        assert comp_ids == ["1", "2", "3", "4"]

        # Verify build item transform centers the 30x30mm canvas at (128.0, 128.0, 0.0)
        build_item = model_root.find("m:build/m:item", ns)
        assert build_item is not None
        assert build_item.attrib["objectid"] == assembly_id
        assert build_item.attrib.get("printable") == "1"
        tx_parts = [float(x) for x in build_item.attrib["transform"].split()]
        assert len(tx_parts) == 12
        # 128.0 - 30.0 / 2 = 113.0 -> model center (15, 15) + (113, 113) = (128.0, 128.0)
        assert math.isclose(tx_parts[9] + 15.0, 128.0, abs_tol=1e-3)
        assert math.isclose(tx_parts[10] + 15.0, 128.0, abs_tol=1e-3)
        assert math.isclose(tx_parts[11], 0.0, abs_tol=1e-3)

        # Verify every <object> mesh in 3D/3dmodel.model is watertight and closed
        parsed_triangle_total = 0
        for obj_el in mesh_objects:
            mesh_el = obj_el.find("m:mesh", ns)
            assert mesh_el is not None
            verts: list[Point3D] = [
                (
                    float(v.attrib["x"]),
                    float(v.attrib["y"]),
                    float(v.attrib["z"]),
                )
                for v in mesh_el.findall("m:vertices/m:vertex", ns)
            ]
            tris: list[tuple[int, int, int]] = [
                (
                    int(t.attrib["v1"]),
                    int(t.attrib["v2"]),
                    int(t.attrib["v3"]),
                )
                for t in mesh_el.findall("m:triangles/m:triangle", ns)
            ]
            parsed_triangle_total += len(tris)
            _assert_watertight_manifold(verts, tris)

        assert parsed_triangle_total == result.total_triangles

        # 4. Validate Metadata/model_settings.config extruder mapping
        ms_root = ET.fromstring(zf.read("Metadata/model_settings.config"))
        ms_obj = ms_root.find("object")
        assert ms_obj is not None
        assert ms_obj.attrib["id"] == assembly_id

        part_extruders: dict[int, int] = {}
        for part_el in ms_obj.findall("part"):
            pid = int(part_el.attrib["id"])
            metas = {
                m.attrib["key"]: m.attrib["value"]
                for m in part_el.findall("metadata")
            }
            part_extruders[pid] = int(metas["extruder"])

        assert part_extruders == {1: 1, 2: 2, 3: 3, 4: 4}

        plate_el = ms_root.find("plate")
        assert plate_el is not None
        plate_metas = {
            m.attrib["key"]: m.attrib["value"] for m in plate_el.findall("metadata")
        }
        assert plate_metas.get("plater_id") == "1"
        assert plate_metas.get("thumbnail_file") == "Metadata/plate_1.png"
        inst_el = plate_el.find("model_instance")
        assert inst_el is not None
        inst_metas = {
            m.attrib["key"]: m.attrib["value"] for m in inst_el.findall("metadata")
        }
        assert inst_metas.get("object_id") == assembly_id


def test_generate_bambu_3mf_60x60_default_canvas_and_custom_thumbnail() -> None:
    """Verify full 60x60 @ 2.5mm (150.0 x 150.0 mm) default grid and custom thumbnail bytes."""
    color_draft = _make_sample_color_draft()
    grid_draft = _make_sample_grid_draft(cols=60, rows=60, cell_size_mm=2.5)
    custom_thumb = b"\x89PNG\r\n\x1a\ncustom_thumbnail_payload"

    archive_bytes, result = generate_bambu_3mf(
        grid_draft,
        color_draft,
        Bambu3mfConfig(include_outer_frame=True, frame_width_mm=0.8),
        thumbnail_png_bytes=custom_thumb,
    )

    assert result.preview_3d.canvas_size_mm == (150.0, 150.0)
    # 61 horizontal + 61 vertical = 122 grid lines
    assert len(result.preview_3d.grid_lines) == 122

    with zipfile.ZipFile(io.BytesIO(archive_bytes), mode="r") as zf:
        assert zf.read("Metadata/plate_1.png") == custom_thumb
        ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
        model_root = ET.fromstring(zf.read("3D/3dmodel.model"))
        build_item = model_root.find("m:build/m:item", ns)
        assert build_item is not None
        tx_parts = [float(x) for x in build_item.attrib["transform"].split()]
        # 150x150 mm canvas centered on 256x256 mm build plate -> offset = (256 - 150)/2 = 53.0 mm
        assert math.isclose(tx_parts[9], 53.0, abs_tol=1e-3)
        assert math.isclose(tx_parts[10], 53.0, abs_tol=1e-3)

