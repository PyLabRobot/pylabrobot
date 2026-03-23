from typing import Optional

from pylabrobot.capabilities.microscopy.backend import MicroscopyBackend
from pylabrobot.capabilities.microscopy.standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.resources.plate import Plate
from pylabrobot.serializer import SerializableMixin

try:
  import numpy as np  # type: ignore

  HAS_NUMPY = True
except ImportError:
  np = None  # type: ignore[assignment]
  HAS_NUMPY = False


class MicroscopyChatterboxBackend(MicroscopyBackend):
  """Mock microscopy backend for testing."""

  async def setup(self) -> None:
    pass

  async def stop(self) -> None:
    pass

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
    if HAS_NUMPY:
      image = np.zeros((512, 512), dtype=np.uint16)
    else:
      image = [[0] * 512 for _ in range(512)]  # type: ignore

    return ImagingResult(
      images=[image],
      exposure_time=exposure_time if isinstance(exposure_time, (int, float)) else 10.0,
      focal_height=focal_height if isinstance(focal_height, (int, float)) else 0.0,
    )
