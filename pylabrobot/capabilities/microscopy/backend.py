from abc import ABCMeta, abstractmethod
from typing import Optional

from pylabrobot.capabilities.microscopy.standard import (
  Exposure,
  FocalPosition,
  Gain,
  ImagingMode,
  ImagingResult,
  Objective,
)
from pylabrobot.device import DeviceBackend
from pylabrobot.resources.plate import Plate
from pylabrobot.serializer import SerializableMixin


class MicroscopyBackend(DeviceBackend, metaclass=ABCMeta):
  """Abstract backend for microscopy devices."""

  @abstractmethod
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
    """Capture an image at the specified well position.

    Args:
      row: 0-indexed row of the well.
      column: 0-indexed column of the well.
      mode: Imaging mode (brightfield, fluorescence channel, etc.).
      objective: Objective lens to use.
      exposure_time: Exposure time in ms, or ``"machine-auto"`` for automatic.
      focal_height: Focal height in mm, or ``"machine-auto"`` for automatic.
      gain: Gain value, or ``"machine-auto"`` for automatic.
      plate: The plate being imaged (used for geometry/labware parameters).

    Returns:
      An :class:`ImagingResult` containing the captured image(s) and metadata.
    """
