"""Simulated BlackFly camera for testing without hardware.

Layer: Simulated camera driver (no native dependencies)
Role: Drop-in replacement for AravisCamera that returns synthetic images.
Adjacent layers:
  - Above: CytationAravisBackend or test code uses this instead of AravisCamera
  - Below: No hardware — generates images from noise + parameters

This module provides a simulated camera that implements the same public API
as AravisCamera but requires no Aravis library, no GObject introspection,
and no physical camera. It is used for:
  - Unit and integration tests (pytest in CI)
  - Development without hardware access
  - Demonstrating the camera API

The simulated images have brightness that scales with exposure and gain,
giving a rough visual indication that parameters are being applied. This
is NOT a physics-accurate simulation — it's enough to verify the API works.

Architecture label: **[Proposed]** — Simulated backend for Aravis driver.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

from .aravis_camera import CameraInfo

logger = logging.getLogger(__name__)


class AravisSimulated:
    """Simulated BlackFly camera — same API as AravisCamera, no native deps.

    Returns synthetic Mono8 images with brightness proportional to exposure
    and gain settings. All parameter operations store and return values
    without hardware access.

    Constitution Principle VII (Simulator Parity): This class implements the
    same interface as AravisCamera so it can be used as a drop-in replacement
    in tests and development environments.

    Usage:
      camera = AravisSimulated(width=720, height=540)
      await camera.setup("SIM-001")
      await camera.set_exposure(10.0)
      image = await camera.trigger()  # synthetic noise image
      await camera.stop()
    """

    def __init__(self, width: int = 720, height: int = 540) -> None:
        self._default_width = width
        self._default_height = height
        self._width: int = 0
        self._height: int = 0
        self._serial_number: Optional[str] = None
        self._exposure_ms: float = 10.0
        self._gain: float = 1.0
        self._auto_exposure: str = "off"
        self._acquiring: bool = False
        self._setup_done: bool = False

    @property
    def width(self) -> int:
        """Image width in pixels (read-only)."""
        return self._width

    @property
    def height(self) -> int:
        """Image height in pixels (read-only)."""
        return self._height

    async def setup(self, serial_number: str) -> None:
        """Simulate camera connection.

        Args:
            serial_number: Any string — stored for get_device_info().
        """
        self._serial_number = serial_number
        self._width = self._default_width
        self._height = self._default_height
        self._exposure_ms = 10.0
        self._gain = 1.0
        self._auto_exposure = "off"
        self._acquiring = False
        self._setup_done = True
        logger.info(
            "AravisSimulated: Connected to simulated camera (SN: %s), %dx%d",
            serial_number,
            self._width,
            self._height,
        )

    def start_acquisition(self) -> None:
        """Simulate starting acquisition."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        if self._acquiring:
            return
        self._acquiring = True

    def stop_acquisition(self) -> None:
        """Simulate stopping acquisition."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        if not self._acquiring:
            return
        self._acquiring = False

    async def trigger(self, timeout_ms: int = 5000) -> np.ndarray:
        """Return a synthetic Mono8 image with brightness scaled by exposure and gain.

        Brightness formula: base = min(255, exposure_ms * 2.5 * (1 + gain / 10))
        Gaussian noise with sigma=10 is added, then clipped to uint8 range.

        Args:
            timeout_ms: Ignored in simulation.

        Returns:
            numpy.ndarray: Synthetic 2D uint8 array (height × width).
        """
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")

        self.start_acquisition()

        # Base brightness scales with exposure and gain.
        base = min(255.0, self._exposure_ms * 2.5 * (1.0 + self._gain / 10.0))
        noise = np.random.normal(loc=base, scale=10.0, size=(self._height, self._width))
        image = np.clip(noise, 0, 255).astype(np.uint8)

        self.stop_acquisition()
        return image

    async def stop(self) -> None:
        """Simulate camera release."""
        if self._acquiring:
            self.stop_acquisition()
        self._serial_number = None
        self._width = 0
        self._height = 0
        self._acquiring = False
        self._setup_done = False

    async def set_exposure(self, exposure_ms: float) -> None:
        """Store exposure value (milliseconds)."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        self._auto_exposure = "off"
        self._exposure_ms = exposure_ms

    async def get_exposure(self) -> float:
        """Return stored exposure value (milliseconds)."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        return self._exposure_ms

    async def set_gain(self, gain: float) -> None:
        """Store gain value."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        self._gain = gain

    async def get_gain(self) -> float:
        """Return stored gain value."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        return self._gain

    async def set_auto_exposure(self, mode: str) -> None:
        """Store auto-exposure mode ("off", "once", "continuous")."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        mode_lower = mode.lower()
        if mode_lower not in ("off", "once", "continuous"):
            raise ValueError(
                f"Invalid auto-exposure mode '{mode}'. "
                "Use 'off', 'once', or 'continuous'."
            )
        self._auto_exposure = mode_lower

    async def set_pixel_format(self, fmt: Optional[int] = None) -> None:
        """No-op in simulation — always returns Mono8."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")

    def get_device_info(self) -> CameraInfo:
        """Return simulated camera info."""
        if not self._setup_done:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        return CameraInfo(
            serial_number=self._serial_number or "SIM-000",
            model_name="Simulated BlackFly S",
            vendor="Simulated",
            firmware_version="1.0.0",
            connection_type="USB3",
        )

    @staticmethod
    def enumerate_cameras() -> list[CameraInfo]:
        """Return a single simulated camera."""
        return [
            CameraInfo(
                serial_number="SIM-001",
                model_name="Simulated BlackFly S",
                vendor="Simulated",
                firmware_version="1.0.0",
                connection_type="USB3",
            )
        ]
