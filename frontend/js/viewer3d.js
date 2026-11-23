/**
 * 3D_PRINT_LACE — Stage 5 Self-Contained 3D Orbit & Zoom BambuLab X2D Lace Previewer
 * Zero external dependencies. Renders:
 *  - 256 x 256 mm BambuLab X2D PEI Build Plate
 *  - Part 1: 0.40mm Light Gray Base Grid Lattice (Z: 0.00 - 0.40 mm, Extruder 1)
 *  - Parts 2..K: 0.40mm Organic Cross-Stitch Threads with 3D Center Z-Arch
 *    ("两条曲线在交叉处中间微微隆起避免呆板", Z: 0.40 - 0.80 mm, Extruders 2..K)
 *  - Interactive Orbit, Pan, Zoom, Preset Views, Extruder Filters, and Macro Cell Z-Arch Inspector.
 */

export class Viewer3D {
  constructor(canvas, { onThreadSelect = null } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.onThreadSelect = onThreadSelect;

    this.payload = null;
    this.zExaggeration = 2.5;
    this.showPlate = true;
    this.showBaseGrid = true;
    this.showLowerThreads = true;
    this.showUpperThreads = true;
    this.hiddenExtruders = new Set();

    // Camera state (spherical orbit around target in mm space)
    this.camera = {
      azimuth: -0.68, // radians (~ -39 deg)
      elevation: 0.64, // radians (~ 37 deg)
      distance: 215, // mm
      target: [0, 0, 0.4],
      fovScale: 520,
    };

    this.inspectedThread = null;
    this._normalizedScene = null;
    this._projectedThreads = [];

    // Pointer interaction state
    this._dragMode = null; // 'orbit' | 'pan'
    this._lastClient = { x: 0, y: 0 };
    this._pointerDownClient = { x: 0, y: 0 };
    this._animFrame = null;

    this._bindEvents();
    this._setupResizeObserver();
  }

  setPayload(payload) {
    this.payload = payload;
    this._normalizePayload();
    if (this.payload?.threads?.length && !this.inspectedThread) {
      this.inspectedThread = this._findCenterMostThread();
    }
    this.render();
  }

  setZExaggeration(val) {
    this.zExaggeration = Math.max(1.0, Math.min(6.0, Number(val) || 1.0));
    this.render();
  }

  setLayerVisibility({ showPlate, showBaseGrid, showLowerThreads, showUpperThreads } = {}) {
    if (showPlate !== undefined) this.showPlate = Boolean(showPlate);
    if (showBaseGrid !== undefined) this.showBaseGrid = Boolean(showBaseGrid);
    if (showLowerThreads !== undefined) this.showLowerThreads = Boolean(showLowerThreads);
    if (showUpperThreads !== undefined) this.showUpperThreads = Boolean(showUpperThreads);
    this.render();
  }

  toggleExtruder(extruderId) {
    const id = Number(extruderId);
    if (this.hiddenExtruders.has(id)) {
      this.hiddenExtruders.delete(id);
    } else {
      this.hiddenExtruders.add(id);
    }
    this.render();
  }

  setCameraPreset(preset) {
    if (preset === 'top') {
      this._animateCamera({
        azimuth: 0.0,
        elevation: 1.53, // ~88 deg
        distance: 225,
        target: [0, 0, 0],
      });
    } else if (preset === 'macro') {
      const focus = this.inspectedThread || this._findCenterMostThread();
      this.inspectedThread = focus;
      let tx = 0;
      let ty = 0;
      if (focus && focus._normCenter) {
        tx = focus._normCenter[0];
        ty = focus._normCenter[1];
      }
      this._animateCamera({
        azimuth: -0.58,
        elevation: 0.36, // Low oblique angle (~20.6 deg) to highlight the 3D Z-arch bridge!
        distance: 28,
        target: [tx, ty, 0.6],
      });
    } else {
      // 'iso' default
      this._animateCamera({
        azimuth: -0.68,
        elevation: 0.64,
        distance: 215,
        target: [0, 0, 0.4],
      });
    }
  }

