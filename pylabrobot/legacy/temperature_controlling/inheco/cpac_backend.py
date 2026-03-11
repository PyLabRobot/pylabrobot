from pylabrobot.legacy.temperature_controlling.inheco.temperature_controller import (
  InhecoTemperatureControllerBackend,
)
from pylabrobot.inheco import cpac


class InhecoCPACBackend(InhecoTemperatureControllerBackend):
  """Legacy. Use pylabrobot.inheco.cpac.InhecoCPACBackend instead."""

  def __init__(self, index: int, control_box):
    self._new = cpac.InhecoCPACBackend(index=index, control_box=control_box)
