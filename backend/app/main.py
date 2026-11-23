"""FastAPI entrypoint for 3D_PRINT_LACE local web studio.

Integrates StorageProvider (DATA_ROOT), Stage 1 (Style Drafts), Stage 2 (Functional Color System),
Stage 3 (60x60 @ 2.5mm Grid Projection Workbench), Stage 4 (Plotter SVG & PNG), and
Stage 5 (BambuLab X2D / MakerWorld Multi-Color .3mf Project Generator).
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .samples import generate_sample_image_bytes
from .schemas import (
    Bambu3mfConfig,
    Bambu3mfExportResult,
    GridConfig,
    Preview3DPayload,
    SessionState,
    SvgConfig,
    SvgExportResult,
)
from .storage import get_storage_provider

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(
    title="3D_PRINT_LACE Creative Studio API",
    description="Cross-Stitch & 3D-Printed Lace Artwork Generator (Plotter SVG & BambuLab X2D .3mf)",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


import os


class StyleRequest(BaseModel):
    engine: str = "algorithmic"
    whitespace_target: float = 0.68
    focal_offset: tuple[float, float] = (0.0, 0.0)
    api_key: Optional[str] = None
    model: str = "gemini-2.5-flash-image"
    active_style_draft_id: Optional[str] = None


@app.get("/api/health")
def health_check() -> dict[str, Any]:
    """Docker / Home NAS healthcheck endpoint."""
    storage = get_storage_provider()
    cues_dir = storage.data_root / "style_cues"
    cue_count = len(list(cues_dir.glob("*"))) if cues_dir.exists() else 0
    return {
        "status": "ok",
        "data_root": str(storage.data_root),
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY")),
        "gemini_image_model": os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
        "style_cue_count": cue_count,
    }


@app.get("/api/style-cues")
def list_style_cues() -> dict[str, Any]:
    """List visual style cue reference images stored in DATA_ROOT/style_cues/."""
    storage = get_storage_provider()
    cues_dir = storage.data_root / "style_cues"
    cues_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p.name for p in cues_dir.iterdir() if p.is_file())
    return {"style_cues": files}


@app.post("/api/style-cues")
async def upload_style_cue(file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload a visual style cue reference image to DATA_ROOT/style_cues/ for Gemini Stage 1."""
    storage = get_storage_provider()
    cues_dir = storage.data_root / "style_cues"
    cues_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename or f"cue_{uuid.uuid4().hex[:6]}.png").name
    content = await file.read()
    (cues_dir / safe_name).write_bytes(content)
    return {"filename": safe_name, "bytes": len(content)}


class ColorRequest(BaseModel):
    max_colors: int = 4
    base_style: str = "warm_white"
    active_palette_id: Optional[str] = None


class ColorUpdateRequest(BaseModel):
    palette_id: Optional[str] = None
    base_name: Optional[str] = None
    base_hex: Optional[str] = None
    grid_filament_hex: Optional[str] = None
    colors: Optional[list[dict[str, Any]]] = None


class PinGridRequest(BaseModel):
    grid_id: str


def _get_active_style_and_color(session: SessionState):
    style_draft = next(
        (d for d in session.style_drafts if d.draft_id == session.active_style_draft_id),
        session.style_drafts[0] if session.style_drafts else None,
    )
    color_draft = next(
        (c for c in session.color_drafts if c.palette_id == session.active_palette_id),
        session.color_drafts[0] if session.color_drafts else None,
    )
    if not style_draft or not color_draft:
        raise HTTPException(status_code=400, detail="Session lacks active style or color draft")
    return style_draft, color_draft


def _get_active_grid(session: SessionState):
    grid_draft = next(
        (g for g in session.grid_drafts if g.grid_id == session.active_grid_id),
        session.grid_drafts[-1] if session.grid_drafts else None,
    )
    if not grid_draft:
        raise HTTPException(status_code=400, detail="Session lacks active grid draft")
    return grid_draft


