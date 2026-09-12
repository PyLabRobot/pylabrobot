"""BlackFly camera driver using Aravis (GenICam/USB3 Vision).

``CytationMicroscope`` delegates camera operations here; Aravis (via PyGObject) talks to the
camera over USB3 Vision/GenICam.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import anyio

from pylabrobot.concurrency import AsyncExitStackWithShielding, HasLifespan

try:
  import numpy as np
except ImportError:
  np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Aravis is an optional dependency. It requires:
#   - System library: brew install aravis (macOS) or apt install libaravis-dev (Linux)
#   - Python bindings: pip install PyGObject
# If not installed, AravisCamera.setup() will raise ImportError with instructions.
try:
  # PyGObject ships no stubs, and it is absent entirely wherever the
  # cytation-microscopy extra is not installed, so both codes can fire.
  import gi  # type: ignore[import-untyped,import-not-found]

  gi.require_version("Aravis", "0.8")
  from gi.repository import (  # type: ignore[attr-defined,import-untyped,import-not-found]
    Aravis,
  )

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


class AravisCamera(HasLifespan):
  """BlackFly camera driver using Aravis for GenICam access over USB3 Vision.

  This class wraps all Aravis/GenICam operations for single-frame image
  acquisition with software triggering.

  GenICam primer for non-camera-experts:
    GenICam is a standard that defines how camera features (exposure, gain,
    trigger, etc.) are named and accessed. Every compliant camera publishes
    an XML file describing its features as "nodes." Aravis reads this XML
    and lets you get/set node values by name (e.g., "ExposureTime", "Gain").
    Aravis provides a Python API to access these nodes via PyGObject.

  Buffer management:
    Aravis uses a producer-consumer model with pre-allocated buffers.
    During setup(), we allocate a small pool of buffers and push them to
    the stream. When we trigger a capture, Aravis fills a buffer with image
    data. We pop the buffer, copy the data to a numpy array, and push the
    buffer back to the pool for reuse. The copy is necessary because the
    buffer memory is owned by Aravis and will be reused.

  Usage:
    camera = AravisCamera()
    async with camera.lifespan(serial_number="12345678"):
      await camera.set_exposure(10.0)  # 10 ms
      image = await camera.trigger()    # numpy array, Mono8
  """

  def __init__(self) -> None:
    super().__init__()
    self._camera: Optional[Any] = None  # Aravis.Camera
    self._device: Optional[Any] = None  # Aravis.Device
    self._stream: Optional[Any] = None  # Aravis.Stream
    self._serial_number: Optional[str] = None
    self._acquiring: bool = False
    self._width: int = 0
    self._height: int = 0
    self._payload_size: int = 0
    self._acquisition_transition_lock = anyio.Lock()
    self._trigger_lock = anyio.Lock()
    self._acquisition_scope_token: Optional[object] = None
    self._acquisition_state_unknown = False

  @property
  def width(self) -> int:
    """Image width in pixels (read-only, from camera default)."""
    return self._width

  @property
  def height(self) -> int:
    """Image height in pixels (read-only, from camera default)."""
    return self._height

  @property
  def camera(self) -> Any:
    """The ``Aravis.Camera`` bound during the lifespan."""
    if self._camera is None:
      raise RuntimeError("Camera is not initialized. Call setup() first.")
    return self._camera

  @property
  def device(self) -> Any:
    """The ``Aravis.Device`` bound during the lifespan."""
    if self._device is None:
      raise RuntimeError("Camera is not initialized. Call setup() first.")
    return self._device

  @property
  def stream(self) -> Any:
    """The ``Aravis.Stream`` allocated during the lifespan."""
    if self._stream is None:
      raise RuntimeError("Camera is not initialized. Call setup() first.")
    return self._stream

  async def _enter_lifespan(
    self,
    stack: AsyncExitStackWithShielding,
    *,
    serial_number: Optional[str] = None,
  ) -> None:
    """Connect to the camera and register rollback after every acquisition phase.

    The software trigger configuration:
    TriggerSelector=FrameStart, TriggerSource=Software, TriggerMode=On.

    Args:
        serial_number: Camera serial number (e.g., "12345678"). If None, uses
            the first available camera. Use enumerate_cameras() to discover
            available cameras.

    Raises:
        ImportError: If Aravis/PyGObject is not installed.
        RuntimeError: If camera cannot be found or connected.
    """
    await super()._enter_lifespan(stack)
    if not HAS_ARAVIS:
      raise ImportError(
        "Aravis is not installed. Install it with:\n"
        "  macOS:   brew install aravis && pip install PyGObject\n"
        "  Linux:   sudo apt-get install libaravis-dev gobject-introspection "
        "&& pip install PyGObject\n"
        "  Windows: pacman -S mingw-w64-x86_64-aravis (in MSYS2)"
      )

    self._serial_number = serial_number
    stack.callback(self._release_camera)

    await anyio.to_thread.run_sync(Aravis.update_device_list)
    n_devices = await anyio.to_thread.run_sync(Aravis.get_n_devices)

    if n_devices == 0:
      raise RuntimeError("No cameras found.")

    device_id_to_connect = None
    if serial_number is None:
      # Use the first available camera.
      device_id_to_connect = await anyio.to_thread.run_sync(Aravis.get_device_id, 0)
    else:
      for i in range(n_devices):
        dev_serial = await anyio.to_thread.run_sync(Aravis.get_device_serial_nbr, i) or ""
        dev_id = await anyio.to_thread.run_sync(Aravis.get_device_id, i) or ""
        if serial_number in (dev_serial, dev_id):
          device_id_to_connect = dev_id
          break

    if device_id_to_connect is None:
      raise RuntimeError(
        f"Camera with serial '{serial_number}' not found. "
        f"Available devices: "
        f"{[await anyio.to_thread.run_sync(Aravis.get_device_id, i) for i in range(n_devices)]}"
      )

    try:
      self._camera = await anyio.to_thread.run_sync(Aravis.Camera.new, device_id_to_connect)
    except Exception as e:
      raise RuntimeError(
        f"Failed to connect to camera '{device_id_to_connect}'. "
        f"Is the camera in use by another process? Error: {e}"
      ) from e
    self._device = await anyio.to_thread.run_sync(self.camera.get_device)

    # Configure software trigger mode.
    # GenICam nodes: TriggerSelector, TriggerSource, TriggerMode are
    # standard SFNC (Standard Features Naming Convention) names.
    await anyio.to_thread.run_sync(
      self.device.set_string_feature_value, "TriggerSelector", "FrameStart"
    )
    await anyio.to_thread.run_sync(
      self.device.set_string_feature_value, "TriggerSource", "Software"
    )
    await anyio.to_thread.run_sync(self.device.set_string_feature_value, "TriggerMode", "On")
    stack.push_shielded_async_callback(self._reset_trigger_mode)

    # Read image dimensions and payload size from camera.
    region = await anyio.to_thread.run_sync(self.camera.get_region)
    self._width = region[2]  # x, y, width, height
    self._height = region[3]
    self._payload_size = await anyio.to_thread.run_sync(self.camera.get_payload)

    # BlackFly/Flea3 cameras need a delay after trigger mode change.
    await anyio.sleep(1)

    # Create stream and pre-allocate buffer pool.
    # Aravis requires buffers to be pushed to the stream before acquisition.
    # We allocate a small pool (5 buffers) — for single-frame software
    # trigger, we only use one at a time but the pool prevents starvation.
    self._stream = await anyio.to_thread.run_sync(self.camera.create_stream, None, None)
    stack.push_shielded_async_callback(self._release_stream)
    for _ in range(_BUFFER_COUNT):
      await anyio.to_thread.run_sync(self._allocate_buffer)
    stack.push_shielded_async_callback(self._force_stop_acquisition)

    logger.info(
      "AravisCamera: Connected to %s (SN: %s), %dx%d",
      await anyio.to_thread.run_sync(self.device.get_string_feature_value, "DeviceModelName"),
      serial_number,
      self._width,
      self._height,
    )

  def _allocate_buffer(self) -> None:
    """Allocate and publish one stream buffer on a worker thread."""
    self.stream.push_buffer(Aravis.Buffer.new_allocate(self._payload_size))

  async def _stop_acquisition(self) -> None:
    """Stop active acquisition during lifespan rollback or exit."""
    camera = self._camera
    if not (self._acquiring or self._acquisition_state_unknown) or camera is None:
      return
    async with self._trigger_lock:
      await anyio.to_thread.run_sync(self._stop_acquisition_sync, camera, abandon_on_cancel=False)

  def _stop_acquisition_sync(self, camera: Any) -> None:
    """Stop acquisition and publish state in the same worker operation."""
    self._acquisition_state_unknown = True
    camera.stop_acquisition()
    self._acquiring = False
    self._acquisition_state_unknown = False

  async def _force_stop_acquisition(self) -> None:
    """Stop tracked acquisition during parent cleanup and invalidate scope ownership."""
    async with self._acquisition_transition_lock:
      try:
        await self._stop_acquisition()
      finally:
        self._acquisition_scope_token = None

  async def _release_stream(self) -> None:
    """Drain allocated buffers before releasing the stream reference."""
    stream = self._stream
    try:
      if stream is not None:
        await anyio.to_thread.run_sync(self._drain_stream, stream)
    finally:
      self._stream = None

  @staticmethod
  def _drain_stream(stream: Any) -> None:
    """Pop every immediately available buffer from an Aravis stream."""
    while stream.try_pop_buffer() is not None:
      pass

  async def _reset_trigger_mode(self) -> None:
    """Restore free-running trigger mode before releasing the camera."""
    device = self._device
    if device is not None:
      await anyio.to_thread.run_sync(device.set_string_feature_value, "TriggerMode", "Off")

  def _release_camera(self) -> None:
    """Release all camera references and cached dimensions."""
    self._camera = None
    self._device = None
    self._stream = None
    self._serial_number = None
    self._acquiring = False
    self._acquisition_state_unknown = False
    self._width = 0
    self._height = 0
    self._payload_size = 0
    self._acquisition_scope_token = None

  @property
  def is_acquiring(self) -> bool:
    """Whether camera acquisition is currently active."""
    return self._acquiring

  @property
  def is_acquisition_scoped(self) -> bool:
    """Whether acquisition is owned by an active scope."""
    return self._acquisition_scope_token is not None

  def _require_active_configured_camera(self) -> Any:
    """Return the configured camera only during an active lifespan."""
    if not self.is_active or self._camera is None:
      raise RuntimeError("Acquisition requires an active configured camera")
    return self._camera

  async def _start_acquisition(self) -> None:
    """Start acquisition without applying the public scope guard."""
    camera = self._require_active_configured_camera()
    if self._acquisition_state_unknown:
      raise RuntimeError("Camera acquisition state is unknown; stop acquisition before starting")
    if self._acquiring:
      return
    try:
      await anyio.to_thread.run_sync(self._start_acquisition_sync, camera, abandon_on_cancel=False)
    except BaseException:
      with anyio.CancelScope(shield=True):
        await self._stop_acquisition()
      raise

  def _start_acquisition_sync(self, camera: Any) -> None:
    """Start acquisition and publish state in the same worker operation."""
    self._acquisition_state_unknown = True
    camera.start_acquisition()
    self._acquiring = True
    self._acquisition_state_unknown = False

  async def start_acquisition(self) -> None:
    """Begin camera acquisition (no-op if already acquiring).

    After this call, the
    camera is ready to receive software triggers and produce image buffers.
    """
    async with self._acquisition_transition_lock:
      if self._acquisition_scope_token is not None:
        raise RuntimeError("Cannot start acquisition while an acquisition scope is active")
      await anyio.lowlevel.checkpoint()
      await self._start_acquisition()

  async def stop_acquisition(self) -> None:
    """End camera acquisition (no-op if not acquiring).

    Stop camera acquisition.
    """
    async with self._acquisition_transition_lock:
      if self._acquisition_scope_token is not None:
        raise RuntimeError("Cannot stop acquisition while an acquisition scope is active")
      await self._stop_acquisition()

  @asynccontextmanager
  async def acquisition(self) -> AsyncIterator[None]:
    """Own camera acquisition for the lifetime of this context."""
    token = object()
    async with self._acquisition_transition_lock:
      self._require_active_configured_camera()
      if self._acquisition_scope_token is not None:
        raise RuntimeError("Acquisition is already scoped")
      if self._acquiring or self._acquisition_state_unknown:
        raise RuntimeError("Acquisition is already active or its state is unknown")
      await anyio.lowlevel.checkpoint()
      await self._start_acquisition()
      self._acquisition_scope_token = token

    try:
      await anyio.lowlevel.checkpoint()
      yield
    finally:
      with anyio.CancelScope(shield=True):
        async with self._acquisition_transition_lock:
          if self._acquisition_scope_token is token:
            try:
              await self._stop_acquisition()
            finally:
              self._acquisition_scope_token = None

  async def trigger(self, timeout_ms: int = 5000) -> np.ndarray:
    """Capture a single frame: software trigger → grab.

    Acquisition must already be started via start_acquisition().
    Start/stop acquisition bracket the capture loop, not each individual frame.

    Args:
        timeout_ms: Maximum time to wait for image buffer, in milliseconds.
            Default 5000 (5 seconds).

    Returns:
        numpy.ndarray: Image as 2D uint8 array (height × width), Mono8 format.

    Raises:
        RuntimeError: If camera not initialized, not acquiring, or times out.
    """
    async with self._trigger_lock:
      _ = self.camera
      if not self._acquiring:
        raise RuntimeError("Camera is not acquiring. Call start_acquisition() first.")

      await anyio.lowlevel.checkpoint()
      with anyio.CancelScope(shield=True):
        await anyio.to_thread.run_sync(self.device.execute_command, "TriggerSoftware")
        stream = self.stream
        buffer = await anyio.to_thread.run_sync(stream.timeout_pop_buffer, timeout_ms * 1000)
        if buffer is None:
          raise RuntimeError(
            f"Camera capture timed out after {timeout_ms}ms. "
            "Is the camera connected and trigger mode configured?"
          )

        try:
          data = await anyio.to_thread.run_sync(buffer.get_data)
          image = np.frombuffer(data, dtype=np.uint8).reshape(self._height, self._width).copy()
        finally:
          await anyio.to_thread.run_sync(stream.push_buffer, buffer)
      await anyio.lowlevel.checkpoint()
      return image

  async def set_exposure(self, exposure_ms: float) -> None:
    """Set exposure time in milliseconds.

    Disables auto-exposure first, then sets the GenICam ExposureTime node.
    GenICam uses microseconds internally; this method handles the conversion.

    Args:
        exposure_ms: Exposure time in milliseconds (e.g., 10.0 = 10 ms).
    """
    camera = self.camera
    # Disable auto-exposure before setting manual value.
    await anyio.to_thread.run_sync(self.device.set_string_feature_value, "ExposureAuto", "Off")
    # GenICam ExposureTime is in microseconds.
    exposure_us = exposure_ms * 1000.0
    await anyio.to_thread.run_sync(camera.set_exposure_time, exposure_us)

  async def get_exposure(self) -> float:
    """Read current exposure time in milliseconds (from hardware, not cached)."""
    exposure_us = await anyio.to_thread.run_sync(self.camera.get_exposure_time)
    return float(exposure_us) / 1000.0

  async def set_gain(self, gain: float) -> None:
    """Set gain value.

    Disables auto-gain first, then sets the GenICam Gain node.
    The gain range depends on the camera model (typically 0-30 for BlackFly).

    Args:
        gain: Gain value (e.g., 1.0).
    """
    camera = self.camera
    await anyio.to_thread.run_sync(self.device.set_string_feature_value, "GainAuto", "Off")
    await anyio.to_thread.run_sync(camera.set_gain, gain)

  async def set_auto_gain(self, mode: str) -> None:
    """Set auto-gain mode.

    Args:
        mode: One of "off", "once", "continuous". Maps to GenICam
            GainAuto node values: Off, Once, Continuous.
    """
    _ = self.camera
    mode_map = {"off": "Off", "once": "Once", "continuous": "Continuous"}
    aravis_mode = mode_map.get(mode.lower())
    if aravis_mode is None:
      raise ValueError(f"Invalid auto-gain mode '{mode}'. Use 'off', 'once', or 'continuous'.")
    await anyio.to_thread.run_sync(self.device.set_string_feature_value, "GainAuto", aravis_mode)

  async def get_gain(self) -> float:
    """Read current gain value (from hardware, not cached)."""
    return float(await anyio.to_thread.run_sync(self.camera.get_gain))

  async def set_auto_exposure(self, mode: str) -> None:
    """Set auto-exposure mode.

    Args:
        mode: One of "off", "once", "continuous". Maps to GenICam
            ExposureAuto node values: Off, Once, Continuous.
    """
    _ = self.camera
    mode_map = {"off": "Off", "once": "Once", "continuous": "Continuous"}
    aravis_mode = mode_map.get(mode.lower())
    if aravis_mode is None:
      raise ValueError(f"Invalid auto-exposure mode '{mode}'. Use 'off', 'once', or 'continuous'.")
    await anyio.to_thread.run_sync(
      self.device.set_string_feature_value, "ExposureAuto", aravis_mode
    )

  async def set_pixel_format(self, fmt: Optional[int] = None) -> None:
    """Set pixel format. Default is Mono8.

    Must be called before start_acquisition(). The format value is an
    Aravis pixel format constant (e.g., Aravis.PIXEL_FORMAT_MONO_8).

    Args:
        fmt: Aravis pixel format constant. If None, uses Mono8.
    """
    camera = self.camera
    if fmt is None:
      if HAS_ARAVIS:
        fmt = Aravis.PIXEL_FORMAT_MONO_8
      else:
        return
    await anyio.to_thread.run_sync(camera.set_pixel_format, fmt)

  async def get_device_info(self) -> CameraInfo:
    """Read camera identification from GenICam nodes.

    Returns model name, serial number, vendor, and firmware version
    without requiring acquisition to be active.

    Returns:
        CameraInfo with fields populated from the camera's GenICam XML.
    """
    device = self.device
    return await anyio.to_thread.run_sync(self._get_device_info_sync, device)

  @staticmethod
  def _get_device_info_sync(device: Any) -> CameraInfo:
    """Read all camera identity nodes on one worker thread."""
    return CameraInfo(
      serial_number=device.get_string_feature_value("DeviceSerialNumber"),
      model_name=device.get_string_feature_value("DeviceModelName"),
      vendor=device.get_string_feature_value("DeviceVendorName"),
      firmware_version=device.get_string_feature_value("DeviceFirmwareVersion"),
      connection_type="USB3",
    )

  @staticmethod
  async def enumerate_cameras() -> list[CameraInfo]:
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
        "  Linux:   sudo apt-get install libaravis-dev gobject-introspection "
        "&& pip install PyGObject\n"
        "  Windows: pacman -S mingw-w64-x86_64-aravis (in MSYS2)"
      )

    return await anyio.to_thread.run_sync(AravisCamera._enumerate_cameras_sync)

  @staticmethod
  def _enumerate_cameras_sync() -> list[CameraInfo]:
    """Enumerate camera metadata on a worker thread."""
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
