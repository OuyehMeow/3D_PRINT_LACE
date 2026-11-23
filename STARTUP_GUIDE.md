# 3D_PRINT_LACE — Startup & Home NAS Deployment Guide

This guide walks you through running **3D_PRINT_LACE** on your computer today and migrating it seamlessly to a **Home NAS** (Synology DSM, TrueNAS Scale, Unraid, QNAP, or Ubuntu/Mac home server) with zero rework.

---

## 1. Prerequisites

- **For Docker / Home NAS (Recommended)**: Docker & Docker Compose v2 (`docker compose`).
- **For Local Python**: Python 3.12+ and `pip`.
- **For Stage 1 AI Style Drafts (Recommended)**: A Google Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey) to unlock `gemini-2.5-flash-image` / `gemini-3-pro-image-preview` minimalist geometric image-to-image abstraction.

---

## 2. Option A: Docker / Home NAS Deployment (Recommended)

This is the cleanest way to run the studio on your laptop **and** later move it to a Home NAS. All user images, visual style cues, session drafts, and exported `.svg` / `.png` / `.3mf` files stay safely on your host/NAS disk outside the container.

### Step 1 — Clone & Configure Environment
```bash
git clone https://github.com/OuyehMeow/3D_PRINT_LACE.git
cd 3D_PRINT_LACE
cp .env.example .env
```

Edit `.env` and set your **data directory** and **Gemini API key**:
```env
PORT=8000

# Point to an external folder on your computer or NAS:
#   Mac/Linux: /Users/yourname/lace_data_root
#   Synology:  /volume1/docker/3d_print_lace/data
#   TrueNAS:   /mnt/tank/apps/3d_print_lace/data
LACE_HOST_DATA_DIR=/volume1/docker/3d_print_lace/data

# Enable Gemini native image generation for Stage 1 Style Drafts
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_IMAGE_MODEL=gemini-2.5-flash-image
```

### Step 2 — Launch the Container
```bash
docker compose up -d --build
```

### Step 3 — Verify Health & Open the Studio
```bash
curl http://localhost:8000/api/health
```
You should see `"status": "ok"` and `"gemini_configured": true`.  
Now open **`http://localhost:8000`** (or **`http://<YOUR-NAS-IP>:8000`** from any device on your home LAN).

### Migrating Later to a Home NAS
Because `LACE_HOST_DATA_DIR` holds **100% of your data** (`inputs/`, `style_cues/`, `sessions/`, `exports/`), moving to a NAS takes 2 steps:
1. Copy your local data folder onto your NAS shared folder (e.g. `/volume1/docker/3d_print_lace/data`).
2. Clone the repo (or import `docker-compose.yml` in Synology Container Manager / Portainer / TrueNAS Apps), set `LACE_HOST_DATA_DIR` + `GEMINI_API_KEY`, and click **Start**. All previous sessions and style cues appear immediately.

---

## 3. Option B: Local Python Quickstart (No Docker)

```bash
git clone https://github.com/OuyehMeow/3D_PRINT_LACE.git
cd 3D_PRINT_LACE

python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

export LACE_DATA_DIR="$HOME/lace_data_root"
export GEMINI_API_KEY="your_gemini_api_key_here"
export GEMINI_IMAGE_MODEL="gemini-2.5-flash-image"

uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
Open **`http://127.0.0.1:8000`** in your browser.

---

## 4. Uploading Visual Style Cues for Gemini Stage 1

To guide Gemini's image generation toward your exact architectural / Swiss modernist geometric aesthetic, drop 1–3 reference images into `$LACE_DATA_DIR/style_cues/` (or upload via API):

```bash
curl -F "file=@/path/to/style_cue_1.png" http://localhost:8000/api/style-cues
curl -F "file=@/path/to/style_cue_2.png" http://localhost:8000/api/style-cues
```
Whenever you click **Regenerate Style Drafts** with Engine = **AI Symbolic Vision (`gemini-2.5-flash-image`)**, the backend automatically sends your source photo + up to 3 stored visual cues to Gemini to generate 3 crisp flat-color Style Drafts and vectorizes them for Stage 3.

---

## 5. Step-by-Step Creative Workflow

1. **Upload Image**: Drag-and-drop any photo (or click a built-in sample preset).
2. **Stage 1 — 风格草稿 (Style Drafts)**: Choose **AI Symbolic Vision (Gemini Image Generation)**, set your target whitespace (`65%–75%`), and pick the cleanest candidate from the 3 generated drafts.
3. **Stage 2 — 配色草稿 (Functional Color System)**: Review the reconstructed OKLCH non-neon functional palette. `Extruder 1` is reserved for the **`0.4mm` Light Gray base grid (`#C4C8CE`)**; `Extruders 2..K` map 1:1 to your artwork stitch colors.
4. **Stage 3 — 画布处理与50%法则测试 (`60×60 @ 2.5mm` Grid Workbench)**:
   - Compare **Mode A (`Strict 50%`)**, **Mode B (`Dominant Foreground`)**, and **Mode C (`Hybrid Block 50% + Structural Line Preservation`)** with live threshold sliders.
   - Pin up to 3 configurations side-by-side and use the **Manual Cell Brush** to polish individual cells.
5. **Stage 4 — Plotter `.svg` & `.png`**: Adjust `0.4mm` stroke width and center bulge $\delta_{\text{xy}}$ (`两条曲线在交叉处中间微微隆起`), inspect the 40× single-cell Bezier diagram, and export `.svg` / `.png`.
6. **Stage 5 — BambuLab X2D `.3mf` (MakerWorld Ready)**: Inspect the 3D woven lace mesh (Light Gray grid $Z \in [0, 0.4\text{ mm}]$ + arched colored cross-stitches $Z \in [0.4, 0.8\text{ mm}]$) and click **Generate & Download MakerWorld `.3mf` Project File** to open directly in Bambu Studio.
