# Architectural Invariants — 3D_PRINT_LACE

## Method in the Cloud, Data at Local

This repository enforces a strict separation between **reusable logic ("method")** and **user assets, intermediate drafts, and fabrication outputs ("data")**:

1. **Git Repository = Method Only**
   - Commit ONLY source code (`backend/`, `frontend/`), schemas, 3MF XML/JSON config templates, tests, and documentation (`AGENTS.md`, `README.md`, `config.example.yaml`).
   - NEVER commit user input images (`*.png`, `*.jpg`, `*.webp`), generated drafts, exported `.svg` / `.png` / `.3mf` / `.gcode` files, runtime session ledgers, or `.env` secrets.

2. **External Storage (`DATA_ROOT`) = Data Only**
   - All input images, session metadata, intermediate drafts (Style Drafts, Color Drafts, Pixelated Grid Drafts), and exported outputs (`.svg`, `.png`, `.3mf`) reside outside the repository under `DATA_ROOT`.
   - `DATA_ROOT` is resolved in strict precedence order:
     1. Explicit CLI flag (`--data-dir`)
     2. Environment variable (`LACE_DATA_DIR`)
     3. User-local config file (`~/.config/3d_print_lace/config.json`)
     4. Fallback external directory (`/home/antigravity/lace_data_root` or `~/lace_data_root`, never inside the Git repository).

3. **External `DATA_ROOT` Directory Layout**
   ```text
   $DATA_ROOT/
   ├── inputs/                  # Uploaded or synced source images
   ├── sessions/                # Per-session JSON ledgers & draft snapshots
   │   └── <session_id>/
   │       ├── session.json     # Active style drafts, color drafts, grid state
   │       ├── drafts/          # Rendered PNG/SVG previews
   │       └── exports/         # Final plotter .svg, .png, and BambuLab X2D .3mf files
   └── cache/                   # Ephemeral processing cache
   ```

4. **Storage Adapter Pattern**
   - All data I/O goes through `StorageProvider` (`backend/app/storage/provider.py`):
     - `LocalStorageProvider`: Primary local filesystem provider operating against `DATA_ROOT`.
     - `GDriveStorageProvider`: Cloud adapter connecting to Google Drive folder `3D_PRINT_LACE/` (`1laThzfsycHIn_dkOsp18LKkRyTfInlc-`) via the `gdrive` CLI.
