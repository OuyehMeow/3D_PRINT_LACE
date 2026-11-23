/**
 * 3D_PRINT_LACE — Stage 3 Interactive 2D Grid & 50%-Rule Workbench
 * Renders the 60x60 @ 2.5mm physical cell grid, organic cross-stitch threads,
 * 50%-rule coverage heatmap, StyleDraft vector ghost overlay, and brush/eraser tools.
 */

export class Workbench2D {
  constructor(canvas, { onCellHover = null, onManualOverrideChange = null } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onCellHover = onCellHover;
    this.onManualOverrideChange = onManualOverrideChange;

    // Active data models
    this.gridDraft = null;
    this.styleDraft = null;
    this.colorDraft = null;

    // Viewport state
    this.zoom = 1.0;
    this.panX = 0;
    this.panY = 0;
    this.rulerSize = 28;

    // Display options
    this.renderMode = 'stitch'; // 'stitch' | 'pixel' | 'heatmap'
    this.showGhostVector = true;
    this.ghostOpacity = 0.38;
    this.showGridLines = true;
    this.bulgeRatio = 0.09;

    // Interactive tool state
    this.activeTool = 'brush'; // 'inspect' | 'brush' | 'eraser'
    this.activeColorSlot = 1;
    this.hoveredCell = null; // { row, col }

    // Pointer interaction tracking
    this._isDragging = false;
    this._isPanning = false;
    this._spacePressed = false;
    this._lastPanClient = { x: 0, y: 0 };
    this._lastPaintedKey = null;
    this._overrideDirty = false;

    this._bindEvents();
    this._setupResizeObserver();
  }

  setData({ gridDraft, styleDraft, colorDraft, bulgeRatio } = {}) {
    if (gridDraft !== undefined) this.gridDraft = gridDraft;
    if (styleDraft !== undefined) this.styleDraft = styleDraft;
    if (colorDraft !== undefined) this.colorDraft = colorDraft;
    if (bulgeRatio !== undefined) this.bulgeRatio = Number(bulgeRatio);
    this.render();
  }

  setRenderMode(mode) {
    this.renderMode = mode;
    this.render();
  }

  setGhostVector(visible, opacity = null) {
    this.showGhostVector = Boolean(visible);
    if (opacity !== null) this.ghostOpacity = Number(opacity);
    this.render();
  }

  setTool(tool, colorSlot = null) {
    this.activeTool = tool;
    if (colorSlot !== null) this.activeColorSlot = Number(colorSlot);
    this._updateCursor();
  }

  setActiveColorSlot(slot) {
    this.activeColorSlot = Number(slot);
  }

  resetView() {
    this.zoom = 1.0;
    this.panX = 0;
    this.panY = 0;
    this.render();
  }

  zoomBy(factor) {
    const next = Math.max(0.65, Math.min(8.0, this.zoom * factor));
    this.zoom = next;
    this.render();
  }

  _updateCursor() {
    if (!this.canvas) return;
    if (this._isPanning || this._spacePressed || this.activeTool === 'inspect') {
      this.canvas.style.cursor = this._isPanning ? 'grabbing' : 'grab';
    } else if (this.activeTool === 'eraser') {
      this.canvas.style.cursor = 'cell';
    } else {
      this.canvas.style.cursor = 'crosshair';
    }
  }

  _setupResizeObserver() {
    if (typeof ResizeObserver !== 'undefined' && this.canvas.parentElement) {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(this.canvas.parentElement);
    }
    window.addEventListener('resize', () => this.resize());
    setTimeout(() => this.resize(), 10);
  }

  resize() {
    const parent = this.canvas.parentElement;
    if (!parent) return;
    const rect = parent.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(320, Math.floor(rect.width));
    const height = Math.max(320, Math.floor(rect.height));

    if (this.canvas.width !== width * dpr || this.canvas.height !== height * dpr) {
      this.canvas.width = width * dpr;
      this.canvas.height = height * dpr;
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;
    }
    this.render();
  }

