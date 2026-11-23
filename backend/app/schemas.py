"""Shared Pydantic data contracts connecting Stages 1-5, StorageProvider, and Frontend API."""

from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

Point2D = tuple[float, float]
Point3D = tuple[float, float, float]
ProjectionMode = Literal["strict_50", "dominant_foreground", "hybrid_line_preserving"]


class VectorRegion(BaseModel):
    """A flat geometric polygon region in normalized [0.0, 1.0] canvas coordinates."""

    id: str
    color_slot: int = Field(
        ...,
        description="1-based index into ColorDraft.colors (or 0 for negative-space cutout).",
    )
    exterior: list[Point2D] = Field(
        ...,
        description="Counter-clockwise polygon vertices in normalized [0.0, 1.0] coordinates.",
    )
    holes: list[list[Point2D]] = Field(
        default_factory=list,
        description="Optional interior cutout rings in normalized [0.0, 1.0] coordinates.",
    )
    semantic_role: str = Field(
        default="primary_subject",
        description="primary_subject | modular_accent | negative_space",
    )


class StructuralLine(BaseModel):
    """A geometric structural line / skeleton polyline in normalized [0.0, 1.0] coordinates."""

    id: str
    color_slot: int = Field(
        ...,
        description="1-based index into ColorDraft.colors.",
    )
    points: list[Point2D] = Field(
        ...,
        description="Ordered polyline vertices in normalized [0.0, 1.0] coordinates.",
    )
    weight: float = Field(
        default=0.016,
        description="Normalized stroke width relative to canvas size.",
    )
    role: str = Field(
        default="structural_contour",
        description="structural_contour | posture_axis | internal_structure",
    )


class StyleDraft(BaseModel):
    """Stage 1 output: Minimalist geometric & symbolic vector abstraction of the input image."""

    draft_id: str
    name: str
    description: str
    engine: Literal["algorithmic", "ai"] = "algorithmic"
    whitespace_ratio: float = Field(
        default=0.68,
        description="Fraction of canvas area left as breathing negative space (target >= 0.55).",
    )
    focal_center: Point2D = Field(
        default=(0.5, 0.5),
        description="Normalized focal center (centered or slightly offset).",
    )
    regions: list[VectorRegion] = Field(default_factory=list)
    structural_lines: list[StructuralLine] = Field(default_factory=list)
    preview_svg: Optional[str] = None


class FunctionalColor(BaseModel):
    """A single high-recognition functional color slot in the reconstructed artwork palette."""

    slot: int = Field(
        ...,
        ge=1,
        description="1-based artwork color slot (1..K). Maps to Bambu extruder slot + 1.",
    )
    name: str
    hex: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$")
    oklch: tuple[float, float, float] = Field(
        ...,
        description="(L, C, H) where L in [0,1], C in [0,0.4], H in [0,360].",
    )
    role: str = Field(
        default="primary_code",
        description="primary_code | secondary_code | accent_code | structural_code",
    )
    bambu_extruder: int = Field(
        ...,
        ge=2,
        description="1-based Bambu Studio extruder index (Extruder 1 is reserved for Light Gray Grid).",
    )


class ColorDraft(BaseModel):
    """Stage 2 output: Functional color encoding system + neutral base + Light Gray 3D grid filament."""

    palette_id: str
    name: str
    base_name: str = "Warm White / 暖白"
    base_hex: str = "#F5F1E6"
    grid_filament_name: str = "Light Gray Grid / 浅灰网格"
    grid_filament_hex: str = "#C4C8CE"
    colors: list[FunctionalColor] = Field(default_factory=list)


class GridConfig(BaseModel):
    """Stage 3 configuration for projecting a StyleDraft onto the physical cell grid."""

    cols: int = Field(default=60, ge=10, le=120)
    rows: int = Field(default=60, ge=10, le=120)
    cell_size_mm: float = Field(default=2.5, ge=1.0, le=10.0)
    projection_mode: ProjectionMode = "hybrid_line_preserving"
    block_threshold: float = Field(
        default=0.50,
        ge=0.05,
        le=0.95,
        description="Area coverage threshold for solid modular regions (default 50%).",
    )
    line_threshold: float = Field(
        default=0.20,
        ge=0.05,
        le=0.80,
        description="Coverage/distance threshold for preserving structural lines in Mode C.",
    )
    offset_x: float = Field(
        default=0.0,
        ge=-0.5,
        le=0.5,
        description="Sub-cell horizontal alignment nudge in cell units.",
    )
    offset_y: float = Field(
        default=0.0,
        ge=-0.5,
        le=0.5,
        description="Sub-cell vertical alignment nudge in cell units.",
    )
    scale: float = Field(
        default=1.0,
        ge=0.5,
        le=1.5,
        description="Uniform scale factor around canvas center.",
    )
    despeckle: bool = Field(
        default=True,
        description="Remove isolated 1-cell noise speckles not belonging to structural lines.",
    )
    manual_overrides: dict[str, int] = Field(
        default_factory=dict,
        description="Map of 'row,col' -> color_slot (0 to clear cell, 1..K to fill).",
    )