def _bootstrap_session_from_image(
    name: str, filename: str, image_bytes: bytes
) -> SessionState:
    from .stages.s1_style import generate_style_drafts, render_style_draft_preview_svg
    from .stages.s2_color import generate_color_drafts
    from .stages.s3_grid import project_style_to_grid

    storage = get_storage_provider()
    session_id = f"session_{uuid.uuid4().hex[:8]}"
    stored_filename = f"{session_id}_{Path(filename).name}"
    storage.save_input_image(stored_filename, image_bytes)

    color_drafts = generate_color_drafts(image_bytes, max_colors=4, base_style="warm_white")
    active_color = color_drafts[0]

    style_drafts = generate_style_drafts(
        image_bytes,
        engine="algorithmic",
        whitespace_target=0.68,
        focal_offset=(0.0, 0.0),
    )
    for sd in style_drafts:
        sd.preview_svg = render_style_draft_preview_svg(sd, active_color)
    active_style = style_drafts[0]

    default_grid_cfg = GridConfig()
    initial_grid = project_style_to_grid(active_style, active_color, default_grid_cfg)

    session = SessionState(
        session_id=session_id,
        name=name,
        source_image_filename=stored_filename,
        style_drafts=style_drafts,
        active_style_draft_id=active_style.draft_id,
        color_drafts=color_drafts,
        active_palette_id=active_color.palette_id,
        grid_drafts=[initial_grid],
        active_grid_id=initial_grid.grid_id,
        pinned_grid_ids=[initial_grid.grid_id],
    )
    storage.save_session(session)
    return session


@app.get("/api/sessions")
def list_sessions() -> dict[str, Any]:
    storage = get_storage_provider()
    return {
        "data_root": str(storage.data_root),
        "sessions": storage.list_sessions(),
    }


@app.post("/api/sessions/upload", response_model=SessionState)
async def upload_or_create_session(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
    preset: Optional[str] = Form(default=None),
    name: Optional[str] = Form(default=None),
) -> SessionState:
    if file is not None and file.filename:
        image_bytes = await file.read()
        filename = file.filename
        session_name = name or Path(filename).stem
        return _bootstrap_session_from_image(session_name, filename, image_bytes)

    chosen_preset = preset
    if not chosen_preset:
        try:
            body = await request.json()
            chosen_preset = body.get("preset", "crane")
            name = name or body.get("name")
        except Exception:
            chosen_preset = "crane"

    chosen_preset = (chosen_preset or "crane").lower()
    image_bytes = generate_sample_image_bytes(chosen_preset)
    preset_titles = {
        "crane": "丹顶鹤 · Minimalist Crane",
        "cat": "几何猫 · Modular Cat",
        "mountain": "远山日出 · Alpine Sun",
        "lotus": "青莲盛放 · Lotus Icon",
    }
    session_name = name or preset_titles.get(chosen_preset, f"Sample ({chosen_preset})")
    return _bootstrap_session_from_image(
        session_name, f"sample_{chosen_preset}.png", image_bytes
    )


@app.get("/api/sessions/{session_id}", response_model=SessionState)
def get_session(session_id: str) -> SessionState:
    storage = get_storage_provider()
    try:
        return storage.load_session(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}/source-image")
def get_source_image(session_id: str) -> Response:
    storage = get_storage_provider()
    try:
        session = storage.load_session(session_id)
        img_path = storage.get_input_image_path(session.source_image_filename)
        return FileResponse(str(img_path))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/style", response_model=SessionState)
def update_or_generate_style(session_id: str, req: StyleRequest) -> SessionState:
    from .stages.s1_style import generate_style_drafts, render_style_draft_preview_svg
    from .stages.s3_grid import project_style_to_grid

    storage = get_storage_provider()
    session = storage.load_session(session_id)
    _, active_color = _get_active_style_and_color(session)

    if req.active_style_draft_id:
        matching = next(
            (d for d in session.style_drafts if d.draft_id == req.active_style_draft_id),
            None,
        )
        if not matching:
            raise HTTPException(status_code=404, detail="Style draft ID not found")
        session.active_style_draft_id = matching.draft_id
        current_cfg = _get_active_grid(session).config if session.grid_drafts else GridConfig()
        new_grid = project_style_to_grid(matching, active_color, current_cfg)
        session.grid_drafts.append(new_grid)
        session.active_grid_id = new_grid.grid_id
        storage.save_session(session)
        return session

    img_bytes = storage.get_input_image_path(session.source_image_filename).read_bytes()
    cues_dir = storage.data_root / "style_cues"
    style_cue_images: list[bytes] = []
    if cues_dir.exists():
        for cue_path in sorted(p for p in cues_dir.iterdir() if p.is_file())[:3]:
            style_cue_images.append(cue_path.read_bytes())

    new_drafts = generate_style_drafts(
        img_bytes,
        engine=req.engine,
        whitespace_target=req.whitespace_target,
        focal_offset=req.focal_offset,
        api_key=req.api_key,
        model=req.model,
        style_cue_images=style_cue_images or None,
    )
    for sd in new_drafts:
        if sd.engine != "ai" or not sd.preview_svg:
            sd.preview_svg = render_style_draft_preview_svg(sd, active_color)

    session.style_drafts = new_drafts
    session.active_style_draft_id = new_drafts[0].draft_id

    current_cfg = _get_active_grid(session).config if session.grid_drafts else GridConfig()
    new_grid = project_style_to_grid(new_drafts[0], active_color, current_cfg)
    session.grid_drafts.append(new_grid)
    session.active_grid_id = new_grid.grid_id
    storage.save_session(session)
    return session


