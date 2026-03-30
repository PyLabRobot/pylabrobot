"""Legacy. Use pylabrobot.byonoy instead."""

from typing import Tuple

from pylabrobot.byonoy.absorbance_96 import (
  ByonoyAbsorbanceBaseUnit,
  byonoy_a96a_illumination_unit,
)
from pylabrobot.legacy.plate_reading.byonoy.byonoy_backend import ByonoyAbsorbance96AutomateBackend
from pylabrobot.legacy.plate_reading.plate_reader import PlateReader
from pylabrobot.resources import Resource


class ByonoyAbsorbance96Automate(PlateReader, ByonoyAbsorbanceBaseUnit):
  """Legacy. Use pylabrobot.byonoy.ByonoyAbsorbance96 instead."""

  def __init__(self, name: str):
    ByonoyAbsorbanceBaseUnit.__init__(self, name=name + "_base")
    PlateReader.__init__(
      self,
      name=name + "_reader",
      size_x=138,
      size_y=95.7,
      size_z=0,
      backend=ByonoyAbsorbance96AutomateBackend(),
    )


def byonoy_a96a_detection_unit(name: str) -> ByonoyAbsorbance96Automate:
  """Legacy. Use pylabrobot.byonoy.byonoy_a96a_detection_unit instead."""
  return ByonoyAbsorbance96Automate(name=name)


def byonoy_a96a(name: str, assign: bool = True) -> Tuple[ByonoyAbsorbance96Automate, Resource]:
  """Legacy. Use pylabrobot.byonoy.byonoy_a96a instead."""
  reader = byonoy_a96a_detection_unit(name=name + "_reader")
  illumination_unit = byonoy_a96a_illumination_unit(name=name + "_illumination_unit")
  if assign:
    reader.illumination_unit_holder.assign_child_resource(illumination_unit)
  return reader, illumination_unit
