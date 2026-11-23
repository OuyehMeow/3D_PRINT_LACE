/**
 * 3D_PRINT_LACE — Frontend REST API Client & Color Utilities
 * Enforces "Method in the Cloud, Data at Local" (all session data & exports reside in DATA_ROOT).
 */

export class LaceAPI {
  constructor(baseUrl = '/api') {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  async _request(path, options = {}) {
    const url = `${this.baseUrl}${path}`;
    const headers = { ...(options.headers || {}) };
    if (options.body && !(options.body instanceof FormData) && !headers['Content-Type']) {
      headers['Content-Type'] = 'application/json';
    }

    const response = await fetch(url, {
      ...options,
      headers,
    });

    if (!response.ok) {
      let detail = `${response.status} ${response.statusText}`;
      try {
        const errJson = await response.json();
        detail = errJson.detail
          ? typeof errJson.detail === 'string'
            ? errJson.detail
            : JSON.stringify(errJson.detail)
          : JSON.stringify(errJson);
      } catch (_) {
        const txt = await response.text().catch(() => '');
        if (txt) detail = txt.slice(0, 240);
      }
      const err = new Error(detail);
      err.status = response.status;
      throw err;
    }

    const contentType = response.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      return response.json();
    }
    return response;
  }

  /**
   * GET /api/sessions -> list[dict] or { sessions: list[dict], ... }
   */
  async listSessions() {
    const data = await this._request('/sessions', { method: 'GET' });
    if (Array.isArray(data)) {
      return {
        sessions: data,
        storageInfo: data[0]?.data_root ? { data_root: data[0].data_root } : null,
      };
    }
    return {
      sessions: data.sessions || data.items || [],
      storageInfo: data.storage_info || data.storage || {
        data_root: data.data_root || 'DATA_ROOT (Local)',
        provider: data.provider || 'LocalStorageProvider',
      },
    };
  }

  /**
   * POST /api/sessions/upload
   * Supports either file (File/Blob) upload OR built-in sample preset ("crane" | "cat" | "mountain" | "lotus").
   */
  async uploadSession({ file = null, preset = null, name = null } = {}) {
    if (file) {
      const formData = new FormData();
      formData.append('file', file);
      if (name) formData.append('name', name);
      return this._request('/sessions/upload', {
        method: 'POST',
        body: formData,
      });
    }

    const chosenPreset = preset || 'crane';
    const qs = new URLSearchParams({ preset: chosenPreset });
    if (name) qs.set('name', name);

    // Try multipart FormData with query string first (works with FastAPI File(None) + Form/Query)
    // and automatically fallback to JSON body if endpoint expects application/json.
    try {
      const formData = new FormData();
      formData.append('preset', chosenPreset);
      if (name) formData.append('name', name);
      return await this._request(`/sessions/upload?${qs.toString()}`, {
        method: 'POST',
        body: formData,
      });
    } catch (firstErr) {
      if (firstErr.status === 415 || firstErr.status === 422 || firstErr.status === 400) {
        return await this._request(`/sessions/upload?${qs.toString()}`, {
          method: 'POST',
          body: JSON.stringify({ preset: chosenPreset, ...(name ? { name } : {}) }),
        });
      }
      throw firstErr;
    }
  }

