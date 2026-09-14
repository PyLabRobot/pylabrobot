import * as THREE from "three";
import { button, input } from "./dom.js";

/**
 * Wires up the GIF recording panel.
 *
 * Recording is a render pass of its own, so it needs the renderer, the scene and the camera. It
 * owns everything else it touches. The returned `tick` belongs in the animation loop, after the
 * frame has been drawn.
 *
 * @param {{renderer: any, view: any, camera: any}} deps
 * @returns {{isRecording: () => boolean, tick: () => void}}
 */
export function initGif({ renderer, view, camera }) {
  let recording = false;
  let capturedFrames = [];
  let frameInterval = 8;
  let captureDue = 0;
  let renderedGif = null;
  let captureBroken = false;

  const gifBoxes = {
    start: document.getElementById("gif-start"),
    recording: document.getElementById("gif-recording"),
    processing: document.getElementById("gif-processing"),
    download: document.getElementById("gif-download"),
  };

  function showGifBox(which) {
    for (const [name, box] of Object.entries(gifBoxes)) {
      box.style.display = name === which ? "flex" : "none";
    }
  }

  document.getElementById("gif-frame-rate").addEventListener("input", (e) => {
    frameInterval = Number(/** @type {HTMLInputElement} */ (e.target).value);
    document.getElementById("current-value").textContent = `Frame Interval: ${frameInterval}`;
  });

  const gifNotice = document.createElement("p");
  gifNotice.style.cssText = "font-size:12px;color:#b02a37;line-height:1.4;text-align:center;";
  document.getElementById("gif-panel").appendChild(gifNotice);

  document.getElementById("start-recording-button").addEventListener("click", () => {
    if (captureBroken) return;
    gifNotice.textContent = "";
    capturedFrames = [];
    recording = true;
    captureDue = 0;
    showGifBox("recording");
  });

  document.getElementById("stop-recording-button").addEventListener("click", () => {
    recording = false;
    showGifBox("processing");
    const progress = document.getElementById("progressBar");
    if (!capturedFrames.length) {
      progress.textContent = "No frames captured.";
      setTimeout(() => showGifBox("start"), 1500);
      return;
    }
    const gif = new GIF({
      workers: 4,
      workerScript: "./vendor/gif.worker.js",
      background: "#FFFFFF",
      width: capturedFrames[0].width,
      height: capturedFrames[0].height,
    });
    for (const frameCanvas of capturedFrames) {
      gif.addFrame(frameCanvas, { delay: Math.max(80, frameInterval * 20) });
    }
    gif.on("progress", (p) => (progress.textContent = `Rendering: ${Math.round(p * 100)}%`));
    gif.on("finished", (blob) => {
      renderedGif = blob;
      showGifBox("download");
    });
    gif.render();
  });

  document.getElementById("gif-download-button").addEventListener("click", () => {
    if (!renderedGif) return;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(renderedGif);
    link.download = input("fileName").value || "plr-visualizer.gif";
    link.click();
    URL.revokeObjectURL(link.href);
    showGifBox("start");
  });

  // Copying the canvas directly comes back blank: the drawing buffer is gone by the time a copy
  // runs. Rendering the frame into a render target and reading it back works on both backends,
  // and it is the same render call, so the frame is exactly what is on screen. Only the viewport
  // is captured, not the floating panels over it.
  let captureTarget = null;
  let capturing = false;

  async function captureFrame() {
    if (capturing) return;
    capturing = true;
    try {
      const width = renderer.domElement.width;
      const height = renderer.domElement.height;
      if (!captureTarget || captureTarget.width !== width || captureTarget.height !== height) {
        captureTarget?.dispose();
        captureTarget = new THREE.RenderTarget(width, height);
      }
      renderer.setRenderTarget(captureTarget);
      renderer.render(view, camera);
      const pixels = new Uint8Array(width * height * 4);
      await renderer.readRenderTargetPixelsAsync(captureTarget, 0, 0, width, height, pixels);
      renderer.setRenderTarget(null);

      const scratch = document.createElement("canvas");
      scratch.width = width;
      scratch.height = height;
      const context = scratch.getContext("2d");
      const image = context.createImageData(width, height);
      // Readback starts at the bottom-left, so the rows go back in upside down.
      for (let row = 0; row < height; row++) {
        const from = (height - 1 - row) * width * 4;
        image.data.set(pixels.subarray(from, from + width * 4), row * width * 4);
      }
      context.putImageData(image, 0, 0);
      capturedFrames.push(scratch);
    } catch (error) {
      // three's WebGL2 backend cannot read a render target back (r180), so recording only works
      // on the WebGPU path. Say so rather than producing an empty GIF.
      console.warn("frame capture failed", error);
      recording = false;
      captureBroken = true;
      showGifBox("start");
      gifNotice.textContent =
        "Recording needs the WebGPU backend; this browser fell back to WebGL2.";
      button("start-recording-button").disabled = true;
    } finally {
      capturing = false;
    }
  }

  showGifBox("start");

  return {
    isRecording: () => recording,
    tick() {
      if (recording && performance.now() >= captureDue) {
        captureFrame();
        captureDue = performance.now() + Math.max(80, frameInterval * 20);
      }
    },
  };
}
