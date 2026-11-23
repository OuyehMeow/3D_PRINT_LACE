"""Unit tests for Stage 1 (s1_style.py) and Stage 2 (s2_color.py).

All test images are synthesized in-memory using io.BytesIO and PIL.Image
to strictly uphold the 'Method in the Cloud, Data at Local' architectural invariant.
"""

from __future__ import annotations

import io
from PIL import Image, ImageDraw

from backend.app.stages.s1_style import (
    generate_style_drafts,
    rdp_simplify,
    render_style_draft_preview_svg,
)
from backend.app.stages.s2_color import (
    generate_color_drafts,
    oklch_to_hex,
    srgb_to_oklch,
)


def _make_in_memory_subject_png(width: int = 128, height: int = 128) -> bytes:
    """Create an in-memory PNG with a vivid multi-part geometric bird/vessel subject on warm white."""
    img = Image.new("RGBA", (width, height), (245, 241, 230, 255))
    draw = ImageDraw.Draw(img)
    # Primary cobalt blue torso polygon
    draw.polygon(
        [(36, 88), (64, 28), (94, 82), (66, 98)],
        fill=(25, 92, 158, 255),
    )
    # Secondary teal wing block
    draw.polygon(
        [(52, 54), (98, 44), (86, 76)],
        fill=(0, 145, 122, 255),
    )
    # Warm orange head/accent circle
    draw.ellipse((54, 20, 74, 40), fill=(232, 93, 42, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_blank_in_memory_png() -> bytes:
    """Create a uniform solid-color in-memory PNG to verify degenerate input resilience."""
    img = Image.new("RGB", (64, 64), (240, 240, 240))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_stage1_generate_style_drafts_structure_and_bounds() -> None:
    """Verify generate_style_drafts returns 3 distinct drafts with valid coordinates and whitespace."""
    image_bytes = _make_in_memory_subject_png()
    drafts = generate_style_drafts(
        image_bytes,
        engine="algorithmic",
        whitespace_target=0.68,
        focal_offset=(0.04, -0.03),
    )

    assert len(drafts) == 3
    draft_ids = {d.draft_id for d in drafts}
    assert len(draft_ids) == 3

    for draft in drafts:
        assert draft.whitespace_ratio >= 0.55
        assert draft.focal_center == (0.54, 0.47)
        assert len(draft.regions) >= 2
        assert len(draft.structural_lines) >= 2
        assert draft.preview_svg is not None
        assert draft.preview_svg.strip().startswith("<svg")
        assert draft.preview_svg.strip().endswith("</svg>")

        for reg in draft.regions:
            assert reg.color_slot >= 0
            assert len(reg.exterior) >= 3
            for x, y in reg.exterior:
                assert 0.0 <= x <= 1.0
                assert 0.0 <= y <= 1.0
            for hole in reg.holes:
                assert len(hole) >= 3
                for hx, hy in hole:
                    assert 0.0 <= hx <= 1.0
                    assert 0.0 <= hy <= 1.0

        for line in draft.structural_lines:
            assert line.color_slot >= 1
            assert len(line.points) >= 2
            assert line.weight > 0.0
            for lx, ly in line.points:
                assert 0.0 <= lx <= 1.0
                assert 0.0 <= ly <= 1.0


def test_stage1_blank_image_and_ai_fallback_resilience() -> None:
    """Verify uniform images and offline engine='ai' gracefully return 3 valid algorithmic drafts."""
    blank_bytes = _make_blank_in_memory_png()
    drafts = generate_style_drafts(
        blank_bytes,
        engine="ai",
        whitespace_target=0.72,
        api_key=None,
    )
    assert len(drafts) == 3
    for d in drafts:
        assert d.whitespace_ratio >= 0.55
        assert len(d.regions) >= 2
        assert len(d.structural_lines) >= 2
        assert d.preview_svg and "<svg" in d.preview_svg


def test_stage1_rdp_and_preview_svg_with_color_draft() -> None:
    """Verify rdp_simplify and render_style_draft_preview_svg with a Stage 2 ColorDraft."""
    pts = [(0.1, 0.1), (0.3, 0.101), (0.5, 0.1), (0.5, 0.6), (0.9, 0.6)]
    simplified = rdp_simplify(pts, epsilon=0.01)
    assert simplified == [(0.1, 0.1), (0.5, 0.1), (0.5, 0.6), (0.9, 0.6)]

    img_bytes = _make_in_memory_subject_png()
    style_draft = generate_style_drafts(img_bytes)[0]
    color_draft = generate_color_drafts(img_bytes)[1]

    svg_str = render_style_draft_preview_svg(style_draft, color_draft)
    assert color_draft.base_hex in svg_str
    assert color_draft.grid_filament_hex in svg_str
    assert color_draft.colors[0].hex in svg_str


def test_stage2_oklch_conversions_and_gamut_clamping() -> None:
    """Verify exact sRGB <-> OKLCH conversions and high-chroma gamut clamping."""
    # Pure white and black
    l_w, c_w, _ = srgb_to_oklch(255, 255, 255)
    assert abs(l_w - 1.0) < 1e-3
    assert c_w < 1e-3
    assert oklch_to_hex(1.0, 0.0, 0.0) == "#FFFFFF"

    l_k, c_k, _ = srgb_to_oklch(0, 0, 0)
    assert l_k < 1e-3
    assert c_k < 1e-3
    assert oklch_to_hex(0.0, 0.0, 0.0) == "#000000"

    # Chromatic round-trip for Teal (#008F7A)
    L, C, H = srgb_to_oklch(0x00, 0x8F, 0x7A)
    assert 0.50 <= L <= 0.65
    assert 0.09 <= C <= 0.16
    assert 170.0 <= H <= 195.0
    hex_rt = oklch_to_hex(L, C, H)
    r2 = int(hex_rt[1:3], 16)
    g2 = int(hex_rt[3:5], 16)
    b2 = int(hex_rt[5:7], 16)
    assert abs(r2 - 0x00) <= 2
    assert abs(g2 - 0x8F) <= 2
    assert abs(b2 - 0x7A) <= 2

    # Extreme out-of-gamut chroma should clamp cleanly into valid 6-digit hex
    clamped_hex = oklch_to_hex(0.65, 0.38, 45.0)
    assert len(clamped_hex) == 7 and clamped_hex.startswith("#")


def test_stage2_generate_color_drafts_anti_neon_and_extruder_mapping() -> None:
    """Verify generate_color_drafts returns 3 palettes with anti-neon OKLCH bounds and Extruder 2..K+1."""
    image_bytes = _make_in_memory_subject_png()
    palettes = generate_color_drafts(image_bytes, max_colors=4, base_style="warm_white")

    assert len(palettes) == 3
    palette_ids = [p.palette_id for p in palettes]
    assert palette_ids == ["pal_signature", "pal_functional_code", "pal_architectural"]

    for p_idx, pal in enumerate(palettes):
        assert pal.grid_filament_hex == "#C4C8CE"
        if p_idx < 2:
            assert pal.base_hex == "#F5F1E6"
        else:
            assert pal.base_hex == "#ECEEEB"

        assert len(pal.colors) == 4
        for slot_idx, fc in enumerate(pal.colors, start=1):
            assert fc.slot == slot_idx
            assert fc.bambu_extruder == slot_idx + 1
            assert len(fc.hex) == 7 and fc.hex.startswith("#")

            L, C, H = fc.oklch
            assert 0.0 <= H < 360.0

            # Also verify actual hex sRGB -> OKLCH satisfies anti-neon constraints
            r = int(fc.hex[1:3], 16)
            g = int(fc.hex[3:5], 16)
            b = int(fc.hex[5:7], 16)
            hex_L, hex_C, _ = srgb_to_oklch(r, g, b)

            if pal.palette_id == "pal_architectural" and fc.slot == 1:
                # Deep structural ink bounds: L in [0.20, 0.35], C <= 0.05
                assert 0.20 <= L <= 0.35
                assert C <= 0.05
                assert 0.19 <= hex_L <= 0.36
                assert hex_C <= 0.055
            else:
                # Functional chromatic anti-neon bounds: L in [0.42, 0.78], C in [0.09, 0.22]
                assert 0.42 <= L <= 0.78
                assert 0.09 <= C <= 0.22
                assert 0.41 <= hex_L <= 0.79
                assert 0.085 <= hex_C <= 0.225
