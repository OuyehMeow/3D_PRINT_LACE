"""Unit and integration tests for StorageProvider (Method in the Cloud, Data at Local)
and the end-to-end 3D_PRINT_LACE 5-stage pipeline.
"""

from __future__ import annotations

import base64
from pathlib import Path
import pytest

from backend.app.samples import generate_sample_image_bytes
from backend.app.schemas import Bambu3mfConfig, GridConfig, SessionState, SvgConfig
from backend.app.stages.s1_style import generate_style_drafts, render_style_draft_preview_svg
from backend.app.stages.s2_color import generate_color_drafts
from backend.app.stages.s3_grid import project_style_to_grid
from backend.app.stages.s4_svg import generate_svg_and_png
from backend.app.stages.s5_3mf import generate_bambu_3mf
from backend.app.storage.provider import (
    REPO_ROOT,
    LocalStorageProvider,
    resolve_data_root,
)


def test_resolve_data_root_rejects_path_inside_repo() -> None:
    """Verify Method in the Cloud, Data at Local: DATA_ROOT inside Git repo is blocked."""
    inside_path = str(REPO_ROOT / "data")
    with pytest.raises(ValueError, match="outside the Git repository"):
        resolve_data_root(inside_path)


def test_local_storage_provider_persists_outside_repo(tmp_path: Path) -> None:
    """Verify LocalStorageProvider saves inputs, session ledgers, and exports outside Git."""
    storage = LocalStorageProvider(data_dir=tmp_path)
    assert storage.data_root == tmp_path.resolve()

    img_bytes = generate_sample_image_bytes("crane", size=128)
    saved_img = storage.save_input_image("test_crane.png", img_bytes)
    assert saved_img.exists()
    assert storage.get_input_image_path("test_crane.png") == saved_img

    session = SessionState(
        session_id="sess_test01",
        name="Test Crane Session",
        source_image_filename="test_crane.png",
    )
    ledger_path = storage.save_session(session)
    assert ledger_path.exists()

    loaded = storage.load_session("sess_test01")
    assert loaded.session_id == "sess_test01"
    assert loaded.name == "Test Crane Session"

    sessions = storage.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "sess_test01"

    svg_export = storage.save_export("sess_test01", "out.svg", "<svg></svg>")
    assert svg_export.exists()
    assert storage.get_export_path("sess_test01", "out.svg") == svg_export


def test_end_to_end_5_stage_pipeline_with_storage(tmp_path: Path) -> None:
    """Verify Stages 1 -> 2 -> 3 (60x60 @ 2.5mm) -> 4 (.svg/.png) -> 5 (.3mf) end-to-end."""
    storage = LocalStorageProvider(data_dir=tmp_path)
    img_bytes = generate_sample_image_bytes("crane", size=256)
    storage.save_input_image("crane_source.png", img_bytes)

    # Stage 2: Functional Color System
    color_drafts = generate_color_drafts(img_bytes, max_colors=4, base_style="warm_white")
    assert len(color_drafts) == 3
    palette = color_drafts[0]
    assert palette.grid_filament_hex == "#C4C8CE"

    # Stage 1: Minimalist Geometric Style Drafts
    style_drafts = generate_style_drafts(img_bytes, engine="algorithmic", whitespace_target=0.68)
    assert len(style_drafts) == 3
    style = style_drafts[0]
    style.preview_svg = render_style_draft_preview_svg(style, palette)
    assert "<svg" in (style.preview_svg or "")

    # Stage 3: 60x60 @ 2.5mm Grid Projection (Mode A vs Mode C)
    grid_strict = project_style_to_grid(
        style,
        palette,
        GridConfig(cols=60, rows=60, cell_size_mm=2.5, projection_mode="strict_50"),
    )
    grid_hybrid = project_style_to_grid(
        style,
        palette,
        GridConfig(cols=60, rows=60, cell_size_mm=2.5, projection_mode="hybrid_line_preserving"),
    )
    assert grid_hybrid.canvas_width_mm == 150.0
    assert grid_hybrid.canvas_height_mm == 150.0
    assert grid_hybrid.filled_cell_count >= grid_strict.filled_cell_count > 0

    # Stage 4: Plotter SVG (0.4mm center-bulge curves) & PNG Draft
    svg_res = generate_svg_and_png(
        grid_hybrid,
        palette,
        SvgConfig(stroke_width_mm=0.4, bulge_ratio=0.09),
    )
    assert svg_res.width_mm == 150.0
    assert svg_res.height_mm == 150.0
    assert svg_res.stitch_count == grid_hybrid.filled_cell_count
    svg_path = storage.save_export("sess_e2e", svg_res.svg_filename, svg_res.svg_content)
    png_bytes = base64.b64decode(svg_res.png_base64)
    png_path = storage.save_export("sess_e2e", svg_res.png_filename, png_bytes)
    assert svg_path.exists() and png_path.exists()

    # Stage 5: MakerWorld-Ready BambuLab X2D .3mf Project File
    archive_bytes, b3mf_res = generate_bambu_3mf(
        grid_hybrid,
        palette,
        Bambu3mfConfig(line_width_mm=0.4, stitch_arch_z_mm=0.20, bulge_ratio=0.09),
        thumbnail_png_bytes=png_bytes,
    )
    b3mf_path = storage.save_export("sess_e2e", b3mf_res.filename, archive_bytes)
    assert b3mf_path.exists()
    assert b3mf_res.file_size_bytes > 1000
    assert b3mf_res.part_count >= 2
    assert b3mf_res.total_triangles > 100
