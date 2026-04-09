"""Standalone BlackFly camera driver using Aravis (GenICam/USB3 Vision).

Layer: Camera driver (standalone, no PLR dependencies except numpy)
Role: Replaces PySpin for image acquisition from FLIR/Teledyne BlackFly cameras.
Adjacent layers:
  - Above: CytationAravisBackend delegates camera operations here
  - Below: Aravis library (via PyGObject) talks to camera via USB3 Vision/GenICam

This module provides a pure-Aravis alternative to PySpin for controlling BlackFly
cameras. It eliminates the Spinnaker SDK dependency and the Python 3.10 version cap
that PySpin imposes. Aravis talks directly to the camera via the GenICam standard
over USB3 Vision — no vendor runtime needed.

Architecture label: **[Proposed]** — Aravis as alternative to PySpin for PLR.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Aravis is an optional dependency. It requires:
#   - System library: brew install aravis (macOS) or apt install libaravis-dev (Linux)
#   - Python bindings: pip install PyGObject
# If not installed, AravisCamera.setup() will raise ImportError with instructions.
try:
    import gi
    gi.require_version("Aravis", "0.8")
    from gi.repository import Aravis  # type: ignore[attr-defined]
    HAS_ARAVIS = True
except (ImportError, ValueError):
    HAS_ARAVIS = False
    Aravis = None  # type: ignore[assignment]

# Number of pre-allocated buffers for the Aravis stream.
# For single-frame software-triggered capture, 5 is more than sufficient.
_BUFFER_COUNT = 5


@dataclass
class CameraInfo:
    """Discovery result for a GenICam-compatible camera.

    Returned by AravisCamera.enumerate_cameras() and get_device_info().
    Contains identification and connection metadata — no hardware access needed
    after discovery.
    """

    serial_number: str
    model_name: str
    vendor: str
    firmware_version: str
    connection_type: str  # "USB3" for the Cytation 5 BlackFly


class AravisCamera:
    """BlackFly camera driver using Aravis for GenICam access over USB3 Vision.

    This class wraps all Aravis/GenICam operations for single-frame image
    acquisition with software triggering. It mirrors the camera methods that
    PLR's CytationBackend calls on PySpin:

      PySpin                    → AravisCamera
      _set_up_camera()          → setup(serial_number)
      _stop_camera()            → stop()
      start_acquisition()       → start_acquisition()
      stop_acquisition()        → stop_acquisition()
      set_exposure(ms)          → set_exposure(ms)
      set_gain(val)             → set_gain(val)
      set_auto_exposure(mode)   → set_auto_exposure(mode)
      _acquire_image()          → trigger(timeout_ms)

    GenICam primer for non-camera-experts:
      GenICam is a standard that defines how camera features (exposure, gain,
      trigger, etc.) are named and accessed. Every compliant camera publishes
      an XML file describing its features as "nodes." Aravis reads this XML
      and lets you get/set node values by name (e.g., "ExposureTime", "Gain").
      This is the same mechanism PySpin uses internally — Aravis just provides
      a different Python API to access it.

    Buffer management:
      Aravis uses a producer-consumer model with pre-allocated buffers.
      During setup(), we allocate a small pool of buffers and push them to
      the stream. When we trigger a capture, Aravis fills a buffer with image
      data. We pop the buffer, copy the data to a numpy array, and push the
      buffer back to the pool for reuse. The copy is necessary because the
      buffer memory is owned by Aravis and will be reused.

    Usage:
      camera = AravisCamera()
      await camera.setup("12345678")
      await camera.set_exposure(10.0)  # 10 ms
      image = await camera.trigger()    # numpy array, Mono8
      await camera.stop()
    """

    def __init__(self) -> None:
        self._camera: Optional[object] = None  # Aravis.Camera
        self._device: Optional[object] = None  # Aravis.Device
        self._stream: Optional[object] = None  # Aravis.Stream
        self._serial_number: Optional[str] = None
        self._acquiring: bool = False
        self._width: int = 0
        self._height: int = 0
        self._payload_size: int = 0

    @property
    def width(self) -> int:
        """Image width in pixels (read-only, from camera default)."""
        return self._width

    @property
    def height(self) -> int:
        """Image height in pixels (read-only, from camera default)."""
        return self._height

    async def setup(self, serial_number: str) -> None:
        """Connect to camera, configure software trigger, allocate buffers.

        This mirrors CytationBackend._set_up_camera() but uses Aravis instead
        of PySpin. The software trigger configuration matches PLR's pattern:
        TriggerSelector=FrameStart, TriggerSource=Software, TriggerMode=On.

        Args:
            serial_number: Camera serial number (e.g., "12345678"). Use
                enumerate_cameras() to discover available cameras.

        Raises:
            ImportError: If Aravis/PyGObject is not installed.
            RuntimeError: If camera cannot be found or connected.
        """
        if not HAS_ARAVIS:
            raise ImportError(
                "Aravis is not installed. Install it with:\n"
                "  macOS:   brew install aravis && pip install PyGObject\n"
                "  Linux:   sudo apt-get install libaravis-dev gir1.2-aravis-0.8 "
                "&& pip install PyGObject\n"
                "  Windows: pacman -S mingw-w64-x86_64-aravis (in MSYS2)"
            )

        self._serial_number = serial_number

        # Connect to camera by serial number.
        # Aravis.Camera.new() expects either the full device ID string or None
        # (for first available). We search the device list to find the matching
        # device ID for the given serial number.
        Aravis.update_device_list()
        device_id_to_connect = None
        for i in range(Aravis.get_n_devices()):
            dev_serial = Aravis.get_device_serial_nbr(i) or ""
            dev_id = Aravis.get_device_id(i) or ""
            if serial_number in (dev_serial, dev_id) or serial_number in dev_id:
                device_id_to_connect = dev_id
                break

        if device_id_to_connect is None:
            raise RuntimeError(
                f"Camera with serial '{serial_number}' not found. "
                f"Available devices: {[Aravis.get_device_id(i) for i in range(Aravis.get_n_devices())]}"
            )

        try:
            self._camera = Aravis.Camera.new(device_id_to_connect)
        except Exception as e:
            raise RuntimeError(
                f"Failed to connect to camera '{device_id_to_connect}'. "
                f"Is the camera in use by another process? Error: {e}"
            ) from e

        self._device = self._camera.get_device()

        # Configure software trigger mode.
        # GenICam nodes: TriggerSelector, TriggerSource, TriggerMode are
        # standard SFNC (Standard Features Naming Convention) names.
        self._device.set_string_feature_value("TriggerSelector", "FrameStart")
        self._device.set_string_feature_value("TriggerSource", "Software")
        self._device.set_string_feature_value("TriggerMode", "On")

        # Read image dimensions and payload size from camera.
        self._width = self._camera.get_region()[2]   # x, y, width, height
        self._height = self._camera.get_region()[3]
        self._payload_size = self._camera.get_payload()

        # BlackFly/Flea3 cameras need a delay after trigger mode change.
        # Same workaround as CytationBackend (PLR commit 226e6d41).
        await asyncio.sleep(1)

        # Create stream and pre-allocate buffer pool.
        # Aravis requires buffers to be pushed to the stream before acquisition.
        # We allocate a small pool (5 buffers) — for single-frame software
        # trigger, we only use one at a time but the pool prevents starvation.
        self._stream = self._camera.create_stream(None, None)
        for _ in range(_BUFFER_COUNT):
            self._stream.push_buffer(Aravis.Buffer.new_allocate(self._payload_size))

        logger.info(
            "AravisCamera: Connected to %s (SN: %s), %dx%d",
            self._device.get_string_feature_value("DeviceModelName"),
            serial_number,
            self._width,
            self._height,
        )

    def start_acquisition(self) -> None:
        """Begin camera acquisition (no-op if already acquiring).

        Mirrors CytationBackend.start_acquisition(). After this call, the
        camera is ready to receive software triggers and produce image buffers.
        """
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        if self._acquiring:
            return
        self._camera.start_acquisition()
        self._acquiring = True

    def stop_acquisition(self) -> None:
        """End camera acquisition (no-op if not acquiring).

        Mirrors CytationBackend.stop_acquisition().
        """
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        if not self._acquiring:
            return
        self._camera.stop_acquisition()
        self._acquiring = False

    async def trigger(self, timeout_ms: int = 5000) -> np.ndarray:
        """Capture a single frame: start → software trigger → grab → stop.

        This mirrors CytationBackend._acquire_image() but uses Aravis buffer
        management instead of PySpin's GetNextImage().

        The flow:
          1. Start acquisition (if not already active)
          2. Execute TriggerSoftware GenICam command
          3. Pop a filled buffer from the stream (with timeout)
          4. Copy buffer data to numpy array (Mono8 → uint8)
          5. Push buffer back to pool for reuse
          6. Stop acquisition

        Args:
            timeout_ms: Maximum time to wait for image buffer, in milliseconds.
                Default 5000 (5 seconds).

        Returns:
            numpy.ndarray: Image as 2D uint8 array (height × width), Mono8 format.

        Raises:
            RuntimeError: If camera not initialized or capture times out.
        """
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")

        self.start_acquisition()

        try:
            # Send software trigger command.
            # This is equivalent to PySpin's TriggerSoftware.Execute().
            self._device.execute_command("TriggerSoftware")

            # Pop the filled buffer from the stream.
            # timeout_pop_buffer takes microseconds, so convert from ms.
            buffer = self._stream.timeout_pop_buffer(timeout_ms * 1000)
            if buffer is None:
                raise RuntimeError(
                    f"Camera capture timed out after {timeout_ms}ms. "
                    "Is the camera connected and trigger mode configured?"
                )

            # Extract image data and copy to numpy array.
            # We must copy because the buffer memory is owned by Aravis and
            # will be reused when we push the buffer back to the pool.
            data = buffer.get_data()
            image = np.frombuffer(data, dtype=np.uint8).reshape(
                self._height, self._width
            ).copy()

            # Return buffer to pool for reuse.
            self._stream.push_buffer(buffer)

            return image
        finally:
            self.stop_acquisition()

    async def stop(self) -> None:
        """Release camera and free all Aravis resources.

        Mirrors CytationBackend._stop_camera(). Safe to call at any point —
        stops acquisition if active, resets trigger mode, releases camera.
        """
        try:
            if self._acquiring and self._camera is not None:
                self.stop_acquisition()

            if self._device is not None:
                try:
                    self._device.set_string_feature_value("TriggerMode", "Off")
                except Exception:
                    pass  # Camera may already be disconnected
        finally:
            self._camera = None
            self._device = None
            self._stream = None
            self._serial_number = None
            self._acquiring = False
            self._width = 0
            self._height = 0
            self._payload_size = 0

    async def set_exposure(self, exposure_ms: float) -> None:
        """Set exposure time in milliseconds.

        Disables auto-exposure first, then sets the GenICam ExposureTime node.
        PLR's CytationBackend uses milliseconds externally — GenICam uses
        microseconds internally. This method handles the conversion.

        Args:
            exposure_ms: Exposure time in milliseconds (e.g., 10.0 = 10 ms).
        """
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        # Disable auto-exposure before setting manual value.
        self._device.set_string_feature_value("ExposureAuto", "Off")
        # GenICam ExposureTime is in microseconds.
        exposure_us = exposure_ms * 1000.0
        self._camera.set_exposure_time(exposure_us)

    async def get_exposure(self) -> float:
        """Read current exposure time in milliseconds (from hardware, not cached)."""
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        exposure_us = self._camera.get_exposure_time()
        return exposure_us / 1000.0

    async def set_gain(self, gain: float) -> None:
        """Set gain value.

        Disables auto-gain first, then sets the GenICam Gain node.
        The gain range depends on the camera model (typically 0-30 for BlackFly).

        Args:
            gain: Gain value (e.g., 1.0).
        """
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        self._device.set_string_feature_value("GainAuto", "Off")
        self._camera.set_gain(gain)

    async def get_gain(self) -> float:
        """Read current gain value (from hardware, not cached)."""
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        return self._camera.get_gain()

    async def set_auto_exposure(self, mode: str) -> None:
        """Set auto-exposure mode.

        Args:
            mode: One of "off", "once", "continuous". Maps to GenICam
                ExposureAuto node values: Off, Once, Continuous.
        """
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        mode_map = {"off": "Off", "once": "Once", "continuous": "Continuous"}
        aravis_mode = mode_map.get(mode.lower())
        if aravis_mode is None:
            raise ValueError(
                f"Invalid auto-exposure mode '{mode}'. Use 'off', 'once', or 'continuous'."
            )
        self._device.set_string_feature_value("ExposureAuto", aravis_mode)

    async def set_pixel_format(self, fmt: Optional[int] = None) -> None:
        """Set pixel format. Default is Mono8.

        Must be called before start_acquisition(). The format value is an
        Aravis pixel format constant (e.g., Aravis.PIXEL_FORMAT_MONO_8).

        Args:
            fmt: Aravis pixel format constant. If None, uses Mono8.
        """
        if self._camera is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        if fmt is None:
            if HAS_ARAVIS:
                fmt = Aravis.PIXEL_FORMAT_MONO_8
            else:
                return
        self._camera.set_pixel_format(fmt)

    def get_device_info(self) -> CameraInfo:
        """Read camera identification from GenICam nodes.

        Returns model name, serial number, vendor, and firmware version
        without requiring acquisition to be active.

        Returns:
            CameraInfo with fields populated from the camera's GenICam XML.
        """
        if self._device is None:
            raise RuntimeError("Camera is not initialized. Call setup() first.")
        return CameraInfo(
            serial_number=self._device.get_string_feature_value("DeviceSerialNumber"),
            model_name=self._device.get_string_feature_value("DeviceModelName"),
            vendor=self._device.get_string_feature_value("DeviceVendorName"),
            firmware_version=self._device.get_string_feature_value(
                "DeviceFirmwareVersion"
            ),
            connection_type="USB3",
        )

    @staticmethod
    def enumerate_cameras() -> list[CameraInfo]:
        """List all connected GenICam-compatible cameras.

        Uses Aravis device enumeration — finds cameras across USB3 Vision
        and GigE Vision transports (though this driver targets USB3 only).

        Returns:
            List of CameraInfo for each detected camera. Empty list if none
            found (not an error — matches PLR's graceful enumeration pattern).

        Raises:
            ImportError: If Aravis/PyGObject is not installed.
        """
        if not HAS_ARAVIS:
            raise ImportError(
                "Aravis is not installed. Install it with:\n"
                "  macOS:   brew install aravis && pip install PyGObject\n"
                "  Linux:   sudo apt-get install libaravis-dev gir1.2-aravis-0.8 "
                "&& pip install PyGObject\n"
                "  Windows: pacman -S mingw-w64-x86_64-aravis (in MSYS2)"
            )

        Aravis.update_device_list()
        n_devices = Aravis.get_n_devices()
        cameras: list[CameraInfo] = []

        for i in range(n_devices):
            # Parse info from device ID string without opening the camera.
            # Opening the camera here would lock the USB device and prevent
            # a subsequent setup() from connecting (GObject doesn't release
            # the USB handle reliably on del).
            device_id = Aravis.get_device_id(i)
            # device_id format varies: "USB3Vision-vendor-model-serial" or similar
            serial = Aravis.get_device_serial_nbr(i) or device_id
            model = Aravis.get_device_model(i) or "Unknown"
            vendor = Aravis.get_device_vendor(i) or "Unknown"
            protocol = Aravis.get_device_protocol(i) or "USB3"

            info = CameraInfo(
                serial_number=serial,
                model_name=model,
                vendor=vendor,
                firmware_version="",  # Not available without opening camera
                connection_type=protocol,
            )
            cameras.append(info)

        return cameras