  _animateCamera(goal) {
    if (this._animFrame) cancelAnimationFrame(this._animFrame);
    const start = {
      azimuth: this.camera.azimuth,
      elevation: this.camera.elevation,
      distance: this.camera.distance,
      target: [...this.camera.target],
    };
    const startTime = performance.now();
    const duration = 340;

    const step = (now) => {
      const t = Math.min(1, (now - startTime) / duration);
      const ease = 1 - Math.pow(1 - t, 3);
      this.camera.azimuth = start.azimuth + (goal.azimuth - start.azimuth) * ease;
      this.camera.elevation = start.elevation + (goal.elevation - start.elevation) * ease;
      this.camera.distance = start.distance + (goal.distance - start.distance) * ease;
      this.camera.target = [
        start.target[0] + (goal.target[0] - start.target[0]) * ease,
        start.target[1] + (goal.target[1] - start.target[1]) * ease,
        start.target[2] + (goal.target[2] - start.target[2]) * ease,
      ];
      this.render();
      if (t < 1) {
        this._animFrame = requestAnimationFrame(step);
      } else {
        this._animFrame = null;
      }
    };
    this._animFrame = requestAnimationFrame(step);
  }

  _findCenterMostThread() {
    const threads = this._normalizedScene?.threads || [];
    if (!threads.length) return null;
    let best = threads[0];
    let bestDist = Infinity;
    for (const th of threads) {
      const [cx, cy] = th._normCenter || [0, 0];
      const d = cx * cx + cy * cy;
      if (d < bestDist) {
        bestDist = d;
        best = th;
      }
    }
    return best;
  }

  _normalizePayload() {
    if (!this.payload) {
      this._normalizedScene = null;
      return;
    }

    const plateW = this.payload.plate_size_mm?.[0] || 256.0;
    const plateH = this.payload.plate_size_mm?.[1] || 256.0;
    const canvasW = this.payload.canvas_size_mm?.[0] || 150.0;
    const canvasH = this.payload.canvas_size_mm?.[1] || 150.0;

    // Determine coordinate bounds of incoming grid_lines / threads so we center them at (0,0)
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;

    for (const seg of this.payload.grid_lines || []) {
      for (const pt of seg) {
        if (pt[0] < minX) minX = pt[0];
        if (pt[0] > maxX) maxX = pt[0];
        if (pt[1] < minY) minY = pt[1];
        if (pt[1] > maxY) maxY = pt[1];
      }
    }
    for (const th of this.payload.threads || []) {
      for (const pt of th.lower_points || []) {
        if (pt[0] < minX) minX = pt[0];
        if (pt[0] > maxX) maxX = pt[0];
        if (pt[1] < minY) minY = pt[1];
        if (pt[1] > maxY) maxY = pt[1];
      }
    }

    const centerX = Number.isFinite(minX) ? (minX + maxX) * 0.5 : canvasW * 0.5;
    const centerY = Number.isFinite(minY) ? (minY + maxY) * 0.5 : canvasH * 0.5;

    const normPt = (pt) => [pt[0] - centerX, pt[1] - centerY, pt[2] || 0];

    const gridLines = (this.payload.grid_lines || []).map((seg) => [
      normPt(seg[0]),
      normPt(seg[1]),
    ]);

    const threads = (this.payload.threads || []).map((th) => {
      const lower = (th.lower_points || []).map(normPt);
      const upper = (th.upper_points || []).map(normPt);
      const midPt =
        upper.length > 0
          ? upper[Math.floor(upper.length / 2)]
          : lower.length > 0
            ? lower[Math.floor(lower.length / 2)]
            : [0, 0, 0.6];
      return {
        ...th,
        _lowerNorm: lower,
        _upperNorm: upper,
        _normCenter: [midPt[0], midPt[1], midPt[2]],
      };
    });

    this._normalizedScene = {
      plateW,
      plateH,
      canvasW,
      canvasH,
      gridLines,
      threads,
      gridHex: this.payload.grid_hex || '#C4C8CE',
      gridHeightMm: this.payload.grid_height_mm || 0.4,
      lineWidthMm: this.payload.line_width_mm || 0.4,
      stitchThicknessMm: this.payload.stitch_thickness_mm || 0.2,
    };
  }

