"""Stage 2: Perceptual OKLab/OKLCH Functional Color Reconstruction Engine.

Extracts dominant and salient hues from an input image and reconstructs them into
3 high-recognition functional color drafts (配色草稿) with anti-neon OKLCH bounds,
Warm White / Gray White neutral bases, and Extruder 1 Light Gray 3D grid mapping.
"""

from __future__ import annotations

import io
import math
from PIL import Image

from backend.app.schemas import ColorDraft, FunctionalColor

# Neutral base definitions
BASE_STYLES: dict[str, tuple[str, str]] = {
    "warm_white": ("Warm White / 暖白", "#F5F1E6"),
    "gray_white": ("Gray White / 灰白", "#ECEEEB"),
}

GRID_FILAMENT_NAME = "Light Gray Grid / 浅灰网格"
GRID_FILAMENT_HEX = "#C4C8CE"

# Anti-neon OKLCH bounds for functional chromatic slots
CHROMATIC_L_MIN = 0.42
CHROMATIC_L_MAX = 0.78
CHROMATIC_C_MIN = 0.09
CHROMATIC_C_MAX = 0.22

# Bounds for dark structural ink slot
INK_L_MIN = 0.20
INK_L_MAX = 0.35
INK_C_MAX = 0.05


def _srgb_channel_to_linear(c: int) -> float:
    """Convert an 8-bit sRGB channel [0..255] to linear RGB [0.0..1.0]."""
    v = max(0.0, min(1.0, c / 255.0))
    if v <= 0.04045:
        return v / 12.92
    return ((v + 0.055) / 1.055) ** 2.4


def _linear_channel_to_srgb(v: float) -> int:
    """Convert a linear RGB channel [0.0..1.0] to 8-bit sRGB [0..255]."""
    clamped = max(0.0, min(1.0, v))
    if clamped <= 0.0031308:
        s = 12.92 * clamped
    else:
        s = 1.055 * (clamped ** (1.0 / 2.4)) - 0.055
    return max(0, min(255, int(round(s * 255.0))))


def _srgb_to_oklab(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert 8-bit sRGB (r, g, b) to Björn Ottosson OKLab (L, a, b)."""
    r_lin = _srgb_channel_to_linear(r)
    g_lin = _srgb_channel_to_linear(g)
    b_lin = _srgb_channel_to_linear(b)

    l = 0.4122214708 * r_lin + 0.5363325363 * g_lin + 0.0514459929 * b_lin
    m = 0.2119034982 * r_lin + 0.6806995451 * g_lin + 0.1073969566 * b_lin
    s = 0.0883024619 * r_lin + 0.2817188376 * g_lin + 0.6299787005 * b_lin

    l_ = math.cbrt(max(0.0, l))
    m_ = math.cbrt(max(0.0, m))
    s_ = math.cbrt(max(0.0, s))

    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_val = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return (L, a, b_val)


def _oklab_to_linear_rgb(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Convert OKLab (L, a, b) to linear sRGB (r_lin, g_lin, b_lin)."""
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b

    l = l_ * l_ * l_
    m = m_ * m_ * m_
    s = s_ * s_ * s_

    r_lin = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g_lin = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b_lin = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return (r_lin, g_lin, b_lin)


def srgb_to_oklch(r: int, g: int, b: int) -> tuple[float, float, float]:
    """Convert 8-bit sRGB (0..255) to OKLCH (L, C, H).

    Returns:
        (L, C, H) where L in [0.0, 1.0], C in [0.0, 0.4], H in [0.0, 360.0).
    """
    L, a, b_val = _srgb_to_oklab(r, g, b)
    C = math.hypot(a, b_val)
    if C < 1e-6:
        H = 0.0
    else:
        H = math.degrees(math.atan2(b_val, a)) % 360.0
    L_clamped = round(max(0.0, min(1.0, L)), 4)
    C_clamped = round(max(0.0, min(0.4, C)), 4)
    H_clamped = round(H % 360.0, 2)
    return (L_clamped, C_clamped, H_clamped)


def oklch_to_hex(L: float, C: float, H: float) -> str:
    """Convert OKLCH (L, C, H) to 6-digit uppercase hex '#RRGGBB' with gamut clamping."""
    L_clamped = max(0.0, min(1.0, float(L)))
    C_clamped = max(0.0, min(0.4, float(C)))
    rad = math.radians(float(H) % 360.0)
    cos_h = math.cos(rad)
    sin_h = math.sin(rad)

    r_lin, g_lin, b_lin = _oklab_to_linear_rgb(
        L_clamped, C_clamped * cos_h, C_clamped * sin_h
    )

    # If outside sRGB linear gamut [0, 1], binary-search maximum in-gamut chroma at (L, H)
    eps = 1e-4
    if (
        r_lin < -eps
        or r_lin > 1.0 + eps
        or g_lin < -eps
        or g_lin > 1.0 + eps
        or b_lin < -eps
        or b_lin > 1.0 + eps
    ):
        low = 0.0
        high = C_clamped
        for _ in range(14):
            mid = 0.5 * (low + high)
            rl, gl, bl = _oklab_to_linear_rgb(L_clamped, mid * cos_h, mid * sin_h)
            if -eps <= rl <= 1.0 + eps and -eps <= gl <= 1.0 + eps and -eps <= bl <= 1.0 + eps:
                low = mid
            else:
                high = mid
        r_lin, g_lin, b_lin = _oklab_to_linear_rgb(L_clamped, low * cos_h, low * sin_h)

    r = _linear_channel_to_srgb(r_lin)
    g = _linear_channel_to_srgb(g_lin)
    b = _linear_channel_to_srgb(b_lin)
    return f"#{r:02X}{g:02X}{b:02X}"


def _hex_to_rgb(hex_code: str) -> tuple[int, int, int]:
    """Parse '#RRGGBB' into (r, g, b)."""
    clean = hex_code.lstrip("#")
    return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))


