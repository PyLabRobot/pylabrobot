"""CytationMicroscopyBackend — MicroscopyBackend for the Cytation with Aravis.

Orchestrates capture by sequencing the driver's optics and camera methods.
Same role as STARPIPBackend: translates capability operations into driver calls.

Layer: Capability backend (orchestration)
Adjacent layers:
  - Above: Microscopy capability calls capture()
  - Below: CytationAravisDriver (optics + camera commands)
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Literal, Optional, Tuple, Union

from pylabrobot.capabilities.capability import BackendParams
from pylabrobot.capabilities.microscopy.backend import MicroscopyBackend
from pylabrobot.capabilities.microscopy.standard import (
  Exposure,
  FocalPosition,
  Gain,
  Image,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.serializer import SerializableMixin

if TYPE_CHECKING:
  from .cytation_aravis_driver import CytationAravisDriver

logger = logging.getLogger(__name__)


class CytationMicroscopyBackend(MicroscopyBackend):
  """MicroscopyBackend for the Cytation using Aravis camera.

  Orchestrates a capture by calling the driver's optics and camera methods
  in the correct sequence. Same pattern as STARPIPBackend: the backend
  translates capability operations into driver calls.

  Created by CytationAravisDriver during setup() and accessed via
  ``driver.microscopy_backend``.
  """

  def __init__(self, driver: CytationAravisDriver) -> None:
    self.driver = driver

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

  # ─── Vendor Params ──────────────────────────────────────────────────

  @dataclass
  class CaptureParams(BackendParams):
    """Cytation-specific parameters for image capture.

    Args:
      led_intensity: LED intensity (0-100). Default 10.
      coverage: Image tiling coverage. ``"full"`` for full-well montage, or a
        ``(rows, cols)`` tuple for a specific tile grid. Default ``(1, 1)`` (single
        image).
      center_position: Center position of the capture area as ``(x_mm, y_mm)`` relative
        to the well center. If None, centers on the well. Default None.
      overlap: Fractional overlap between tiles (0.0-1.0) for montage stitching.
        If None, no overlap. Only used when coverage produces multiple tiles.
      auto_stop_acquisition: Whether to automatically stop image acquisition after
        capture. Default True.
    """

    led_intensity: int = 10
    coverage: Union[Literal["full"], Tuple[int, int]] = (1, 1)
    center_position: Optional[Tuple[float, float]] = None
    overlap: Optional[float] = None
    auto_stop_acquisition: bool = True

  # ─── MicroscopyBackend.capture() ─────────────────────────────────────

  async def capture(
    self,
    row: int,
    column: int,
    mode: ImagingMode,
    objective: Objective,
    exposure_time: Exposure,
    focal_height: FocalPosition,
    gain: Gain,
    plate: Plate,
    backend_params: Optional[SerializableMixin] = None,
  ) -> ImagingResult:
    if not isinstance(backend_params, self.CaptureParams):
      backend_params = CytationMicroscopyBackend.CaptureParams()

    led_intensity = backend_params.led_intensity
    coverage = backend_params.coverage
    center_position = backend_params.center_position
    overlap = backend_params.overlap
    auto_stop_acquisition = backend_params.auto_stop_acquisition

    assert overlap is None, "not implemented yet"

    d = self.driver
    await d.set_plate(plate)

    if not d._acquiring:
      d.start_acquisition()

    try:
      await d.set_objective(objective)
      await d.set_imaging_mode(mode, led_intensity=led_intensity)
      await d.select(row, column)
      await d.set_exposure(exposure_time)
      await d.set_gain(gain)
      await d.set_focus(focal_height)

      def image_size(magnification: float) -> Tuple[float, float]:
        if magnification == 4:
          return (3474 / 1000, 3474 / 1000)
        if magnification == 20:
          return (694 / 1000, 694 / 1000)
        if magnification == 40:
          return (347 / 1000, 347 / 1000)
        raise ValueError(f"Don't know image size for magnification {magnification}")

      if d._objective is None:
        raise RuntimeError("Objective not set. Run set_objective() first.")
      magnification = d._objective.magnification
      img_width, img_height = image_size(magnification)

      first_well = plate.get_item(0)
      well_size_x, well_size_y = (first_well.get_size_x(), first_well.get_size_y())
      if coverage == "full":
        coverage = (
          math.ceil(well_size_x / image_size(magnification)[0]),
          math.ceil(well_size_y / image_size(magnification)[1]),
        )
      rows, cols = coverage

      if center_position is None:
        center_position = (0, 0)
      positions = [
        (x * img_width + center_position[0], -y * img_height + center_position[1])
        for y in [i - (rows - 1) / 2 for i in range(rows)]
        for x in [i - (cols - 1) / 2 for i in range(cols)]
      ]

      images: List[Image] = []
      for x_pos, y_pos in positions:
        await d.set_position(x=x_pos, y=y_pos)
        t0 = time.time()
        images.append(await d.acquire_image())
        t1 = time.time()
        logger.debug("[cytation] acquired image in %.2f seconds", t1 - t0)
    finally:
      await d.led_off()
      if auto_stop_acquisition:
        d.stop_acquisition()

    exposure_ms = await d.get_exposure()
    assert d._focal_height is not None
    focal_height_val = float(d._focal_height)

    return ImagingResult(images=images, exposure_time=exposure_ms, focal_height=focal_height_val)
