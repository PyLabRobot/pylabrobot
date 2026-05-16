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

  // V-bottom: straight upper cylinder + cone tapering to a centre apex.
  function createVBottomGeometry(size, segments) {
    const sx = size[0];
    const sy = size[1];
    const sz = size[2];
    const radiusX = sx / 2;
    const radiusY = sy / 2;
    const centerX = sx / 2;
    const centerY = sy / 2;
    const coneTopZ = sz * 0.2; // lower 20% of the depth is the V taper
    const vertices = [];
    const shoulder = [];
    const top = [];

    for (let index = 0; index < segments; index += 1) {
      const theta = (Math.PI * 2 * index) / segments;
      const x = centerX + Math.cos(theta) * radiusX;
      const y = centerY + Math.sin(theta) * radiusY;
      shoulder.push(vertices.length);
      vertices.push({ x, y, z: coneTopZ });
      top.push(vertices.length);
      vertices.push({ x, y, z: sz });
    }
    const apex = vertices.length;
    vertices.push({ x: centerX, y: centerY, z: 0 });

    const faces = [top.slice()];
    for (let index = 0; index < segments; index += 1) {
      const next = (index + 1) % segments;
      faces.push([shoulder[index], shoulder[next], top[next], top[index]]);
      faces.push([apex, shoulder[next], shoulder[index]]);
    }
    return { vertices, faces };
  }

  // U-bottom: straight upper cylinder + a rounded (quarter-ellipse) cap.
  function createUBottomGeometry(size, segments, lat) {
    const sx = size[0];
    const sy = size[1];
    const sz = size[2];
    const radiusX = sx / 2;
    const radiusY = sy / 2;
    const centerX = sx / 2;
    const centerY = sy / 2;
    const capH = Math.min((radiusX + radiusY) / 2, sz * 0.6);
    const vertices = [];
    const rings = [];

    for (let r = 1; r <= lat; r += 1) {
      const tt = r / lat; // fraction up the cap, 1 == equator
      const z = capH * tt;
      const factor = Math.sin((tt * Math.PI) / 2); // 0 -> full radius
      const ring = [];
      for (let index = 0; index < segments; index += 1) {
        const theta = (Math.PI * 2 * index) / segments;
        ring.push(vertices.length);
        vertices.push({
          x: centerX + Math.cos(theta) * radiusX * factor,
          y: centerY + Math.sin(theta) * radiusY * factor,
          z,
        });
      }
      rings.push(ring);
    }
    const top = [];
    for (let index = 0; index < segments; index += 1) {
      const theta = (Math.PI * 2 * index) / segments;
      top.push(vertices.length);
      vertices.push({
        x: centerX + Math.cos(theta) * radiusX,
        y: centerY + Math.sin(theta) * radiusY,
        z: sz,
      });
    }
    const apex = vertices.length;
    vertices.push({ x: centerX, y: centerY, z: 0 });

    const faces = [top.slice()];
    const first = rings[0];
    for (let index = 0; index < segments; index += 1) {
      const next = (index + 1) % segments;
      faces.push([apex, first[next], first[index]]);
    }
    for (let r = 0; r < lat - 1; r += 1) {
      const a = rings[r];
      const b = rings[r + 1];
      for (let index = 0; index < segments; index += 1) {
        const next = (index + 1) % segments;
        faces.push([a[index], a[next], b[next], b[index]]);
      }
    }
    const equator = rings[lat - 1];
    for (let index = 0; index < segments; index += 1) {
      const next = (index + 1) % segments;
      faces.push([equator[index], equator[next], top[next], top[index]]);
    }
    return { vertices, faces };
  }

  function createShapeGeometry(prototype) {
    const geometry = prototype.geometry || {};
    const size = displaySizeForPrototype(prototype);
    const type = prototype.type || "";

    if (geometry.shape === "well" && geometry.cross_section === "circle") {
      if (geometry.bottom === "V") {
        return createVBottomGeometry(size, 18);
      }
      if (geometry.bottom === "U") {
        return createUBottomGeometry(size, 18, 3);
      }
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

      this.homeButton = document.createElement("button");
      this.homeButton.type = "button";
      this.homeButton.className = "plr-geometry-home";
      this.homeButton.title = "Reset view";
      this.homeButton.setAttribute("aria-label", "Reset view");
      this.homeButton.innerHTML =
        '<svg width="28" height="28" viewBox="0 0 20 20" aria-hidden="true">' +
        '<path d="M10 1L1 9h3v8h5v-5h2v5h5V9h3L10 1z" fill="currentColor"/></svg>';
      this.root.appendChild(this.homeButton);
      this.drawables = [];
      this.bounds = computeBounds([]);
      this.defaultRotation = { yaw: -0.95 + Math.PI / 2, pitch: -0.9 };
      this.rotation = { ...this.defaultRotation };
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.pixelRatio = 1;
      this._proj = null; // per-frame projection cache (see _updateProjection)
      this._rafId = null; // pending requestAnimationFrame, for render coalescing
      this.isDragging = false;
      this.dragMode = "orbit";
      this.lastPointer = { x: 0, y: 0 };

      this.handlePointerDown = this.handlePointerDown.bind(this);
      this.handlePointerMove = this.handlePointerMove.bind(this);
      this.handlePointerUp = this.handlePointerUp.bind(this);
      this.handleWheel = this.handleWheel.bind(this);
      this.handleDoubleClick = this.handleDoubleClick.bind(this);
      this.handleHomeClick = this.handleHomeClick.bind(this);
      this.handleResize = this.handleResize.bind(this);
      this.resize = this.resize.bind(this);

      this.canvas.addEventListener("pointerdown", this.handlePointerDown);
      window.addEventListener("pointermove", this.handlePointerMove);
      window.addEventListener("pointerup", this.handlePointerUp);
      this.canvas.addEventListener("wheel", this.handleWheel, { passive: false });
      this.canvas.addEventListener("dblclick", this.handleDoubleClick);
      this.homeButton.addEventListener("click", this.handleHomeClick);
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
      this.homeButton.removeEventListener("click", this.handleHomeClick);
      window.removeEventListener("resize", this.handleResize);
      if (this.resizeObserver) {
        this.resizeObserver.disconnect();
      }
      if (this._rafId !== null) {
        window.cancelAnimationFrame(this._rafId);
        this._rafId = null;
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

    handleHomeClick() {
      this.resetView();
      this.homeButton.classList.add("plr-geometry-home--active");
      window.setTimeout(() => {
        this.homeButton.classList.remove("plr-geometry-home--active");
      }, 220);
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
      this.requestRender();
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
      this.requestRender();
    }

    handleResize() {
      const width = Math.max(this.root.clientWidth, 200);
      const height = Math.max(this.root.clientHeight, 200);
      this.pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.floor(width * this.pixelRatio);
      this.canvas.height = Math.floor(height * this.pixelRatio);
      this.canvas.style.width = `${width}px`;
      this.canvas.style.height = `${height}px`;
      this.requestRender();
    }

    resize() {
      this.handleResize();
    }

    // Per-frame transform constants. The camera is fixed for a whole render,
    // so the trig/scale are computed once here instead of per projected vertex.
    _updateProjection() {
      const c = this.bounds.center;
      this._proj = {
        cx: c.x,
        cy: c.y,
        cz: c.z,
        cosYaw: Math.cos(this.rotation.yaw),
        sinYaw: Math.sin(this.rotation.yaw),
        cosPitch: Math.cos(this.rotation.pitch),
        sinPitch: Math.sin(this.rotation.pitch),
        scale:
          (Math.min(this.canvas.width, this.canvas.height) / (this.bounds.span * 1.4)) * this.zoom,
        halfW: this.canvas.width / 2,
        halfH: this.canvas.height / 2,
        panX: this.pan.x,
        panY: this.pan.y,
      };
    }

    project(point) {
      const p = this._proj || (this._updateProjection(), this._proj);
      const cx = point.x - p.cx;
      const cy = point.y - p.cy;
      const cz = point.z - p.cz;
      const yawedX = cx * p.cosYaw - cy * p.sinYaw;
      const yawedY = cx * p.sinYaw + cy * p.cosYaw;
      const pitchedY = yawedY * p.cosPitch - cz * p.sinPitch;
      const pitchedZ = yawedY * p.sinPitch + cz * p.cosPitch;
      return {
        x: p.halfW + yawedX * p.scale + p.panX,
        y: p.halfH - pitchedY * p.scale + p.panY,
        depth: pitchedZ,
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

      // Tick marks: short stubs pointing outward toward each axis's labels.
      ctx.lineWidth = 2;
      const tickLen = step * 0.12;
      ctx.strokeStyle = AXIS_COLORS.x;
      for (let x = x0; x <= x1 + epsilon; x += step) {
        const tickStart = this.project({ x, y: y0, z: groundZ });
        const tickEnd = this.project({ x, y: y0 - tickLen, z: groundZ });
        ctx.beginPath();
        ctx.moveTo(tickStart.x, tickStart.y);
        ctx.lineTo(tickEnd.x, tickEnd.y);
        ctx.stroke();
      }
      ctx.strokeStyle = AXIS_COLORS.y;
      for (let y = y0; y <= y1 + epsilon; y += step) {
        const tickStart = this.project({ x: x0, y, z: groundZ });
        const tickEnd = this.project({ x: x0 - tickLen, y, z: groundZ });
        ctx.beginPath();
        ctx.moveTo(tickStart.x, tickStart.y);
        ctx.lineTo(tickEnd.x, tickEnd.y);
        ctx.stroke();
      }

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
      const items = [];
      // Outline segments share this layer so they depth-interleave with the
      // wells/tip-spots (Well/TipSpot get layer 4 in drawableLayer()) instead
      // of being painted on top as a flat overlay.
      const outlineLayer = 4;

      this.drawables.forEach((drawable) => {
        drawable.faces.forEach((face) => {
          const projected = face.map((vertexIndex) => this.project(drawable.vertices[vertexIndex]));
          const depth =
            projected.reduce((sum, point) => sum + point.depth, 0) / Math.max(projected.length, 1);
          items.push({
            kind: "poly",
            points: projected,
            depth,
            color: drawable.color,
            layer: drawable.layer,
            alpha: drawable.alpha,
          });
        });

        if (drawable.outline) {
          const color = shadeColor(drawable.color, 0.42);
          // Subdivide each edge so it depth-sorts against the well field in
          // many short pieces (one average depth per whole edge can't be
          // partly-behind / partly-in-front of 96 wells). Still an
          // approximation -- exact occlusion would need a depth buffer.
          const SUBDIV = 18;
          const addSegment = (a, b, width) => {
            let prev = this.project(a);
            for (let k = 1; k <= SUBDIV; k += 1) {
              const t = k / SUBDIV;
              const next = this.project({
                x: a.x + (b.x - a.x) * t,
                y: a.y + (b.y - a.y) * t,
                z: a.z + (b.z - a.z) * t,
              });
              items.push({
                kind: "stroke",
                a: prev,
                b: next,
                depth: (prev.depth + next.depth) / 2,
                color,
                width,
                layer: outlineLayer,
              });
              prev = next;
            }
          };
          const ring = (pts, width) => {
            for (let i = 0; i < pts.length; i += 1) {
              addSegment(pts[i], pts[(i + 1) % pts.length], width);
            }
          };
          ring(drawable.outline.top, 3);
          ring(drawable.outline.bottom, 1.5);
          drawable.outline.verticals.forEach((pair) => addSegment(pair[0], pair[1], 1.5));
        }
      });

      items.sort((left, right) => {
        if (left.layer !== right.layer) {
          return left.layer - right.layer;
        }
        return left.depth - right.depth;
      });

      const ctx = this.context;
      const edgeColor = readThemeColors(this.root).text;
      items.forEach((item) => {
        if (item.kind === "stroke") {
          ctx.globalAlpha = 1;
          ctx.strokeStyle = item.color;
          ctx.lineWidth = item.width;
          ctx.beginPath();
          ctx.moveTo(item.a.x, item.a.y);
          ctx.lineTo(item.b.x, item.b.y);
          ctx.stroke();
          return;
        }

        const shade =
          item.points.length >= 4
            ? shadeColor(item.color, 0.9 + (item.depth / (this.bounds.span || 1)) * 0.15)
            : item.color;

        ctx.beginPath();
        ctx.moveTo(item.points[0].x, item.points[0].y);
        for (let index = 1; index < item.points.length; index += 1) {
          ctx.lineTo(item.points[index].x, item.points[index].y);
        }
        ctx.closePath();
        ctx.fillStyle = shade;
        ctx.globalAlpha = item.alpha;
        ctx.fill();
        ctx.globalAlpha = 0.28;
        ctx.strokeStyle = edgeColor;
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.globalAlpha = 1;
      });
    }

    requestRender() {
      if (this._rafId !== null) {
        return;
      }
      this._rafId = window.requestAnimationFrame(() => {
        this._rafId = null;
        this.render();
      });
    }

    render() {
      if (!this.context) {
        return;
      }

      this._updateProjection();
      this.drawBackground();
      this.drawRuler();
      this.drawDrawables();
      this.drawZAxis();
      this.drawSizeReadout();
      this.drawOrientationCube();
    }

    drawSizeReadout() {
      const ctx = this.context;
      const sizeX = this.bounds.max.x - this.bounds.min.x;
      const sizeY = this.bounds.max.y - this.bounds.min.y;
      const sizeZ = this.bounds.max.z - this.bounds.min.z;
      if (sizeX <= 0 && sizeY <= 0 && sizeZ <= 0) {
        return;
      }

      const fmt = (value) => (Math.round(value * 100) / 100).toFixed(2);
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
        lines.push(`${tips} Tip Spot${tips === 1 ? "" : "s"}`);
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

    drawOrientationCube() {
      const ctx = this.context;
      const dpr = this.pixelRatio;
      const halfCss = 31.5; // half widget size in CSS px
      const marginCss = 16;
      const topCss = 14; // top-right corner, right of the home button
      const cx = this.canvas.width - (marginCss + halfCss) * dpr;
      const cy = (topCss + halfCss) * dpr;
      const s = halfCss * dpr * 0.6; // projected half-extent of the cube

      const cosYaw = Math.cos(this.rotation.yaw);
      const sinYaw = Math.sin(this.rotation.yaw);
      const cosPitch = Math.cos(this.rotation.pitch);
      const sinPitch = Math.sin(this.rotation.pitch);

      const projectCubePoint = (p) => {
        const yx = p.x * cosYaw - p.y * sinYaw;
        const yy = p.x * sinYaw + p.y * cosYaw;
        const py = yy * cosPitch - p.z * sinPitch;
        const pz = yy * sinPitch + p.z * cosPitch;
        return { x: cx + yx * s, y: cy - py * s, depth: pz };
      };

      const add = (a, b, t) => ({
        x: a.x + b.x * t,
        y: a.y + b.y * t,
        z: a.z + b.z * t,
      });
      const depthAt = (p) => projectCubePoint(p).depth;

      // Chamfered cube: each corner cut by a plane -> 6 octagon faces + 8
      // corner triangles. `chamfer` is the fraction of each edge removed.
      const chamfer = 0.504;
      const k = 1 - chamfer;
      const ring = [
        [1, k], [k, 1], [-k, 1], [-1, k],
        [-1, -k], [-k, -1], [k, -1], [1, -k],
      ];
      // (b, d) in-plane coords -> 3D, per axis (0=x,1=y,2=z), at axis=sg.
      const place = {
        0: (sg, b, d) => ({ x: sg, y: b, z: d }),
        1: (sg, b, d) => ({ x: d, y: sg, z: b }),
        2: (sg, b, d) => ({ x: b, y: d, z: sg }),
      };
      const inPlaneU = { 0: { x: 0, y: 1, z: 0 }, 1: { x: 1, y: 0, z: 0 }, 2: { x: 1, y: 0, z: 0 } };
      const inPlaneW = { 0: { x: 0, y: 0, z: 1 }, 1: { x: 0, y: 0, z: 1 }, 2: { x: 0, y: 1, z: 0 } };
      const axisNormal = (axis, sg) => ({
        x: axis === 0 ? sg : 0,
        y: axis === 1 ? sg : 0,
        z: axis === 2 ? sg : 0,
      });

      const faces = [];

      [
        // Faces colored by the axis their long edge spans: FRONT/BACK run
        // along X (red), LEFT/RIGHT along Y (green), TOP/BOTTOM up Z (blue).
        { axis: 2, sg: 1, label: "TOP", color: AXIS_COLORS.z },
        { axis: 2, sg: -1, label: "BOTTOM", color: AXIS_COLORS.z },
        { axis: 0, sg: 1, label: "RIGHT", color: AXIS_COLORS.y },
        { axis: 0, sg: -1, label: "LEFT", color: AXIS_COLORS.y },
        { axis: 1, sg: 1, label: "BACK", color: AXIS_COLORS.x },
        { axis: 1, sg: -1, label: "FRONT", color: AXIS_COLORS.x },
      ].forEach((f) => {
        const pts3 = ring.map(([b, d]) => place[f.axis](f.sg, b, d));
        const center3 = axisNormal(f.axis, f.sg);
        const normal = center3;
        const visible = depthAt(add(center3, normal, 0.02)) > depthAt(center3);
        const pts = pts3.map(projectCubePoint);
        const depth = pts.reduce((sum, p) => sum + p.depth, 0) / pts.length;
        faces.push({
          kind: "oct",
          label: f.label,
          color: f.color,
          pts,
          depth,
          visible,
          center3,
          uW: inPlaneU[f.axis],
          wW: inPlaneW[f.axis],
        });
      });

      [-1, 1].forEach((sx) => {
        [-1, 1].forEach((sy) => {
          [-1, 1].forEach((sz) => {
            const pts3 = [
              { x: sx * k, y: sy, z: sz },
              { x: sx, y: sy * k, z: sz },
              { x: sx, y: sy, z: sz * k },
            ];
            const centroid = {
              x: (pts3[0].x + pts3[1].x + pts3[2].x) / 3,
              y: (pts3[0].y + pts3[1].y + pts3[2].y) / 3,
              z: (pts3[0].z + pts3[1].z + pts3[2].z) / 3,
            };
            const inv = 1 / Math.sqrt(3);
            const normal = { x: sx * inv, y: sy * inv, z: sz * inv };
            const visible = depthAt(add(centroid, normal, 0.02)) > depthAt(centroid);
            const pts = pts3.map(projectCubePoint);
            const depth = pts.reduce((sum, p) => sum + p.depth, 0) / 3;
            faces.push({ kind: "chamfer", pts, depth, visible });
          });
        });
      });

      faces.sort((a, b) => a.depth - b.depth);

      const theme = readThemeColors(this.root);
      ctx.save();
      ctx.lineJoin = "round";
      faces.forEach((f) => {
        ctx.beginPath();
        ctx.moveTo(f.pts[0].x, f.pts[0].y);
        for (let i = 1; i < f.pts.length; i += 1) {
          ctx.lineTo(f.pts[i].x, f.pts[i].y);
        }
        ctx.closePath();

        if (f.kind === "chamfer") {
          ctx.globalAlpha = f.visible ? 0.5 : 0.12;
          ctx.fillStyle = "rgba(150, 165, 180, 0.9)";
          ctx.fill();
          ctx.globalAlpha = f.visible ? 0.45 : 0.12;
          ctx.lineWidth = 1;
          ctx.strokeStyle = "rgba(120, 135, 150, 0.9)";
          ctx.stroke();
          return;
        }

        ctx.globalAlpha = f.visible ? 0.82 : 0.18;
        ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
        ctx.fill();
        ctx.globalAlpha = f.visible ? 0.9 : 0.25;
        ctx.lineWidth = Math.max(1, dpr);
        ctx.strokeStyle = f.color;
        ctx.stroke();

        if (!f.visible) {
          return;
        }
        const fc = projectCubePoint(f.center3);
        const pU = projectCubePoint(add(f.center3, f.uW, 1));
        const pW = projectCubePoint(add(f.center3, f.wW, 1));
        let ux = pU.x - fc.x;
        let uy = pU.y - fc.y;
        let vx = pW.x - fc.x;
        let vy = pW.y - fc.y;
        const exLen = Math.hypot(ux, uy);
        const eyLen = Math.hypot(vx, vy);
        if (exLen > 4 && eyLen > 4) {
          // Unit in-plane basis keeps the face's shear/foreshorten so the
          // label reads as painted on the surface; sign-normalize so it
          // stays roughly upright rather than mirrored/upside-down.
          ux /= exLen;
          uy /= exLen;
          vx /= eyLen;
          vy /= eyLen;
          if (ux < 0) {
            ux = -ux;
            uy = -uy;
          }
          if (vy < 0) {
            vx = -vx;
            vy = -vy;
          }
          const fontPx = Math.min(exLen, eyLen) * 0.486;
          ctx.save();
          ctx.setTransform(ux, uy, vx, vy, fc.x, fc.y);
          ctx.globalAlpha = 0.95;
          ctx.fillStyle = theme.text;
          ctx.font = `bold ${fontPx}px system-ui, -apple-system, sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "middle";
          ctx.fillText(f.label, 0, 0);
          ctx.restore();
        }
      });
      ctx.restore();
    }
  }

  window.PLRGeometryViewer = {
    CanvasCatalogViewer,
  };
})();