  _getBoardMetrics() {
    const dpr = window.devicePixelRatio || 1;
    const W = this.canvas.width / dpr;
    const H = this.canvas.height / dpr;
    const cols = this.gridDraft?.config?.cols || 60;
    const rows = this.gridDraft?.config?.rows || 60;
    const cellSizeMm = this.gridDraft?.config?.cell_size_mm || 2.5;

    const availW = Math.max(120, W - this.rulerSize - 28);
    const availH = Math.max(120, H - this.rulerSize - 28);

    const aspect = cols / rows;
    let baseBoardW = availW;
    let baseBoardH = baseBoardW / aspect;
    if (baseBoardH > availH) {
      baseBoardH = availH;
      baseBoardW = baseBoardH * aspect;
    }

    const boardW = baseBoardW * this.zoom;
    const boardH = baseBoardH * this.zoom;

    const originX = this.rulerSize + (availW - boardW) * 0.5 + 10 + this.panX;
    const originY = this.rulerSize + (availH - boardH) * 0.5 + 10 + this.panY;

    const cellPxW = boardW / cols;
    const cellPxH = boardH / rows;

    return {
      W,
      H,
      cols,
      rows,
      cellSizeMm,
      boardW,
      boardH,
      originX,
      originY,
      cellPxW,
      cellPxH,
    };
  }

  _clientToCell(clientX, clientY) {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const m = this._getBoardMetrics();

    const col = Math.floor((x - m.originX) / m.cellPxW);
    const row = Math.floor((y - m.originY) / m.cellPxH);

    if (row >= 0 && row < m.rows && col >= 0 && col < m.cols) {
      return { row, col, x, y };
    }
    return null;
  }