  _setupResizeObserver() {
    if (typeof ResizeObserver !== 'undefined' && this.canvas.parentElement) {
      this._ro = new ResizeObserver(() => this.resize());
      this._ro.observe(this.canvas.parentElement);
    }
    window.addEventListener('resize', () => this.resize());
    setTimeout(() => this.resize(), 15);
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

  _bindEvents() {
    this.canvas.addEventListener('contextmenu', (e) => e.preventDefault());

    this.canvas.addEventListener('mousedown', (e) => {
      if (this._animFrame) {
        cancelAnimationFrame(this._animFrame);
        this._animFrame = null;
      }
      this._pointerDownClient = { x: e.clientX, y: e.clientY };
      this._lastClient = { x: e.clientX, y: e.clientY };
      if (e.button === 1 || e.button === 2 || e.shiftKey) {
        this._dragMode = 'pan';
      } else if (e.button === 0) {
        this._dragMode = 'orbit';
      }
    });

    window.addEventListener('mousemove', (e) => {
      if (!this._dragMode) return;
      const dx = e.clientX - this._lastClient.x;
      const dy = e.clientY - this._lastClient.y;
      this._lastClient = { x: e.clientX, y: e.clientY };

      if (this._dragMode === 'orbit') {
        this.camera.azimuth -= dx * 0.0075;
        this.camera.elevation = Math.max(
          0.08,
          Math.min(1.55, this.camera.elevation + dy * 0.0065)
        );
        this.render();
      } else if (this._dragMode === 'pan') {
        const panScale = this.camera.distance * 0.0018;
        const cosA = Math.cos(this.camera.azimuth);
        const sinA = Math.sin(this.camera.azimuth);
        // Right vector in XY plane: [cosA, -sinA]
        // Up vector projected in XY plane: [sinA, cosA]
        this.camera.target[0] -= (dx * cosA + dy * sinA) * panScale;
        this.camera.target[1] -= (-dx * sinA + dy * cosA) * panScale;
        this.render();
      }
    });

    window.addEventListener('mouseup', (e) => {
      if (!this._dragMode) return;
      const moved =
        Math.hypot(
          e.clientX - this._pointerDownClient.x,
          e.clientY - this._pointerDownClient.y
        ) > 4;
      this._dragMode = null;

      if (!moved && e.button === 0) {
        this._pickThreadAtClient(e.clientX, e.clientY);
      }
    });

    this.canvas.addEventListener(
      'wheel',
      (e) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 0.88 : 1.13;
        this.camera.distance = Math.max(14, Math.min(480, this.camera.distance * factor));
        this.render();
      },
      { passive: false }
    );
  }

  _pickThreadAtClient(clientX, clientY) {
    if (!this._projectedThreads.length) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;

    let closest = null;
    let minD = 18;
    for (const item of this._projectedThreads) {
      const d = Math.hypot(item.sx - x, item.sy - y);
      if (d < minD) {
        minD = d;
        closest = item.thread;
      }
    }
    if (closest) {
      this.inspectedThread = closest;
      if (this.onThreadSelect) this.onThreadSelect(closest);
      this.render();
    }
  }

  _buildProjector(W, H) {
    const { azimuth, elevation, distance, target } = this.camera;
    const cosA = Math.cos(azimuth);
    const sinA = Math.sin(azimuth);
    const cosE = Math.cos(elevation);
    const sinE = Math.sin(elevation);

    // Camera basis with X right, Y down on table, Z pointing UP
    const zScale = this.zExaggeration;
    const fov = Math.min(W, H) * 0.92;

    return (x, y, z = 0) => {
      const dx = x - target[0];
      const dy = y - target[1];
      const dz = z * zScale - target[2] * zScale;

      // Rotate around Z by azimuth
      const rx = dx * cosA - dy * sinA;
      const ry = dx * sinA + dy * cosA;

      // Tilt by elevation (looking down at XY plane where +Y is downward on screen)
      const camX = rx;
      const camY = ry * sinE - dz * cosE;
      const camZ = distance + ry * cosE + dz * sinE;

      const safeZ = Math.max(4, camZ);
      const scale = fov / safeZ;

      return {
        x: W * 0.5 + camX * scale,
        y: H * 0.5 + camY * scale,
        depth: safeZ,
        scale,
      };
    };
  }

