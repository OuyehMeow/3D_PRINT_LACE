# 3D_PRINT_LACE — Cross-Stitch & 3D-Printed Lace Artwork Studio

A local creative web application that transforms an input image into a minimalist geometric cross-stitch artwork, exporting:
1. **Pen-Plotter `.svg` (`0.4mm` lines with organic center-bulge curves)** & **`.png` drafts**
2. **MakerWorld & Bambu Studio–ready Multi-Color `.3mf` Project Files** for **BambuLab X2D** (`0.4mm` light-gray base cell grid + `0.4mm` colored 3D center-arched cross-stitches)

## 5-Stage Pipeline

1. **Stage 1 — 风格草稿 (Style Draft Engine)**: Dual-mode minimalist geometric deconstruction (built-in offline algorithmic engine + pluggable AI vision adapter) producing 3 candidate vector abstractions with modular facets, structural lines, negative space, and $\ge 55\%$ breathing whitespace.
2. **Stage 2 — 配色草稿 (Functional Color System)**: Perceptual OKLab/OKLCH extraction & anti-neon functional color reconstruction paired with Warm-White / Gray-White base and 1:1 Bambu extruder slot mapping (`Extruder 1 = #C4C8CE Light Gray Grid`, `Extruders 2..K = Stitch Colors`).
3. **Stage 3 — 画布处理与像素化草稿 (`60 × 60` @ `2.5mm` Grid & 50%-Rule Workbench)**: Exact vector-to-cell projection (`150 × 150 mm` default canvas) with interactive A/B/C testing of `Mode A: Strict 50%`, `Mode B: Dominant Foreground`, and `Mode C: Hybrid Block + Structural Line Preservation`, live sliders, sub-cell nudging, multi-draft pinning, and manual cell brush.
4. **Stage 4 — SVG 与 PNG 草稿生成**: `0.4mm` diagonal cross-stitches rendered with center-bulge Cubic Bézier curves (`两条曲线在交叉处中间微微隆起避免呆板`), Inkscape color layers with Nearest-Neighbor TSP plotter path sorting, and high-res shaded PNG drafts.
5. **Stage 5 — BambuLab X2D `.3mf` Project File (MakerWorld Ready)**: Watertight 2-manifold 3D tubular mesh generation (Light Gray base grid $Z \in [0, 0.40\text{ mm}]$ + colored cross-stitches with 3D $Z$-arch bridge at the intersection $Z \in [0.60, 0.80\text{ mm}]$), packaged with BambuLab X2D `0.4mm` Arachne settings and plate thumbnail.

## Architecture: Method in the Cloud, Data at Local

Per [AGENTS.md](AGENTS.md), this repository stores **only reusable code and templates**. All uploaded images, session ledgers, and exported `.svg` / `.png` / `.3mf` files are stored outside the repository under `DATA_ROOT` (configured via `LACE_DATA_DIR`, `~/.config/3d_print_lace/config.json`, or defaulting to `~/lace_data_root`).

## Local Quickstart

```bash
# 1. Clone the repository
git clone https://github.com/OuyehMeow/3D_PRINT_LACE.git
cd 3D_PRINT_LACE

# 2. Install Python dependencies (Python 3.12+)
pip install -r backend/requirements.txt

# 3. Start the local web studio (stores all data in ~/lace_data_root by default)
uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Then open `http://127.0.0.1:8000` in your browser.

## Running Tests

```bash
pytest backend/tests -v
```
