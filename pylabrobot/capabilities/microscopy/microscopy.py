import logging
import math
import time
from typing import Dict, Optional, Tuple, Union, cast

from pylabrobot.capabilities.capability import Capability, need_capability_ready
from pylabrobot.capabilities.microscopy.standard import (
  AutoExposure,
  AutoFocus,
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.resources.well import Well
from pylabrobot.serializer import SerializableMixin

from .backend import MicroscopyBackend

try:
  import numpy as np  # type: ignore

  HAS_NUMPY = True
except ImportError:
  np = None  # type: ignore[assignment]
  HAS_NUMPY = False

logger = logging.getLogger(__name__)


async def _golden_ratio_search(func, a: float, b: float, tol: float, timeout: float) -> float:
  """Golden ratio search to maximize a unimodal function over [a, b]."""
  phi = (1 + 5**0.5) / 2
  c = b - (b - a) / phi
  d = a + (b - a) / phi
  cache: Dict[float, float] = {}

  async def cached_func(x: float) -> float:
    x = round(x / tol) * tol
    if x not in cache:
      cache[x] = await func(x)
    return cache[x]

  t0 = time.time()
  while abs(b - a) > tol:
    if (await cached_func(c)) > (await cached_func(d)):
      b = d
    else:
      a = c
    c = b - (b - a) / phi
    d = a + (b - a) / phi
    if time.time() - t0 > timeout:
      raise TimeoutError("Timeout while searching for optimal focus position")

  return (b + a) / 2


class MicroscopyCapability(Capability):
  """Microscopy imaging capability.

  Provides high-level image capture with support for auto-exposure and auto-focus.
  """

  def __init__(self, backend: MicroscopyBackend):
    super().__init__(backend=backend)
    self.backend: MicroscopyBackend = backend

  def _resolve_well(self, well: Union[Well, Tuple[int, int]]) -> Tuple[int, int]:
    """Convert a Well or (row, col) tuple to (row, col) indices."""
    if isinstance(well, tuple):
      return well
    plate = cast(Plate, well.parent)
    idx = plate.index_of_item(well)
    if idx is None:
      raise ValueError(f"Well {well} not found in plate {well.parent}")
    row, column = divmod(idx, plate.num_items_x)
    return row, column

  async def _capture_auto_exposure(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    objective: Objective,
    auto_exposure: AutoExposure,
    focal_height: float,
    gain: float,
    plate: Plate,
    backend_params: Optional[SerializableMixin] = None,
  ) -> ImagingResult:
    """Capture with iterative auto-exposure using weighted binary search."""

    def _rms_split(low: float, high: float) -> float:
      if low == high:
        return low
      return math.sqrt((low**2 + high**2) / 2)

    low, high = auto_exposure.low, auto_exposure.high
    rounds = 0
    while high - low > 1e-3:
      if auto_exposure.max_rounds is not None and rounds >= auto_exposure.max_rounds:
        raise ValueError("Exceeded maximum number of auto-exposure rounds")
      rounds += 1

      p = _rms_split(low, high)
      res = await self.capture(
        well=well,
        mode=mode,
        objective=objective,
        exposure_time=p,
        focal_height=focal_height,
        gain=gain,
        plate=plate,
        backend_params=backend_params,
      )
      assert len(res.images) == 1, "Expected exactly one image for auto-exposure"
      evaluation = await auto_exposure.evaluate_exposure(res.images[0])

      if evaluation == "good":
        return res
      if evaluation == "lower":
        high = p
      elif evaluation == "higher":
        low = p
      else:
        raise ValueError(f"Unexpected evaluation result: {evaluation}")

    raise RuntimeError("Failed to find a good exposure time.")

  async def _capture_auto_focus(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    objective: Objective,
    exposure_time: float,
    auto_focus: AutoFocus,
    gain: float,
    plate: Plate,
    backend_params: Optional[SerializableMixin] = None,
  ) -> ImagingResult:
    """Capture with golden-ratio auto-focus search."""

    async def local_capture(focal_height: float) -> ImagingResult:
      return await self.capture(
        well=well,
        mode=mode,
        objective=objective,
        exposure_time=exposure_time,
        focal_height=focal_height,
        gain=gain,
        plate=plate,
        backend_params=backend_params,
      )

    async def capture_and_evaluate(focal_height: float) -> float:
      res = await local_capture(focal_height)
      return auto_focus.evaluate_focus(res.images[0])

    best_focal_height = await _golden_ratio_search(
      func=capture_and_evaluate,
      a=auto_focus.low,
      b=auto_focus.high,
      tol=auto_focus.tolerance,
      timeout=auto_focus.timeout,
    )
    return await local_capture(best_focal_height)

  @need_capability_ready
  async def capture(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    objective: Objective,
    plate: Plate,
    exposure_time: Union[Exposure, AutoExposure] = "machine-auto",
    focal_height: Union[FocalPosition, AutoFocus] = "machine-auto",
    gain: Gain = "machine-auto",
    backend_params: Optional[SerializableMixin] = None,
  ) -> ImagingResult:
    """Capture an image of a well.

    Args:
      well: A :class:`Well` instance or a ``(row, column)`` tuple (0-indexed).
      mode: Imaging mode (brightfield, fluorescence channel, etc.).
      objective: Objective lens to use.
      plate: The plate being imaged.
      exposure_time: Exposure time in ms, :class:`AutoExposure`, or ``"machine-auto"``.
      focal_height: Focal height in mm, :class:`AutoFocus`, or ``"machine-auto"``.
      gain: Gain value or ``"machine-auto"``.
      backend_params: Backend-specific parameters.

    Returns:
      An :class:`ImagingResult` with captured image(s) and metadata.
    """
    if isinstance(exposure_time, AutoExposure):
      if not isinstance(focal_height, (int, float)):
        raise ValueError("Focal height must be a number when using AutoExposure")
      if not isinstance(gain, (int, float)):
        raise ValueError("Gain must be a number when using AutoExposure")
      return await self._capture_auto_exposure(
        well=well,
        mode=mode,
        objective=objective,
        auto_exposure=exposure_time,
        focal_height=focal_height,
        gain=gain,
        plate=plate,
        backend_params=backend_params,
      )

    if isinstance(focal_height, AutoFocus):
      if not isinstance(exposure_time, (int, float)):
        raise ValueError("Exposure time must be a number when using AutoFocus")
      if not isinstance(gain, (int, float)):
        raise ValueError("Gain must be a number when using AutoFocus")
      return await self._capture_auto_focus(
        well=well,
        mode=mode,
        objective=objective,
        exposure_time=exposure_time,
        auto_focus=focal_height,
        gain=gain,
        plate=plate,
        backend_params=backend_params,
      )

    row, column = self._resolve_well(well)
    return await self.backend.capture(
      row=row,
      column=column,
      mode=mode,
      objective=objective,
      exposure_time=exposure_time,
      focal_height=focal_height,
      gain=gain,
      plate=plate,
      backend_params=backend_params,
    )

  async def _on_stop(self):
    await super()._on_stop()


# ---------------------------------------------------------------------------
# Exposure / focus evaluation helpers
# ---------------------------------------------------------------------------

try:
  import cv2 as _cv2  # type: ignore

  _CV2_AVAILABLE = True
except ImportError as _e:
  _cv2 = None  # type: ignore
  _CV2_AVAILABLE = False
  _CV2_IMPORT_ERROR = _e


def max_pixel_at_fraction(fraction: float, margin: float):
  """Return an evaluate_exposure callback targeting *fraction* of max pixel value.

  Args:
    fraction: desired ratio of actual max pixel to theoretical max (e.g. 0.8).
    margin: acceptable error as a fraction of theoretical max (e.g. 0.05).
  """
  if np is None:
    raise ImportError("numpy is required for max_pixel_at_fraction")

  async def evaluate_exposure(im):
    array = np.array(im, dtype=np.float32)
    value = np.max(array) - (255.0 * fraction)
    margin_value = 255.0 * margin
    if abs(value) <= margin_value:
      return "good"
    return "lower" if value > 0 else "higher"

  return evaluate_exposure


def fraction_overexposed(fraction: float, margin: float, max_pixel_value: int = 255):
  """Return an evaluate_exposure callback targeting a fraction of saturated pixels.

  Args:
    fraction: desired fraction of overexposed pixels (e.g. 0.005).
    margin: acceptable error on that fraction (e.g. 0.001).
    max_pixel_value: threshold for "overexposed" (default 255).
  """
  if np is None:
    raise ImportError("numpy is required for fraction_overexposed")

  async def evaluate_exposure(im):
    arr = np.asarray(im, dtype=np.uint8)
    actual_fraction = np.count_nonzero(arr > max_pixel_value) / arr.size
    lower_bound, upper_bound = fraction - margin, fraction + margin
    if lower_bound <= actual_fraction <= upper_bound:
      return "good"
    return "lower" if (actual_fraction - fraction) > 0 else "higher"

  return evaluate_exposure


def evaluate_focus_nvmg_sobel(image) -> float:
  """Evaluate focus via Normalized Variance of Gradient Magnitude (Sobel).

  Uses the center 50 % of the image to avoid edge effects.
  """
  if not _CV2_AVAILABLE:
    raise RuntimeError(
      f"cv2 needs to be installed for auto focus. Import error: {_CV2_IMPORT_ERROR}"
    )
  if np is None:
    raise ImportError("numpy is required for evaluate_focus_nvmg_sobel")

  np_image = np.array(image, dtype=np.float64)
  height, width = np_image.shape[:2]
  crop_h, crop_w = height // 4, width // 4
  np_image = np_image[crop_h : height - crop_h, crop_w : width - crop_w]

  sobel_x = _cv2.Sobel(np_image, _cv2.CV_64F, 1, 0, ksize=3)
  sobel_y = _cv2.Sobel(np_image, _cv2.CV_64F, 0, 1, ksize=3)
  gradient_magnitude = np.sqrt(sobel_x**2 + sobel_y**2)

  mean_gm = np.mean(gradient_magnitude)
  var_gm = np.var(gradient_magnitude)
  return float(var_gm / (mean_gm + 1e-6))
