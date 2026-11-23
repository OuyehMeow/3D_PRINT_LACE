/**
 * 3D_PRINT_LACE — Main Studio Application Controller
 * Coordinates Stages 1-5, Workbench2D, Viewer3D, and FastAPI StorageProvider endpoints.
 */

import { LaceAPI, hexToOklch, formatBytes } from './api.js';
import { Workbench2D } from './workbench2d.js';
import { Viewer3D } from './viewer3d.js';

class LaceStudioApp {
  constructor() {
    this.api = new LaceAPI('/api');
    this.session = null;
    this.sessionsList = [];
    this.activeStage = '1';
    this.splitStudioMode = false;
    this.activePreset = 'crane';

    // Cached Stage 4 & Stage 5 results
    this.lastSvgResult = null;
    this.last3mfExport = null;
    this.svgZoom = 1;

    // Debounce timers
    this._gridDebounceTimer = null;
    this._overrideDebounceTimer = null;

    // Canvas controllers
    this.workbench2d = null;
    this.viewer3d = null;
  }

  async init() {
    this._initCanvases();
    this._bindNavigation();
    this._bindHeaderActions();
    this._bindStage1Controls();
    this._bindStage2Controls();
    this._bindStage3Controls();
    this._bindStage4Controls();
    this._bindStage5Controls();

    this._renderSingleCellBezierDiagram();
    await this._bootstrapSession();
  }

  _initCanvases() {
    const wbCanvas = document.getElementById('workbench-2d-canvas');
    if (wbCanvas) {
      this.workbench2d = new Workbench2D(wbCanvas, {
        onCellHover: (info) => this._updateCellHoverBar(info),
        onManualOverrideChange: (overrides) => this._handleManualOverrideChange(overrides),
      });
    }

    const v3dCanvas = document.getElementById('viewer-3d-canvas');
    if (v3dCanvas) {
      this.viewer3d = new Viewer3D(v3dCanvas, {
        onThreadSelect: (th) => {
          this.setStatus(
            `已选中 3D 十字绣单元格 (${th.row}, ${th.col}) · 挤出机 Extruder #${th.extruder} (${th.hex})`
          );
        },
      });
    }
  }

  setStatus(message, isError = false) {
    const el = document.getElementById('studio-status-msg');
    if (!el) return;
    el.textContent = message;
    el.style.color = isError ? '#FF7B72' : '#4AC29A';
  }

  async _bootstrapSession() {
    try {
      this.setStatus('正在连接本地 DATA_ROOT 存储与会话账本...');
      const { sessions, storageInfo } = await this.api.listSessions();
      this.sessionsList = sessions || [];

      if (storageInfo?.data_root) {
        const badge = document.getElementById('data-root-badge');
        if (badge) {
          badge.title = `DATA_ROOT: ${storageInfo.data_root}`;
        }
      }

      if (this.sessionsList.length > 0) {
        const latestId =
          this.sessionsList[0].session_id || this.sessionsList[0].id;
        this._populateSessionDropdown(latestId);
        const sessionState = await this.api.getSession(latestId);
        await this.loadSessionState(sessionState);
        this.setStatus(`已加载工程会话: ${sessionState.name}`);
      } else {
        await this.loadPreset('crane');
      }
    } catch (err) {
      console.warn('Bootstrap session info:', err);
      // Try creating default 'crane' preset session
      try {
        await this.loadPreset('crane');
      } catch (innerErr) {
        this.setStatus(`初始化提示: ${innerErr.message}`, true);
      }
    }
  }

  _populateSessionDropdown(selectedId = '') {
    const sel = document.getElementById('session-select');
    if (!sel) return;
    sel.innerHTML = '';

    if (!this.sessionsList.length && this.session) {
      this.sessionsList = [
        { session_id: this.session.session_id, name: this.session.name },
      ];
    }

    for (const item of this.sessionsList) {
      const id = item.session_id || item.id;
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = `${item.name || id} (${String(id).slice(0, 8)})`;
      if (id === selectedId) opt.selected = true;
      sel.appendChild(opt);
    }
  }

  async _refreshSessionsList(activeId) {
    try {
      const { sessions } = await this.api.listSessions();
      this.sessionsList = sessions || [];
      this._populateSessionDropdown(activeId);
    } catch (_) {
      this._populateSessionDropdown(activeId);
    }
  }