  /**
   * GET /api/sessions/{session_id} -> SessionState
   */
  async getSession(sessionId) {
    return this._request(`/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'GET',
    });
  }

  /**
   * POST /api/sessions/{session_id}/style -> SessionState
   * Body: { engine: "algorithmic" | "ai", whitespace_target: 0.68, focal_offset: [0.0, 0.0], api_key?: string }
   */
  async generateStyle(
    sessionId,
    {
      engine = 'algorithmic',
      whitespace_target = 0.68,
      focal_offset = [0.0, 0.0],
      api_key = undefined,
      active_style_draft_id = undefined,
    } = {}
  ) {
    const payload = {
      engine,
      whitespace_target: Number(whitespace_target),
      focal_offset: [Number(focal_offset[0] || 0), Number(focal_offset[1] || 0)],
    };
    if (api_key) payload.api_key = api_key;
    if (active_style_draft_id) payload.active_style_draft_id = active_style_draft_id;

    return this._request(`/sessions/${encodeURIComponent(sessionId)}/style`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /**
   * POST /api/sessions/{session_id}/color -> SessionState
   * Body: { max_colors: 4, base_style: "warm_white" | "gray_white" }
   */
  async generateColor(sessionId, { max_colors = 4, base_style = 'warm_white' } = {}) {
    return this._request(`/sessions/${encodeURIComponent(sessionId)}/color`, {
      method: 'POST',
      body: JSON.stringify({
        max_colors: Number(max_colors),
        base_style,
      }),
    });
  }

  /**
   * PUT /api/sessions/{session_id}/color -> SessionState
   * Body: { palette_id: string, base_hex: string, grid_filament_hex: string, colors: list[{slot, name, hex}] }
   */
  async updateColor(sessionId, { palette_id, base_hex, grid_filament_hex, colors }) {
    return this._request(`/sessions/${encodeURIComponent(sessionId)}/color`, {
      method: 'PUT',
      body: JSON.stringify({
        palette_id,
        base_hex,
        grid_filament_hex,
        colors,
      }),
    });
  }

  /**
   * POST /api/sessions/{session_id}/grid -> SessionState
   * Body: GridConfig (+ optional style_draft_id / palette_id query parameters)
   */
  async generateGrid(sessionId, gridConfig, { style_draft_id = null, palette_id = null } = {}) {
    const qs = new URLSearchParams();
    if (style_draft_id) qs.set('style_draft_id', style_draft_id);
    if (palette_id) qs.set('palette_id', palette_id);
    const querySuffix = qs.toString() ? `?${qs.toString()}` : '';

    const bodyPayload = {
      cols: Number(gridConfig.cols ?? 60),
      rows: Number(gridConfig.rows ?? 60),
      cell_size_mm: Number(gridConfig.cell_size_mm ?? 2.5),
      projection_mode: gridConfig.projection_mode || 'hybrid_line_preserving',
      block_threshold: Number(gridConfig.block_threshold ?? 0.5),
      line_threshold: Number(gridConfig.line_threshold ?? 0.2),
      offset_x: Number(gridConfig.offset_x ?? 0.0),
      offset_y: Number(gridConfig.offset_y ?? 0.0),
      scale: Number(gridConfig.scale ?? 1.0),
      despeckle: Boolean(gridConfig.despeckle ?? true),
      manual_overrides: gridConfig.manual_overrides || {},
    };
    if (style_draft_id) bodyPayload.style_draft_id = style_draft_id;
    if (palette_id) bodyPayload.palette_id = palette_id;

    return this._request(`/sessions/${encodeURIComponent(sessionId)}/grid${querySuffix}`, {
      method: 'POST',
      body: JSON.stringify(bodyPayload),
    });
  }

  /**
   * POST /api/sessions/{session_id}/grid/pin -> SessionState
   * Body: { grid_id: string }
   */
  async togglePinGrid(sessionId, gridId) {
    return this._request(`/sessions/${encodeURIComponent(sessionId)}/grid/pin`, {
      method: 'POST',
      body: JSON.stringify({ grid_id: gridId }),
    });
  }

  /**
   * POST /api/sessions/{session_id}/svg -> SvgExportResult
   * Body: SvgConfig
   */
  async generateSvg(sessionId, svgConfig = {}) {
    const payload = {
      stroke_width_mm: Number(svgConfig.stroke_width_mm ?? 0.4),
      bulge_ratio: Number(svgConfig.bulge_ratio ?? 0.09),
      corner_inset_mm: Number(svgConfig.corner_inset_mm ?? 0.0),
      include_grid_layer: Boolean(svgConfig.include_grid_layer ?? true),
      optimize_plotter_path: Boolean(svgConfig.optimize_plotter_path ?? true),
    };
    return this._request(`/sessions/${encodeURIComponent(sessionId)}/svg`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  /**
   * POST /api/sessions/{session_id}/3mf/preview -> Preview3DPayload
   * Body: Bambu3mfConfig
   */
  async preview3mf(sessionId, bambuConfig = {}) {
    return this._request(`/sessions/${encodeURIComponent(sessionId)}/3mf/preview`, {
      method: 'POST',
      body: JSON.stringify(this._normalizeBambuConfig(bambuConfig)),
    });
  }

  /**
   * POST /api/sessions/{session_id}/3mf/export -> Bambu3mfExportResult
   * Body: Bambu3mfConfig
   */
  async export3mf(sessionId, bambuConfig = {}) {
    return this._request(`/sessions/${encodeURIComponent(sessionId)}/3mf/export`, {
      method: 'POST',
      body: JSON.stringify(this._normalizeBambuConfig(bambuConfig)),
    });
  }

  _normalizeBambuConfig(cfg = {}) {
    return {
      printer_model: cfg.printer_model || 'Bambu Lab X2D',
      nozzle_diameter_mm: Number(cfg.nozzle_diameter_mm ?? 0.4),
      line_width_mm: Number(cfg.line_width_mm ?? 0.4),
      grid_height_mm: Number(cfg.grid_height_mm ?? 0.4),
      stitch_thickness_mm: Number(cfg.stitch_thickness_mm ?? 0.2),
      stitch_arch_z_mm: Number(cfg.stitch_arch_z_mm ?? 0.2),
      bulge_ratio: Number(cfg.bulge_ratio ?? 0.09),
      include_outer_frame: Boolean(cfg.include_outer_frame ?? true),
      frame_width_mm: Number(cfg.frame_width_mm ?? 0.8),
      curve_segments: Number(cfg.curve_segments ?? 8),
    };
  }

  getSourceImageUrl(sessionId, cacheBust = '') {
    const suffix = cacheBust ? `?t=${encodeURIComponent(cacheBust)}` : '';
    return `${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/source-image${suffix}`;
  }

  getExportDownloadUrl(sessionId, filename) {
    return `${this.baseUrl}/sessions/${encodeURIComponent(sessionId)}/exports/${encodeURIComponent(filename)}`;
  }
}

/**
 * Perceptual OKLCH approximation from sRGB Hex (#RRGGBB) for real-time Stage 2 color inspection.
 */
export function hexToOklch(hex) {
  const clean = String(hex || '#000000').replace('#', '').trim();
  const num = parseInt(clean.length === 3 ? clean.replace(/(.)/g, '$1$1') : clean, 16) || 0;
  const srgbToLinear = (c) => {
    const v = c / 255;
    return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };

  const r = srgbToLinear((num >> 16) & 255);
  const g = srgbToLinear((num >> 8) & 255);
  const b = srgbToLinear(num & 255);

  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);

  const L = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const a = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bOk = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;

  const C = Math.sqrt(a * a + bOk * bOk);
  let H = (Math.atan2(bOk, a) * 180) / Math.PI;
  if (H < 0) H += 360;

  return [
    Number(Math.max(0, Math.min(1, L)).toFixed(3)),
    Number(Math.max(0, C).toFixed(3)),
    Number(H.toFixed(1)),
  ];
}

export function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}
