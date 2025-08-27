import math
from typing import Awaitable, Callable, Literal, Optional, Tuple, Union, cast

from pylabrobot.machines import Machine
from pylabrobot.plate_reading.backend import ImagerBackend
from pylabrobot.plate_reading.standard import (
  AutoExposure,
  Exposure,
  FocalPosition,
  Gain,
  Image,
  ImagingMode,
  ImagingResult,
  NoPlateError,
  Objective,
)
from pylabrobot.resources import Plate, Resource, Well

try:
  import numpy as np
except ImportError:
  np = None  # type: ignore[assignment]


class Imager(Resource, Machine):
  """Microscope"""

  def __init__(
    self,
    name: str,
    size_x: float,
    size_y: float,
    size_z: float,
    backend: ImagerBackend,
    category: Optional[str] = None,
    model: Optional[str] = None,
  ):
    Resource.__init__(
      self,
      name=name,
      size_x=size_x,
      size_y=size_y,
      size_z=size_z,
      category=category,
      model=model,
    )
    Machine.__init__(self, backend=backend)
    self.backend: ImagerBackend = backend  # fix type

    self.register_will_assign_resource_callback(self._will_assign_resource)

  def _will_assign_resource(self, resource: Resource):
    if len(self.children) >= 1:
      raise ValueError(
        f"Imager {self} already has a plate assigned " f"(attempting to assign {resource})"
      )

  def get_plate(self) -> Plate:
    if len(self.children) == 0:
      raise NoPlateError("There is no plate in the plate reader.")
    return cast(Plate, self.children[0])

  async def _capture_auto_exposure(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    objective: Objective,
    auto_exposure: AutoExposure,
    focal_height: float,
    gain: float,
    **backend_kwargs,
  ) -> ImagingResult:
    """
    Capture an image with auto exposure.

    This function will iteratively adjust the exposure time until a good exposure is found.
    It uses the provided `evaluate_exposure` function to determine if the exposure is good, too high, or too low.
    It uses a weighted binary search to find the optimal exposure time. The search is weighted by exposure time,
    meaning that instead of splitting the range in half, we split the range at the point that equalizes the integral
    of the exposure time on both sides (this works out to be equal to the root mean square of the endpoints).
    """

    if focal_height == "auto":
      raise ValueError("Focal height must be specified for auto exposure")
    if gain == "auto":
      raise ValueError("Gain must be specified for auto exposure")

    def _rms_split(low: float, high: float) -> float:
      """Split point that equalizes ∫t dt on both sides (RMS of endpoints)."""
      if low == high:
        return low
      return math.sqrt((low**2 + high**2) / 2)

    low, high = auto_exposure.low, auto_exposure.high

    rounds = 0
    while high - low > 1e-3:
      if auto_exposure.max_rounds is not None and rounds >= auto_exposure.max_rounds:
        raise ValueError("Exceeded maximum number of rounds")
      rounds += 1

      p = _rms_split(low, high)
      res = await self.capture(
        well=well,
        mode=mode,
        objective=objective,
        exposure_time=p,
        focal_height=focal_height,
        gain=gain,
        **backend_kwargs,
      )
      assert len(res.images) == 1, "Expected exactly one image to be returned"
      im = res.images[0]
      evaluation = await auto_exposure.evaluate_exposure(im)

      if evaluation == "good":
        return res
      if evaluation == "lower":
        high = p
      elif evaluation == "higher":
        low = p
      else:
        raise ValueError(f"Unexpected evaluation result: {evaluation}")

    raise RuntimeError("Failed to find a good exposure time.")

  async def capture(
    self,
    well: Union[Well, Tuple[int, int]],
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Union[Exposure, AutoExposure] = "machine-auto",
    focal_height: FocalPosition = "machine-auto",
    gain: Gain = "machine-auto",
    **backend_kwargs,
  ) -> ImagingResult:
    if not isinstance(exposure_time, (int, float, AutoExposure)):
      raise TypeError(f"Invalid exposure time: {exposure_time}")
    if not isinstance(focal_height, (int, float)) and focal_height != "machine-auto":
      raise TypeError(f"Invalid focal height: {focal_height}")

    if isinstance(well, tuple):
      row, column = well
    else:
      idx = cast(Plate, well.parent).index_of_item(well)
      if idx is None:
        raise ValueError(f"Well {well} not in plate {well.parent}")
      row, column = divmod(idx, cast(Plate, well.parent).num_items_x)

    if isinstance(exposure_time, AutoExposure):
      assert focal_height != "machine-auto", "Focal height must be specified for auto exposure"
      assert gain != "machine-auto", "Gain must be specified for auto exposure"
      return await self._capture_auto_exposure(
        well=well,
        mode=mode,
        objective=objective,
        auto_exposure=exposure_time,
        focal_height=focal_height,
        gain=gain,
        **backend_kwargs,
      )

    return await self.backend.capture(
      row=row,
      column=column,
      mode=mode,
      objective=objective,
      exposure_time=exposure_time,
      focal_height=focal_height,
      gain=gain,
      plate=self.get_plate(),
      **backend_kwargs,
    )