  async loadPreset(presetName) {
    try {
      this.activePreset = presetName;
      document.querySelectorAll('.btn-preset').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.preset === presetName);
      });
      this.setStatus(`正在生成内置几何样本 [${presetName.toUpperCase()}] 并运行 5 阶段流水线...`);
      const sessionState = await this.api.uploadSession({ preset: presetName });
      await this._refreshSessionsList(sessionState.session_id);
      await this.loadSessionState(sessionState);
      this.setStatus(`已载入 [${sessionState.name}] · 5 阶段草稿就绪`);
    } catch (err) {
      this.setStatus(`载入示例失败: ${err.message}`, true);
    }
  }

  async uploadImageFile(file) {
    if (!file) return;
    try {
      this.setStatus(`正在上传图像 [${file.name}] 至本地 DATA_ROOT/inputs/...`);
      const sessionState = await this.api.uploadSession({
        file,
        name: file.name.replace(/\.[^.]+$/, ''),
      });
      await this._refreshSessionsList(sessionState.session_id);
      await this.loadSessionState(sessionState);
      this.setStatus(`图像 [${file.name}] 已解构为极简几何草稿与 60×60 网格`);
    } catch (err) {
      this.setStatus(`上传图像失败: ${err.message}`, true);
    }
  }

  // ==================== SESSION STATE SYNCHRONIZATION ====================

  getActiveStyleDraft() {
    if (!this.session?.style_drafts?.length) return null;
    return (
      this.session.style_drafts.find(
        (d) => d.draft_id === this.session.active_style_draft_id
      ) || this.session.style_drafts[0]
    );
  }

  getActiveColorDraft() {
    if (!this.session?.color_drafts?.length) return null;
    return (
      this.session.color_drafts.find(
        (p) => p.palette_id === this.session.active_palette_id
      ) || this.session.color_drafts[0]
    );
  }

  getActiveGridDraft() {
    if (!this.session?.grid_drafts?.length) return null;
    return (
      this.session.grid_drafts.find(
        (g) => g.grid_id === this.session.active_grid_id
      ) || this.session.grid_drafts[this.session.grid_drafts.length - 1]
    );
  }

  async loadSessionState(sessionState) {
    this.session = sessionState;
    if (!this.session) return;

    // Ensure active IDs default sensibly
    if (!this.session.active_style_draft_id && this.session.style_drafts?.length) {
      this.session.active_style_draft_id = this.session.style_drafts[0].draft_id;
    }
    if (!this.session.active_palette_id && this.session.color_drafts?.length) {
      this.session.active_palette_id = this.session.color_drafts[0].palette_id;
    }
    if (!this.session.active_grid_id && this.session.grid_drafts?.length) {
      this.session.active_grid_id =
        this.session.grid_drafts[this.session.grid_drafts.length - 1].grid_id;
    }

    this._renderTelemetryBar();
    this._renderSourceImage();
    this._renderStage1StyleDrafts();
    this._renderStage2ColorSystem();
    this._renderStage3Workbench();
    this._renderStage4PlotterPreview();
    this._renderStage5BambuStudio();
  }

  _renderTelemetryBar() {
    const style = this.getActiveStyleDraft();
    const palette = this.getActiveColorDraft();
    const grid = this.getActiveGridDraft();

    const setTxt = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    setTxt('tele-session-name', this.session?.name || '--');
    setTxt('tele-active-style', style?.name || '--');
    setTxt('tele-active-palette', palette?.name || '--');

    if (grid) {
      const cfg = grid.config || {};
      setTxt(
        'tele-grid-spec',
        `${cfg.cols || 60}×${cfg.rows || 60} @ ${cfg.cell_size_mm || 2.5}mm (${grid.canvas_width_mm || 150}×${grid.canvas_height_mm || 150}mm)`
      );
      setTxt(
        'tele-stitch-count',
        `${grid.filled_cell_count} 格 (${Number(grid.fill_percentage || 0).toFixed(1)}% 填色 / ${(100 - Number(grid.fill_percentage || 0)).toFixed(1)}% 留白)`
      );
    }
  }

  _renderSourceImage() {
    if (!this.session) return;
    const img = document.getElementById('source-image-preview');
    const badge = document.getElementById('source-filename-badge');
    if (badge) {
      badge.textContent = this.session.source_image_filename || 'input.png';
    }
    if (img) {
      img.src = this.api.getSourceImageUrl(this.session.session_id, Date.now());
    }
  }

  // ==================== STAGE 1: STYLE DRAFTS ====================

  _renderStage1StyleDrafts() {
    const container = document.getElementById('style-drafts-grid');
    if (!container || !this.session) return;
    container.innerHTML = '';

    const activePalette = this.getActiveColorDraft();
    const activeStyleId = this.session.active_style_draft_id;

    (this.session.style_drafts || []).forEach((draft, idx) => {
      const isActive = draft.draft_id === activeStyleId;
      const card = document.createElement('article');
      card.className = `style-draft-card ${isActive ? 'active' : ''}`;
      card.id = `style-card-${draft.draft_id}`;

      const wsPct = Math.round((draft.whitespace_ratio || 0.68) * 100);
      const regCount = draft.regions?.length || 0;
      const lineCount = draft.structural_lines?.length || 0;
      const svgMarkup = Workbench2D.renderStyleDraftSvg(draft, activePalette);

      card.innerHTML = `
        <div class="draft-card-top">
          <span class="draft-card-title">0${idx + 1}. ${draft.name}</span>
          <span class="draft-active-pill">${isActive ? '✓ 当前选用 ACTIVE' : '点击选用 SELECT'}</span>
        </div>
        <div class="draft-svg-stage">${svgMarkup}</div>
        <p class="draft-card-desc">${draft.description || ''}</p>
        <div class="draft-metrics-row">
          <span>负空间留白: <strong>${wsPct}%</strong></span>
          <span>几何色块: <strong>${regCount}</strong></span>
          <span>结构线: <strong>${lineCount}</strong></span>
        </div>
      `;

      card.addEventListener('click', () => this.selectStyleDraft(draft.draft_id));
      container.appendChild(card);
    });
  }

  async selectStyleDraft(draftId) {
    if (!this.session) return;
    try {
      this.session.active_style_draft_id = draftId;
      this._renderStage1StyleDrafts();
      this.setStatus('正在切换极简几何风格草稿并重新投射 60×60 像素化网格...');
      const updated = await this.api.generateStyle(this.session.session_id, {
        active_style_draft_id: draftId,
      });
      await this.loadSessionState(updated);
      this.setStatus(`已切换风格草稿: ${this.getActiveStyleDraft()?.name || draftId}`);
    } catch (err) {
      this.setStatus(`切换风格草稿失败: ${err.message}`, true);
    }
  }

  // ==================== STAGE 2: FUNCTIONAL COLOR SYSTEM ====================

  _renderStage2ColorSystem() {
    if (!this.session) return;
    const paletteList = document.getElementById('color-drafts-list');
    const amsEditor = document.getElementById('ams-slot-editor');
    const activePalette = this.getActiveColorDraft();
    const activeStyle = this.getActiveStyleDraft();
    const activeGrid = this.getActiveGridDraft();

    if (paletteList) {
      paletteList.innerHTML = '';
      for (const pal of this.session.color_drafts || []) {
        const isActive = pal.palette_id === activePalette?.palette_id;
        const item = document.createElement('div');
        item.className = `palette-option-card ${isActive ? 'active' : ''}`;

        const swatchesHtml = [
          `<div class="palette-swatch-segment" style="background:${pal.base_hex}" title="Base: ${pal.base_name}"></div>`,
          `<div class="palette-swatch-segment" style="background:${pal.grid_filament_hex}" title="Extruder 1 Grid: ${pal.grid_filament_hex}"></div>`,
          ...(pal.colors || []).map(
            (c) =>
              `<div class="palette-swatch-segment" style="background:${c.hex}" title="Slot ${c.slot}: ${c.name} (${c.hex})"></div>`
          ),
        ].join('');

        item.innerHTML = `
          <div class="palette-option-header">
            <span>${pal.name}</span>
            <span class="badge-tag">${isActive ? 'ACTIVE' : '选用'}</span>
          </div>
          <div class="palette-swatch-bar">${swatchesHtml}</div>
        `;

        item.addEventListener('click', () => this.selectColorPalette(pal.palette_id));
        paletteList.appendChild(item);
      }
    }

    if (amsEditor && activePalette) {
      amsEditor.innerHTML = '';

      // 0. Base Canvas Background Card
      const baseCard = document.createElement('div');
      baseCard.className = 'ams-slot-card';
      const baseOklch = hexToOklch(activePalette.base_hex);
      baseCard.innerHTML = `
        <div class="ams-slot-top">
          <span class="extruder-badge" style="background:#555B61">CANVAS BASE · 留白基底</span>
          <span class="role-badge">Negative Space</span>
        </div>
        <strong>${activePalette.base_name || 'Neutral Base'}</strong>
        <div class="ams-color-picker-row">
          <input type="color" class="ams-color-input" id="picker-base-hex" value="${activePalette.base_hex}" />
          <input type="text" class="ams-hex-input" id="input-base-hex" value="${activePalette.base_hex}" />
        </div>
        <div class="ams-oklch-readout" id="oklch-base">
          OKLCH: L=${baseOklch[0]} · C=${baseOklch[1]} · H=${baseOklch[2]}°
        </div>
      `;
      amsEditor.appendChild(baseCard);

      // 1. Extruder 1: Fixed Light Gray 3D Base Grid
      const gridCard = document.createElement('div');
      gridCard.className = 'ams-slot-card extruder-grid';
      const gridOklch = hexToOklch(activePalette.grid_filament_hex);
      gridCard.innerHTML = `
        <div class="ams-slot-top">
          <span class="extruder-badge" style="background:#2E343B">AMS SLOT 1 · EXTRUDER 1</span>
          <span class="role-badge">Z: 0.0–0.4mm Base Grid</span>
        </div>
        <strong>${activePalette.grid_filament_name || 'Light Gray Grid / 浅灰网格'}</strong>
        <div class="ams-color-picker-row">
          <input type="color" class="ams-color-input" id="picker-grid-hex" value="${activePalette.grid_filament_hex}" />
          <input type="text" class="ams-hex-input" id="input-grid-hex" value="${activePalette.grid_filament_hex}" />
        </div>
        <div class="ams-oklch-readout" id="oklch-grid">
          OKLCH: L=${gridOklch[0]} · C=${gridOklch[1]} · H=${gridOklch[2]}°
        </div>
      `;
      amsEditor.appendChild(gridCard);

      // Bind Base & Grid color inputs
      const bindPair = (pickerId, inputId, oklchId, onUpdate) => {
        const picker = document.getElementById(pickerId);
        const txt = document.getElementById(inputId);
        const okEl = document.getElementById(oklchId);
        const handler = (val) => {
          if (!/^#[0-9A-Fa-f]{6}$/.test(val)) return;
          picker.value = val;
          txt.value = val.toUpperCase();
          const ok = hexToOklch(val);
          if (okEl) okEl.textContent = `OKLCH: L=${ok[0]} · C=${ok[1]} · H=${ok[2]}°`;
          onUpdate(val.toUpperCase());
          this._refreshLiveColorProofs();
        };
        picker?.addEventListener('input', (e) => handler(e.target.value));
        txt?.addEventListener('change', (e) => {
          let v = e.target.value.trim();
          if (!v.startsWith('#')) v = `#${v}`;
          handler(v);
        });
      };

      bindPair('picker-base-hex', 'input-base-hex', 'oklch-base', (hex) => {
        activePalette.base_hex = hex;
      });
      bindPair('picker-grid-hex', 'input-grid-hex', 'oklch-grid', (hex) => {
        activePalette.grid_filament_hex = hex;
      });

      // 2..K: Functional Color Slots (Extruders 2..K)
      for (const color of activePalette.colors || []) {
        const slotCard = document.createElement('div');
        slotCard.className = 'ams-slot-card';
        const ok = hexToOklch(color.hex);
        const stitchCount =
          activeGrid?.color_counts?.[color.slot] ??
          activeGrid?.color_counts?.[String(color.slot)] ??
          0;

        slotCard.innerHTML = `
          <div class="ams-slot-top">
            <span class="extruder-badge" style="background:${color.hex}" id="badge-slot-${color.slot}">
              AMS SLOT ${color.bambu_extruder} · EXT #${color.bambu_extruder}
            </span>
            <span class="role-badge">${color.role}</span>
          </div>
          <strong>Slot ${color.slot}: ${color.name}</strong>
          <div class="ams-color-picker-row">
            <input type="color" class="ams-color-input" id="picker-slot-${color.slot}" value="${color.hex}" />
            <input type="text" class="ams-hex-input" id="input-slot-${color.slot}" value="${color.hex}" />
            <span class="badge-tag">${stitchCount} 格</span>
          </div>
          <div class="ams-oklch-readout" id="oklch-slot-${color.slot}">
            OKLCH: L=${ok[0]} · C=${ok[1]} · H=${ok[2]}°
          </div>
        `;
        amsEditor.appendChild(slotCard);

        bindPair(
          `picker-slot-${color.slot}`,
          `input-slot-${color.slot}`,
          `oklch-slot-${color.slot}`,
          (hex) => {
            color.hex = hex;
            color.oklch = hexToOklch(hex);
            const b = document.getElementById(`badge-slot-${color.slot}`);
            if (b) b.style.background = hex;
          }
        );
      }
    }

    this._refreshLiveColorProofs();
  }

  _refreshLiveColorProofs() {
    const activeStyle = this.getActiveStyleDraft();
    const activePalette = this.getActiveColorDraft();
    const activeGrid = this.getActiveGridDraft();

    const vectorBox = document.getElementById('stage2-vector-proof');
    if (vectorBox && activeStyle) {
      vectorBox.innerHTML = Workbench2D.renderStyleDraftSvg(activeStyle, activePalette);
    }

    const proofStyleName = document.getElementById('proof-style-name');
    if (proofStyleName && activeStyle) {
      proofStyleName.textContent = activeStyle.name;
    }

    const proofGridCanvas = document.getElementById('stage2-grid-proof-canvas');
    if (proofGridCanvas && activeGrid) {
      Workbench2D.renderMiniGrid(proofGridCanvas, activeGrid, activePalette);
    }

    const proofGridSummary = document.getElementById('proof-grid-summary');
    if (proofGridSummary && activeGrid) {
      proofGridSummary.textContent = `${activeGrid.filled_cell_count} Stitches (${activeGrid.fill_percentage}%)`;
    }

    // Keep Stage 1 cards, Stage 3 workbench, and Stage 5 3D preview synchronized with live color tweaks
    this._renderStage1StyleDrafts();
    if (this.workbench2d) {
      this.workbench2d.setData({ colorDraft: activePalette });
    }
    this._renderWorkbenchSlotSwatches();
    this._updateClient3DPreview();
  }

  async selectColorPalette(paletteId) {
    if (!this.session) return;
    this.session.active_palette_id = paletteId;
    const pal = this.getActiveColorDraft();
    this._renderStage2ColorSystem();
    this._renderTelemetryBar();

    if (pal) {
      try {
        const updated = await this.api.updateColor(this.session.session_id, {
          palette_id: pal.palette_id,
          base_hex: pal.base_hex,
          grid_filament_hex: pal.grid_filament_hex,
          colors: pal.colors.map((c) => ({ slot: c.slot, name: c.name, hex: c.hex })),
        });
        await this.loadSessionState(updated);
        this.setStatus(`已切换配色体系: ${pal.name}`);
      } catch (err) {
        this.setStatus(`切换色板提示: ${err.message}`, true);
      }
    }
  }

  // ==================== STAGE 3: 60x60 @ 2.5mm WORKBENCH & 50%-RULE ====================

  _renderStage3Workbench() {
    const grid = this.getActiveGridDraft();
    const style = this.getActiveStyleDraft();
    const palette = this.getActiveColorDraft();

    if (grid?.config) {
      const cfg = grid.config;
      this._setInputValue('grid-projection-mode', cfg.projection_mode || 'hybrid_line_preserving');
      this._setInputValue('grid-block-threshold', cfg.block_threshold ?? 0.5);
      this._setText('val-grid-block-threshold', `${Math.round((cfg.block_threshold ?? 0.5) * 100)}%`);

      this._setInputValue('grid-line-threshold', cfg.line_threshold ?? 0.2);
      this._setText('val-grid-line-threshold', `${Math.round((cfg.line_threshold ?? 0.2) * 100)}%`);

      this._setInputValue('grid-cols', cfg.cols ?? 60);
      this._setInputValue('grid-rows', cfg.rows ?? 60);
      this._setInputValue('grid-cell-size', cfg.cell_size_mm ?? 2.5);

      this._setInputValue('grid-offset-x', cfg.offset_x ?? 0);
      this._setText('val-grid-offset-x', Number(cfg.offset_x ?? 0).toFixed(2));

      this._setInputValue('grid-offset-y', cfg.offset_y ?? 0);
      this._setText('val-grid-offset-y', Number(cfg.offset_y ?? 0).toFixed(2));

      this._setInputValue('grid-scale', cfg.scale ?? 1.0);
      this._setText('val-grid-scale', `${Number(cfg.scale ?? 1.0).toFixed(2)}×`);

      const despeckleEl = document.getElementById('grid-despeckle');
      if (despeckleEl) despeckleEl.checked = Boolean(cfg.despeckle ?? true);

      this._updatePhysicalDimensionReadout();
    }

    this._renderWorkbenchSlotSwatches();

    if (this.workbench2d) {
      this.workbench2d.setData({
        gridDraft: grid,
        styleDraft: style,
        colorDraft: palette,
      });
    }

    this._renderPinnedGridsBar();
  }

  _renderWorkbenchSlotSwatches() {
    const container = document.getElementById('workbench-slot-picker');
    const palette = this.getActiveColorDraft();
    if (!container || !palette) return;

    container.innerHTML = '';
    for (const c of palette.colors || []) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `slot-swatch-btn ${this.workbench2d?.activeColorSlot === c.slot ? 'active' : ''}`;
      btn.style.background = c.hex;
      btn.title = `Slot ${c.slot}: ${c.name} (Extruder #${c.bambu_extruder})`;
      btn.addEventListener('click', () => {
        if (this.workbench2d) {
          this.workbench2d.setTool('brush', c.slot);
        }
        document.querySelectorAll('#workbench-tool-select .seg-btn').forEach((b) => {
          b.classList.toggle('active', b.dataset.tool === 'brush');
        });
        this._renderWorkbenchSlotSwatches();
      });
      container.appendChild(btn);
    }
  }

  _updateCellHoverBar(info) {
    const setTxt = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };

    if (!info) {
      setTxt('hover-cell-coord', '移动鼠标检视单元格 (Hover any cell)');
      setTxt('hover-cell-mm', '--');
      setTxt('hover-cell-cov', '--');
      setTxt('hover-cell-rule', '--');
      setTxt('hover-cell-slot', '--');
      return;
    }

    setTxt('hover-cell-coord', `Row ${info.row}, Col ${info.col}`);
    setTxt(
      'hover-cell-mm',
      `X: ${info.xMm}mm, Y: ${info.yMm}mm (${info.cellSize}×${info.cellSize}mm)`
    );
    setTxt('hover-cell-cov', `${info.coveragePct}%`);
    setTxt('hover-cell-rule', info.ruleStatus);

    if (info.slot > 0 && info.color) {
      setTxt(
        'hover-cell-slot',
        `Slot ${info.slot} · ${info.color.name} (${info.color.hex}) [Ext #${info.color.bambu_extruder}]`
      );
    } else {
      setTxt('hover-cell-slot', '0 (留白空置 · Base Grid Only)');
    }
  }

  _readGridConfigFromUI(preserveOverrides = true) {
    const currentGrid = this.getActiveGridDraft();
    const overrides = preserveOverrides
      ? { ...(currentGrid?.config?.manual_overrides || {}) }
      : {};

    return {
      cols: Number(document.getElementById('grid-cols')?.value || 60),
      rows: Number(document.getElementById('grid-rows')?.value || 60),
      cell_size_mm: Number(document.getElementById('grid-cell-size')?.value || 2.5),
      projection_mode:
        document.getElementById('grid-projection-mode')?.value ||
        'hybrid_line_preserving',
      block_threshold: Number(
        document.getElementById('grid-block-threshold')?.value || 0.5
      ),
      line_threshold: Number(
        document.getElementById('grid-line-threshold')?.value || 0.2
      ),
      offset_x: Number(document.getElementById('grid-offset-x')?.value || 0.0),
      offset_y: Number(document.getElementById('grid-offset-y')?.value || 0.0),
      scale: Number(document.getElementById('grid-scale')?.value || 1.0),
      despeckle: Boolean(document.getElementById('grid-despeckle')?.checked ?? true),
      manual_overrides: overrides,
    };
  }

  _updatePhysicalDimensionReadout() {
    const cols = Number(document.getElementById('grid-cols')?.value || 60);
    const rows = Number(document.getElementById('grid-rows')?.value || 60);
    const cellMm = Number(document.getElementById('grid-cell-size')?.value || 2.5);
    const wMm = (cols * cellMm).toFixed(1);
    const hMm = (rows * cellMm).toFixed(1);
    const total = (cols * rows).toLocaleString();
    const el = document.getElementById('physical-canvas-readout');
    if (el) {
      el.innerHTML = `物理尺寸: <strong>${wMm} × ${hMm} mm</strong> · 总格数: <strong>${total}</strong>`;
    }
  }

  async recalculateGrid({ style_draft_id = null, palette_id = null, configOverride = null } = {}) {
    if (!this.session) return;
    try {
      const cfg = configOverride || this._readGridConfigFromUI(true);
      this.setStatus(
        `正在投射 ${cfg.cols}×${cfg.rows} @ ${cfg.cell_size_mm}mm 网格 (${cfg.projection_mode}, τ_block=${Math.round(cfg.block_threshold * 100)}%)...`
      );
      const updated = await this.api.generateGrid(this.session.session_id, cfg, {
        style_draft_id: style_draft_id || this.session.active_style_draft_id,
        palette_id: palette_id || this.session.active_palette_id,
      });
      await this.loadSessionState(updated);
      this.setStatus(
        `网格已更新: ${this.getActiveGridDraft()?.filled_cell_count || 0} 个十字绣格 (${this.getActiveGridDraft()?.fill_percentage || 0}%)`
      );
    } catch (err) {
      this.setStatus(`网格计算失败: ${err.message}`, true);
    }
  }

  _handleManualOverrideChange(overrides) {
    this._renderTelemetryBar();
    this._updateClient3DPreview();
    this._renderStage4ClientSvg();

    if (this._overrideDebounceTimer) clearTimeout(this._overrideDebounceTimer);
    this._overrideDebounceTimer = setTimeout(async () => {
      if (!this.session) return;
      try {
        const cfg = this._readGridConfigFromUI(false);
        cfg.manual_overrides = overrides;
        const updated = await this.api.generateGrid(this.session.session_id, cfg);
        this.session = updated;
        this._renderTelemetryBar();
      } catch (err) {
        console.warn('Manual override sync warning:', err);
      }
    }, 450);
  }

  _renderPinnedGridsBar() {
    const bar = document.getElementById('pinned-grids-bar');
    const badge = document.getElementById('pinned-count-badge');
    if (!bar || !this.session) return;

    const pinnedIds = this.session.pinned_grid_ids || [];
    const allGrids = this.session.grid_drafts || [];

    // Show pinned grids, or if none pinned yet, show up to 3 most recent grid drafts for immediate comparison
    let displayGrids = pinnedIds
      .map((id) => allGrids.find((g) => g.grid_id === id))
      .filter(Boolean);

    if (displayGrids.length === 0 && allGrids.length > 0) {
      displayGrids = allGrids.slice(-3);
    }

    if (badge) {
      badge.textContent = `${pinnedIds.length} Pinned / ${displayGrids.length} Shown`;
    }

    bar.innerHTML = '';
    const palette = this.getActiveColorDraft();

    const modeLabels = {
      strict_50: 'Mode A · 严格50%',
      dominant_foreground: 'Mode B · 前景占优',
      hybrid_line_preserving: 'Mode C · 50%+结构线',
    };

    for (const g of displayGrids) {
      const isPinned = pinnedIds.includes(g.grid_id);
      const isActive = g.grid_id === this.session.active_grid_id;
      const card = document.createElement('div');
      card.className = `pinned-grid-card ${isActive ? 'active' : ''}`;

      const canvas = document.createElement('canvas');
      canvas.width = 120;
      canvas.height = 120;
      Workbench2D.renderMiniGrid(canvas, g, palette);

      const meta = document.createElement('div');
      meta.className = 'pinned-grid-meta';
      const modeName = modeLabels[g.config?.projection_mode] || g.config?.projection_mode;
      const bPct = Math.round((g.config?.block_threshold ?? 0.5) * 100);
      const lPct = Math.round((g.config?.line_threshold ?? 0.2) * 100);

      meta.innerHTML = `
        <div class="pinned-grid-title">
          ${modeName} ${isPinned ? '📌' : ''}
        </div>
        <div class="pinned-grid-stats">
          τ_block: ${bPct}% · τ_line: ${lPct}%
        </div>
        <div class="pinned-grid-stats">
          <strong>${g.filled_cell_count} 格</strong> (${g.fill_percentage}% 填色)
        </div>
      `;

      card.appendChild(canvas);
      card.appendChild(meta);

      card.addEventListener('click', () => {
        this.session.active_grid_id = g.grid_id;
        this._renderTelemetryBar();
        this._renderStage3Workbench();
        this._renderStage4PlotterPreview();
        this._renderStage5BambuStudio();
        this.setStatus(`已切换至网格快照: ${modeName} (${g.filled_cell_count} 格)`);
      });

      bar.appendChild(card);
    }
  }

  async compareThreeProjectionModes() {
    if (!this.session) return;
    try {
      this.setStatus('正在自动生成 Mode A (严格50%)、Mode B (前景占优)、Mode C (50%+结构线保留) 三组并排对比快照...');
      const baseCfg = this._readGridConfigFromUI(true);
      const modes = ['strict_50', 'dominant_foreground', 'hybrid_line_preserving'];

      for (const mode of modes) {
        const cfg = { ...baseCfg, projection_mode: mode };
        const sAfterGrid = await this.api.generateGrid(this.session.session_id, cfg);
        const newestGrid = sAfterGrid.grid_drafts[sAfterGrid.grid_drafts.length - 1];
        if (newestGrid && !(sAfterGrid.pinned_grid_ids || []).includes(newestGrid.grid_id)) {
          this.session = await this.api.togglePinGrid(
            this.session.session_id,
            newestGrid.grid_id
          );
        } else {
          this.session = sAfterGrid;
        }
      }

      await this.loadSessionState(this.session);
      this.setStatus('已完成 Mode A / Mode B / Mode C 三组 50% 法则对比快照固定！');
    } catch (err) {
      this.setStatus(`生成三模式对比失败: ${err.message}`, true);
    }
  }

  // ==================== STAGE 4: 0.4MM ORGANIC CROSS-STITCH SVG & PNG ====================

  _readSvgConfigFromUI() {
    return {
      stroke_width_mm: Number(document.getElementById('svg-stroke-width')?.value || 0.4),
      bulge_ratio: Number(document.getElementById('svg-bulge-ratio')?.value || 0.09),
      corner_inset_mm: Number(document.getElementById('svg-corner-inset')?.value || 0.0),
      include_grid_layer: Boolean(
        document.getElementById('svg-include-grid')?.checked ?? true
      ),
      optimize_plotter_path: Boolean(
        document.getElementById('svg-optimize-path')?.checked ?? true
      ),
    };
  }

  _renderSingleCellBezierDiagram() {
    const box = document.getElementById('single-cell-bezier-diagram');
    if (!box) return;
    const cfg = this._readSvgConfigFromUI();
    const palette = this.getActiveColorDraft();
    const color1 = palette?.colors?.[0]?.hex || '#008F7A';
    const color2 = palette?.colors?.[1]?.hex || '#D9532F';
    const gridHex = palette?.grid_filament_hex || '#C4C8CE';

    const S = 150;
    const pad = 20;
    const cellS = S - pad * 2;
    const bulge = cfg.bulge_ratio * cellS;
    const inset = (cfg.corner_inset_mm / 2.5) * cellS;
    const sw = Math.max(3, (cfg.stroke_width_mm / 2.5) * cellS);

    const x0 = pad + inset;
    const y0 = pad + inset;
    const x1 = pad + cellS - inset;
    const y1 = pad + cellS - inset;

    // Lower diagonal: (x0, y1) -> (x1, y0)
    const l1x = pad + cellS * 0.36 - bulge;
    const l1y = pad + cellS * 0.64 - bulge;
    const l2x = pad + cellS * 0.64 - bulge;
    const l2y = pad + cellS * 0.36 - bulge;

    // Upper arching diagonal: (x0, y0) -> (x1, y1)
    const u1x = pad + cellS * 0.36 + bulge;
    const u1y = pad + cellS * 0.36 - bulge;
    const u2x = pad + cellS * 0.64 + bulge;
    const u2y = pad + cellS * 0.64 - bulge;

    box.innerHTML = `
      <svg viewBox="0 0 ${S} ${S}" width="150" height="150">
        <rect x="${pad}" y="${pad}" width="${cellS}" height="${cellS}" fill="none" stroke="${gridHex}" stroke-width="4" rx="2"/>
        <line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="rgba(0,0,0,0.14)" stroke-width="1" stroke-dasharray="3,3"/>
        <line x1="${x0}" y1="${y1}" x2="${x1}" y2="${y0}" stroke="rgba(0,0,0,0.14)" stroke-width="1" stroke-dasharray="3,3"/>
        <path d="M ${x0} ${y1} C ${l1x} ${l1y}, ${l2x} ${l2y}, ${x1} ${y0}" fill="none" stroke="${color1}" stroke-width="${sw}" stroke-linecap="round"/>
        <path d="M ${x0} ${y0} C ${u1x} ${u1y}, ${u2x} ${u2y}, ${x1} ${y1}" fill="none" stroke="${color2}" stroke-width="${sw}" stroke-linecap="round"/>
        <circle cx="${S * 0.5 + bulge * 0.6}" cy="${S * 0.5 - bulge * 0.6}" r="3.5" fill="#FFD166" stroke="#1B1D20" stroke-width="1.2"/>
      </svg>
    `;
  }

  _renderStage4ClientSvg() {
    const grid = this.getActiveGridDraft();
    const palette = this.getActiveColorDraft();
    const svgBox = document.getElementById('svg-live-viewport');
    if (!grid || !svgBox) return;

    const cfg = this._readSvgConfigFromUI();
    const svgString = this._buildClientPlotterSvg(grid, palette, cfg);
    svgBox.innerHTML = svgString;
    this._applySvgViewportZoom();

    const summary = document.getElementById('svg-export-summary');
    if (summary) {
      summary.textContent = `物理画布: ${grid.canvas_width_mm} × ${grid.canvas_height_mm} mm · 线宽: ${cfg.stroke_width_mm.toFixed(2)} mm · 微隆曲率 δ_xy: ${cfg.bulge_ratio.toFixed(2)} · 十字绣总针数: ${grid.filled_cell_count * 2} 根曲线`;
    }
  }

  _buildClientPlotterSvg(gridDraft, colorDraft, svgConfig) {
    const rows = gridDraft.cells.length;
    const cols = gridDraft.cells[0]?.length || 60;
    const cellSize = gridDraft.config?.cell_size_mm || 2.5;
    const W = cols * cellSize;
    const H = rows * cellSize;

    const baseHex = colorDraft?.base_hex || '#F5F1E6';
    const gridHex = colorDraft?.grid_filament_hex || '#C4C8CE';
    const sw = svgConfig.stroke_width_mm || 0.4;
    const bulge = (svgConfig.bulge_ratio ?? 0.09) * cellSize;
    const inset = svgConfig.corner_inset_mm ?? 0.0;

    const colorMap = {};
    for (const c of colorDraft?.colors || []) {
      colorMap[c.slot] = c.hex;
    }

    const parts = [
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}mm" height="${H}mm">`,
      `<rect width="${W}" height="${H}" fill="${baseHex}"/>`,
    ];

    if (svgConfig.include_grid_layer) {
      const gLines = [];
      for (let c = 0; c <= cols; c++) {
        const x = (c * cellSize).toFixed(2);
        gLines.push(`M${x},0 L${x},${H}`);
      }
      for (let r = 0; r <= rows; r++) {
        const y = (r * cellSize).toFixed(2);
        gLines.push(`M0,${y} L${W},${y}`);
      }
      parts.push(
        `<path d="${gLines.join(' ')}" stroke="${gridHex}" stroke-width="${sw}" fill="none" opacity="0.85"/>`
      );
    }

    // Group paths by color slot for clean plotter layers
    const slotPaths = {};
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const slot = gridDraft.cells[r][c];
        if (!slot || slot <= 0) continue;
        if (!slotPaths[slot]) slotPaths[slot] = [];

        const x0 = c * cellSize;
        const y0 = r * cellSize;
        const s = cellSize;

        // Lower diagonal /
        const lx0 = (x0 + inset).toFixed(3);
        const ly0 = (y0 + s - inset).toFixed(3);
        const lc1x = (x0 + s * 0.36 - bulge).toFixed(3);
        const lc1y = (y0 + s * 0.64 - bulge).toFixed(3);
        const lc2x = (x0 + s * 0.64 - bulge).toFixed(3);
        const lc2y = (y0 + s * 0.36 - bulge).toFixed(3);
        const lx1 = (x0 + s - inset).toFixed(3);
        const ly1 = (y0 + inset).toFixed(3);

        // Upper arching diagonal \
        const ux0 = (x0 + inset).toFixed(3);
        const uy0 = (y0 + inset).toFixed(3);
        const uc1x = (x0 + s * 0.36 + bulge).toFixed(3);
        const uc1y = (y0 + s * 0.36 - bulge).toFixed(3);
        const uc2x = (x0 + s * 0.64 + bulge).toFixed(3);
        const uc2y = (y0 + s * 0.64 - bulge).toFixed(3);
        const ux1 = (x0 + s - inset).toFixed(3);
        const uy1 = (y0 + s - inset).toFixed(3);

        slotPaths[slot].push(
          `M${lx0},${ly0} C${lc1x},${lc1y} ${lc2x},${lc2y} ${lx1},${ly1}`,
          `M${ux0},${uy0} C${uc1x},${uc1y} ${uc2x},${uc2y} ${ux1},${uy1}`
        );
      }
    }

    for (const [slotStr, dList] of Object.entries(slotPaths)) {
      const hex = colorMap[Number(slotStr)] || '#1D5B9B';
      parts.push(
        `<path id="layer-slot-${slotStr}" d="${dList.join(' ')}" fill="none" stroke="${hex}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"/>`
      );
    }

    parts.push('</svg>');
    return parts.join('\n');
  }

  async _renderStage4PlotterPreview() {
    this._renderSingleCellBezierDiagram();
    this._renderStage4ClientSvg();

    // Also render PNG preview from client SVG immediately so PNG box is never empty
    this._updatePngFromSvgContent(
      document.getElementById('svg-live-viewport')?.innerHTML || ''
    );
  }

  _updatePngFromSvgContent(svgContent) {
    const img = document.getElementById('png-preview-img');
    if (!img || !svgContent) return;
    const blob = new Blob([svgContent], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    img.onload = () => URL.revokeObjectURL(url);
    img.src = url;
  }

  async generatePlotterSvgAndPng() {
    if (!this.session) return;
    try {
      const cfg = this._readSvgConfigFromUI();
      this.setStatus('正在生成 0.4mm 绘图仪矢量 SVG 与高清 PNG 草稿并写入 DATA_ROOT...');
      const res = await this.api.generateSvg(this.session.session_id, cfg);
      this.lastSvgResult = res;

      const svgBox = document.getElementById('svg-live-viewport');
      if (svgBox && res.svg_content) {
        svgBox.innerHTML = res.svg_content;
        this._applySvgViewportZoom();
      }

      const pngImg = document.getElementById('png-preview-img');
      if (pngImg) {
        if (res.png_base64) {
          pngImg.src = res.png_base64.startsWith('data:')
            ? res.png_base64
            : `data:image/png;base64,${res.png_base64}`;
        } else if (res.png_filename) {
          pngImg.src = this.api.getExportDownloadUrl(
            this.session.session_id,
            res.png_filename
          );
        }
      }

      this._setText('svg-filename-tag', res.svg_filename || 'cross_stitch.svg');
      this._setText('png-filename-tag', res.png_filename || 'cross_stitch.png');
      this.setStatus(
        `已生成 ${res.svg_filename} 与 ${res.png_filename} (${res.stitch_count} 个微隆十字格)`
      );
    } catch (err) {
      this.setStatus(`SVG/PNG 生成提示: ${err.message}`, true);
    }
  }

  _applySvgViewportZoom() {
    const svgEl = document.querySelector('#svg-live-viewport svg');
    const pngEl = document.getElementById('png-preview-img');
    if (svgEl) {
      svgEl.style.transform = `scale(${this.svgZoom})`;
    }
    if (pngEl) {
      pngEl.style.transform = `scale(${this.svgZoom})`;
    }
  }

  // ==================== STAGE 5: BAMBULAB X2D .3MF 3D LACE STUDIO ====================

  _readBambuConfigFromUI() {
    return {
      printer_model: 'Bambu Lab X2D',
      nozzle_diameter_mm: 0.4,
      line_width_mm: Number(document.getElementById('bambu-line-width')?.value || 0.4),
      grid_height_mm: Number(document.getElementById('bambu-grid-height')?.value || 0.4),
      stitch_thickness_mm: Number(
        document.getElementById('bambu-stitch-thickness')?.value || 0.2
      ),
      stitch_arch_z_mm: Number(document.getElementById('bambu-arch-z')?.value || 0.2),
      bulge_ratio: Number(document.getElementById('svg-bulge-ratio')?.value || 0.09),
      include_outer_frame: Boolean(
        document.getElementById('bambu-include-frame')?.checked ?? true
      ),
      frame_width_mm: Number(document.getElementById('bambu-frame-width')?.value || 0.8),
      curve_segments: 8,
    };
  }

  _renderStage5BambuStudio() {
    this._updateClient3DPreview();
    this._renderExtruderFilterChips();
    this._renderMeshManifest();
  }

  _updateClient3DPreview() {
    if (!this.viewer3d) return;
    const grid = this.getActiveGridDraft();
    const palette = this.getActiveColorDraft();
    const cfg = this._readBambuConfigFromUI();
    const localPayload = Viewer3D.buildPayloadFromGrid(grid, palette, cfg);
    if (localPayload) {
      this.viewer3d.setPayload(localPayload);
    }
  }

  _renderExtruderFilterChips() {
    const container = document.getElementById('viewer3d-extruder-filters');
    const palette = this.getActiveColorDraft();
    if (!container || !palette) return;

    container.innerHTML = '';
    for (const c of palette.colors || []) {
      const ext = c.bambu_extruder;
      const chip = document.createElement('button');
      chip.type = 'button';
      const isMuted = this.viewer3d?.hiddenExtruders.has(Number(ext));
      chip.className = `extruder-filter-chip ${isMuted ? 'muted' : ''}`;
      chip.innerHTML = `
        <span class="color-dot" style="background:${c.hex}"></span>
        Ext #${ext}
      `;
      chip.addEventListener('click', () => {
        this.viewer3d?.toggleExtruder(ext);
        this._renderExtruderFilterChips();
      });
      container.appendChild(chip);
    }
  }

  _renderMeshManifest(exportResult = null) {
    const box = document.getElementById('bambu-export-breakdown');
    const badge = document.getElementById('bambu-filesize-badge');
    if (!box) return;

    const palette = this.getActiveColorDraft();
    const grid = this.getActiveGridDraft();

    if (exportResult) {
      if (badge) badge.textContent = formatBytes(exportResult.file_size_bytes);
      const rowsHtml = (exportResult.extruder_map || [])
        .map(
          (item) => `
          <div class="manifest-row">
            <span>
              <span class="manifest-swatch" style="background:${item.hex || '#C4C8CE'}"></span>
              Ext #${item.extruder}: ${item.name || item.role || 'Part'}
            </span>
            <strong>${item.triangles ? `${item.triangles.toLocaleString()} tris` : item.hex}</strong>
          </div>
        `
        )
        .join('');

      box.innerHTML = `
        <div class="manifest-row">
          <span>3MF 工程文件:</span>
          <strong>${exportResult.filename}</strong>
        </div>
        <div class="manifest-row">
          <span>独立多色实体构件 (Parts):</span>
          <strong>${exportResult.part_count} Parts</strong>
        </div>
        <div class="manifest-row">
          <span>流形网格总三角面 (Triangles):</span>
          <strong>${Number(exportResult.total_triangles || 0).toLocaleString()}</strong>
        </div>
        ${rowsHtml}
      `;
      return;
    }

    // Default pre-export manifest estimation
    if (!palette || !grid) return;
    if (badge) badge.textContent = `${1 + (palette.colors?.length || 0)} Parts`;

    const items = [
      `<div class="manifest-row">
        <span><span class="manifest-swatch" style="background:${palette.grid_filament_hex}"></span>Part 1 (Ext #1): 浅灰 0.4mm 基础网格</span>
        <strong>Z: 0.0–0.4mm</strong>
      </div>`,
    ];

    for (const c of palette.colors || []) {
      const cnt =
        grid.color_counts?.[c.slot] ?? grid.color_counts?.[String(c.slot)] ?? 0;
      items.push(`
        <div class="manifest-row">
          <span><span class="manifest-swatch" style="background:${c.hex}"></span>Part ${c.bambu_extruder} (Ext #${c.bambu_extruder}): ${c.name}</span>
          <strong>${cnt} 格 (Z: 0.4–0.8mm)</strong>
        </div>
      `);
    }

    box.innerHTML = items.join('');
  }

  async syncBackend3mfPreview() {
    if (!this.session) return;
    try {
      const cfg = this._readBambuConfigFromUI();
      this.setStatus('正在从后端计算 BambuLab X2D 3D 丝线中心线与微拱几何预览...');
      const payload = await this.api.preview3mf(this.session.session_id, cfg);
      if (this.viewer3d && payload) {
        this.viewer3d.setPayload(payload);
      }
      this.setStatus(`3D 预览已同步 (${payload.threads?.length || 0} 个 3D 十字绣微拱单元)`);
    } catch (err) {
      this.setStatus(`3D 预览同步提示: ${err.message}`, true);
    }
  }

  async generateAndDownload3mf() {
    if (!this.session) return;
    try {
      const cfg = this._readBambuConfigFromUI();
      this.setStatus('正在构建 MakerWorld 标准多色流形网格 .3mf 工程文件 (含 Bambu 挤出机配置)...');
      const result = await this.api.export3mf(this.session.session_id, cfg);
      this.last3mfExport = result;

      if (result.preview_3d && this.viewer3d) {
        this.viewer3d.setPayload(result.preview_3d);
      }
      this._renderMeshManifest(result);

      if (result.filename) {
        const downloadUrl = this.api.getExportDownloadUrl(
          this.session.session_id,
          result.filename
        );
        this._triggerUrlDownload(downloadUrl, result.filename);
      }

      this.setStatus(
        `MakerWorld .3mf 导出完成: ${result.filename} (${formatBytes(result.file_size_bytes)}, ${result.total_triangles?.toLocaleString()} 三角面)`
      );
    } catch (err) {
      this.setStatus(`导出 .3mf 失败: ${err.message}`, true);
    }
  }

  _triggerUrlDownload(url, filename) {
    const a = document.createElement('a');
    a.href = url;
    a.download = filename || '';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // ==================== EVENT BINDINGS ====================

  _bindNavigation() {
    const tabs = document.querySelectorAll('#pipeline-tabs .step-tab');
    tabs.forEach((tab) => {
      tab.addEventListener('click', () => {
        const stage = tab.dataset.stage;
        this.switchStage(stage);
      });
    });

    const splitBtn = document.getElementById('btn-toggle-split-studio');
    splitBtn?.addEventListener('click', () => {
      this.splitStudioMode = !this.splitStudioMode;
      document.body.classList.toggle('split-studio-mode', this.splitStudioMode);
      splitBtn.classList.toggle('active', this.splitStudioMode);
      setTimeout(() => {
        this.workbench2d?.resize();
        this.viewer3d?.resize();
      }, 40);
    });

    document.getElementById('btn-stage1-to-stage3')?.addEventListener('click', () => {
      this.switchStage('3');
    });
  }

  switchStage(stageNum) {
    this.activeStage = String(stageNum);
    document.querySelectorAll('#pipeline-tabs .step-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.stage === this.activeStage);
    });
    document.querySelectorAll('.stage-panel').forEach((panel) => {
      panel.classList.toggle('active', panel.dataset.stagePanel === this.activeStage);
    });

    setTimeout(() => {
      if (this.activeStage === '3' || this.splitStudioMode) {
        this.workbench2d?.resize();
      }
      if (this.activeStage === '4' || this.splitStudioMode) {
        this._renderStage4PlotterPreview();
      }
      if (this.activeStage === '5' || this.splitStudioMode) {
        this.viewer3d?.resize();
      }
    }, 20);
  }

  _bindHeaderActions() {
    // Session selector
    const sessionSelect = document.getElementById('session-select');
    sessionSelect?.addEventListener('change', async (e) => {
      const id = e.target.value;
      if (!id) return;
      try {
        this.setStatus(`正在切换会话 ${id}...`);
        const s = await this.api.getSession(id);
        await this.loadSessionState(s);
        this.setStatus(`已切换至会话: ${s.name}`);
      } catch (err) {
        this.setStatus(`切换会话失败: ${err.message}`, true);
      }
    });

    // Quick sample preset buttons
    document.querySelectorAll('.btn-preset').forEach((btn) => {
      btn.addEventListener('click', () => {
        const preset = btn.dataset.preset;
        if (preset) this.loadPreset(preset);
      });
    });

    // File upload button & input
    const uploadBtn = document.getElementById('btn-upload-image');
    const fileInput = document.getElementById('file-upload-input');
    const dropzone = document.getElementById('source-dropzone');

    uploadBtn?.addEventListener('click', () => fileInput?.click());
    dropzone?.addEventListener('click', () => fileInput?.click());

    fileInput?.addEventListener('change', (e) => {
      const file = e.target.files?.[0];
      if (file) this.uploadImageFile(file);
    });

    dropzone?.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropzone.style.borderColor = '#D9532F';
    });
    dropzone?.addEventListener('dragleave', () => {
      dropzone.style.borderColor = '';
    });
    dropzone?.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.style.borderColor = '';
      const file = e.dataTransfer?.files?.[0];
      if (file) this.uploadImageFile(file);
    });
  }

  _bindStage1Controls() {
    const engineSelect = document.getElementById('style-engine-select');
    const apiKeyGroup = document.getElementById('style-api-key-group');
    engineSelect?.addEventListener('change', () => {
      apiKeyGroup?.classList.toggle('hidden', engineSelect.value !== 'ai');
    });

    const wsSlider = document.getElementById('style-whitespace-slider');
    wsSlider?.addEventListener('input', (e) => {
      this._setText('val-style-whitespace', `${Math.round(Number(e.target.value) * 100)}%`);
    });

    const fxSlider = document.getElementById('style-focal-x');
    fxSlider?.addEventListener('input', (e) => {
      this._setText('val-style-focal-x', Number(e.target.value).toFixed(2));
    });

    const fySlider = document.getElementById('style-focal-y');
    fySlider?.addEventListener('input', (e) => {
      this._setText('val-style-focal-y', Number(e.target.value).toFixed(2));
    });

    document.getElementById('btn-regenerate-style')?.addEventListener('click', async () => {
      if (!this.session) return;
      try {
        const engine = engineSelect?.value || 'algorithmic';
        const whitespace_target = Number(wsSlider?.value || 0.68);
        const focal_offset = [
          Number(fxSlider?.value || 0),
          Number(fySlider?.value || 0),
        ];
        const api_key = document.getElementById('style-api-key')?.value?.trim() || undefined;

        this.setStatus(
          `正在运行 [${engine === 'ai' ? 'AI Symbolic Vision' : '本地几何解构'}] 生成 3 组极简风格草稿 (目标留白 ${Math.round(whitespace_target * 100)}%)...`
        );
        const updated = await this.api.generateStyle(this.session.session_id, {
          engine,
          whitespace_target,
          focal_offset,
          api_key,
        });
        await this.loadSessionState(updated);
        this.setStatus('已重新生成 3 组极简几何风格草稿并同步网格');
      } catch (err) {
        this.setStatus(`生成风格草稿失败: ${err.message}`, true);
      }
    });
  }

  _bindStage2Controls() {
    let selectedBaseStyle = 'warm_white';
    document.querySelectorAll('#base-style-toggle .seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        selectedBaseStyle = btn.dataset.baseStyle || 'warm_white';
        document.querySelectorAll('#base-style-toggle .seg-btn').forEach((b) => {
          b.classList.toggle('active', b === btn);
        });
        // Also immediately preview the base hex swap
        const pal = this.getActiveColorDraft();
        if (pal) {
          pal.base_hex = selectedBaseStyle === 'warm_white' ? '#F5F1E6' : '#ECEEEB';
          pal.base_name =
            selectedBaseStyle === 'warm_white'
              ? 'Warm White / 暖白'
              : 'Gray White / 灰白';
          this._renderStage2ColorSystem();
        }
      });
    });

    const maxSlider = document.getElementById('color-max-slider');
    maxSlider?.addEventListener('input', (e) => {
      this._setText('val-color-max', `${e.target.value} 色 (+浅灰网格)`);
    });

    document.getElementById('btn-regenerate-color')?.addEventListener('click', async () => {
      if (!this.session) return;
      try {
        const max_colors = Number(maxSlider?.value || 4);
        this.setStatus(`正在重构 ${max_colors} 色高识别度功能性配色体系...`);
        const updated = await this.api.generateColor(this.session.session_id, {
          max_colors,
          base_style: selectedBaseStyle,
        });
        await this.loadSessionState(updated);
        this.setStatus('已重新生成 3 组功能性配色体系');
      } catch (err) {
        this.setStatus(`生成配色失败: ${err.message}`, true);
      }
    });

    document.getElementById('btn-apply-custom-color')?.addEventListener('click', async () => {
      if (!this.session) return;
      const pal = this.getActiveColorDraft();
      if (!pal) return;
      try {
        this.setStatus('正在保存自定义 BambuLab X2D 耗材槽色板至会话账本...');
        const updated = await this.api.updateColor(this.session.session_id, {
          palette_id: pal.palette_id,
          base_hex: pal.base_hex,
          grid_filament_hex: pal.grid_filament_hex,
          colors: pal.colors.map((c) => ({
            slot: c.slot,
            name: c.name,
            hex: c.hex,
          })),
        });
        await this.loadSessionState(updated);
        this.setStatus('自定义耗材色板已保存并同步至所有阶段');
      } catch (err) {
        this.setStatus(`保存自定义色板失败: ${err.message}`, true);
      }
    });
  }

  _bindStage3Controls() {
    const scheduleLiveGridUpdate = () => {
      if (this._gridDebounceTimer) clearTimeout(this._gridDebounceTimer);
      this._gridDebounceTimer = setTimeout(() => {
        this.recalculateGrid();
      }, 220);
    };

    document
      .getElementById('grid-projection-mode')
      ?.addEventListener('change', () => scheduleLiveGridUpdate());

    const blockSlider = document.getElementById('grid-block-threshold');
    blockSlider?.addEventListener('input', (e) => {
      this._setText(
        'val-grid-block-threshold',
        `${Math.round(Number(e.target.value) * 100)}%`
      );
      this.workbench2d?.render();
    });
    blockSlider?.addEventListener('change', () => scheduleLiveGridUpdate());

    const lineSlider = document.getElementById('grid-line-threshold');
    lineSlider?.addEventListener('input', (e) => {
      this._setText(
        'val-grid-line-threshold',
        `${Math.round(Number(e.target.value) * 100)}%`
      );
      this.workbench2d?.render();
    });
    lineSlider?.addEventListener('change', () => scheduleLiveGridUpdate());

    ['grid-cols', 'grid-rows', 'grid-cell-size'].forEach((id) => {
      document.getElementById(id)?.addEventListener('change', () => {
        this._updatePhysicalDimensionReadout();
        scheduleLiveGridUpdate();
      });
    });

    const bindNudge = (id, valId, fmt) => {
      const el = document.getElementById(id);
      el?.addEventListener('input', (e) => {
        const v = Number(e.target.value);
        this._setText(valId, fmt(v));
        if (this.workbench2d?.gridDraft?.config) {
          if (id === 'grid-offset-x') this.workbench2d.gridDraft.config.offset_x = v;
          if (id === 'grid-offset-y') this.workbench2d.gridDraft.config.offset_y = v;
          if (id === 'grid-scale') this.workbench2d.gridDraft.config.scale = v;
          this.workbench2d.render();
        }
      });
      el?.addEventListener('change', () => scheduleLiveGridUpdate());
    };

    bindNudge('grid-offset-x', 'val-grid-offset-x', (v) => v.toFixed(2));
    bindNudge('grid-offset-y', 'val-grid-offset-y', (v) => v.toFixed(2));
    bindNudge('grid-scale', 'val-grid-scale', (v) => `${v.toFixed(2)}×`);

    document
      .getElementById('grid-despeckle')
      ?.addEventListener('change', () => scheduleLiveGridUpdate());

    document
      .getElementById('btn-recalculate-grid')
      ?.addEventListener('click', () => this.recalculateGrid());

    // Pin Current Grid Snapshot
    document.getElementById('btn-pin-grid')?.addEventListener('click', async () => {
      const activeGrid = this.getActiveGridDraft();
      if (!this.session || !activeGrid) return;
      try {
        const updated = await this.api.togglePinGrid(
          this.session.session_id,
          activeGrid.grid_id
        );
        this.session = updated;
        this._renderPinnedGridsBar();
        this.setStatus(`已固定/切换网格对比快照 (${activeGrid.filled_cell_count} 格)`);
      } catch (err) {
        this.setStatus(`固定网格快照失败: ${err.message}`, true);
      }
    });

    // Quick Compare Mode A / B / C
    document
      .getElementById('btn-compare-modes')
      ?.addEventListener('click', () => this.compareThreeProjectionModes());

    // Workbench Toolbar: Render Mode
    document.querySelectorAll('#workbench-render-mode .seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const mode = btn.dataset.renderMode;
        document.querySelectorAll('#workbench-render-mode .seg-btn').forEach((b) => {
          b.classList.toggle('active', b === btn);
        });
        this.workbench2d?.setRenderMode(mode);
      });
    });

    // Ghost Vector Overlay toggle & opacity
    const ghostToggle = document.getElementById('toggle-ghost-vector');
    const ghostOpacity = document.getElementById('ghost-opacity-slider');
    ghostToggle?.addEventListener('change', () => {
      this.workbench2d?.setGhostVector(
        ghostToggle.checked,
        Number(ghostOpacity?.value || 0.38)
      );
    });
    ghostOpacity?.addEventListener('input', () => {
      this.workbench2d?.setGhostVector(
        Boolean(ghostToggle?.checked ?? true),
        Number(ghostOpacity.value)
      );
    });

    // Tool selector: Brush / Eraser / Pan
    document.querySelectorAll('#workbench-tool-select .seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const tool = btn.dataset.tool;
        document.querySelectorAll('#workbench-tool-select .seg-btn').forEach((b) => {
          b.classList.toggle('active', b === btn);
        });
        this.workbench2d?.setTool(tool);
      });
    });

    // Clear manual overrides
    document.getElementById('btn-clear-overrides')?.addEventListener('click', () => {
      const g = this.getActiveGridDraft();
      if (g?.config) {
        g.config.manual_overrides = {};
        this.recalculateGrid({ configOverride: { ...g.config, manual_overrides: {} } });
      }
    });

    // Zoom controls
    document
      .getElementById('btn-wb-zoom-in')
      ?.addEventListener('click', () => this.workbench2d?.zoomBy(1.25));
    document
      .getElementById('btn-wb-zoom-out')
      ?.addEventListener('click', () => this.workbench2d?.zoomBy(0.8));
    document
      .getElementById('btn-wb-zoom-reset')
      ?.addEventListener('click', () => this.workbench2d?.resetView());
  }

  _bindStage4Controls() {
    const swSlider = document.getElementById('svg-stroke-width');
    swSlider?.addEventListener('input', (e) => {
      this._setText('val-svg-stroke-width', `${Number(e.target.value).toFixed(2)} mm`);
      this._renderSingleCellBezierDiagram();
      this._renderStage4ClientSvg();
    });

    const bulgeSlider = document.getElementById('svg-bulge-ratio');
    bulgeSlider?.addEventListener('input', (e) => {
      const r = Number(e.target.value);
      const cellMm = this.getActiveGridDraft()?.config?.cell_size_mm || 2.5;
      this._setText(
        'val-svg-bulge-ratio',
        `${r.toFixed(2)} (${(r * cellMm).toFixed(2)}mm)`
      );
      if (this.workbench2d) this.workbench2d.setData({ bulgeRatio: r });
      this._renderSingleCellBezierDiagram();
      this._renderStage4ClientSvg();
      this._updateClient3DPreview();
    });

    const insetSlider = document.getElementById('svg-corner-inset');
    insetSlider?.addEventListener('input', (e) => {
      this._setText('val-svg-corner-inset', `${Number(e.target.value).toFixed(2)} mm`);
      this._renderSingleCellBezierDiagram();
      this._renderStage4ClientSvg();
    });

    document.getElementById('svg-include-grid')?.addEventListener('change', () => {
      this._renderStage4ClientSvg();
    });

    document
      .getElementById('btn-generate-svg')
      ?.addEventListener('click', () => this.generatePlotterSvgAndPng());

    document.querySelectorAll('#svg-zoom-control .seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        this.svgZoom = Number(btn.dataset.svgZoom || 1);
        document.querySelectorAll('#svg-zoom-control .seg-btn').forEach((b) => {
          b.classList.toggle('active', b === btn);
        });
        this._applySvgViewportZoom();
      });
    });

    // Download Plotter SVG
    document.getElementById('btn-download-svg')?.addEventListener('click', async () => {
      if (!this.lastSvgResult) {
        await this.generatePlotterSvgAndPng();
      }
      if (this.lastSvgResult?.svg_filename && this.session) {
        const url = this.api.getExportDownloadUrl(
          this.session.session_id,
          this.lastSvgResult.svg_filename
        );
        this._triggerUrlDownload(url, this.lastSvgResult.svg_filename);
      } else {
        const svgContent =
          document.getElementById('svg-live-viewport')?.innerHTML || '';
        const blob = new Blob([svgContent], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        this._triggerUrlDownload(url, 'lace_cross_stitch_0.4mm.svg');
      }
    });

    // Download PNG Draft
    document.getElementById('btn-download-png')?.addEventListener('click', async () => {
      if (!this.lastSvgResult) {
        await this.generatePlotterSvgAndPng();
      }
      if (this.lastSvgResult?.png_filename && this.session) {
        const url = this.api.getExportDownloadUrl(
          this.session.session_id,
          this.lastSvgResult.png_filename
        );
        this._triggerUrlDownload(url, this.lastSvgResult.png_filename);
      }
    });
  }

  _bindStage5Controls() {
    const bindSlider = (id, valId, unit = ' mm') => {
      const el = document.getElementById(id);
      el?.addEventListener('input', (e) => {
        this._setText(valId, `${Number(e.target.value).toFixed(2)}${unit}`);
        this._updateClient3DPreview();
      });
    };

    bindSlider('bambu-grid-height', 'val-bambu-grid-height');
    bindSlider('bambu-stitch-thickness', 'val-bambu-stitch-thickness');
    bindSlider('bambu-arch-z', 'val-bambu-arch-z');
    bindSlider('bambu-line-width', 'val-bambu-line-width');
    bindSlider('bambu-frame-width', 'val-bambu-frame-width');

    document
      .getElementById('bambu-include-frame')
      ?.addEventListener('change', () => this._updateClient3DPreview());

    document
      .getElementById('btn-preview-3mf')
      ?.addEventListener('click', () => this.syncBackend3mfPreview());

    document
      .getElementById('btn-export-3mf')
      ?.addEventListener('click', () => this.generateAndDownload3mf());

    // 3D Viewport Camera Presets
    document.querySelectorAll('#viewer3d-cam-presets .seg-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const preset = btn.dataset.camPreset;
        document.querySelectorAll('#viewer3d-cam-presets .seg-btn').forEach((b) => {
          b.classList.toggle('active', b === btn);
        });
        this.viewer3d?.setCameraPreset(preset);
      });
    });

    // Z-Arch Exaggeration Slider
    const zScaleSlider = document.getElementById('viewer3d-z-scale');
    zScaleSlider?.addEventListener('input', (e) => {
      const val = Number(e.target.value);
      this._setText('val-viewer3d-z-scale', `${val.toFixed(1)}×`);
      this.viewer3d?.setZExaggeration(val);
    });

    // 3D Layer visibility checkboxes
    const bindLayerToggle = (id, key) => {
      const el = document.getElementById(id);
      el?.addEventListener('change', () => {
        this.viewer3d?.setLayerVisibility({ [key]: el.checked });
      });
    };
    bindLayerToggle('v3d-toggle-grid', 'showBaseGrid');
    bindLayerToggle('v3d-toggle-lower', 'showLowerThreads');
    bindLayerToggle('v3d-toggle-upper', 'showUpperThreads');
  }

  _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  _setInputValue(id, val) {
    const el = document.getElementById(id);
    if (el) el.value = val;
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const app = new LaceStudioApp();
  window.laceStudio = app;
  app.init();
});