@app.post("/api/sessions/{session_id}/color", response_model=SessionState)
def generate_or_select_color(session_id: str, req: ColorRequest) -> SessionState:
    from .stages.s1_style import render_style_draft_preview_svg
    from .stages.s2_color import generate_color_drafts
    from .stages.s3_grid import project_style_to_grid

    storage = get_storage_provider()
    session = storage.load_session(session_id)

    if req.active_palette_id:
        matching = next(
            (c for c in session.color_drafts if c.palette_id == req.active_palette_id),
            None,
        )
        if not matching:
            raise HTTPException(status_code=404, detail="Color palette ID not found")
        session.active_palette_id = matching.palette_id
    else:
        img_bytes = storage.get_input_image_path(session.source_image_filename).read_bytes()
        palettes = generate_color_drafts(
            img_bytes, max_colors=req.max_colors, base_style=req.base_style
        )
        session.color_drafts = palettes
        session.active_palette_id = palettes[0].palette_id

    active_style, active_color = _get_active_style_and_color(session)
    for sd in session.style_drafts:
        sd.preview_svg = render_style_draft_preview_svg(sd, active_color)

    current_cfg = _get_active_grid(session).config if session.grid_drafts else GridConfig()
    new_grid = project_style_to_grid(active_style, active_color, current_cfg)
    session.grid_drafts.append(new_grid)
    session.active_grid_id = new_grid.grid_id
    storage.save_session(session)
    return session


@app.put("/api/sessions/{session_id}/color", response_model=SessionState)
def customize_color_palette(session_id: str, req: ColorUpdateRequest) -> SessionState:
    from .stages.s1_style import render_style_draft_preview_svg
    from .stages.s2_color import srgb_to_oklch
    from .stages.s3_grid import project_style_to_grid

    storage = get_storage_provider()
    session = storage.load_session(session_id)
    target_id = req.palette_id or session.active_palette_id
    palette = next(
        (c for c in session.color_drafts if c.palette_id == target_id),
        session.color_drafts[0],
    )

    if req.base_hex:
        palette.base_hex = req.base_hex
    if req.base_name:
        palette.base_name = req.base_name
    if req.grid_filament_hex:
        palette.grid_filament_hex = req.grid_filament_hex
    if req.colors:
        for update_item in req.colors:
            slot = int(update_item.get("slot", 0))
            new_hex = update_item.get("hex")
            new_name = update_item.get("name")
            for fc in palette.colors:
                if fc.slot == slot:
                    if new_hex and len(new_hex) == 7 and new_hex.startswith("#"):
                        fc.hex = new_hex.upper()
                        r = int(fc.hex[1:3], 16)
                        g = int(fc.hex[3:5], 16)
                        b = int(fc.hex[5:7], 16)
                        fc.oklch = srgb_to_oklch(r, g, b)
                    if new_name:
                        fc.name = new_name

    session.active_palette_id = palette.palette_id
    active_style, active_color = _get_active_style_and_color(session)
    for sd in session.style_drafts:
        sd.preview_svg = render_style_draft_preview_svg(sd, active_color)

    current_cfg = _get_active_grid(session).config if session.grid_drafts else GridConfig()
    new_grid = project_style_to_grid(active_style, active_color, current_cfg)
    session.grid_drafts[-1] = new_grid
    session.active_grid_id = new_grid.grid_id
    storage.save_session(session)
    return session