  _bindEvents() {
    window.addEventListener('keydown', (e) => {
      if (e.code === 'Space' && document.activeElement?.tagName !== 'INPUT') {
        this._spacePressed = true;
        this._updateCursor();
      }
    });

    window.addEventListener('keyup', (e) => {
      if (e.code === 'Space') {
        this._spacePressed = false;
        this._updateCursor();
      }
    });

    this.canvas.addEventListener('contextmenu', (e) => e.preventDefault());

    this.canvas.addEventListener('mousedown', (e) => {
      if (e.button === 1 || e.button === 2 || this._spacePressed || this.activeTool === 'inspect') {
        this._isPanning = true;
        this._lastPanClient = { x: e.clientX, y: e.clientY };
        this._updateCursor();
        return;
      }

      if (e.button === 0 && (this.activeTool === 'brush' || this.activeTool === 'eraser')) {
        this._isDragging = true;
        this._lastPaintedKey = null;
        this._overrideDirty = false;
        const hit = this._clientToCell(e.clientX, e.clientY);
        if (hit) {
          this._applyToolAtCell(hit.row, hit.col);
        }
      }
    });

    window.addEventListener('mousemove', (e) => {
      if (this._isPanning) {
        const dx = e.clientX - this._lastPanClient.x;
        const dy = e.clientY - this._lastPanClient.y;
        this._lastPanClient = { x: e.clientX, y: e.clientY };
        this.panX += dx;
        this.panY += dy;
        this.render();
        return;
      }

      const rect = this.canvas.getBoundingClientRect();
      const insideCanvas =
        e.clientX >= rect.left &&
        e.clientX <= rect.right &&
        e.clientY >= rect.top &&
        e.clientY <= rect.bottom;

      if (!insideCanvas) {
        if (this.hoveredCell) {
          this.hoveredCell = null;
          this.render();
          if (this.onCellHover) this.onCellHover(null);
        }
        return;
      }

      const hit = this._clientToCell(e.clientX, e.clientY);
      if (
        (hit?.row ?? -1) !== (this.hoveredCell?.row ?? -1) ||
        (hit?.col ?? -1) !== (this.hoveredCell?.col ?? -1)
      ) {
        this.hoveredCell = hit ? { row: hit.row, col: hit.col } : null;
        this._emitHoverTelemetry(this.hoveredCell);
        this.render();
      }

      if (this._isDragging && hit) {
        this._applyToolAtCell(hit.row, hit.col);
      }
    });

    window.addEventListener('mouseup', () => {
      if (this._isPanning) {
        this._isPanning = false;
        this._updateCursor();
      }
      if (this._isDragging) {
        this._isDragging = false;
        this._lastPaintedKey = null;
        if (this._overrideDirty && this.onManualOverrideChange && this.gridDraft) {
          this._overrideDirty = false;
          this.onManualOverrideChange(
            { ...(this.gridDraft.config.manual_overrides || {}) },
            this.gridDraft
          );
        }
      }
    });

    this.canvas.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        const rect = this.canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        const mBefore = this._getBoardMetrics();
        const relX = (mouseX - mBefore.originX) / mBefore.boardW;
        const relY = (mouseY - mBefore.originY) / mBefore.boardH;

        const factor = e.deltaY < 0 ? 1.12 : 0.89;
        this.zoom = Math.max(0.65, Math.min(8.0, this.zoom * factor));

        const mAfter = this._getBoardMetrics();
        if (relX >= 0 && relX <= 1 && relY >= 0 && relY <= 1) {
          const targetOriginX = mouseX - relX * mAfter.boardW;
          const targetOriginY = mouseY - relY * mAfter.boardH;
          this.panX += targetOriginX - mAfter.originX;
          this.panY += targetOriginY - mAfter.originY;
        }
        this.render();
      },
      { passive: false }
    );
  }

  _applyToolAtCell(row, col) {
    if (!this.gridDraft || !this.gridDraft.cells) return;
    const key = `${row},${col}`;
    if (this._lastPaintedKey === key) return;
    this._lastPaintedKey = key;

    const targetSlot = this.activeTool === 'eraser' ? 0 : this.activeColorSlot;
    const currentSlot = this.gridDraft.cells[row]?.[col] ?? 0;

    if (!this.gridDraft.config.manual_overrides) {
      this.gridDraft.config.manual_overrides = {};
    }

    this.gridDraft.config.manual_overrides[key] = targetSlot;
    if (currentSlot !== targetSlot) {
      this.gridDraft.cells[row][col] = targetSlot;
      this._recomputeGridMetrics();
    }
    this._overrideDirty = true;
    this._emitHoverTelemetry({ row, col });
    this.render();
  }

  _recomputeGridMetrics() {
    if (!this.gridDraft || !this.gridDraft.cells) return;
    const rows = this.gridDraft.cells.length;
    const cols = this.gridDraft.cells[0]?.length || 0;
    let filled = 0;
    const counts = {};
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const s = this.gridDraft.cells[r][c] || 0;
        if (s > 0) {
          filled++;
          counts[s] = (counts[s] || 0) + 1;
        }
      }
    }
    this.gridDraft.filled_cell_count = filled;
    this.gridDraft.fill_percentage =
      rows * cols > 0 ? Number(((filled / (rows * cols)) * 100).toFixed(2)) : 0;
    this.gridDraft.color_counts = counts;
  }

  _emitHoverTelemetry(cell) {
    if (!this.onCellHover) return;
    if (!cell || !this.gridDraft) {
      this.onCellHover(null);
      return;
    }

    const { row, col } = cell;
    const cellSize = this.gridDraft.config?.cell_size_mm || 2.5;
    const xMm = Number((col * cellSize).toFixed(1));
    const yMm = Number((row * cellSize).toFixed(1));
    const slot = this.gridDraft.cells?.[row]?.[col] ?? 0;
    const rawCov = this.gridDraft.cell_coverages?.[row]?.[col];
    const coveragePct =
      rawCov !== undefined && rawCov !== null
        ? Number((rawCov * 100).toFixed(1))
        : slot > 0
          ? 100.0
          : 0.0;

    const key = `${row},${col}`;
    const isManual =
      this.gridDraft.config?.manual_overrides &&
      Object.prototype.hasOwnProperty.call(this.gridDraft.config.manual_overrides, key);

    let colorObj = null;
    if (slot > 0 && this.colorDraft?.colors) {
      colorObj = this.colorDraft.colors.find((c) => c.slot === slot) || null;
    }

    const blockThreshPct = Number(((this.gridDraft.config?.block_threshold ?? 0.5) * 100).toFixed(0));
    const lineThreshPct = Number(((this.gridDraft.config?.line_threshold ?? 0.2) * 100).toFixed(0));

    let ruleStatus = 'Empty (< Threshold)';
    if (isManual) {
      ruleStatus = slot > 0 ? 'Manual Brush Override' : 'Manual Eraser Clear';
    } else if (slot > 0) {
      if (coveragePct >= blockThreshPct) {
        ruleStatus = `Block >= ${blockThreshPct}% (100% Fill)`;
      } else {
        ruleStatus = `Line Preserved >= ${lineThreshPct}%`;
      }
    }

    this.onCellHover({
      row,
      col,
      xMm,
      yMm,
      cellSize,
      slot,
      coveragePct,
      isManual,
      ruleStatus,
      color: colorObj,
    });
  }

  _getColorMap() {
    const map = {};
    if (this.colorDraft?.colors) {
      for (const c of this.colorDraft.colors) {
        map[c.slot] = c.hex;
      }
    }
    return map;
  }

  render() {
    if (!this.canvas || !this.ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const ctx = this.ctx;
    ctx.save();
    ctx.scale(dpr, dpr);

    const m = this._getBoardMetrics();
    // Studio background surrounding the physical board
    ctx.fillStyle = '#E8E4DA';
    ctx.fillRect(0, 0, m.W, m.H);

    if (!this.gridDraft || !this.gridDraft.cells) {
      ctx.fillStyle = '#5A6065';
      ctx.font = '500 13px "Inter", system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Loading 60×60 Physical Lace Workbench...', m.W * 0.5, m.H * 0.5);
      ctx.restore();
      return;
    }

    const baseHex = this.colorDraft?.base_hex || '#F5F1E6';
    const gridHex = this.colorDraft?.grid_filament_hex || '#C4C8CE';
    const colorMap = this._getColorMap();

    // Subtle elevation shadow under the physical 150x150mm canvas
    ctx.save();
    ctx.shadowColor = 'rgba(24, 24, 22, 0.14)';
    ctx.shadowBlur = 18;
    ctx.shadowOffsetY = 5;
    ctx.fillStyle = baseHex;
    ctx.fillRect(m.originX, m.originY, m.boardW, m.boardH);
    ctx.restore();

    // Clip to board interior for grid + cells + vector overlay
    ctx.save();
    ctx.beginPath();
    ctx.rect(m.originX, m.originY, m.boardW, m.boardH);
    ctx.clip();

    // 1. Render cells according to active renderMode
    if (this.renderMode === 'heatmap') {
      this._renderHeatmapCells(ctx, m, colorMap);
    } else if (this.renderMode === 'pixel') {
      this._renderPixelCells(ctx, m, colorMap);
    } else {
      // In 'stitch' mode, draw the physical 0.4mm Light Gray Grid first (Z: 0.0 - 0.4mm),
      // then weave the colored cross-stitch threads on top (Z: 0.4 - 0.8mm)!
      if (this.showGridLines) {
        this._renderPhysicalGridLattice(ctx, m, gridHex, true);
      }
      this._renderCrossStitchCells(ctx, m, colorMap);
    }

    // 2. If in 'pixel' or 'heatmap' mode, overlay grid hairlines on top of blocks
    if (this.renderMode !== 'stitch' && this.showGridLines) {
      this._renderPhysicalGridLattice(ctx, m, gridHex, false);
    }

    // 3. Optional Vector Style Draft Ghost Overlay
    if (this.showGhostVector && this.styleDraft) {
      this._renderVectorGhostOverlay(ctx, m, colorMap);
    }

    // 4. Highlight manual override cells with a tiny corner tick
    this._renderManualOverrideMarkers(ctx, m);

    // 5. Hovered cell cursor frame
    if (this.hoveredCell) {
      const { row, col } = this.hoveredCell;
      const cx = m.originX + col * m.cellPxW;
      const cy = m.originY + row * m.cellPxH;
      ctx.strokeStyle = '#D9532F';
      ctx.lineWidth = 2;
      ctx.strokeRect(cx + 0.5, cy + 0.5, m.cellPxW - 1, m.cellPxH - 1);
    }

    ctx.restore();

    // Outer physical frame border (0.8mm BambuLab X2D outer lace frame)
    ctx.strokeStyle = gridHex;
    ctx.lineWidth = Math.max(2, (0.8 / m.cellSizeMm) * m.cellPxW);
    ctx.strokeRect(m.originX, m.originY, m.boardW, m.boardH);

    // 6. Top and Left physical millimeter & cell index rulers
    this._renderRulers(ctx, m);

    ctx.restore();
  }

  _renderPhysicalGridLattice(ctx, m, gridHex, isPhysical3DBeam) {
    ctx.save();
    ctx.strokeStyle = gridHex;
    // Physical 0.4mm line width relative to 2.5mm cell size = 16% of cell width
    const physicalRatio = 0.4 / (m.cellSizeMm || 2.5);
    ctx.lineWidth = isPhysical3DBeam
      ? Math.max(0.8, m.cellPxW * physicalRatio)
      : Math.max(0.6, Math.min(1.5, m.cellPxW * 0.08));
    ctx.globalAlpha = isPhysical3DBeam ? 0.92 : 0.65;

    ctx.beginPath();
    for (let c = 0; c <= m.cols; c++) {
      const x = m.originX + c * m.cellPxW;
      ctx.moveTo(x, m.originY);
      ctx.lineTo(x, m.originY + m.boardH);
    }
    for (let r = 0; r <= m.rows; r++) {
      const y = m.originY + r * m.cellPxH;
      ctx.moveTo(m.originX, y);
      ctx.lineTo(m.originX + m.boardW, y);
    }
    ctx.stroke();

    // Major 10-cell (25mm) guide lines
    ctx.strokeStyle = 'rgba(60, 66, 72, 0.22)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let c = 0; c <= m.cols; c += 10) {
      const x = m.originX + c * m.cellPxW;
      ctx.moveTo(x, m.originY);
      ctx.lineTo(x, m.originY + m.boardH);
    }
    for (let r = 0; r <= m.rows; r += 10) {
      const y = m.originY + r * m.cellPxH;
      ctx.moveTo(m.originX, y);
      ctx.lineTo(m.originX + m.boardW, y);
    }
    ctx.stroke();
    ctx.restore();
  }

  _renderPixelCells(ctx, m, colorMap) {
    const cells = this.gridDraft.cells;
    for (let r = 0; r < m.rows; r++) {
      const rowArr = cells[r];
      if (!rowArr) continue;
      for (let c = 0; c < m.cols; c++) {
        const slot = rowArr[c];
        if (!slot || slot <= 0) continue;
        ctx.fillStyle = colorMap[slot] || '#1D5B9B';
        const x = m.originX + c * m.cellPxW;
        const y = m.originY + r * m.cellPxH;
        ctx.fillRect(x, y, Math.ceil(m.cellPxW), Math.ceil(m.cellPxH));
      }
    }
  }

  _renderHeatmapCells(ctx, m, colorMap) {
    const cells = this.gridDraft.cells;
    const covs = this.gridDraft.cell_coverages;
    const blockT = this.gridDraft.config?.block_threshold ?? 0.5;
    const lineT = this.gridDraft.config?.line_threshold ?? 0.2;

    for (let r = 0; r < m.rows; r++) {
      for (let c = 0; c < m.cols; c++) {
        const slot = cells[r]?.[c] || 0;
        const cov = covs?.[r]?.[c] ?? (slot > 0 ? 1.0 : 0.0);
        if (cov <= 0.01 && slot === 0) continue;

        const x = m.originX + c * m.cellPxW;
        const y = m.originY + r * m.cellPxH;

        if (slot > 0 && cov >= blockT) {
          // Passed 50% area rule -> solid color with high opacity
          ctx.fillStyle = colorMap[slot] || '#008F7A';
          ctx.globalAlpha = 0.85;
          ctx.fillRect(x, y, m.cellPxW, m.cellPxH);
        } else if (slot > 0 && cov >= lineT) {
          // Rescued by structural line rule -> hatched / distinct badge color
          ctx.fillStyle = colorMap[slot] || '#D9532F';
          ctx.globalAlpha = 0.72;
          ctx.fillRect(x, y, m.cellPxW, m.cellPxH);
          ctx.strokeStyle = '#FFFFFF';
          ctx.lineWidth = 1;
          ctx.strokeRect(x + 1.5, y + 1.5, Math.max(1, m.cellPxW - 3), Math.max(1, m.cellPxH - 3));
        } else if (cov > 0.02) {
          // Below 50% threshold -> sub-threshold ghost amber tint
          ctx.fillStyle = 'rgba(217, 83, 47, 0.22)';
          ctx.globalAlpha = Math.min(0.65, cov + 0.15);
          ctx.fillRect(x, y, m.cellPxW, m.cellPxH);
        }

        ctx.globalAlpha = 1.0;
        if (m.cellPxW >= 24 && cov > 0.03) {
          ctx.fillStyle = slot > 0 ? '#FFFFFF' : '#5A6065';
          ctx.font = `600 ${Math.min(11, Math.floor(m.cellPxW * 0.34))}px ui-monospace, monospace`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(`${Math.round(cov * 100)}%`, x + m.cellPxW * 0.5, y + m.cellPxH * 0.5);
        }
      }
    }
    ctx.globalAlpha = 1.0;
  }

  /**
   * Renders every filled cell as two 0.4mm organic center-bulge Cubic Bezier cross-stitch diagonals:
   *   "用两条0.4mm粗细曲线，画交叉十字模拟十字绣的效果，两条曲线在交叉处中间微微隆起避免呆板"
   */
  _renderCrossStitchCells(ctx, m, colorMap) {
    const cells = this.gridDraft.cells;
    const strokeRatio = 0.4 / (m.cellSizeMm || 2.5); // 0.4mm / 2.5mm = 0.16 of cell size
    const threadWidth = Math.max(1.35, m.cellPxW * strokeRatio * 1.15);
    const bulge = this.bulgeRatio ?? 0.09;

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    for (let r = 0; r < m.rows; r++) {
      const rowArr = cells[r];
      if (!rowArr) continue;
      for (let c = 0; c < m.cols; c++) {
        const slot = rowArr[c];
        if (!slot || slot <= 0) continue;

        const hex = colorMap[slot] || '#1D5B9B';
        const x0 = m.originX + c * m.cellPxW;
        const y0 = m.originY + r * m.cellPxH;
        const sW = m.cellPxW;
        const sH = m.cellPxH;

        // Subtle soft background tint in cell so silhouette reads clearly at any zoom
        ctx.fillStyle = hex;
        ctx.globalAlpha = 0.12;
        ctx.fillRect(x0, y0, sW, sH);
        ctx.globalAlpha = 1.0;

        const insetX = sW * 0.07;
        const insetY = sH * 0.07;
        const deltaX = sW * bulge;
        const deltaY = sH * bulge;

        // Lower Diagonal 2: Bottom-Left -> Top-Right (bends slightly toward Top-Left at center)
        ctx.strokeStyle = hex;
        ctx.lineWidth = threadWidth;
        ctx.beginPath();
        ctx.moveTo(x0 + insetX, y0 + sH - insetY);
        ctx.bezierCurveTo(
          x0 + sW * 0.36 - deltaX,
          y0 + sH * 0.64 - deltaY,
          x0 + sW * 0.64 - deltaX,
          y0 + sH * 0.36 - deltaY,
          x0 + sW - insetX,
          y0 + insetY
        );
        ctx.stroke();

        // Crossing Z-arch shadow at center intersection
        if (sW >= 7) {
          ctx.strokeStyle = 'rgba(20, 22, 25, 0.24)';
          ctx.lineWidth = threadWidth + 1.1;
          ctx.beginPath();
          ctx.moveTo(x0 + sW * 0.32, y0 + sH * 0.32);
          ctx.lineTo(x0 + sW * 0.68, y0 + sH * 0.68);
          ctx.stroke();
        }

        // Upper Arching Diagonal 1: Top-Left -> Bottom-Right (bends slightly toward Top-Right at center)
        ctx.strokeStyle = hex;
        ctx.lineWidth = threadWidth;
        ctx.beginPath();
        ctx.moveTo(x0 + insetX, y0 + insetY);
        ctx.bezierCurveTo(
          x0 + sW * 0.36 + deltaX,
          y0 + sH * 0.36 - deltaY,
          x0 + sW * 0.64 + deltaX,
          y0 + sH * 0.64 - deltaY,
          x0 + sW - insetX,
          y0 + sH - insetY
        );
        ctx.stroke();

        // Subtle center arch highlight dot simulating the 3D Z-bump
        if (sW >= 10) {
          ctx.fillStyle = 'rgba(255, 255, 255, 0.42)';
          ctx.beginPath();
          ctx.arc(
            x0 + sW * 0.5 + deltaX * 0.55,
            y0 + sH * 0.5 - deltaY * 0.55,
            Math.max(0.9, threadWidth * 0.32),
            0,
            Math.PI * 2
          );
          ctx.fill();
        }
      }
    }
    ctx.restore();
  }

  _renderVectorGhostOverlay(ctx, m, colorMap) {
    if (!this.styleDraft) return;
    const cfg = this.gridDraft?.config || {};
    const scale = Number(cfg.scale ?? 1.0);
    const offX = Number(cfg.offset_x ?? 0.0) / m.cols;
    const offY = Number(cfg.offset_y ?? 0.0) / m.rows;

    const toCanvasPt = (pt) => {
      const nx = (pt[0] - 0.5) * scale + 0.5 + offX;
      const ny = (pt[1] - 0.5) * scale + 0.5 + offY;
      return [m.originX + nx * m.boardW, m.originY + ny * m.boardH];
    };

    ctx.save();
    ctx.globalAlpha = this.ghostOpacity;

    // Draw vector regions
    for (const reg of this.styleDraft.regions || []) {
      if (!reg.exterior || reg.exterior.length < 3) continue;
      const hex =
        reg.color_slot === 0
          ? this.colorDraft?.base_hex || '#F5F1E6'
          : colorMap[reg.color_slot] || '#1D5B9B';

      ctx.beginPath();
      reg.exterior.forEach((pt, idx) => {
        const [px, py] = toCanvasPt(pt);
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.closePath();

      for (const hole of reg.holes || []) {
        if (hole.length < 3) continue;
        hole.forEach((pt, idx) => {
          const [px, py] = toCanvasPt(pt);
          if (idx === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();
      }

      ctx.fillStyle = hex;
      ctx.fill('evenodd');
      ctx.strokeStyle = '#1C1E21';
      ctx.lineWidth = 1.25;
      ctx.setLineDash([4, 3]);
      ctx.stroke();
    }

    // Draw structural lines
    ctx.setLineDash([]);
    for (const line of this.styleDraft.structural_lines || []) {
      if (!line.points || line.points.length < 2) continue;
      const hex = colorMap[line.color_slot] || '#D9532F';
      ctx.beginPath();
      line.points.forEach((pt, idx) => {
        const [px, py] = toCanvasPt(pt);
        if (idx === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      });
      ctx.strokeStyle = hex;
      ctx.lineWidth = Math.max(2, (line.weight || 0.016) * m.boardW * scale);
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.stroke();
    }

    ctx.restore();
  }

  _renderManualOverrideMarkers(ctx, m) {
    const overrides = this.gridDraft?.config?.manual_overrides;
    if (!overrides) return;
    const keys = Object.keys(overrides);
    if (!keys.length || m.cellPxW < 6) return;

    ctx.save();
    ctx.fillStyle = '#D9532F';
    for (const key of keys) {
      const [rStr, cStr] = key.split(',');
      const r = Number(rStr);
      const c = Number(cStr);
      if (r >= 0 && r < m.rows && c >= 0 && c < m.cols) {
        const x = m.originX + c * m.cellPxW;
        const y = m.originY + r * m.cellPxH;
        ctx.beginPath();
        ctx.arc(x + m.cellPxW - 2.5, y + 2.5, Math.min(2.2, m.cellPxW * 0.16), 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.restore();
  }

  _renderRulers(ctx, m) {
    const rSize = this.rulerSize;
    ctx.save();

    // Top ruler bar
    ctx.fillStyle = '#F6F3EC';
    ctx.fillRect(rSize, 0, m.W - rSize, rSize);
    // Left ruler bar
    ctx.fillRect(0, rSize, rSize, m.H - rSize);
    // Top-left corner badge (mm)
    ctx.fillStyle = '#EAE5D9';
    ctx.fillRect(0, 0, rSize, rSize);

    ctx.strokeStyle = '#D2CCC0';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, rSize + 0.5);
    ctx.lineTo(m.W, rSize + 0.5);
    ctx.moveTo(rSize + 0.5, 0);
    ctx.lineTo(rSize + 0.5, m.H);
    ctx.stroke();

    ctx.fillStyle = '#5A6065';
    ctx.font = '600 9px ui-monospace, monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('mm', rSize * 0.5, rSize * 0.5);

    const step = m.cellPxW >= 18 ? 5 : 10;

    // Horizontal ticks (Cols & X mm)
    for (let c = 0; c <= m.cols; c += step) {
      const x = m.originX + c * m.cellPxW;
      if (x < rSize || x > m.W) continue;
      ctx.strokeStyle = '#9A9488';
      ctx.beginPath();
      ctx.moveTo(x, rSize - 6);
      ctx.lineTo(x, rSize);
      ctx.stroke();

      const mmVal = Math.round(c * m.cellSizeMm);
      ctx.fillStyle = '#3B4045';
      ctx.fillText(`${mmVal}`, x, rSize - 14);
    }

    // Vertical ticks (Rows & Y mm)
    for (let r = 0; r <= m.rows; r += step) {
      const y = m.originY + r * m.cellPxH;
      if (y < rSize || y > m.H) continue;
      ctx.strokeStyle = '#9A9488';
      ctx.beginPath();
      ctx.moveTo(rSize - 6, y);
      ctx.lineTo(rSize, y);
      ctx.stroke();

      const mmVal = Math.round(r * m.cellSizeMm);
      ctx.save();
      ctx.translate(rSize - 14, y);
      ctx.rotate(-Math.PI / 2);
      ctx.fillStyle = '#3B4045';
      ctx.fillText(`${mmVal}`, 0, 0);
      ctx.restore();
    }

    ctx.restore();
  }

  /**
   * Static helper: Render a compact thumbnail canvas for the Multi-Draft Side-by-Side Comparison Bar.
   */
  static renderMiniGrid(canvas, gridDraft, colorDraft) {
    if (!canvas || !gridDraft || !gridDraft.cells) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width;
    const H = canvas.height;
    const rows = gridDraft.cells.length;
    const cols = gridDraft.cells[0]?.length || 60;

    const baseHex = colorDraft?.base_hex || '#F5F1E6';
    const gridHex = colorDraft?.grid_filament_hex || '#C4C8CE';
    const colorMap = {};
    for (const c of colorDraft?.colors || []) {
      colorMap[c.slot] = c.hex;
    }

    ctx.fillStyle = baseHex;
    ctx.fillRect(0, 0, W, H);

    const cellW = W / cols;
    const cellH = H / rows;

    // Subtle grid lattice
    ctx.strokeStyle = gridHex;
    ctx.lineWidth = 0.35;
    ctx.globalAlpha = 0.45;
    ctx.beginPath();
    for (let c = 0; c <= cols; c += 2) {
      ctx.moveTo(c * cellW, 0);
      ctx.lineTo(c * cellW, H);
    }
    for (let r = 0; r <= rows; r += 2) {
      ctx.moveTo(0, r * cellH);
      ctx.lineTo(W, r * cellH);
    }
    ctx.stroke();
    ctx.globalAlpha = 1.0;

    // Filled cross-stitch cells
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const slot = gridDraft.cells[r][c];
        if (!slot || slot <= 0) continue;
        ctx.fillStyle = colorMap[slot] || '#1D5B9B';
        ctx.fillRect(c * cellW, r * cellH, Math.ceil(cellW), Math.ceil(cellH));
      }
    }

    ctx.strokeStyle = gridHex;
    ctx.lineWidth = 2;
    ctx.strokeRect(0, 0, W, H);
  }

  /**
   * Static helper: Synthesize an SVG preview string for a StyleDraft using active ColorDraft colors.
   */
  static renderStyleDraftSvg(styleDraft, colorDraft) {
    if (!styleDraft) return '';
    const baseHex = colorDraft?.base_hex || '#F5F1E6';
    const colorMap = {};
    for (const c of colorDraft?.colors || []) {
      colorMap[c.slot] = c.hex;
    }

    // If backend provided preview_svg and no custom palette override is needed, we can still
    // render dynamically from regions + structural_lines so Stage 2 palette edits reflect live!
    if (
      (!styleDraft.regions || styleDraft.regions.length === 0) &&
      (!styleDraft.structural_lines || styleDraft.structural_lines.length === 0) &&
      styleDraft.preview_svg
    ) {
      return styleDraft.preview_svg;
    }

    const size = 300;
    const parts = [
      `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="100%" height="100%">`,
      `<rect width="${size}" height="${size}" fill="${baseHex}" rx="4" />`,
    ];

    // Subtle rule-of-thirds composition hairlines
    parts.push(
      `<g stroke="rgba(28,30,33,0.06)" stroke-width="0.75" stroke-dasharray="3,3">`,
      `<line x1="${size / 3}" y1="0" x2="${size / 3}" y2="${size}"/>`,
      `<line x1="${(size * 2) / 3}" y1="0" x2="${(size * 2) / 3}" y2="${size}"/>`,
      `<line x1="0" y1="${size / 3}" x2="${size}" y2="${size / 3}"/>`,
      `<line x1="0" y1="${(size * 2) / 3}" x2="${size}" y2="${(size * 2) / 3}"/>`,
      `</g>`
    );

    for (const reg of styleDraft.regions || []) {
      if (!reg.exterior || reg.exterior.length < 3) continue;
      const fill =
        reg.color_slot === 0 ? baseHex : colorMap[reg.color_slot] || '#1D5B9B';
      let d = reg.exterior
        .map((pt, i) => `${i === 0 ? 'M' : 'L'}${(pt[0] * size).toFixed(1)},${(pt[1] * size).toFixed(1)}`)
        .join(' ') + ' Z';
      for (const hole of reg.holes || []) {
        if (hole.length >= 3) {
          d +=
            ' ' +
            hole
              .map((pt, i) => `${i === 0 ? 'M' : 'L'}${(pt[0] * size).toFixed(1)},${(pt[1] * size).toFixed(1)}`)
              .join(' ') +
            ' Z';
        }
      }
      parts.push(`<path d="${d}" fill="${fill}" fill-rule="evenodd" />`);
    }

    for (const line of styleDraft.structural_lines || []) {
      if (!line.points || line.points.length < 2) continue;
      const stroke = colorMap[line.color_slot] || '#25282A';
      const sw = Math.max(1.8, (line.weight || 0.016) * size);
      const pts = line.points
        .map((pt) => `${(pt[0] * size).toFixed(1)},${(pt[1] * size).toFixed(1)}`)
        .join(' ');
      parts.push(
        `<polyline fill="none" stroke="${stroke}" stroke-width="${sw.toFixed(1)}" stroke-linecap="round" stroke-linejoin="round" points="${pts}" />`
      );
    }

    parts.push('</svg>');
    return parts.join('');
  }
}
