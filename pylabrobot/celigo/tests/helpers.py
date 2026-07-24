"""Shared constructor-based test fixtures for the Celigo driver."""

from typing import Any, Optional, Tuple, TypeVar
from unittest.mock import patch

from pylabrobot.celigo.camera import CameraFrame
from pylabrobot.celigo.celigo import Celigo
from pylabrobot.celigo.config import HardwareDefaultConfig

_T = TypeVar("_T")


def require(value: Optional[_T]) -> _T:
  """Return a required parsed value or fail the test with a clear error."""
  if value is None:
    raise AssertionError("expected a configured value, got None")
  return value


def stub(celigo: Celigo, **attributes: Any) -> None:
  """Install explicitly scoped test doubles without weakening the driver's type."""
  for name, value in attributes.items():
    getattr(celigo, name)  # fail immediately if a test misspells the production attribute
    setattr(celigo, name, value)


class FakeCamera:
  """In-memory camera that satisfies the lifecycle expected by ``Celigo`` tests."""

  def __init__(self, sdk_library: Optional[str] = None) -> None:
    self.sdk_library = sdk_library
    self.is_open = False
    self.width = 1
    self.height = 1
    self.bit_depth = 8
    self.x_offset = 0
    self.y_offset = 0
    self.frame_rate = 1.0
    self.exposure_ms = 1.0
    self.gain = 0.0

  async def setup(self) -> None:
    self.is_open = True

  async def stop(self) -> None:
    self.is_open = False

  async def set_exposure(self, exposure_ms: float) -> float:
    self.exposure_ms = exposure_ms
    return exposure_ms

  async def set_gain(self, gain: float) -> float:
    self.gain = gain
    return gain

  async def set_frame_format(
    self,
    width: int,
    height: int,
    x_offset: Optional[int] = None,
    y_offset: Optional[int] = None,
  ) -> Tuple[int, int]:
    self.width = width
    self.height = height
    if x_offset is not None:
      self.x_offset = x_offset
    if y_offset is not None:
      self.y_offset = y_offset
    return width, height

  async def capture(self, flush_frames: int = 2) -> CameraFrame:
    del flush_frames
    return CameraFrame(
      data=bytes(self.width * self.height),
      width=self.width,
      height=self.height,
      bit_depth=self.bit_depth,
      exposure_ms=self.exposure_ms,
      gain=self.gain,
      captured_at=0.0,
    )


class FakeTransport:
  """Non-I/O transport carrying only constructor-level identity."""

  def __init__(self, device_id: Optional[str] = None) -> None:
    self.device_id = device_id


def make_celigo(**kwargs: Any) -> Celigo:
  """Construct a hardware-free ``Celigo`` that tests may replace methods on dynamically."""
  kwargs.setdefault("hardware_defaults", HardwareDefaultConfig())
  camera = FakeCamera()
  transport = FakeTransport(device_id=kwargs.get("device_id"))
  with (
    patch("pylabrobot.celigo.celigo.FTDI", return_value=transport),
    patch("pylabrobot.celigo.celigo.LumeneraCamera", return_value=camera),
  ):
    return Celigo(**kwargs)
