"""In-memory procedural sample input image generator for immediate testing and demos.

All generated images are saved strictly into DATA_ROOT/inputs/ via StorageProvider,
never into the Git repository.
"""

from __future__ import annotations

import io
import math
from PIL import Image, ImageDraw


def generate_sample_image_bytes(preset: str = "crane", size: int = 512) -> bytes:
    """Render a high-contrast artistic subject image in memory for testing the 5-stage pipeline."""
    preset = (preset or "crane").lower().strip()
    img = Image.new("RGB", (size, size), (244, 240, 230))
    draw = ImageDraw.Draw(img)

    cx, cy = size * 0.5, size * 0.5

    if preset == "cat":
        # Sitting cat silhouette with geometric collar, ears, tail curve, and warm orange/teal accents
        # Background warm sun circle
        draw.ellipse(
            [cx - size * 0.26, cy - size * 0.28, cx + size * 0.26, cy + size * 0.24],
            fill=(232, 93, 42),
        )
        # Cat body
        body_poly = [
            (cx - size * 0.14, cy + size * 0.28),
            (cx - size * 0.16, cy - size * 0.02),
            (cx - size * 0.09, cy - size * 0.14),
            (cx + size * 0.08, cy - size * 0.14),
            (cx + size * 0.15, cy + size * 0.04),
            (cx + size * 0.14, cy + size * 0.28),
        ]
        draw.polygon(body_poly, fill=(29, 91, 155))
        # Cat head & pointed ears
        head_poly = [
            (cx - size * 0.12, cy - size * 0.12),
            (cx - size * 0.14, cy - size * 0.30),
            (cx - size * 0.05, cy - size * 0.21),
            (cx + size * 0.05, cy - size * 0.21),
            (cx + size * 0.14, cy - size * 0.30),
            (cx + size * 0.12, cy - size * 0.12),
        ]
        draw.polygon(head_poly, fill=(37, 40, 42))
        # Teal collar & eye accents
        draw.rectangle(
            [cx - size * 0.10, cy - size * 0.13, cx + size * 0.10, cy - size * 0.09],
            fill=(0, 143, 122),
        )
        # Curving tail
        tail_pts = [
            (cx + size * 0.13, cy + size * 0.25),
            (cx + size * 0.25, cy + size * 0.20),
            (cx + size * 0.28, cy + size * 0.06),
            (cx + size * 0.22, cy - size * 0.04),
        ]
        draw.line(tail_pts, fill=(200, 43, 110), width=int(size * 0.045))

    elif preset == "mountain":
        # Minimalist alpine peaks, rising sun, and winding river line
        draw.ellipse(
            [cx - size * 0.18, cy - size * 0.30, cx + size * 0.18, cy + size * 0.06],
            fill=(232, 93, 42),
        )
        peak_main = [
            (cx - size * 0.32, cy + size * 0.24),
            (cx - size * 0.04, cy - size * 0.22),
            (cx + size * 0.26, cy + size * 0.24),
        ]
        draw.polygon(peak_main, fill=(29, 91, 155))
        peak_sec = [
            (cx - size * 0.06, cy + size * 0.24),
            (cx + size * 0.16, cy - size * 0.10),
            (cx + size * 0.34, cy + size * 0.24),
        ]
        draw.polygon(peak_sec, fill=(0, 143, 122))
        # Structural ridge line
        draw.line(
            [
                (cx - size * 0.04, cy - size * 0.22),
                (cx + size * 0.02, cy + size * 0.04),
                (cx - size * 0.08, cy + size * 0.24),
            ],
            fill=(245, 241, 230),
            width=int(size * 0.025),
        )

    elif preset == "lotus":
        # Geometric lotus blossom with magenta petals, teal calyx, and golden core
        for angle_deg in (-50, -25, 0, 25, 50):
            rad = math.radians(angle_deg - 90)
            px = cx + math.cos(rad) * size * 0.16
            py = cy + math.sin(rad) * size * 0.16
            petal = [
                (cx, cy + size * 0.14),
                (cx + math.cos(rad - 0.35) * size * 0.14, cy + math.sin(rad - 0.35) * size * 0.14),
                (px * 1.05 - cx * 0.05, py * 1.05 - cy * 0.05),
                (cx + math.cos(rad + 0.35) * size * 0.14, cy + math.sin(rad + 0.35) * size * 0.14),
            ]
            color = (200, 43, 110) if abs(angle_deg) <= 25 else (232, 93, 42)
            draw.polygon(petal, fill=color)
        # Base pad
        draw.polygon(
            [
                (cx - size * 0.26, cy + size * 0.16),
                (cx + size * 0.26, cy + size * 0.16),
                (cx + size * 0.18, cy + size * 0.25),
                (cx - size * 0.18, cy + size * 0.25),
            ],
            fill=(0, 143, 122),
        )

    else:
        # Default: "crane" — Origami / Japanese Crane in flight with crimson sun & teal/cobalt wings
        # Crimson / Orange sun disc offset slightly upper-right
        draw.ellipse(
            [cx - size * 0.12, cy - size * 0.28, cx + size * 0.24, cy + size * 0.08],
            fill=(232, 93, 42),
        )
        # Main wing (Teal / Cyan-Green)
        wing_upper = [
            (cx - size * 0.04, cy + size * 0.02),
            (cx - size * 0.30, cy - size * 0.24),
            (cx + size * 0.06, cy - size * 0.10),
        ]
        draw.polygon(wing_upper, fill=(0, 143, 122))
        # Lower wing / body (Cobalt Blue)
        body = [
            (cx - size * 0.14, cy + size * 0.06),
            (cx + size * 0.06, cy - size * 0.10),
            (cx + size * 0.18, cy + size * 0.04),
            (cx - size * 0.02, cy + size * 0.24),
        ]
        draw.polygon(body, fill=(29, 91, 155))
        # Neck & head (Magenta / Deep Ink)
        neck = [
            (cx + size * 0.04, cy - size * 0.06),
            (cx + size * 0.24, cy - size * 0.24),
            (cx + size * 0.32, cy - size * 0.20),
            (cx + size * 0.14, cy + size * 0.02),
        ]
        draw.polygon(neck, fill=(200, 43, 110))
        # Tail spire
        tail = [
            (cx - size * 0.10, cy + size * 0.06),
            (cx - size * 0.28, cy + size * 0.26),
            (cx - size * 0.02, cy + size * 0.18),
        ]
        draw.polygon(tail, fill=(37, 40, 42))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