class GridDraft(BaseModel):
    """Stage 3 output: Projected pixelated cross-stitch cell matrix."""

    grid_id: str
    style_draft_id: str
    palette_id: str
    config: GridConfig
    canvas_width_mm: float
    canvas_height_mm: float
    cells: list[list[int]] = Field(
        ...,
        description="rows x cols matrix: 0 = empty cell (0%), 1..K = filled (100%) with FunctionalColor.slot.",
    )
    cell_coverages: Optional[list[list[float]]] = Field(
        default=None,
        description="Optional rows x cols raw foreground coverage ratio [0.0, 1.0] for threshold inspection.",
    )
    filled_cell_count: int
    fill_percentage: float
    color_counts: dict[int, int] = Field(default_factory=dict)


class SvgConfig(BaseModel):
    """Stage 4 configuration for organic center-bulge cross-stitch SVG & PNG generation."""

    stroke_width_mm: float = Field(default=0.4, ge=0.1, le=2.0)
    bulge_ratio: float = Field(
        default=0.09,
        ge=0.0,
        le=0.30,
        description="Perpendicular center bulge offset relative to cell_size_mm (~0.225mm at 2.5mm cell).",
    )
    corner_inset_mm: float = Field(default=0.0, ge=0.0, le=0.5)
    include_grid_layer: bool = True
    optimize_plotter_path: bool = True


class SvgExportResult(BaseModel):
    """Stage 4 output containing Plotter SVG and PNG draft."""

    svg_content: str
    svg_filename: str
    png_filename: str
    png_base64: str
    width_mm: float
    height_mm: float
    stitch_count: int


class Bambu3mfConfig(BaseModel):
    """Stage 5 configuration for BambuLab X2D / MakerWorld multi-color .3mf generation."""

    printer_model: str = "Bambu Lab X2D"
    nozzle_diameter_mm: float = 0.4
    line_width_mm: float = Field(default=0.4, ge=0.2, le=0.8)
    grid_height_mm: float = Field(default=0.40, ge=0.20, le=1.20)
    stitch_thickness_mm: float = Field(default=0.20, ge=0.12, le=0.60)
    stitch_arch_z_mm: float = Field(
        default=0.20,
        ge=0.08,
        le=0.60,
        description="3D Z-axis arch bump at the center intersection of the upper diagonal stitch.",
    )
    bulge_ratio: float = Field(
        default=0.09,
        ge=0.0,
        le=0.30,
        description="2D XY bow ratio matching Stage 4 organic curves.",
    )
    include_outer_frame: bool = True
    frame_width_mm: float = Field(default=0.8, ge=0.4, le=2.4)
    curve_segments: int = Field(default=8, ge=4, le=16)


class Preview3DThread(BaseModel):
    """3D centerline polylines for a single colored cross-stitch cell in the 3D web previewer."""

    row: int
    col: int
    color_slot: int
    hex: str
    extruder: int
    lower_points: list[Point3D]
    upper_points: list[Point3D]


class Preview3DPayload(BaseModel):
    """Lightweight 3D geometry representation for the Three.js BambuLab X2D viewport."""

    plate_size_mm: Point2D = (256.0, 256.0)
    canvas_size_mm: Point2D
    grid_lines: list[tuple[Point3D, Point3D]]
    grid_hex: str = "#C4C8CE"
    grid_height_mm: float = 0.40
    line_width_mm: float = 0.40
    stitch_thickness_mm: float = 0.20
    threads: list[Preview3DThread] = Field(default_factory=list)


class Bambu3mfExportResult(BaseModel):
    """Stage 5 output metadata and download handle for the generated .3mf project file."""

    filename: str
    file_size_bytes: int
    part_count: int
    total_triangles: int
    extruder_map: list[dict[str, Any]]
    preview_3d: Preview3DPayload


class SessionState(BaseModel):
    """Complete persisted session ledger stored in DATA_ROOT/sessions/<session_id>/session.json."""

    session_id: str
    name: str
    source_image_filename: str
    style_drafts: list[StyleDraft] = Field(default_factory=list)
    active_style_draft_id: Optional[str] = None
    color_drafts: list[ColorDraft] = Field(default_factory=list)
    active_palette_id: Optional[str] = None
    grid_drafts: list[GridDraft] = Field(default_factory=list)
    active_grid_id: Optional[str] = None
    pinned_grid_ids: list[str] = Field(default_factory=list)
