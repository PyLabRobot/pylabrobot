(function () {
  "use strict";

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function niceStep(span, targetCount) {
    const raw = Math.max(span, 1) / Math.max(targetCount, 1);
    const magnitude = Math.pow(10, Math.floor(Math.log10(raw)));
    const normalized = raw / magnitude;
    let factor;
    if (normalized <= 1) {
      factor = 1;
    } else if (normalized <= 2) {
      factor = 2;
    } else if (normalized <= 5) {
      factor = 5;
    } else {
      factor = 10;
    }
    return factor * magnitude;
  }

  const AXIS_COLORS = { x: "#e84a4a", y: "#4caf3e", z: "#3b7dd8" };

  // Minimum on-screen spacing (canvas px) between drawn tick labels; closer
  // ones are skipped so they don't pile up when an axis goes near edge-on.
  const TICK_MIN_GAP = 30;

  function colorForPrototype(prototype) {
    const geometry = prototype.geometry || {};
    const type = prototype.type || "";
    const category = prototype.category || "";

    if (geometry.shape === "deck") return "#dbe7f1";
    if (type.includes("Carrier")) return "#9eb3c4";
    if (type.includes("Plate")) return "#4a5f73";
    if (type.includes("TipRack")) return "#8f6e34";
    if (type.includes("TubeRack")) return "#5f7c55";
    if (type.includes("Well")) return "#66b8c4";
    if (type.includes("TipSpot")) return "#d8c48a";
    if (category === "trash") return "#9f7c7c";
    return "#8ea3b6";
  }

  function hexToRgb(hex) {
    const normalized = hex.replace("#", "");
    const value = parseInt(normalized, 16);
    return {
      r: (value >> 16) & 255,
      g: (value >> 8) & 255,
      b: value & 255,
    };
  }

  function shadeColor(hex, factor) {
    const rgb = hexToRgb(hex);
    const scale = clamp(factor, 0.45, 1.2);
    return `rgb(${Math.round(rgb.r * scale)}, ${Math.round(rgb.g * scale)}, ${Math.round(rgb.b * scale)})`;
  }

  function readThemeColors(root) {
    const probe = root || document.documentElement;
    const style = getComputedStyle(probe);
    const bg = style.getPropertyValue("--pst-color-background").trim() || "#f7fafc";
    const surface = style.getPropertyValue("--pst-color-surface").trim() || bg;
    const text = style.getPropertyValue("--pst-color-text-base").trim() || "#17334a";
    return { bg, surface, text };
  }

  function multiplyMatrix(a, b) {
    return [
      [
        a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
        a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
        a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2],
      ],
      [
        a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
        a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
        a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2],
      ],
      [
        a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
        a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
        a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2],
      ],
    ];
  }

  function rotationMatrix(rotation) {
    const [rx, ry, rz] = (rotation || [0, 0, 0]).map((degrees) => (degrees * Math.PI) / 180);

    const cx = Math.cos(rx);
    const sx = Math.sin(rx);
    const cy = Math.cos(ry);
    const sy = Math.sin(ry);
    const cz = Math.cos(rz);
    const sz = Math.sin(rz);

    const mx = [
      [1, 0, 0],
      [0, cx, -sx],
      [0, sx, cx],
    ];
    const my = [
      [cy, 0, sy],
      [0, 1, 0],
      [-sy, 0, cy],
    ];
    const mz = [
      [cz, -sz, 0],
      [sz, cz, 0],
      [0, 0, 1],
    ];

    return multiplyMatrix(mz, multiplyMatrix(my, mx));
  }

  function applyMatrix(point, matrix) {
    return {
      x: point.x * matrix[0][0] + point.y * matrix[0][1] + point.z * matrix[0][2],
      y: point.x * matrix[1][0] + point.y * matrix[1][1] + point.z * matrix[1][2],
      z: point.x * matrix[2][0] + point.y * matrix[2][1] + point.z * matrix[2][2],
    };
  }

  function translatePoint(point, offset) {
    return {
      x: point.x + offset[0],
      y: point.y + offset[1],
      z: point.z + offset[2],
    };
  }

  function createBoxGeometry(size) {
    const sx = size[0];
    const sy = size[1];
    const sz = size[2];

    const vertices = [
      { x: 0, y: 0, z: 0 },
      { x: sx, y: 0, z: 0 },
      { x: sx, y: sy, z: 0 },
      { x: 0, y: sy, z: 0 },
      { x: 0, y: 0, z: sz },
      { x: sx, y: 0, z: sz },
      { x: sx, y: sy, z: sz },
      { x: 0, y: sy, z: sz },
    ];

    const faces = [
      [0, 1, 2, 3],
      [4, 5, 6, 7],
      [0, 1, 5, 4],
      [1, 2, 6, 5],
      [2, 3, 7, 6],
      [3, 0, 4, 7],
    ];

    return { vertices, faces };
  }

  function createOffsetBoxGeometry(box) {
    const geometry = createBoxGeometry([box.sx, box.sy, box.sz]);
    geometry.vertices = geometry.vertices.map((vertex) => ({
      x: vertex.x + box.x,
      y: vertex.y + box.y,
      z: vertex.z + box.z,
    }));
    return geometry;
  }

  function mergeGeometries(geometries) {
    const merged = { vertices: [], faces: [] };
    geometries.forEach((geometry) => {
      const offset = merged.vertices.length;
      geometry.vertices.forEach((vertex) => merged.vertices.push(vertex));
      geometry.faces.forEach((face) => {
        merged.faces.push(face.map((index) => index + offset));
      });
    });
    return merged;
  }

  function createTrayGeometry(size) {
    const sx = size[0];
    const sy = size[1];
    const sz = size[2];
    const wallThickness = clamp(Math.min(sx, sy) * 0.055, 2.2, 6.5);
    const baseThickness = clamp(sz * 0.18, 1.8, Math.max(2.2, sz * 0.28));

    return mergeGeometries([
      createOffsetBoxGeometry({ x: 0, y: 0, z: 0, sx, sy, sz: baseThickness }),
      createOffsetBoxGeometry({ x: 0, y: 0, z: baseThickness, sx, sy: wallThickness, sz: sz - baseThickness }),
      createOffsetBoxGeometry({
        x: 0,
        y: sy - wallThickness,
        z: baseThickness,
        sx,
        sy: wallThickness,
        sz: sz - baseThickness,
      }),
      createOffsetBoxGeometry({
        x: 0,
        y: wallThickness,
        z: baseThickness,
        sx: wallThickness,
        sy: sy - wallThickness * 2,
        sz: sz - baseThickness,
      }),
      createOffsetBoxGeometry({
        x: sx - wallThickness,
        y: wallThickness,
        z: baseThickness,
        sx: wallThickness,
        sy: sy - wallThickness * 2,
        sz: sz - baseThickness,
      }),
    ]);
  }

  function createCylinderGeometry(size, segments) {
    const sx = size[0];
    const sy = size[1];
    const sz = size[2];
    const radiusX = sx / 2;
    const radiusY = sy / 2;
    const centerX = sx / 2;
    const centerY = sy / 2;
    const vertices = [];
    const top = [];
    const bottom = [];
    const faces = [];

    for (let index = 0; index < segments; index += 1) {
      const theta = (Math.PI * 2 * index) / segments;
      const x = centerX + Math.cos(theta) * radiusX;
      const y = centerY + Math.sin(theta) * radiusY;
      bottom.push(vertices.length);
      vertices.push({ x, y, z: 0 });
      top.push(vertices.length);
      vertices.push({ x, y, z: sz });
    }

    faces.push(bottom.slice().reverse());
    faces.push(top.slice());
    for (let index = 0; index < segments; index += 1) {
      const next = (index + 1) % segments;
      faces.push([
        bottom[index],
        bottom[next],
        top[next],
        top[index],
      ]);
    }

    return { vertices, faces };
  }

  function createShapeGeometry(prototype) {
    const geometry = prototype.geometry || {};
    const size = displaySizeForPrototype(prototype);
    const type = prototype.type || "";

    if (geometry.shape === "well" && geometry.cross_section === "circle") {
      return createCylinderGeometry(size, 18);
    }

    if (geometry.shape === "tip_spot") {
      return createCylinderGeometry(size, 12);
    }

    if (
      type.includes("Plate") ||
      type.includes("TipRack") ||
      type.includes("TubeRack")
    ) {
      return createTrayGeometry(size);
    }

    return createBoxGeometry(size);
  }

  function displaySizeForPrototype(prototype) {
    const size = (prototype.size || [10, 10, 10]).slice();
    if (size[2] > 0) {
      return size;
    }

    const type = prototype.type || "";
    const minPlanar = Math.max(1, Math.min(size[0], size[1]));
    let fallbackHeight = Math.max(2, minPlanar * 0.1);

    if (type.includes("TipSpot")) {
      fallbackHeight = Math.max(2, minPlanar * 0.3);
    } else if (type.includes("Well")) {
      fallbackHeight = Math.max(2, minPlanar * 0.35);
    } else if (type.includes("Holder")) {
      fallbackHeight = Math.max(3, minPlanar * 0.18);
    }

    size[2] = fallbackHeight;
    return size;
  }

  function normalizeCatalog(catalog) {
    if (!catalog || !catalog.prototypes || !catalog.instances) {
      return [];
    }

    const drawables = [];
    Object.entries(catalog.instances).forEach(([name, instance]) => {
      const prototype = catalog.prototypes[instance.prototype];
      if (!prototype || !instance.pose) {
        return;
      }

      const baseGeometry = createShapeGeometry(prototype);
      const matrix = rotationMatrix(instance.rotation || [0, 0, 0]);
      const translatedVertices = baseGeometry.vertices.map((vertex) =>
        translatePoint(applyMatrix(vertex, matrix), instance.pose),
      );
      const outline = createOutlinePoints(prototype, matrix, instance.pose);

      drawables.push({
        name,
        prototype,
        color: colorForPrototype(prototype),
        layer: drawableLayer(prototype),
        alpha: drawableAlpha(prototype),
        vertices: translatedVertices,
        faces: baseGeometry.faces,
        outline,
      });
    });

    return drawables;
  }

  function createOutlinePoints(prototype, matrix, pose) {
    const type = prototype.type || "";
    if (
      !type.includes("Plate") &&
      !type.includes("TipRack") &&
      !type.includes("TubeRack")
    ) {
      return null;
    }

    const size = displaySizeForPrototype(prototype);
    const z = size[2];
    const top = [
      { x: 0, y: 0, z },
      { x: size[0], y: 0, z },
      { x: size[0], y: size[1], z },
      { x: 0, y: size[1], z },
    ].map((corner) => translatePoint(applyMatrix(corner, matrix), pose));
    const bottom = [
      { x: 0, y: 0, z: 0 },
      { x: size[0], y: 0, z: 0 },
      { x: size[0], y: size[1], z: 0 },
      { x: 0, y: size[1], z: 0 },
    ].map((corner) => translatePoint(applyMatrix(corner, matrix), pose));

    return {
      top,
      bottom,
      verticals: [
        [bottom[0], top[0]],
        [bottom[1], top[1]],
        [bottom[2], top[2]],
        [bottom[3], top[3]],
      ],
    };
  }

  function drawableLayer(prototype) {
    const geometry = prototype.geometry || {};
    const type = prototype.type || "";

    if (geometry.shape === "deck") return 0;
    if (type.includes("Carrier") || type.includes("Holder")) return 1;
    if (type.includes("Plate") || type.includes("TipRack") || type.includes("TubeRack")) return 2;
    if (type.includes("Well") || type.includes("TipSpot")) return 4;
    return 3;
  }

  function drawableAlpha(prototype) {
    const type = prototype.type || "";
    const geometry = prototype.geometry || {};

    if (geometry.shape === "deck") return 0.38;
    if (type.includes("Plate") || type.includes("TipRack") || type.includes("TubeRack")) return 0.84;
    if (type.includes("Well") || type.includes("TipSpot")) return 0.68;
    return 0.78;
  }

  function computeBounds(drawables) {
    const points = [];
    drawables.forEach((drawable) => {
      drawable.vertices.forEach((vertex) => points.push(vertex));
    });

    if (points.length === 0) {
      return {
        min: { x: 0, y: 0, z: 0 },
        max: { x: 1, y: 1, z: 1 },
        center: { x: 0.5, y: 0.5, z: 0.5 },
        span: 1,
      };
    }

    const min = { x: Infinity, y: Infinity, z: Infinity };
    const max = { x: -Infinity, y: -Infinity, z: -Infinity };
    points.forEach((point) => {
      min.x = Math.min(min.x, point.x);
      min.y = Math.min(min.y, point.y);
      min.z = Math.min(min.z, point.z);
      max.x = Math.max(max.x, point.x);
      max.y = Math.max(max.y, point.y);
      max.z = Math.max(max.z, point.z);
    });

    const center = {
      x: (min.x + max.x) / 2,
      y: (min.y + max.y) / 2,
      z: (min.z + max.z) / 2,
    };

    const span = Math.max(max.x - min.x, max.y - min.y, max.z - min.z, 1);
    return { min, max, center, span };
  }

  class CanvasCatalogViewer {
    constructor(root) {
      this.root = root;
      this.canvas = document.createElement("canvas");
      this.canvas.className = "plr-geometry-canvas";
      this.root.innerHTML = "";
      this.root.appendChild(this.canvas);
      this.context = this.canvas.getContext("2d");
      this.drawables = [];
      this.bounds = computeBounds([]);
      this.defaultRotation = { yaw: -0.95 + Math.PI / 2, pitch: -0.9 };
      this.rotation = { ...this.defaultRotation };
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.pixelRatio = 1;
      this.isDragging = false;
      this.dragMode = "orbit";
      this.lastPointer = { x: 0, y: 0 };

      this.handlePointerDown = this.handlePointerDown.bind(this);
      this.handlePointerMove = this.handlePointerMove.bind(this);
      this.handlePointerUp = this.handlePointerUp.bind(this);
      this.handleWheel = this.handleWheel.bind(this);
      this.handleDoubleClick = this.handleDoubleClick.bind(this);
      this.handleResize = this.handleResize.bind(this);
      this.resize = this.resize.bind(this);

      this.canvas.addEventListener("pointerdown", this.handlePointerDown);
      window.addEventListener("pointermove", this.handlePointerMove);
      window.addEventListener("pointerup", this.handlePointerUp);
      this.canvas.addEventListener("wheel", this.handleWheel, { passive: false });
      this.canvas.addEventListener("dblclick", this.handleDoubleClick);
      window.addEventListener("resize", this.handleResize);
      if (typeof ResizeObserver !== "undefined") {
        this.resizeObserver = new ResizeObserver(this.handleResize);
        this.resizeObserver.observe(this.root);
      }

      this.handleResize();
    }

    destroy() {
      this.canvas.removeEventListener("pointerdown", this.handlePointerDown);
      window.removeEventListener("pointermove", this.handlePointerMove);
      window.removeEventListener("pointerup", this.handlePointerUp);
      this.canvas.removeEventListener("wheel", this.handleWheel);
      this.canvas.removeEventListener("dblclick", this.handleDoubleClick);
      window.removeEventListener("resize", this.handleResize);
      if (this.resizeObserver) {
        this.resizeObserver.disconnect();
      }
    }

    resetView() {
      this.rotation = { ...this.defaultRotation };
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.render();
    }

    handleDoubleClick(event) {
      event.preventDefault();
      this.resetView();
    }

    setCatalog(catalog) {
      this.drawables = normalizeCatalog(catalog);
      this.bounds = computeBounds(this.drawables);
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.render();
    }

    handlePointerDown(event) {
      if (event.button === 1) {
        event.preventDefault(); // suppress middle-click autoscroll
      }
      this.dragMode = event.button === 1 || event.shiftKey ? "pan" : "orbit";
      this.isDragging = true;
      this.lastPointer = { x: event.clientX, y: event.clientY };
      this.canvas.setPointerCapture(event.pointerId);
    }

    handlePointerMove(event) {
      if (!this.isDragging) {
        return;
      }

      const deltaX = event.clientX - this.lastPointer.x;
      const deltaY = event.clientY - this.lastPointer.y;
      this.lastPointer = { x: event.clientX, y: event.clientY };
      if (this.dragMode === "pan") {
        // pointer deltas are CSS px; project() outputs device px.
        this.pan.x += deltaX * this.pixelRatio;
        this.pan.y += deltaY * this.pixelRatio;
      } else {
        this.rotation.yaw += deltaX * 0.01;
        this.rotation.pitch = clamp(
          this.rotation.pitch + deltaY * 0.01,
          -Math.PI / 2,
          Math.PI / 2,
        );
      }
      this.render();
    }

    handlePointerUp(event) {
      this.isDragging = false;
      if (event.pointerId != null && this.canvas.hasPointerCapture(event.pointerId)) {
        this.canvas.releasePointerCapture(event.pointerId);
      }
    }

    handleWheel(event) {
      event.preventDefault();
      const zoomDelta = event.deltaY > 0 ? 0.92 : 1.08;
      this.zoom = clamp(this.zoom * zoomDelta, 0.25, 6);
      this.render();
    }

    handleResize() {
      const width = Math.max(this.root.clientWidth, 200);
      const height = Math.max(this.root.clientHeight, 200);
      this.pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.floor(width * this.pixelRatio);
      this.canvas.height = Math.floor(height * this.pixelRatio);
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;
      this.render();
    }

    resize() {
      this.handleResize();
    }

    project(point) {
      const centered = {
        x: point.x - this.bounds.center.x,
        y: point.y - this.bounds.center.y,
        z: point.z - this.bounds.center.z,
      };

      const cosYaw = Math.cos(this.rotation.yaw);
      const sinYaw = Math.sin(this.rotation.yaw);
      const cosPitch = Math.cos(this.rotation.pitch);
      const sinPitch = Math.sin(this.rotation.pitch);

      const yawed = {
        x: centered.x * cosYaw - centered.y * sinYaw,
        y: centered.x * sinYaw + centered.y * cosYaw,
        z: centered.z,
      };

      const pitched = {
        x: yawed.x,
        y: yawed.y * cosPitch - yawed.z * sinPitch,
        z: yawed.y * sinPitch + yawed.z * cosPitch,
      };

      const scaleBase = Math.min(this.canvas.width, this.canvas.height) / (this.bounds.span * 1.8);
      const scale = scaleBase * this.zoom;
      return {
        x: this.canvas.width / 2 + pitched.x * scale + this.pan.x,
        y: this.canvas.height / 2 - pitched.y * scale + this.pan.y,
        depth: pitched.z,
      };
    }

    drawBackground() {
      const ctx = this.context;
      const theme = readThemeColors(this.root);
      const gradient = ctx.createLinearGradient(0, 0, 0, this.canvas.height);
      gradient.addColorStop(0, theme.bg);
      gradient.addColorStop(1, theme.surface);
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    }

    drawRuler() {
      const ctx = this.context;
      const theme = readThemeColors(this.root);
      const groundZ = this.bounds.min.z;

      const spanX = this.bounds.max.x - this.bounds.min.x;
      const spanY = this.bounds.max.y - this.bounds.min.y;
      const step = Math.max(niceStep(spanX, 8), niceStep(spanY, 8));

      const x0 = Math.floor(this.bounds.min.x / step) * step;
      const x1 = Math.ceil(this.bounds.max.x / step) * step;
      const y0 = Math.floor(this.bounds.min.y / step) * step;
      const y1 = Math.ceil(this.bounds.max.y / step) * step;
      const epsilon = step * 1e-6;
      const labelGap = step * 0.2;

      ctx.save();
      ctx.strokeStyle = theme.text;

      // Minor mm grid.
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.18;
      for (let x = x0; x <= x1 + epsilon; x += step) {
        const start = this.project({ x, y: y0, z: groundZ });
        const end = this.project({ x, y: y1, z: groundZ });
        ctx.beginPath();
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
      }
      for (let y = y0; y <= y1 + epsilon; y += step) {
        const start = this.project({ x: x0, y, z: groundZ });
        const end = this.project({ x: x1, y, z: groundZ });
        ctx.beginPath();
        ctx.moveTo(start.x, start.y);
        ctx.lineTo(end.x, end.y);
        ctx.stroke();
      }

      // Origin axes (Blender-style colors: X red, Y green).
      ctx.globalAlpha = 0.9;
      ctx.lineWidth = 3;
      const xAxisStart = this.project({ x: x0, y: y0, z: groundZ });
      const xAxisEnd = this.project({ x: x1, y: y0, z: groundZ });
      ctx.strokeStyle = AXIS_COLORS.x;
      ctx.beginPath();
      ctx.moveTo(xAxisStart.x, xAxisStart.y);
      ctx.lineTo(xAxisEnd.x, xAxisEnd.y);
      ctx.stroke();
      const yAxisEnd = this.project({ x: x0, y: y1, z: groundZ });
      ctx.strokeStyle = AXIS_COLORS.y;
      ctx.beginPath();
      ctx.moveTo(xAxisStart.x, xAxisStart.y);
      ctx.lineTo(yAxisEnd.x, yAxisEnd.y);
      ctx.stroke();

      // Tick labels in mm (matched to axis colors).
      ctx.globalAlpha = 0.9;
      ctx.font = "bold 12px system-ui, -apple-system, sans-serif";
      ctx.fillStyle = AXIS_COLORS.x;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      let lastXLabel = null;
      for (let x = x0; x <= x1 + epsilon; x += step) {
        const point = this.project({ x, y: y0 - labelGap, z: groundZ });
        if (
          lastXLabel !== null &&
          Math.hypot(point.x - lastXLabel.x, point.y - lastXLabel.y) < TICK_MIN_GAP
        ) {
          continue;
        }
        lastXLabel = point;
        ctx.fillText(String(Math.round(x)), point.x, point.y + 5);
      }
      ctx.fillStyle = AXIS_COLORS.y;
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      let lastYLabel = null;
      for (let y = y0; y <= y1 + epsilon; y += step) {
        const point = this.project({ x: x0 - labelGap, y, z: groundZ - step * 0.2 });
        if (
          lastYLabel !== null &&
          Math.hypot(point.x - lastYLabel.x, point.y - lastYLabel.y) < TICK_MIN_GAP
        ) {
          continue;
        }
        lastYLabel = point;
        ctx.fillText(String(Math.round(y)), point.x - 7, point.y);
      }

      // Axis unit captions.
      ctx.globalAlpha = 1;
      ctx.font = "bold 11px system-ui, -apple-system, sans-serif";
      const xCaption = this.project({ x: x1, y: y0, z: groundZ });
      ctx.fillStyle = AXIS_COLORS.x;
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      ctx.fillText("X (mm)", xCaption.x + 25, xCaption.y);
      const yCaption = this.project({ x: x0, y: y1, z: groundZ });
      ctx.fillStyle = AXIS_COLORS.y;
      ctx.textAlign = "right";
      ctx.textBaseline = "bottom";
      ctx.fillText("Y (mm)", yCaption.x - 7, yCaption.y - 22);

      ctx.restore();
    }

    drawZAxis() {
      const z0 = this.bounds.min.z;
      const z1 = this.bounds.max.z;
      if (z1 - z0 < 1e-6) {
        return;
      }

      const step = niceStep(z1 - z0, 4);
      const zStart = Math.floor(z0 / step) * step;
      const zEnd = Math.ceil(z1 / step) * step;
      const epsilon = step * 1e-6;

      // Share the origin corner with the X/Y axes (matplotlib/Blender convention)
      // so all three meet at one point and Z rides the front silhouette edge
      // rather than piercing the translucent body.
      const gridStep = Math.max(
        niceStep(this.bounds.max.x - this.bounds.min.x, 8),
        niceStep(this.bounds.max.y - this.bounds.min.y, 8),
      );
      const corner = {
        x: Math.floor(this.bounds.min.x / gridStep) * gridStep,
        y: Math.floor(this.bounds.min.y / gridStep) * gridStep,
      };

      const centerScreen = this.project({
        x: this.bounds.center.x,
        y: this.bounds.center.y,
        z: z0,
      });
      const baseScreen = this.project({ x: corner.x, y: corner.y, z: z0 });
      const outwardSign = baseScreen.x >= centerScreen.x ? 1 : -1;

      const ctx = this.context;
      ctx.save();
      ctx.strokeStyle = AXIS_COLORS.z;
      ctx.fillStyle = AXIS_COLORS.z;

      ctx.globalAlpha = 0.9;
      ctx.lineWidth = 3;
      const axisBottom = this.project({ x: corner.x, y: corner.y, z: zStart });
      const axisTop = this.project({ x: corner.x, y: corner.y, z: zEnd });
      ctx.beginPath();
      ctx.moveTo(axisBottom.x, axisBottom.y);
      ctx.lineTo(axisTop.x, axisTop.y);
      ctx.stroke();

      ctx.font = "bold 12px system-ui, -apple-system, sans-serif";
      ctx.textAlign = outwardSign > 0 ? "left" : "right";
      ctx.textBaseline = "middle";
      let lastZLabel = null;
      for (let z = zStart; z <= zEnd + epsilon; z += step) {
        const point = this.project({ x: corner.x, y: corner.y, z });
        if (
          lastZLabel !== null &&
          Math.hypot(point.x - lastZLabel.x, point.y - lastZLabel.y) < TICK_MIN_GAP
        ) {
          continue;
        }
        lastZLabel = point;
        ctx.beginPath();
        ctx.moveTo(point.x, point.y);
        ctx.lineTo(point.x + outwardSign * 6, point.y);
        ctx.stroke();
        ctx.fillText(String(Math.round(z)), point.x + outwardSign * 10, point.y);
      }

      ctx.globalAlpha = 1;
      ctx.font = "bold 11px system-ui, -apple-system, sans-serif";
      ctx.textBaseline = "bottom";
      ctx.fillText("Z (mm)", axisTop.x + outwardSign * 10, axisTop.y - 8);

      ctx.restore();
    }

    drawDrawables() {
      const faces = [];
      const outlines = [];

      this.drawables.forEach((drawable) => {
        drawable.faces.forEach((face) => {
          const projected = face.map((vertexIndex) => this.project(drawable.vertices[vertexIndex]));
          const depth =
            projected.reduce((sum, point) => sum + point.depth, 0) / Math.max(projected.length, 1);
          faces.push({
            points: projected,
            depth,
            color: drawable.color,
            layer: drawable.layer,
            alpha: drawable.alpha,
          });
        });

        if (drawable.outline) {
          outlines.push({
            top: drawable.outline.top.map((point) => this.project(point)),
            bottom: drawable.outline.bottom.map((point) => this.project(point)),
            verticals: drawable.outline.verticals.map((pair) => pair.map((point) => this.project(point))),
            color: shadeColor(drawable.color, 0.42),
          });
        }
      });

      faces.sort((left, right) => {
        if (left.layer !== right.layer) {
          return left.layer - right.layer;
        }
        return left.depth - right.depth;
      });

      const ctx = this.context;
      const edgeColor = readThemeColors(this.root).text;
      faces.forEach((face) => {
        const shade =
          face.points.length >= 4
            ? shadeColor(face.color, 0.9 + (face.depth / (this.bounds.span || 1)) * 0.15)
            : face.color;

        ctx.beginPath();
        ctx.moveTo(face.points[0].x, face.points[0].y);
        for (let index = 1; index < face.points.length; index += 1) {
          ctx.lineTo(face.points[index].x, face.points[index].y);
        }
        ctx.closePath();
        ctx.fillStyle = shade;
        ctx.globalAlpha = face.alpha;
        ctx.fill();
        ctx.globalAlpha = 0.28;
        ctx.strokeStyle = edgeColor;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.globalAlpha = 1;
      });

      outlines.forEach((outline) => {
        ctx.strokeStyle = outline.color;
        ctx.lineWidth = 3;

        ctx.beginPath();
        ctx.moveTo(outline.top[0].x, outline.top[0].y);
        for (let index = 1; index < outline.top.length; index += 1) {
          ctx.lineTo(outline.top[index].x, outline.top[index].y);
        }
        ctx.closePath();
        ctx.stroke();

        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(outline.bottom[0].x, outline.bottom[0].y);
        for (let index = 1; index < outline.bottom.length; index += 1) {
          ctx.lineTo(outline.bottom[index].x, outline.bottom[index].y);
        }
        ctx.closePath();
        ctx.stroke();

        outline.verticals.forEach((pair) => {
          ctx.beginPath();
          ctx.moveTo(pair[0].x, pair[0].y);
          ctx.lineTo(pair[1].x, pair[1].y);
          ctx.stroke();
        });
      });
    }

    render() {
      if (!this.context) {
        return;
      }

      this.drawBackground();
      this.drawRuler();
      this.drawDrawables();
      this.drawZAxis();
      this.drawSizeReadout();
    }

    drawSizeReadout() {
      const ctx = this.context;
      const sizeX = this.bounds.max.x - this.bounds.min.x;
      const sizeY = this.bounds.max.y - this.bounds.min.y;
      const sizeZ = this.bounds.max.z - this.bounds.min.z;
      if (sizeX <= 0 && sizeY <= 0 && sizeZ <= 0) {
        return;
      }

      const fmt = (value) => (Math.round(value * 10) / 10).toFixed(1);
      const lines = [`${fmt(sizeX)} × ${fmt(sizeY)} × ${fmt(sizeZ)} mm`];

      const wells = this.drawables.filter(
        (d) => d.prototype.geometry && d.prototype.geometry.shape === "well",
      ).length;
      const tips = this.drawables.filter(
        (d) => d.prototype.geometry && d.prototype.geometry.shape === "tip_spot",
      ).length;
      if (wells > 0) {
        lines.push(`${wells} well${wells === 1 ? "" : "s"}`);
      } else if (tips > 0) {
        lines.push(`${tips} tip${tips === 1 ? "" : "s"} spot${tips === 1 ? "" : "s"}`);
      }

      ctx.save();
      ctx.textAlign = "left";
      ctx.textBaseline = "top";
      let y = 12;
      lines.forEach((line, index) => {
        ctx.font = index === 0
          ? "bold 13px system-ui, -apple-system, sans-serif"
          : "12px system-ui, -apple-system, sans-serif";
        ctx.fillStyle = readThemeColors(this.root).text;
        ctx.globalAlpha = index === 0 ? 0.95 : 0.7;
        ctx.fillText(line, 14, y);
        y += index === 0 ? 19 : 16;
      });
      ctx.restore();
    }
  }

  window.PLRGeometryViewer = {
    CanvasCatalogViewer,
  };
})();