  render() {
    if (!this.canvas || !this.ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const ctx = this.ctx;
    const W = this.canvas.width / dpr;
    const H = this.canvas.height / dpr;

    ctx.save();
    ctx.scale(dpr, dpr);

    // Deep studio slate background tailored for BambuLab 3D slicer clarity
    const bgGrad = ctx.createLinearGradient(0, 0, 0, H);
    bgGrad.addColorStop(0, '#1B1E23');
    bgGrad.addColorStop(1, '#121417');
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, W, H);

    if (!this._normalizedScene) {
      ctx.fillStyle = '#9BA1A8';
      ctx.font = '500 13px "Inter", system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Preparing BambuLab X2D 3D Lace Geometry...', W * 0.5, H * 0.5);
      ctx.restore();
      return;
    }

    const project = this._buildProjector(W, H);
    const scene = this._normalizedScene;

    // 1. Render 256 x 256 mm BambuLab X2D Build Plate
    if (this.showPlate) {
      this._renderBambuBuildPlate(ctx, project, scene);
    }

    // 2. Render Part 1: Light Gray Base Grid (Z: 0.00 - 0.40 mm, Extruder 1)
    if (this.showBaseGrid && !this.hiddenExtruders.has(1)) {
      this._renderBaseGrid3D(ctx, project, scene);
    }

    // 3. Render Parts 2..K: Colored 3D Cross-Stitch Threads with Center Z-Arch
    this._renderThreads3D(ctx, project, scene);

    // 4. Render Macro Z-Arch Cross-Section HUD & Telemetry Overlay
    this._renderViewportHUD(ctx, W, H, scene);