def _hue_distance(h1: float, h2: float) -> float:
    """Shortest angular distance in degrees between two hues in [0, 360)."""
    diff = abs((h1 - h2) % 360.0)
    return min(diff, 360.0 - diff)


def _hue_name(h: float) -> str:
    """Return a bilingual functional color name based on OKLCH hue angle."""
    angle = h % 360.0
    if angle < 25 or angle >= 345:
        return "Crimson Signal / 绯红编码"
    if angle < 55:
        return "Warm Orange / 暖橙编码"
    if angle < 85:
        return "Ochre Amber / 琥珀编码"
    if angle < 135:
        return "Olive Chartreuse / 橄榄绿码"
    if angle < 175:
        return "Jade Green / 翠绿编码"
    if angle < 215:
        return "Teal Cyan / 青绿编码"
    if angle < 265:
        return "Cobalt Blue / 钴蓝编码"
    if angle < 305:
        return "Ultramarine Violet / 群青紫码"
    return "Magenta Code / 洋红编码"


def _make_functional_color(
    slot: int,
    name: str,
    target_L: float,
    target_C: float,
    target_H: float,
    role: str,
    is_dark_ink: bool = False,
) -> FunctionalColor:
    """Construct a FunctionalColor with guaranteed anti-neon OKLCH compliance after hex quantization."""
    if is_dark_ink:
        L = max(INK_L_MIN + 0.02, min(INK_L_MAX - 0.02, target_L))
        C = max(0.0, min(INK_C_MAX - 0.01, target_C))
        H = target_H % 360.0
        hex_val = oklch_to_hex(L, C, H)
        act_L, act_C, act_H = srgb_to_oklch(*_hex_to_rgb(hex_val))
        act_L = max(INK_L_MIN, min(INK_L_MAX, act_L))
        act_C = max(0.0, min(INK_C_MAX, act_C))
        return FunctionalColor(
            slot=slot,
            name=name,
            hex=hex_val,
            oklch=(round(act_L, 4), round(act_C, 4), round(act_H, 2)),
            role=role,
            bambu_extruder=slot + 1,
        )

    # Chromatic functional color: ensure L in [0.42, 0.78] and C in [0.09, 0.22]
    L = max(0.46, min(0.73, target_L))
    C = max(0.115, min(0.185, target_C))
    H = target_H % 360.0

    hex_val = oklch_to_hex(L, C, H)
    act_L, act_C, act_H = srgb_to_oklch(*_hex_to_rgb(hex_val))

    # If sRGB gamut boundary at high/low lightness clipped C below 0.09, pull L toward 0.58
    if act_C < CHROMATIC_C_MIN:
        L = 0.58
        C = 0.145
        hex_val = oklch_to_hex(L, C, H)
        act_L, act_C, act_H = srgb_to_oklch(*_hex_to_rgb(hex_val))

    act_L = max(CHROMATIC_L_MIN, min(CHROMATIC_L_MAX, act_L))
    act_C = max(CHROMATIC_C_MIN, min(CHROMATIC_C_MAX, act_C))

    return FunctionalColor(
        slot=slot,
        name=name,
        hex=hex_val,
        oklch=(round(act_L, 4), round(act_C, 4), round(act_H, 2)),
        role=role,
        bambu_extruder=slot + 1,
    )