def max_pixel_at_fraction(
  fraction: float, margin: float
) -> Callable[[Image], Awaitable[Literal["higher", "lower", "good"]]]:
  """The maximum pixel value in a given image should be a fraction of the maximum possible pixel value (eg 255 for 8-bit images).

  Args:
    fraction: the desired fraction of the actual maximum pixel value over the theoretically maximum pixel value (e.g. 0.8 for 80%). If it is an 8-bit image, the maximum value would be 0.8 * 255 = 204.
    margin: the margin of error that is accepted. A fraction of the theoretical maximum pixel value, e.g. 0.05 for 5%, so the maximum pixel value should be between 0.75 * 255 and 0.85 * 255.
  """

  if np is None:
    raise ImportError("numpy is required for max_pixel_at_fraction")

  async def evaluate_exposure(im) -> Literal["higher", "lower", "good"]:
    array = np.array(im, dtype=np.float32)
    value = np.max(array) - (255.0 * fraction)
    margin_value = 255.0 * margin
    if abs(value) <= margin_value:
      return "good"
    # lower the exposure time if the max pixel value is too high
    return "lower" if value > 0 else "higher"

  return evaluate_exposure


def fraction_overexposed(
  fraction: float, margin: float, max_pixel_value: int = 255
) -> Callable[[Image], Awaitable[Literal["higher", "lower", "good"]]]:
  """A certain fraction of pixels in the image should be overexposed (e.g. 0.5%).

  This is useful for images that are not well illuminated, as it ensures that a certain fraction of pixels is overexposed, which can help with image quality.

  Args:
    fraction: the desired fraction of pixels that should be overexposed (e.g. 0.005 for 0.5%). Overexposed is defined as pixels with a value greater than the maximum pixel value (e.g. 255 for 8-bit images). You can customize this number if needed.
    margin: the margin of error for the fraction of pixels that should be overexposed (e.g. 0.001 for 0.1%, so the fraction of overexposed pixels should be between 0.004 and 0.006).
    max_pixel_value: the maximum pixel value for the image (e.g. 255 for 8-bit images). You can override it to change the definition of "overexposed" pixels.
  """

  if np is None:
    raise ImportError("numpy is required for fraction_overexposed")

  async def evaluate_exposure(im) -> Literal["higher", "lower", "good"]:
    # count the number of pixels that are overexposed
    arr = np.asarray(im, dtype=np.uint8)
    actual_fraction = np.count_nonzero(arr > max_pixel_value) / arr.size
    lower_bound, upper_bound = fraction - margin, fraction + margin
    if lower_bound <= actual_fraction <= upper_bound:
      return "good"
    # too many saturated pixels -> shorten exposure
    return "lower" if (actual_fraction - fraction) > 0 else "higher"

  return evaluate_exposure
