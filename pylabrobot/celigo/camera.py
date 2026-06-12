"""Async wrapper for the Celigo's Lumenera camera (``liblucamapi``).

The SDK calls are blocking, so public methods run them in worker threads. Raw image
capture has no third-party Python dependency; :meth:`CameraFrame.to_numpy` requires
NumPy only when requested.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import ctypes
import ctypes.util
import functools
import os
import queue
import sys
import threading
import time
from array import array
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol, Tuple, TypeVar

_T = TypeVar("_T")


class _SerializedDaemonExecutor:
  """One serialized daemon worker for native calls that Python cannot cancel."""

  def __init__(self):
    self._queue: "queue.Queue[Optional[tuple[concurrent.futures.Future[Any], Callable[[], Any]]]]" = queue.Queue()
    self._closed = False
    self._thread = threading.Thread(target=self._run, name="celigo-camera", daemon=True)
    self._thread.start()

  def submit(self, function: Callable[[], _T]) -> "concurrent.futures.Future[_T]":
    if self._closed:
      raise RuntimeError("camera executor is shut down")
    future: "concurrent.futures.Future[_T]" = concurrent.futures.Future()
    self._queue.put((future, function))
    return future

  def shutdown(self) -> None:
    if not self._closed:
      self._closed = True
      self._queue.put(None)

  def _run(self) -> None:
    while True:
      work = self._queue.get()
      if work is None:
        return
      future, function = work
      if not future.set_running_or_notify_cancel():
        continue
      try:
        future.set_result(function())
      except BaseException as exc:
        future.set_exception(exc)


LUCAM_PROP_EXPOSURE = 20
LUCAM_PROP_GAIN = 40
LUCAM_PF_8 = 0
LUCAM_PF_16 = 1
_START_STREAMING = 1
_STOP_STREAMING = 0


class CameraError(RuntimeError):
  """A Lumenera SDK operation failed."""


class _LucamFrameFormat(ctypes.Structure):
  _fields_ = [
    ("x_offset", ctypes.c_uint32),
    ("y_offset", ctypes.c_uint32),
    ("width", ctypes.c_uint32),
    ("height", ctypes.c_uint32),
    ("pixel_format", ctypes.c_uint32),
    ("subsample_x", ctypes.c_uint16),
    ("flags_x", ctypes.c_uint16),
    ("subsample_y", ctypes.c_uint16),
    ("flags_y", ctypes.c_uint16),
  ]


@dataclass(frozen=True)
class CameraFrame:
  """One raw monochrome camera frame and its acquisition metadata."""

  data: bytes
  width: int
  height: int
  bit_depth: int
  exposure_ms: float
  gain: float
  captured_at: float

  @property
  def pixel_count(self) -> int:
    return self.width * self.height

  def pixels(self) -> array:
    """Return pixels as a standard-library array (native-endian for 16-bit frames)."""
    values = array("H" if self.bit_depth > 8 else "B")
    values.frombytes(self.data)
    return values

  def statistics(self) -> Tuple[int, int, float]:
    """Return ``(minimum, maximum, mean)`` without requiring NumPy."""
    values = self.pixels()
    if not values:
      raise CameraError("Camera returned an empty image")
    return min(values), max(values), sum(values) / len(values)

  def sharpness(self, sample_step: int = 2) -> float:
    """Variance of a Laplacian over the central image region.

    ``sample_step`` reduces work on large sensors.
    """
    if sample_step < 1:
      raise ValueError("sample_step must be at least 1")
    values = self.pixels()
    width, height = self.width, self.height
    if width < 3 or height < 3:
      return 0.0
    x0, x1 = width // 4, width - width // 4
    y0, y1 = height // 4, height - height // 4
    total = 0.0
    total_squared = 0.0
    count = 0
    for y in range(max(1, y0), min(height - 1, y1), sample_step):
      row = y * width
      for x in range(max(1, x0), min(width - 1, x1), sample_step):
        center = row + x
        laplacian = (
          values[center - 1]
          + values[center + 1]
          + values[center - width]
          + values[center + width]
          - 4 * values[center]
        )
        total += laplacian
        total_squared += laplacian * laplacian
        count += 1
    if count == 0:
      return 0.0
    mean = total / count
    return total_squared / count - mean * mean

  def to_numpy(self):
    """Return a ``height x width`` NumPy view of the frame."""
    try:
      import numpy as np  # type: ignore
    except ImportError as exc:
      raise RuntimeError("CameraFrame.to_numpy() requires numpy") from exc
    dtype = np.uint16 if self.bit_depth > 8 else np.uint8
    return np.frombuffer(self.data, dtype=dtype).reshape(self.height, self.width)

  def save_pgm(self, path: str) -> None:
    """Save the raw frame as a portable graymap image."""
    body = self.data
    if self.bit_depth > 8 and sys.byteorder == "little":
      values = self.pixels()
      values.byteswap()
      body = values.tobytes()
    maximum = 65535 if self.bit_depth > 8 else 255
    with open(path, "wb") as output:
      output.write(f"P5\n{self.width} {self.height}\n{maximum}\n".encode("ascii"))
      output.write(body)


class CeligoCamera(Protocol):
  """Camera interface consumed by :class:`pylabrobot.celigo.Celigo`."""

  exposure_ms: float
  gain: float
  width: int
  height: int

  @property
  def is_open(self) -> bool: ...

  async def setup(self) -> None: ...

  async def stop(self) -> None: ...

  async def set_exposure(self, exposure_ms: float) -> float: ...

  async def set_gain(self, gain: float) -> float: ...

  async def set_frame_format(
    self,
    width: int,
    height: int,
    x_offset: Optional[int] = None,
    y_offset: Optional[int] = None,
  ) -> Tuple[int, int]: ...

  async def capture(self, flush_frames: int = 2) -> CameraFrame: ...


class LumeneraCamera:
  """Lumenera camera connected to a Celigo, accessed through ``liblucamapi``."""

  def __init__(
    self,
    camera_index: int = 1,
    sdk_library: Optional[str] = None,
    library: Optional[Any] = None,
    sdk_call_timeout: float = 30.0,
  ):
    if sdk_call_timeout <= 0:
      raise ValueError("sdk_call_timeout must be positive")
    self.camera_index = camera_index
    self.sdk_library = sdk_library or os.environ.get("LUCAM_SDK_LIBRARY")
    self._lib = library
    self.sdk_call_timeout = sdk_call_timeout
    self._executor: Optional[_SerializedDaemonExecutor] = _SerializedDaemonExecutor()
    self._handle: Optional[int] = None
    self._streaming = False
    self._pending_cleanup: Optional[concurrent.futures.Future[Any]] = None
    self._lock = asyncio.Lock()
    self.width = 0
    self.height = 0
    self.x_offset = 0
    self.y_offset = 0
    self.bit_depth = 8
    self.frame_rate = 0.0
    self.exposure_ms = 0.0
    self.gain = 0.0

  @property
  def is_open(self) -> bool:
    return self._handle is not None and self._pending_cleanup is None

  def _queue_deferred_close(
    self, executor: _SerializedDaemonExecutor
  ) -> concurrent.futures.Future[Any]:
    cleanup = executor.submit(self._stop_sync)
    self._pending_cleanup = cleanup

    def shutdown_worker(_future: concurrent.futures.Future[Any]) -> None:
      executor.shutdown()
      if self._executor is executor:
        self._executor = None

    cleanup.add_done_callback(shutdown_worker)
    return cleanup

  async def _run_blocking(self, function: Callable[..., _T], *args: Any) -> _T:
    """Run one SDK call without using asyncio's process-wide default executor.

    Some Python runtimes do not reliably shut down their default executor when the
    event loop runs in debug mode. A small, owned executor avoids that lifecycle
    coupling and also ensures that no Lumenera worker thread outlives the call.
    """
    pending_cleanup = self._pending_cleanup
    if pending_cleanup is not None:
      if not pending_cleanup.done():
        raise CameraError(
          "A timed-out Lumenera call is still running; the camera is poisoned until "
          "its deferred close completes"
        )
      # Surface a deferred-close exception before accepting another SDK call.
      pending_cleanup.result()
      self._pending_cleanup = None
    call = functools.partial(function, *args)
    if self._executor is None:
      self._executor = _SerializedDaemonExecutor()
    executor = self._executor
    worker = executor.submit(call)
    deadline = asyncio.get_running_loop().time() + self.sdk_call_timeout
    try:
      while not worker.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
          raise asyncio.TimeoutError
        # Polling avoids relying on a cross-thread event-loop callback after a native
        # call completes, which is unreliable on some embedded/diagnostic event loops.
        await asyncio.sleep(min(0.005, remaining))
      return worker.result()
    except asyncio.CancelledError:
      self._queue_deferred_close(executor)
      raise
    except asyncio.TimeoutError as exc:
      # A running native call cannot be cancelled. Queue close on the same one-worker
      # executor, so it runs after (never concurrently with) the abandoned call.
      self._queue_deferred_close(executor)
      raise CameraError(
        f"Lumenera SDK call exceeded {self.sdk_call_timeout:g} second timeout; "
        "camera close is queued behind the native call"
      ) from exc

  def _load_library(self) -> Any:
    if self._lib is None:
      candidates = [self.sdk_library] if self.sdk_library else []
      discovered = ctypes.util.find_library("lucamapi")
      if discovered:
        candidates.append(discovered)
      candidates.extend(["lucamapi.dll", "liblucamapi.dylib", "liblucamapi.so"])
      errors = []
      for path in dict.fromkeys(candidate for candidate in candidates if candidate):
        try:
          self._lib = ctypes.CDLL(path)
          break
        except OSError as exc:
          errors.append(f"{path}: {exc}")
      if self._lib is None:
        raise CameraError(
          "Could not load the Lumenera SDK library; pass sdk_library= or set "
          f"LUCAM_SDK_LIBRARY. Tried: {'; '.join(errors)}"
        )
    lib = self._lib

    # Bind signatures to the same attribute-resolved function objects used below.
    # ``ctypes.CDLL.__getitem__`` creates a distinct object, so configuring
    # ``lib[name]`` would leave ``lib.LucamCameraOpen`` with its unsafe default
    # 32-bit return type and truncate 64-bit camera handles.
    def set_signature(function: Any, restype: Any, argtypes: list[Any]) -> None:
      try:
        function.restype = restype
        function.argtypes = argtypes
      except AttributeError:
        return

    try:
      set_signature(lib.LucamCameraOpen, ctypes.c_void_p, [ctypes.c_uint32])
      set_signature(lib.LucamCameraClose, ctypes.c_int, [ctypes.c_void_p])
      set_signature(
        lib.LucamStreamVideoControl,
        ctypes.c_int,
        [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p],
      )
      set_signature(
        lib.LucamGetFormat,
        ctypes.c_int,
        [ctypes.c_void_p, ctypes.POINTER(_LucamFrameFormat), ctypes.POINTER(ctypes.c_float)],
      )
      set_signature(
        lib.LucamSetFormat,
        ctypes.c_int,
        [ctypes.c_void_p, ctypes.POINTER(_LucamFrameFormat), ctypes.c_float],
      )
      set_signature(
        lib.LucamTakeVideo,
        ctypes.c_int,
        [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p],
      )
      set_signature(
        lib.LucamGetProperty,
        ctypes.c_int,
        [
          ctypes.c_void_p,
          ctypes.c_uint32,
          ctypes.POINTER(ctypes.c_float),
          ctypes.POINTER(ctypes.c_int32),
        ],
      )
      set_signature(
        lib.LucamSetProperty,
        ctypes.c_int,
        [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_float, ctypes.c_int32],
      )
      set_signature(
        lib.LucamGetLastErrorForCamera,
        ctypes.c_uint32,
        [ctypes.c_void_p],
      )
    except AttributeError as exc:
      raise CameraError(f"Lumenera SDK does not export {exc.name}") from exc
    return lib

  def _request_last_sdk_error_code(self) -> int:
    if self._lib is None or self._handle is None:
      return 0
    return int(self._lib.LucamGetLastErrorForCamera(self._handle))

  def _require_library(self) -> Any:
    if self._lib is None:
      raise CameraError("Lumenera SDK is not loaded; call setup() first")
    return self._lib

  def _raise_if_sdk_call_failed(self, sdk_result: Any, operation: str) -> None:
    if not sdk_result:
      raise CameraError(
        f"{operation} failed (Lumenera error {self._request_last_sdk_error_code()})"
      )

  def _setup_sync(self) -> None:
    lib = self._load_library()
    handle = lib.LucamCameraOpen(self.camera_index)
    if not handle:
      raise CameraError("LucamCameraOpen failed")
    self._handle = handle
    try:
      self._raise_if_sdk_call_failed(
        lib.LucamStreamVideoControl(handle, _START_STREAMING, None), "start camera stream"
      )
      self._streaming = True
      frame_format = _LucamFrameFormat()
      frame_rate = ctypes.c_float()
      self._raise_if_sdk_call_failed(
        lib.LucamGetFormat(handle, ctypes.byref(frame_format), ctypes.byref(frame_rate)),
        "read camera format",
      )
      self._update_frame_format_state(frame_format, float(frame_rate.value))
      self.exposure_ms = self._request_property_value_sync(LUCAM_PROP_EXPOSURE)
      self.gain = self._request_property_value_sync(LUCAM_PROP_GAIN)
    except Exception:
      self._stop_sync()
      raise

  async def setup(self) -> None:
    """Open the first camera and start its video stream."""
    async with self._lock:
      if not self.is_open:
        try:
          await self._run_blocking(self._setup_sync)
        except BaseException:
          if self._pending_cleanup is None and self._executor is not None:
            self._executor.shutdown()
            self._executor = None
          raise

  def _update_frame_format_state(self, frame_format: _LucamFrameFormat, frame_rate: float) -> None:
    if frame_format.pixel_format not in (LUCAM_PF_8, LUCAM_PF_16):
      raise CameraError(
        f"Unsupported Lumenera pixel format {frame_format.pixel_format}; "
        "only monochrome 8-bit and 16-bit formats are supported"
      )
    subsample_x = max(1, int(frame_format.subsample_x))
    subsample_y = max(1, int(frame_format.subsample_y))
    self.width = int(frame_format.width // subsample_x)
    self.height = int(frame_format.height // subsample_y)
    self.x_offset = int(frame_format.x_offset)
    self.y_offset = int(frame_format.y_offset)
    if self.width <= 0 or self.height <= 0:
      raise CameraError(f"Lumenera returned invalid frame dimensions {self.width}x{self.height}")
    self.bit_depth = 16 if frame_format.pixel_format == LUCAM_PF_16 else 8
    self.frame_rate = frame_rate

  def _set_frame_format_sync(
    self,
    width: int,
    height: int,
    x_offset: Optional[int],
    y_offset: Optional[int],
  ) -> Tuple[int, int]:
    handle = self._require_handle()
    lib = self._require_library()
    current = _LucamFrameFormat()
    frame_rate = ctypes.c_float()
    self._raise_if_sdk_call_failed(
      lib.LucamGetFormat(handle, ctypes.byref(current), ctypes.byref(frame_rate)),
      "read camera format",
    )
    subsample_x = max(1, int(current.subsample_x))
    subsample_y = max(1, int(current.subsample_y))
    raw_width = width * subsample_x
    raw_height = height * subsample_y
    if raw_width > current.width or raw_height > current.height:
      raise CameraError(
        f"Requested camera format {width}x{height} exceeds current sensor window "
        f"{current.width // subsample_x}x{current.height // subsample_y}"
      )
    target_x = (
      int(current.x_offset + (current.width - raw_width) // 2) if x_offset is None else x_offset
    )
    target_y = (
      int(current.y_offset + (current.height - raw_height) // 2) if y_offset is None else y_offset
    )
    if target_x < 0 or target_y < 0:
      raise ValueError("camera offsets must be non-negative")
    target = _LucamFrameFormat(
      x_offset=target_x,
      y_offset=target_y,
      width=raw_width,
      height=raw_height,
      pixel_format=current.pixel_format,
      subsample_x=current.subsample_x,
      flags_x=current.flags_x,
      subsample_y=current.subsample_y,
      flags_y=current.flags_y,
    )
    was_streaming = self._streaming
    if was_streaming:
      self._raise_if_sdk_call_failed(
        lib.LucamStreamVideoControl(handle, _STOP_STREAMING, None),
        "stop camera stream for format change",
      )
      self._streaming = False
    try:
      self._raise_if_sdk_call_failed(
        lib.LucamSetFormat(handle, ctypes.byref(target), ctypes.c_float(frame_rate.value)),
        "set camera format",
      )
      actual = _LucamFrameFormat()
      actual_rate = ctypes.c_float()
      self._raise_if_sdk_call_failed(
        lib.LucamGetFormat(handle, ctypes.byref(actual), ctypes.byref(actual_rate)),
        "read back camera format",
      )
      self._update_frame_format_state(actual, float(actual_rate.value))
      if (self.width, self.height) != (width, height):
        raise CameraError(
          f"Lumenera accepted {self.width}x{self.height}, not requested {width}x{height}"
        )
    finally:
      if was_streaming:
        self._raise_if_sdk_call_failed(
          lib.LucamStreamVideoControl(handle, _START_STREAMING, None),
          "restart camera stream after format change",
        )
        self._streaming = True
    return self.width, self.height

  async def set_frame_format(
    self,
    width: int,
    height: int,
    x_offset: Optional[int] = None,
    y_offset: Optional[int] = None,
  ) -> Tuple[int, int]:
    """Set and verify a camera ROI, centered when offsets are omitted."""
    if width <= 0 or height <= 0:
      raise ValueError("camera width and height must be positive")
    async with self._lock:
      return await self._run_blocking(
        self._set_frame_format_sync, width, height, x_offset, y_offset
      )

  def _stop_sync(self) -> None:
    if self._lib is None or self._handle is None:
      return
    handle = self._handle
    if self._streaming:
      self._lib.LucamStreamVideoControl(handle, _STOP_STREAMING, None)
    self._lib.LucamCameraClose(handle)
    self._streaming = False
    self._handle = None

  async def stop(self) -> None:
    """Stop streaming and close the camera."""
    async with self._lock:
      try:
        await self._run_blocking(self._stop_sync)
      finally:
        if self._pending_cleanup is None and self._executor is not None:
          self._executor.shutdown()
          self._executor = None

  def _require_handle(self) -> int:
    if self._handle is None:
      raise CameraError("Camera is not open; call setup() first")
    return self._handle

  def _request_property_value_sync(self, property_id: int) -> float:
    handle = self._require_handle()
    lib = self._require_library()
    value = ctypes.c_float()
    flags = ctypes.c_int32()
    self._raise_if_sdk_call_failed(
      lib.LucamGetProperty(handle, property_id, ctypes.byref(value), ctypes.byref(flags)),
      f"read camera property {property_id}",
    )
    return float(value.value)

  async def request_property_value(self, property_id: int) -> float:
    async with self._lock:
      return await self._run_blocking(self._request_property_value_sync, property_id)

  def _set_property_sync(self, property_id: int, property_value: float) -> float:
    handle = self._require_handle()
    lib = self._require_library()
    self._raise_if_sdk_call_failed(
      lib.LucamSetProperty(handle, property_id, ctypes.c_float(property_value), 0),
      f"set camera property {property_id}",
    )
    return self._request_property_value_sync(property_id)

  async def set_exposure(self, exposure_ms: float) -> float:
    if exposure_ms <= 0:
      raise ValueError("exposure_ms must be positive")
    async with self._lock:
      self.exposure_ms = await self._run_blocking(
        self._set_property_sync, LUCAM_PROP_EXPOSURE, exposure_ms
      )
      return self.exposure_ms

  async def set_gain(self, gain: float) -> float:
    if gain < 0:
      raise ValueError("gain must be non-negative")
    async with self._lock:
      self.gain = await self._run_blocking(self._set_property_sync, LUCAM_PROP_GAIN, gain)
      return self.gain

  def _capture_sync(self, flush_frames: int) -> CameraFrame:
    handle = self._require_handle()
    lib = self._require_library()
    bytes_per_pixel = 2 if self.bit_depth > 8 else 1
    byte_count = self.width * self.height * bytes_per_pixel
    if byte_count <= 0:
      raise CameraError(f"Invalid capture geometry {self.width}x{self.height}")
    buffer = ctypes.create_string_buffer(byte_count)
    for frame_index in range(flush_frames + 1):
      self._raise_if_sdk_call_failed(lib.LucamTakeVideo(handle, 1, buffer), "capture camera frame")
      if frame_index < flush_frames:
        time.sleep(max(0.001, self.exposure_ms / 1000.0))
    return CameraFrame(
      data=buffer.raw[:byte_count],
      width=self.width,
      height=self.height,
      bit_depth=self.bit_depth,
      exposure_ms=self.exposure_ms,
      gain=self.gain,
      captured_at=time.time(),
    )

  async def capture(self, flush_frames: int = 2) -> CameraFrame:
    """Capture a frame, discarding ``flush_frames`` stale streaming frames first."""
    if flush_frames < 0:
      raise ValueError("flush_frames must be non-negative")
    async with self._lock:
      return await self._run_blocking(self._capture_sync, flush_frames)