    ctx.restore();
  }

  _renderBambuBuildPlate(ctx, project, scene) {
    const hw = scene.plateW * 0.5;
    const hh = scene.plateH * 0.5;

    const corners = [
      project(-hw, -hh, 0),
      project(hw, -hh, 0),
      project(hw, hh, 0),
      project(-hw, hh, 0),
    ];

    ctx.save();
    // Textured PEI dark metallic build plate polygon
    ctx.beginPath();
    corners.forEach((p, i) => {
      if (i === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.closePath();
    ctx.fillStyle = '#24282E';
    ctx.fill();
    ctx.strokeStyle = '#4B525C';
    ctx.lineWidth = 1.6;
    ctx.stroke();

    // Front BambuLab X2D build plate tab
    const tabPts = [
      project(-38, hh, 0),
      project(38, hh, 0),
      project(32, hh + 9, 0),
      project(-32, hh + 9, 0),
    ];
    ctx.beginPath();
    tabPts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
    ctx.closePath();
    ctx.fillStyle = '#2D323A';
    ctx.fill();
    ctx.strokeStyle = '#58606C';
    ctx.lineWidth = 1.2;
    ctx.stroke();

    // 16mm build plate calibration grid lines
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.06)';
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    const step = 16;
    for (let x = -hw + step; x < hw; x += step) {
      const p0 = project(x, -hh, 0);
      const p1 = project(x, hh, 0);
      ctx.moveTo(p0.x, p0.y);
      ctx.lineTo(p1.x, p1.y);
    }
    for (let y = -hh + step; y < hh; y += step) {
      const p0 = project(-hw, y, 0);
      const p1 = project(hw, y, 0);
      ctx.moveTo(p0.x, p0.y);
      ctx.lineTo(p1.x, p1.y);
    }
    ctx.stroke();

    // 150x150mm Active Lace Footprint boundary outline on plate
    const chw = scene.canvasW * 0.5;
    const chh = scene.canvasH * 0.5;
    const cCorners = [
      project(-chw, -chh, 0.01),
      project(chw, -chh, 0.01),
      project(chw, chh, 0.01),
      project(-chw, chh, 0.01),
    ];
    ctx.beginPath();
    cCorners.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
    ctx.closePath();
    ctx.strokeStyle = 'rgba(0, 184, 156, 0.35)';
    ctx.lineWidth = 1.1;
    ctx.setLineDash([5, 4]);
    ctx.stroke();
    ctx.setLineDash([]);

    // 3D X / Y / Z Origin Axes Gizmo at bottom-left corner of plate
    const ox = -hw + 10;
    const oy = hh - 10;
    const o0 = project(ox, oy, 0.1);
    const oX = project(ox + 18, oy, 0.1);
    const oY = project(ox, oy - 18, 0.1);
    const oZ = project(ox, oy, 10 / this.zExaggeration);

    ctx.lineWidth = 2;
    // X axis (Red)
    ctx.strokeStyle = '#E5534B';
    ctx.beginPath();
    ctx.moveTo(o0.x, o0.y);
    ctx.lineTo(oX.x, oX.y);
    ctx.stroke();
    // Y axis (Green)
    ctx.strokeStyle = '#3FB950';
    ctx.beginPath();
    ctx.moveTo(o0.x, o0.y);
    ctx.lineTo(oY.x, oY.y);
    ctx.stroke();
    // Z axis (Blue)
    ctx.strokeStyle = '#58A6FF';
    ctx.beginPath();
    ctx.moveTo(o0.x, o0.y);
    ctx.lineTo(oZ.x, oZ.y);
    ctx.stroke();

    ctx.restore();
  }

  _renderBaseGrid3D(ctx, project, scene) {
    const gridHex = scene.gridHex || '#C4C8CE';
    const zTop = scene.gridHeightMm || 0.4;
    const lineW = scene.lineWidthMm || 0.4;

    ctx.save();
    ctx.lineCap = 'round';

    // Base shadow layer at Z = 0.05mm
    ctx.strokeStyle = 'rgba(12, 14, 18, 0.55)';
    ctx.beginPath();
    for (const seg of scene.gridLines) {
      const p0 = project(seg[0][0], seg[0][1], 0.04);
      const p1 = project(seg[1][0], seg[1][1], 0.04);
      ctx.moveTo(p0.x, p0.y);
      ctx.lineTo(p1.x, p1.y);
    }
    const sampleScale = project(0, 0, zTop).scale;
    const pxWidth = Math.max(0.85, lineW * sampleScale);
    ctx.lineWidth = pxWidth * 1.15;
    ctx.stroke();

    // Top extruded surface of Part 1 Light Gray Grid at Z = gridHeightMm (0.40mm)
    ctx.strokeStyle = gridHex;
    ctx.lineWidth = pxWidth;
    ctx.beginPath();
    for (const seg of scene.gridLines) {
      const p0 = project(seg[0][0], seg[0][1], zTop);
      const p1 = project(seg[1][0], seg[1][1], zTop);
      ctx.moveTo(p0.x, p0.y);
      ctx.lineTo(p1.x, p1.y);
    }
    ctx.stroke();

    // Outer 0.8mm Lace Frame around the 150x150mm canvas
    const chw = scene.canvasW * 0.5;
    const chh = scene.canvasH * 0.5;
    const fPts = [
      project(-chw, -chh, zTop),
      project(chw, -chh, zTop),
      project(chw, chh, zTop),
      project(-chw, chh, zTop),
    ];
    ctx.beginPath();
    fPts.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
    ctx.closePath();
    ctx.strokeStyle = gridHex;
    ctx.lineWidth = Math.max(1.8, pxWidth * 2.0);
    ctx.stroke();

    ctx.restore();
  }

  _renderThreads3D(ctx, project, scene) {
    this._projectedThreads = [];
    if (!scene.threads || !scene.threads.length) return;

    const lineW = scene.lineWidthMm || 0.4;

    // Project thread centers and depth-sort back-to-front (Painter's algorithm)
    const drawList = [];
    for (const th of scene.threads) {
      if (this.hiddenExtruders.has(Number(th.extruder))) continue;
      const cProj = project(th._normCenter[0], th._normCenter[1], th._normCenter[2]);
      drawList.push({
        thread: th,
        depth: cProj.depth,
        sx: cProj.x,
        sy: cProj.y,
        scale: cProj.scale,
      });
    }

    drawList.sort((a, b) => b.depth - a.depth);
    this._projectedThreads = drawList;

    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    for (const item of drawList) {
      const th = item.thread;
      const strokePx = Math.max(1.3, lineW * item.scale);
      const isInspected =
        this.inspectedThread &&
        this.inspectedThread.row === th.row &&
        this.inspectedThread.col === th.col;

      // 1. Lower diagonal thread (Z: 0.40 - 0.60 mm)
      if (this.showLowerThreads && th._lowerNorm.length >= 2) {
        const lowerProj = th._lowerNorm.map((pt) => project(pt[0], pt[1], pt[2]));

        // Subtle side wall extrusion shadow
        if (strokePx >= 2.2) {
          const baseProj = th._lowerNorm.map((pt) =>
            project(pt[0], pt[1], Math.max(0.38, pt[2] - 0.16))
          );
          ctx.strokeStyle = 'rgba(10, 12, 16, 0.55)';
          ctx.lineWidth = strokePx * 1.12;
          this._strokeProjectedPolyline(ctx, baseProj);
        }

        ctx.strokeStyle = th.hex || '#1D5B9B';
        ctx.lineWidth = strokePx;
        this._strokeProjectedPolyline(ctx, lowerProj);
      }

      // 2. Upper arching diagonal thread (Z: 0.40 -> 0.80 -> 0.40 mm, "交叉处中间微微隆起")
      if (this.showUpperThreads && th._upperNorm.length >= 2) {
        const upperProj = th._upperNorm.map((pt) => project(pt[0], pt[1], pt[2]));

        // Drop shadow cast by the Z-arch bridge onto the lower diagonal
        if (strokePx >= 1.8) {
          const shadowProj = th._upperNorm.map((pt) =>
            project(pt[0], pt[1], Math.max(0.42, pt[2] - 0.18))
          );
          ctx.strokeStyle = 'rgba(8, 10, 14, 0.62)';
          ctx.lineWidth = strokePx * 1.24;
          this._strokeProjectedPolyline(ctx, shadowProj);
        }

        // Main colored filament extrusion
        ctx.strokeStyle = th.hex || '#D9532F';
        ctx.lineWidth = strokePx * 1.05;
        this._strokeProjectedPolyline(ctx, upperProj);

        // Specular 3D filament ridge along the upper Z-arch peak
        if (strokePx >= 2.6 && upperProj.length >= 3) {
          const midIdx = Math.floor(upperProj.length / 2);
          const sIdx = Math.max(0, midIdx - 2);
          const eIdx = Math.min(upperProj.length - 1, midIdx + 2);
          ctx.strokeStyle = 'rgba(255, 255, 255, 0.38)';
          ctx.lineWidth = Math.max(0.8, strokePx * 0.34);
          ctx.beginPath();
          for (let i = sIdx; i <= eIdx; i++) {
            if (i === sIdx) ctx.moveTo(upperProj[i].x, upperProj[i].y);
            else ctx.lineTo(upperProj[i].x, upperProj[i].y);
          }
          ctx.stroke();
        }
      }

      // Highlight inspected macro cell in 3D space
      if (isInspected) {
        ctx.strokeStyle = '#FFD166';
        ctx.lineWidth = 1.6;
        ctx.beginPath();
        ctx.arc(item.sx, item.sy, Math.max(6, strokePx * 2.3), 0, Math.PI * 2);
        ctx.stroke();
      }
    }

    ctx.restore();
  }

  _strokeProjectedPolyline(ctx, pts) {
    if (!pts || pts.length < 2) return;
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(pts[i].x, pts[i].y);
    }
    ctx.stroke();
  }

  _renderViewportHUD(ctx, W, H, scene) {
    ctx.save();

    // Top-left printer & telemetry badge
    ctx.fillStyle = 'rgba(18, 21, 26, 0.82)';
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.12)';
    ctx.lineWidth = 1;
    this._roundRect(ctx, 14, 14, 248, 66, 6);
    ctx.fill();
    ctx.stroke();

    ctx.fillStyle = '#E6EDF3';
    ctx.font = '600 11px "Inter", system-ui, sans-serif';
    ctx.fillText('BAMBULAB X2D · 256×256mm PEI PLATE', 24, 32);

    ctx.fillStyle = '#9BA1A8';
    ctx.font = '500 11px ui-monospace, monospace';
    ctx.fillText(
      `Canvas: ${scene.canvasW}×${scene.canvasH}mm | Stitches: ${scene.threads.length}`,
      24,
      50
    );
    ctx.fillStyle = '#4AC29A';
    ctx.fillText(
      `Nozzle: 0.4mm | Z-Arch Visual Scale: ${this.zExaggeration.toFixed(1)}×`,
      24,
      67
    );

    // Bottom-right Macro Cell Z-Arch Cross-Section Inspector Diagram
    const boxW = 242;
    const boxH = 112;
    const bx = W - boxW - 14;
    const by = H - boxH - 14;

    ctx.fillStyle = 'rgba(18, 21, 26, 0.88)';
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.14)';
    this._roundRect(ctx, bx, by, boxW, boxH, 6);
    ctx.fill();
    ctx.stroke();

    const th = this.inspectedThread;
    ctx.fillStyle = '#E6EDF3';
    ctx.font = '600 10.5px "Inter", system-ui, sans-serif';
    const cellTitle = th
      ? `MACRO Z-ARCH PROFILE · CELL (${th.row}, ${th.col}) [Ext #${th.extruder}]`
      : 'MACRO Z-ARCH PROFILE · (交叉处中间微微隆起)';
    ctx.fillText(cellTitle, bx + 12, by + 19);

    // Draw cross-section schematic of Z layers inside the HUD box
    const sx0 = bx + 16;
    const sx1 = bx + boxW - 16;
    const baseZ = by + 90;

    // 1. PEI Plate line (Z = 0.00mm)
    ctx.strokeStyle = '#58606C';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(sx0, baseZ);
    ctx.lineTo(sx1, baseZ);
    ctx.stroke();

    // 2. Part 1 Light Gray Grid pillars (Z: 0.00 - 0.40mm)
    ctx.fillStyle = scene.gridHex || '#C4C8CE';
    ctx.fillRect(sx0 + 6, baseZ - 16, 18, 16);
    ctx.fillRect(sx1 - 24, baseZ - 16, 18, 16);
    ctx.fillStyle = 'rgba(196, 200, 206, 0.25)';
    ctx.fillRect(sx0 + 24, baseZ - 16, sx1 - sx0 - 48, 16);

    // 3. Lower Diagonal Thread cross-section (Z: 0.40 - 0.60mm)
    const threadColor = th?.hex || '#008F7A';
    ctx.fillStyle = threadColor;
    ctx.beginPath();
    ctx.arc((sx0 + sx1) * 0.5, baseZ - 23, 7, 0, Math.PI * 2);
    ctx.fill();

    // 4. Upper Arching Diagonal Thread (Z: 0.40 -> 0.80 -> 0.40mm)
    ctx.strokeStyle = threadColor;
    ctx.lineWidth = 5.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(sx0 + 15, baseZ - 19);
    ctx.bezierCurveTo(
      (sx0 + sx1) * 0.5 - 26,
      baseZ - 48,
      (sx0 + sx1) * 0.5 + 26,
      baseZ - 48,
      sx1 - 15,
      baseZ - 19
    );
    ctx.stroke();

    // Highlight arch clearance arrow
    ctx.fillStyle = '#FFD166';
    ctx.font = '500 9.5px ui-monospace, monospace';
    ctx.fillText('Z: 0.80mm (Upper Arch Peak)', bx + 14, by + 36);
    ctx.fillStyle = '#9BA1A8';
    ctx.fillText('Z: 0.40mm (Part 1 Grid) · Click any stitch', bx + 14, by + 104);

    ctx.restore();
  }

  _roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r);
    ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h);
    ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  /**
   * Client-side instant synthesis of Preview3DPayload from active GridDraft + ColorDraft + Bambu3mfConfig.
   * Ensures immediate 60fps 3D preview updates while backend /3mf/preview is in flight.
   */
  static buildPayloadFromGrid(gridDraft, colorDraft, bambuConfig = {}) {
    if (!gridDraft || !gridDraft.cells) return null;
    const rows = gridDraft.cells.length;
    const cols = gridDraft.cells[0]?.length || 60;
    const cellSize = Number(gridDraft.config?.cell_size_mm || 2.5);
    const canvasW = cols * cellSize;
    const canvasH = rows * cellSize;

    const gridHeight = Number(bambuConfig.grid_height_mm ?? 0.4);
    const stitchThick = Number(bambuConfig.stitch_thickness_mm ?? 0.2);
    const archZ = Number(bambuConfig.stitch_arch_z_mm ?? 0.2);
    const bulge = Number(bambuConfig.bulge_ratio ?? 0.09);
    const segs = Math.max(4, Math.min(14, Number(bambuConfig.curve_segments ?? 8)));

    const gridLines = [];
    for (let c = 0; c <= cols; c++) {
      const x = c * cellSize;
      gridLines.push([
        [x, 0, gridHeight],
        [x, canvasH, gridHeight],
      ]);
    }
    for (let r = 0; r <= rows; r++) {
      const y = r * cellSize;
      gridLines.push([
        [0, y, gridHeight],
        [canvasW, y, gridHeight],
      ]);
    }

    const colorMap = {};
    for (const c of colorDraft?.colors || []) {
      colorMap[c.slot] = c;
    }

    const cubicPt = (p0, p1, p2, p3, t) => {
      const u = 1 - t;
      return [
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
      ];
    };

    const threads = [];
    const delta = cellSize * bulge;
    const inset = cellSize * 0.06;

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const slot = gridDraft.cells[r][c];
        if (!slot || slot <= 0) continue;

        const cInfo = colorMap[slot];
        const hex = cInfo?.hex || '#1D5B9B';
        const extruder = cInfo?.bambu_extruder || slot + 1;

        const x0 = c * cellSize;
        const y0 = r * cellSize;
        const s = cellSize;

        // Lower diagonal: Bottom-Left -> Top-Right
        const l0 = [x0 + inset, y0 + s - inset];
        const l1 = [x0 + s * 0.36 - delta, y0 + s * 0.64 - delta];
        const l2 = [x0 + s * 0.64 - delta, y0 + s * 0.36 - delta];
        const l3 = [x0 + s - inset, y0 + inset];

        // Upper arching diagonal: Top-Left -> Bottom-Right
        const u0 = [x0 + inset, y0 + inset];
        const u1 = [x0 + s * 0.36 + delta, y0 + s * 0.36 - delta];
        const u2 = [x0 + s * 0.64 + delta, y0 + s * 0.64 - delta];
        const u3 = [x0 + s - inset, y0 + s - inset];

        const lowerPts = [];
        const upperPts = [];
        const zBase = gridHeight + stitchThick * 0.5;

        for (let i = 0; i <= segs; i++) {
          const t = i / segs;
          const [lx, ly] = cubicPt(l0, l1, l2, l3, t);
          lowerPts.push([lx, ly, zBase]);

          const [ux, uy] = cubicPt(u0, u1, u2, u3, t);
          // Smooth bell curve arch bump peaking at t = 0.5 ("交叉处中间微微隆起")
          const bell = Math.sin(Math.PI * t);
          const uz = zBase + archZ * (bell * bell);
          upperPts.push([ux, uy, uz]);
        }

        threads.push({
          row: r,
          col: c,
          color_slot: slot,
          hex,
          extruder,
          lower_points: lowerPts,
          upper_points: upperPts,
        });
      }
    }

    return {
      plate_size_mm: [256.0, 256.0],
      canvas_size_mm: [canvasW, canvasH],
      grid_lines: gridLines,
      grid_hex: colorDraft?.grid_filament_hex || '#C4C8CE',
      grid_height_mm: gridHeight,
      line_width_mm: Number(bambuConfig.line_width_mm ?? 0.4),
      stitch_thickness_mm: stitchThick,
      threads,
    };
  }
}