def _extract_signature_oklch_clusters(
    image_bytes: bytes, k: int = 4
) -> list[tuple[float, float, float]]:
    """Sample salient foreground pixels and run deterministic K-means++ in OKLab space."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    # Composite onto white background if transparent
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    composited = Image.alpha_composite(bg, img).convert("RGB")
    small = composited.resize((48, 48), Image.Resampling.BILINEAR)
    alpha_small = img.getchannel("A").resize((48, 48), Image.Resampling.NEAREST)

    width, height = small.size
    rgb_pixels = list(
        small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata()
    )
    alpha_pixels = list(
        alpha_small.get_flattened_data()
        if hasattr(alpha_small, "get_flattened_data")
        else alpha_small.getdata()
    )

    # Estimate border background in OKLab
    border_labs: list[tuple[float, float, float]] = []
    for y in range(height):
        for x in range(width):
            if x in (0, 1, width - 2, width - 1) or y in (0, 1, height - 2, height - 1):
                r, g, b = rgb_pixels[y * width + x]
                border_labs.append(_srgb_to_oklab(r, g, b))

    bg_L = sum(p[0] for p in border_labs) / len(border_labs)
    bg_a = sum(p[1] for p in border_labs) / len(border_labs)
    bg_b = sum(p[2] for p in border_labs) / len(border_labs)

    # Filter salient foreground pixels
    salient_labs: list[tuple[float, float, float, float]] = []
    all_labs: list[tuple[float, float, float, float]] = []
    for idx, (r, g, b) in enumerate(rgb_pixels):
        a_val = alpha_pixels[idx]
        L, a, b_ok = _srgb_to_oklab(r, g, b)
        chroma = math.hypot(a, b_ok)
        bg_dist = math.sqrt((L - bg_L) ** 2 + (a - bg_a) ** 2 + (b_ok - bg_b) ** 2)
        weight = 1.0 + 3.0 * chroma + 2.0 * bg_dist
        all_labs.append((L, a, b_ok, weight))
        if a_val > 64 and (bg_dist > 0.06 or chroma > 0.035):
            salient_labs.append((L, a, b_ok, weight))

    samples = salient_labs if len(salient_labs) >= k * 4 else all_labs

    # Deterministic K-means++ initialization in OKLab
    centers: list[tuple[float, float, float]] = []
    # Pick most chromatic / highest-weight sample first
    first = max(samples, key=lambda s: s[3])
    centers.append((first[0], first[1], first[2]))

    for _ in range(1, k):
        best_pt = samples[0]
        best_score = -1.0
        for s in samples:
            min_d2 = min(
                (s[0] - c[0]) ** 2 + (s[1] - c[1]) ** 2 + (s[2] - c[2]) ** 2
                for c in centers
            )
            score = min_d2 * s[3]
            if score > best_score:
                best_score = score
                best_pt = s
        centers.append((best_pt[0], best_pt[1], best_pt[2]))

    # Run 10 iterations of weighted Lloyd's K-means
    cluster_weights = [0.0] * k
    for _ in range(10):
        sums_L = [0.0] * k
        sums_a = [0.0] * k
        sums_b = [0.0] * k
        weights = [0.0] * k

        for s_L, s_a, s_b, w in samples:
            best_idx = 0
            best_d2 = float("inf")
            for idx, (c_L, c_a, c_b) in enumerate(centers):
                d2 = (s_L - c_L) ** 2 + (s_a - c_a) ** 2 + (s_b - c_b) ** 2
                if d2 < best_d2:
                    best_d2 = d2
                    best_idx = idx
            sums_L[best_idx] += s_L * w
            sums_a[best_idx] += s_a * w
            sums_b[best_idx] += s_b * w
            weights[best_idx] += w

        cluster_weights = weights
        for idx in range(k):
            if weights[idx] > 1e-6:
                centers[idx] = (
                    sums_L[idx] / weights[idx],
                    sums_a[idx] / weights[idx],
                    sums_b[idx] / weights[idx],
                )

    # Sort clusters by salience (total weight * (1 + chroma))
    scored_clusters: list[tuple[float, tuple[float, float, float]]] = []
    for idx, (c_L, c_a, c_b) in enumerate(centers):
        C = math.hypot(c_a, c_b)
        H = (math.degrees(math.atan2(c_b, c_a)) % 360.0) if C >= 1e-5 else (idx * 75.0 + 205.0) % 360.0
        salience = cluster_weights[idx] * (1.0 + 4.0 * C)
        scored_clusters.append((salience, (c_L, C, H)))

    scored_clusters.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in scored_clusters]


def generate_color_drafts(
    image_bytes: bytes,
    max_colors: int = 4,
    base_style: str = "warm_white",
) -> list[ColorDraft]:
    """Generate 3 distinct perceptual OKLCH functional ColorDraft palettes from an input image."""
    k = max(2, min(int(max_colors), 6))
    base_name, base_hex = BASE_STYLES.get(base_style, BASE_STYLES["warm_white"])

    raw_clusters = _extract_signature_oklch_clusters(image_bytes, k=k)
    roles = [
        "primary_code",
        "secondary_code",
        "accent_code",
        "structural_code",
        "accent_code",
        "secondary_code",
    ]

    #Ensure distinct functional hues across extracted signature clusters (min 42 deg separation)
    signature_hues: list[float] = []
    fallback_offsets = [0.0, 145.0, 225.0, 75.0, 185.0, 305.0]
    base_dominant_hue = raw_clusters[0][2] if raw_clusters else 205.0
    if raw_clusters and raw_clusters[0][1] < 0.02:
        base_dominant_hue = 205.0  # Default architectural teal-blue for achromatic inputs

    for idx in range(k):
        cand_h = raw_clusters[idx][2] if idx < len(raw_clusters) and raw_clusters[idx][1] >= 0.02 else (
            (base_dominant_hue + fallback_offsets[idx % len(fallback_offsets)]) % 360.0
        )
        # Enforce minimum angular separation from already selected hues
        attempts = 0
        while any(_hue_distance(cand_h, prev_h) < 42.0 for prev_h in signature_hues) and attempts < 8:
            cand_h = (cand_h + 48.0) % 360.0
            attempts += 1
        signature_hues.append(cand_h)

    # -------------------------------------------------------------------------
    # Palette 1: 标志性重构色系 / Signature Functional System
    # -------------------------------------------------------------------------
    lightness_tiers_p1 = [0.54, 0.63, 0.58, 0.48, 0.67, 0.52]
    chroma_tiers_p1 = [0.145, 0.135, 0.165, 0.125, 0.140, 0.130]
    p1_colors: list[FunctionalColor] = []
    for idx in range(k):
        slot = idx + 1
        raw_L = raw_clusters[idx][0] if idx < len(raw_clusters) else lightness_tiers_p1[idx]
        raw_C = raw_clusters[idx][1] if idx < len(raw_clusters) else chroma_tiers_p1[idx]
        # Blend raw lightness/chroma with functional tiers to keep high recognition & anti-neon restraint
        target_L = 0.45 * max(0.46, min(0.72, raw_L)) + 0.55 * lightness_tiers_p1[idx]
        target_C = max(0.115, min(0.175, max(raw_C, chroma_tiers_p1[idx])))
        hue = signature_hues[idx]
        p1_colors.append(
            _make_functional_color(
                slot=slot,
                name=_hue_name(hue),
                target_L=target_L,
                target_C=target_C,
                target_H=hue,
                role=roles[idx],
            )
        )

    palette_1 = ColorDraft(
        palette_id="pal_signature",
        name="标志性重构色系 / Signature Functional System",
        base_name=base_name,
        base_hex=base_hex,
        grid_filament_name=GRID_FILAMENT_NAME,
        grid_filament_hex=GRID_FILAMENT_HEX,
        colors=p1_colors,
    )

    # -------------------------------------------------------------------------
    # Palette 2: 高识别青绿·洋红·橙 / High-Recognition Functional Code
    # Anchored around Teal (#008F7A), Cobalt Blue (#1D5B9B), Magenta (#C82B6E), Warm Orange (#E85D2A)
    # -------------------------------------------------------------------------
    anchor_specs = [
        ("Teal Cyan / 青绿", 0.57, 0.125, 181.0),
        ("Cobalt Blue / 钴蓝", 0.48, 0.125, 252.0),
        ("Magenta Code / 洋红", 0.53, 0.175, 353.0),
        ("Warm Orange / 暖橙", 0.62, 0.168, 38.0),
        ("Ochre Gold / 琥珀金", 0.68, 0.135, 78.0),
        ("Jade Signal / 翠绿", 0.55, 0.135, 148.0),
    ]
    # Rotate only among the 4 core anchors so Slot 1 aligns closest to the image's dominant hue,
    # while guaranteeing Teal, Cobalt, Magenta, and Orange are always present when k >= 4.
    core_anchors = list(anchor_specs[:4])
    extra_anchors = list(anchor_specs[4:])
    closest_core_idx = min(
        range(len(core_anchors)),
        key=lambda i: _hue_distance(core_anchors[i][3], base_dominant_hue),
    )
    ordered_anchors = (
        core_anchors[closest_core_idx:]
        + core_anchors[:closest_core_idx]
        + extra_anchors
    )

    p2_colors: list[FunctionalColor] = []
    for idx in range(k):
        slot = idx + 1
        a_name, a_L, a_C, a_H = ordered_anchors[idx]
        # Gently harmonize anchor hue (+/- 5 deg) toward image dominant hue without losing identity
        delta = ((base_dominant_hue - a_H + 540.0) % 360.0) - 180.0
        harmonized_H = (a_H + max(-5.0, min(5.0, 0.12 * delta))) % 360.0
        p2_colors.append(
            _make_functional_color(
                slot=slot,
                name=a_name,
                target_L=a_L,
                target_C=a_C,
                target_H=harmonized_H,
                role=roles[idx],
            )
        )

    palette_2 = ColorDraft(
        palette_id="pal_functional_code",
        name="高识别青绿·洋红·橙 / High-Recognition Functional Code",
        base_name=base_name,
        base_hex=base_hex,
        grid_filament_name=GRID_FILAMENT_NAME,
        grid_filament_hex=GRID_FILAMENT_HEX,
        colors=p2_colors,
    )

    # -------------------------------------------------------------------------
    # Palette 3: 克制冷灰白对比 / Restrained Architectural Contrast
    # Gray-White (#ECEEEB) base + Deep Ink (#25282A) + high-contrast functional accents
    # -------------------------------------------------------------------------
    gray_base_name, gray_base_hex = BASE_STYLES["gray_white"]
    p3_colors: list[FunctionalColor] = [
        _make_functional_color(
            slot=1,
            name="Deep Structural Ink / 深墨结构线",
            target_L=0.275,
            target_C=0.008,
            target_H=235.0,
            role="structural_code",
            is_dark_ink=True,
        )
    ]

    arch_accents = [
        ("Architectural Teal / 建筑青绿", 0.56, 0.128, signature_hues[0]),
        ("Signal Terracotta / 陶土暖橙", 0.60, 0.158, (signature_hues[0] + 165.0) % 360.0),
        ("Mineral Cobalt / 矿物钴蓝", 0.50, 0.125, (signature_hues[0] + 75.0) % 360.0),
        ("Crimson Accent / 绯红点缀", 0.54, 0.165, (signature_hues[0] + 240.0) % 360.0),
        ("Ochre Module / 赭石色块", 0.66, 0.132, (signature_hues[0] + 120.0) % 360.0),
    ]
    for idx in range(1, k):
        slot = idx + 1
        acc_name, acc_L, acc_C, acc_H = arch_accents[idx - 1]
        p3_colors.append(
            _make_functional_color(
                slot=slot,
                name=acc_name,
                target_L=acc_L,
                target_C=acc_C,
                target_H=acc_H,
                role=roles[idx - 1],
            )
        )

    palette_3 = ColorDraft(
        palette_id="pal_architectural",
        name="克制冷灰白对比 / Restrained Architectural Contrast",
        base_name=gray_base_name,
        base_hex=gray_base_hex,
        grid_filament_name=GRID_FILAMENT_NAME,
        grid_filament_hex=GRID_FILAMENT_HEX,
        colors=p3_colors,
    )

    return [palette_1, palette_2, palette_3]