@app.post("/api/sessions/{session_id}/grid", response_model=SessionState)
def compute_grid(session_id: str, config: GridConfig) -> SessionState:
    from .stages.s3_grid import project_style_to_grid

    storage = get_storage_provider()
    session = storage.load_session(session_id)
    active_style, active_color = _get_active_style_and_color(session)

    new_grid = project_style_to_grid(active_style, active_color, config)
    # Keep pinned grids plus the latest unpinned working grid to prevent unbounded growth
    pinned_set = set(session.pinned_grid_ids)
    kept_grids = [g for g in session.grid_drafts if g.grid_id in pinned_set]
    kept_grids.append(new_grid)
    session.grid_drafts = kept_grids
    session.active_grid_id = new_grid.grid_id
    storage.save_session(session)
    return session


@app.post("/api/sessions/{session_id}/grid/pin", response_model=SessionState)
def toggle_pin_grid(session_id: str, req: PinGridRequest) -> SessionState:
    storage = get_storage_provider()
    session = storage.load_session(session_id)
    if req.grid_id in session.pinned_grid_ids:
        session.pinned_grid_ids.remove(req.grid_id)
    else:
        session.pinned_grid_ids.append(req.grid_id)
        if len(session.pinned_grid_ids) > 4:
            session.pinned_grid_ids = session.pinned_grid_ids[-4:]
    storage.save_session(session)
    return session


@app.post("/api/sessions/{session_id}/svg", response_model=SvgExportResult)
def generate_svg_endpoint(
    session_id: str, config: Optional[SvgConfig] = None
) -> SvgExportResult:
    from .stages.s4_svg import generate_svg_and_png

    storage = get_storage_provider()
    session = storage.load_session(session_id)
    _, active_color = _get_active_style_and_color(session)
    active_grid = _get_active_grid(session)

    cfg = config or SvgConfig()
    result = generate_svg_and_png(active_grid, active_color, cfg)
    storage.save_export(session_id, result.svg_filename, result.svg_content)
    storage.save_export(
        session_id, result.png_filename, base64.b64decode(result.png_base64)
    )
    return result


@app.post("/api/sessions/{session_id}/3mf/preview", response_model=Preview3DPayload)
def preview_3mf_endpoint(
    session_id: str, config: Optional[Bambu3mfConfig] = None
) -> Preview3DPayload:
    from .stages.s5_3mf import build_preview_3d_payload

    storage = get_storage_provider()
    session = storage.load_session(session_id)
    _, active_color = _get_active_style_and_color(session)
    active_grid = _get_active_grid(session)

    cfg = config or Bambu3mfConfig()
    return build_preview_3d_payload(active_grid, active_color, cfg)


@app.post("/api/sessions/{session_id}/3mf/export", response_model=Bambu3mfExportResult)
def export_3mf_endpoint(
    session_id: str, config: Optional[Bambu3mfConfig] = None
) -> Bambu3mfExportResult:
    from .stages.s4_svg import generate_svg_and_png
    from .stages.s5_3mf import generate_bambu_3mf

    storage = get_storage_provider()
    session = storage.load_session(session_id)
    _, active_color = _get_active_style_and_color(session)
    active_grid = _get_active_grid(session)

    cfg = config or Bambu3mfConfig()
    svg_res = generate_svg_and_png(
        active_grid,
        active_color,
        SvgConfig(stroke_width_mm=cfg.line_width_mm, bulge_ratio=cfg.bulge_ratio),
    )
    thumb_bytes = base64.b64decode(svg_res.png_base64)

    archive_bytes, result = generate_bambu_3mf(
        active_grid, active_color, cfg, thumbnail_png_bytes=thumb_bytes
    )
    storage.save_export(session_id, result.filename, archive_bytes)
    return result


@app.get("/api/sessions/{session_id}/exports/{filename}")
def download_export(session_id: str, filename: str) -> Response:
    storage = get_storage_provider()
    try:
        path = storage.get_export_path(session_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    media_type = "application/octet-stream"
    if filename.endswith(".svg"):
        media_type = "image/svg+xml"
    elif filename.endswith(".png"):
        media_type = "image/png"
    elif filename.endswith(".3mf"):
        media_type = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"

    return FileResponse(
        path=str(path),
        filename=filename,
        media_type=media_type,
    )


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:

    @app.get("/")
    def fallback_root() -> HTMLResponse:
        return HTMLResponse("<h1>3D_PRINT_LACE API Running</h1>")
